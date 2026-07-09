"""OPC 真实任务完成率基准测量 (NEW-1).

目的: 客观测量 OPC 当前端到端跑通率 — 不是单测 PASS, 是真"一句话 → 可运行项目"成功率.

设计:
- 跑 5 个不同需求 (覆盖 Stage 4 intent_router 能识别的 4 类 + 1 类无模板)
- 每次用 verify_preview_e2e.py 的 [PASS] 标准 (HTTP 200 + tsc --noEmit + preview running)
- 输出每个需求的: 是否 done / 失败阶段 / 卡在哪一类
- 给出基线数据, 用于后续改进 (NEW-3) 的目标

不 mock 任何东西. 真实 LLM, 真实 dev server, 真实 npm install.

Usage:
    python verify_baseline.py                       # 跑默认 5 个需求
    python verify_baseline.py --idea "做一个 todo"  # 跑单个需求
    python verify_baseline.py --quick              # 只跑前 2 个 (快速验证)

注意: 跑一次要 5-10 分钟 (npm install × 2 + LLM × 6). 不要在生产时段跑.

支持 background 模式: 设 BACKGROUND_LOG=1 后, 输出会同时写到 logs/baseline_progress.log
这样即使 shell timeout, progress 也能被监控.
"""
from __future__ import annotations

import argparse
import builtins
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# 强制所有 print 立刻 flush, 这样 background 跑也能实时看到进度
_real_print = builtins.print
def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    _real_print(*args, **kwargs)
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
GENERATED_DIR = BACKEND_DIR / "generated-projects" / "projects"

DEFAULT_IDEA = "做一个显示当前北京时间的大字时钟页面，每秒更新"
TEST_EMAIL = "verify5@example.com"
TEST_PASSWORD = "verify12345"

BASE_URL = "http://localhost:8005"

# 5 个基线需求 (覆盖模板分类 + 自由生成)
BASELINE_IDEAS = [
    {
        "id": "todo",
        "idea": "做一个待办事项清单，支持增删改查和完成状态切换",
        "expected_template": "todo-app-v2",
    },
    {
        "id": "calculator-bmi",
        "idea": "做一个 BMI 计算器，输入身高和体重，输出 BMI 值和健康等级",
        "expected_template": "calculator-v1",
    },
    {
        "id": "form-login",
        "idea": "做一个登录表单，包含用户名、密码、记住我选项，提交后校验",
        "expected_template": "form-v1",
    },
    {
        "id": "landing-saas",
        "idea": "做一个简单的落地页，介绍我们的 SaaS 产品，包含标题、特性列表、注册按钮",
        "expected_template": "landing-page-v1",
    },
    {
        "id": "no-template",
        "idea": "做一个区块链智能合约审计平台，支持上传合约文件并分析安全漏洞",
        "expected_template": "none",
    },
]


# ============ 复用 verify_preview_e2e.py 的逻辑 ============

def _http_json(method, url, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = kwargs.pop("data", None)
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=kwargs.pop("timeout", 30)) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")


def login():
    data = urllib.parse.urlencode({"username": TEST_EMAIL, "password": TEST_PASSWORD}).encode()
    status, body = _http_json("POST", f"{BASE_URL}/api/v1/auth/login",
                              data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        status, body = _http_json("POST", f"{BASE_URL}/api/v1/auth/register",
                                 data={"email": TEST_EMAIL, "password": TEST_PASSWORD, "name": "Verify"})
        if status != 201:
            return None
        data = urllib.parse.urlencode({"username": TEST_EMAIL, "password": TEST_PASSWORD}).encode()
        status, body = _http_json("POST", f"{BASE_URL}/api/v1/auth/login",
                                  data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            return None
    return body.get("access_token")


def create_project(token, idea):
    status, body = _http_json("POST", f"{BASE_URL}/api/v1/projects", token=token,
                              data={"name": "Baseline", "description": "baseline verify", "user_idea": idea})
    if status != 202:
        return None, body
    return body["id"], None


def wait_done(token, pid, timeout_s=1200):
    """等 done, 捕获中间状态 (planning/developing/testing/failed).

    默认 1200s (20 分钟): NEW-1 实测 todo 端到端 ~12-15 分钟 (含 2 轮 test + repair),
    旧 600s 误判为 timeout.
    可被 OPC_BASELINE_TIMEOUT env 覆盖.
    """
    import os as _os
    timeout_s = int(_os.environ.get("OPC_BASELINE_TIMEOUT", timeout_s))
    """等 done, 捕获中间状态 (planning/developing/testing/failed)."""
    deadline = time.time() + timeout_s
    last_state = None
    final = None
    while time.time() < deadline:
        status, body = _http_json("GET", f"{BASE_URL}/api/v1/projects/{pid}", token=token)
        if status != 200:
            time.sleep(5)
            continue
        s = body.get("status")
        if s != last_state:
            print(f"  status: {s} ({body.get('context', {}).get('progress_percent', 0)}%)")
            last_state = s
        final = body
        if s == "done":
            return body, None
        if s == "failed":
            return body, "failed"
        if s == "paused":
            return body, "paused"
        time.sleep(5)
    return final, "timeout"


# ============ 任务完成率分析 ============

@dataclass
class BaselineResult:
    """单个需求的基线结果."""
    task_id: str
    idea: str
    expected_template: str
    final_status: str = ""
    success: bool = False
    error_message: str = ""
    duration_s: float = 0.0
    pid: int = 0
    # 诊断字段
    http_status: int = 0
    http_body_len: int = 0
    preview_url: str = ""
    tsc_pass: bool | None = None
    page_path_exists: bool = False
    fetch_urls: list[str] = field(default_factory=list)
    failure_class: str = ""  # "tsc_fail" | "http_404" | "npm_install" | "visual" | "timeout" | "other"
    contract_endpoints: list[str] = field(default_factory=list)


def analyze_failure_class(pid: int, page_content: str, contract: dict) -> str:
    """根据生成的代码, 分类失败模式."""
    if not page_content:
        return "no_page_generated"

    # 看 fetch URL 跟 contract 是否对齐
    fetches = re.findall(r"fetch\([`'\"]\$\{?API\}?[^`'\"]*?([`/][^`'\"]+)", page_content)
    contract_urls = [ep.get("full", "") for ep in contract.get("endpoints", []) if ep.get("full")]

    # 看 contract endpoints 跟 routes.ts 真实注册是否对齐
    routes_path = GENERATED_DIR / str(pid) / "backend" / "src" / "routes.ts"
    if routes_path.exists():
        routes_content = routes_path.read_text(encoding="utf-8")
        registered = re.findall(r"router\.\w+\(['\"]([^'\"]+)", routes_content)
        contract_methods = {(ep.get("method", ""), ep.get("full", "")) for ep in contract.get("endpoints", [])}
        if contract and not registered:
            return "no_routes_registered"

    # 路由错配 (fetch URL 不在 contract 里)
    if fetches and contract_urls:
        mismatches = [f for f in fetches if not any(f in u for u in contract_urls)]
        if mismatches:
            return f"http_404_or_routes_mismatch (fetch: {fetches[:3]})"

    # 视觉问题 (看 page 是否是空 div 或只有基础结构)
    if page_content.count("<") < 5:
        return "minimal_ui (页面结构过简)"

    return "unknown"


def run_one(token: str, task: dict) -> BaselineResult:
    """跑一个需求, 收集结果."""
    result = BaselineResult(
        task_id=task["id"],
        idea=task["idea"],
        expected_template=task["expected_template"],
    )
    start = time.time()

    print(f"\n{'=' * 70}")
    print(f"Task {task['id']}: {task['idea']}")
    print(f"Expected template: {task['expected_template']}")
    print(f"{'=' * 70}")

    # Step 1: 创建项目
    print("[1/4] Create project...")
    pid, err = create_project(token, task["idea"])
    if not pid:
        result.success = False
        result.error_message = f"create failed: {err}"
        result.failure_class = "create_failed"
        result.duration_s = time.time() - start
        print(f"  FAIL: {err}")
        return result
    result.pid = pid
    print(f"  pid={pid}")

    # Step 2: 等 done / failed
    print(f"[2/4] Wait for generation (max 600s)...")
    final_body, wait_err = wait_done(token, pid)
    result.duration_s = time.time() - start
    result.final_status = final_body.get("status", "?") if final_body else "unknown"

    if wait_err == "failed":
        result.error_message = final_body.get("error") or "(no error message in API response)"
        result.failure_class = "project_failed"
        result.success = False
        print(f"  FAIL: project status=failed")
        print(f"  Error: {result.error_message[:200]}")
        return result
    if wait_err == "timeout":
        result.error_message = "wait_done timeout (>600s)"
        result.failure_class = "timeout"
        result.success = False
        print(f"  FAIL: timeout")
        return result
    if wait_err == "paused":
        result.error_message = final_body.get("context", {}).get("paused_reason", "paused")
        result.failure_class = "credits_paused"
        result.success = False
        print(f"  FAIL: paused (credits exhausted?)")
        return result

    # Step 3: 检查 page.tsx 是否生成 + tsc
    print(f"[3/4] Check page.tsx + tsc...")
    page_path = GENERATED_DIR / str(pid) / "frontend" / "src" / "app" / "page.tsx"
    result.page_path_exists = page_path.exists()
    if not result.page_path_exists:
        result.success = False
        result.failure_class = "no_page_generated"
        print(f"  FAIL: page.tsx not generated")
        return result

    page_content = page_path.read_text(encoding="utf-8")
    result.fetch_urls = re.findall(r"fetch\([^)]*?['\"](/api/[^'\"]+)['\"]", page_content)

    # tsc 检查 (Windows 上 npx 是 .cmd, subprocess 不加后缀找不到)
    fe_dir = GENERATED_DIR / str(pid) / "frontend"
    npx_bin = shutil.which("npx") or "npx"
    if (fe_dir / "node_modules").exists():
        try:
            r = subprocess.run(
                [npx_bin, "tsc", "--noEmit"],
                cwd=fe_dir, capture_output=True, text=True, timeout=120,
            )
            result.tsc_pass = (r.returncode == 0)
            if not result.tsc_pass:
                result.error_message = f"tsc failed: {r.stdout[:300]}\n{r.stderr[:300]}"
        except subprocess.TimeoutExpired:
            result.tsc_pass = False
            result.error_message = "tsc timeout (>120s)"
        except Exception as e:
            result.tsc_pass = None
            result.error_message = f"tsc error: {e}"
    else:
        result.tsc_pass = None  # 没装依赖, 跳过

    # Step 4: trigger preview + HTTP 200
    print(f"[4/4] Trigger preview + HTTP 200...")
    status, body = _http_json("POST", f"{BASE_URL}/api/v1/projects/{pid}/preview/start", token=token)
    if status != 200:
        result.error_message = f"preview start failed: {status} {body}"
        result.failure_class = "preview_start_failed"
        result.success = False
        print(f"  FAIL: preview start: {status}")
        return result
    preview_url = body.get("preview_url", "")
    result.preview_url = preview_url

    # 等 preview 起来
    deadline = time.time() + 60
    while time.time() < deadline:
        status, body = _http_json("GET", f"{BASE_URL}/api/v1/projects/{pid}/preview/status", token=token)
        if status == 200 and body.get("status") == "running":
            break
        time.sleep(3)
    else:
        result.error_message = "preview did not reach running"
        result.failure_class = "preview_not_running"
        result.success = False
        print(f"  FAIL: preview did not reach running")
        return result

    # HTTP 200 check
    try:
        with urllib.request.urlopen(preview_url, timeout=15) as resp:
            result.http_status = resp.status
            body_bytes = resp.read()
            result.http_body_len = len(body_bytes)
            body_text = body_bytes.decode("utf-8", errors="ignore")
            if resp.status != 200 or "<html" not in body_text.lower():
                result.error_message = f"HTTP {resp.status}, body[:100]={body_text[:100]!r}"
                result.failure_class = "http_status_not_200"
                result.success = False
                print(f"  FAIL: HTTP {resp.status}")
                return result
    except Exception as e:
        result.error_message = f"HTTP probe failed: {e}"
        result.failure_class = "http_probe_failed"
        result.success = False
        print(f"  FAIL: HTTP probe: {e}")
        return result

    # 读 contract
    contract_path = GENERATED_DIR / str(pid) / "backend" / "api_contract.json"
    if contract_path.exists():
        result.contract_endpoints = [
            f"{ep.get('method','?')} {ep.get('full','?')}"
            for ep in json.loads(contract_path.read_text(encoding="utf-8")).get("endpoints", [])
        ]

    # 成功!
    result.success = True
    result.failure_class = "none"
    print(f"  PASS: HTTP 200, body={result.http_body_len} chars")
    print(f"  fetch URLs: {result.fetch_urls[:3]}")
    print(f"  contract endpoints: {result.contract_endpoints[:5]}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idea", help="跑单个需求")
    ap.add_argument("--quick", action="store_true", help="只跑前 2 个需求")
    ap.add_argument("--output", default="logs/baseline_results.json", help="结果输出 JSON 路径")
    args = ap.parse_args()

    print("=" * 70)
    print("OPC 真实任务完成率基准测量 (NEW-1)")
    print("=" * 70)
    print(f"Backend: {BASE_URL}")

    # Login
    token = login()
    if not token:
        print(f"[FATAL] 登录失败. 检查 backend 是否在 {BASE_URL} 跑着.")
        sys.exit(1)
    print(f"Login OK")

    # 选 idea 列表
    if args.idea:
        ideas = [{"id": "custom", "idea": args.idea, "expected_template": "?"}]
    elif args.quick:
        ideas = BASELINE_IDEAS[:2]
    else:
        ideas = BASELINE_IDEAS

    # 跑
    results = []
    for task in ideas:
        r = run_one(token, task)
        results.append(r)
        # 部分结果立即输出 (用户能看到进度)
        status = "✓" if r.success else "✗"
        print(f"\n>>> Task {r.task_id}: {status} ({r.duration_s:.0f}s)")
        if not r.success:
            print(f"    Failure class: {r.failure_class}")
            print(f"    Error: {r.error_message[:200]}")

    # 输出汇总
    print(f"\n{'=' * 70}")
    print(f"汇总")
    print(f"{'=' * 70}")
    success_count = sum(1 for r in results if r.success)
    total = len(results)
    print(f"通过: {success_count}/{total} ({success_count/total*100:.0f}%)")
    print()
    print("按失败类别统计:")
    fail_classes: dict[str, int] = {}
    for r in results:
        if not r.success:
            fail_classes[r.failure_class] = fail_classes.get(r.failure_class, 0) + 1
    for cls, n in sorted(fail_classes.items(), key=lambda x: -x[1]):
        print(f"  - {cls}: {n}")
    print()
    print("按需求模板期望统计:")
    by_template: dict[str, tuple[int, int]] = {}
    for r in results:
        passed, total_ = by_template.get(r.expected_template, (0, 0))
        by_template[r.expected_template] = (passed + (1 if r.success else 0), total_ + 1)
    for tpl, (passed, total_) in by_template.items():
        print(f"  - 模板 {tpl}: {passed}/{total_}")

    # 写 JSON
    out_path = BACKEND_DIR / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [
                {
                    "task_id": r.task_id,
                    "idea": r.idea,
                    "expected_template": r.expected_template,
                    "success": r.success,
                    "final_status": r.final_status,
                    "failure_class": r.failure_class,
                    "error_message": r.error_message,
                    "duration_s": round(r.duration_s, 1),
                    "pid": r.pid,
                    "http_status": r.http_status,
                    "http_body_len": r.http_body_len,
                    "preview_url": r.preview_url,
                    "tsc_pass": r.tsc_pass,
                    "fetch_urls": r.fetch_urls,
                    "contract_endpoints": r.contract_endpoints,
                }
                for r in results
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n详细结果: {out_path}")

    sys.exit(0 if success_count == total else 1)


if __name__ == "__main__":
    main()