# OPC 升级 Plan — Agent Loop 优先 + 模板种子

> 状态：草案，等用户确认 stage 0 后开始执行
> 与 `ARCHITECTURE_SKETCH.md` 配套：那里讲"为什么"，这里讲"怎么做"
> 上一版根因分析 → `OPC_BACKEND_LOG_ANALYSIS_2026-07-03.md`（用户手测 20+ 次只成功 1 次）

---

## 核心思路

把当前架构的"一次性 LLM 自由生成 + 盲重试"改成"**LLM 一次生成 + 自修复 loop + 视觉 loop**"。LLM 仍然是 PM / 实现 / 测试的所有者，但**每一类错误都有具体工具把信号喂回 LLM 让它改**，而不是简单 retry。

**成功模型**：
```
P(step_pass) = 50%  (LLM 一次写对 + 跑通的概率)
P(step_pass after N 次 self-repair) = 1 - 0.5^N
3 次 loop → 87.5%；5 次 loop → 96.9%
```

实际修复率会被错误信号清晰度打折：tsc 报错 70%+、dev server 启动错 80%、HTTP code 60%、视觉判断 30%。综合模型单步 ~75%、3 loop → 98%。

---

## Stage 0 — 基础恢复（P0，**先做，否则后面验证不了**）

**为什么先做**：当前 OPC 后端/前端全挂、链路不通、e2e 脚本端口错——任何改动都没法验证。

**任务清单**（不要触碰 searchengine 8001）：

- [x] 拉起 OPC backend：`cd backend && nohup .venv/Scripts/uvicorn.exe app.main:app --host 0.0.0.0 --port 8005`（PID 27248，LISTEN 8005）
- [x] 启动 OPC frontend：`cd frontend && nohup npm run dev`（PID 8632，LISTEN 3000，"Ready in 1167ms"）
- [x] 改 `verify_preview_e2e.py` 默认 BASE_URL=8005（可被 `OPC_BASE_URL` env 覆盖）；TEST_EMAIL/默认 verify5（可被 `OPC_TEST_EMAIL`/`OPC_TEST_PASSWORD` 覆盖）
- [x] 修 `test_agent._materialize` bytes/str 防御性 decode（`isinstance(content, bytes) → content.decode("utf-8", errors="replace")`）
- [x] 清理磁盘残留：`opc_screenshots_{1,2,23,24,8888,9999}` 临时目录
- [x] 跑通 `verify_preview_e2e.py`（默认 verify5 + 北京时钟 idea）：**新建 project 3，一次过 done，tsc --noEmit PASS，preview HTTP 200，5959 chars**

**实际验证记录（2026-07-05）**：

```
=== Step 1: login ===  token OK (len=279)
=== Step 2: create new project ===  created project id=3
=== Step 3: wait for generation ===
  project status=planning (18%)
  project status=developing (36%)
  project status=testing (82%)
  project status=done (100%)        ← 一次过，无 retry
=== Step 4: verify page.tsx compiles ===
  page.tsx: 230 lines, ends with: '}'
  tsc --noEmit: PASS
=== Step 5: verify package.json covers imports ===
  backend imports covered: ['@prisma/client', '@types/cors', '@types/express',
    '@types/node', 'cors', 'express', 'prisma', 'tsx', 'typescript', 'zod']
=== Step 6: trigger preview and verify HTTP 200 ===
  preview triggered: http://localhost:4206
  preview status=running
  HTTP 200, body length=5959 chars
[PASS] ALL CHECKS PASSED for project 3
```

**退出标准**：

- [x] backend LISTEN 8005，frontend LISTEN 3000
- [x] `curl http://localhost:8005/openapi.json` → 200（包含 `/api/v1/...`）
- [x] `curl http://localhost:3000/` → 307（next.js default redirect 到 `/login`）
- [x] e2e 6/6 step PASS，page.tsx 230 行完整闭合，preview HTTP 200

**预计时间**：半天（≈4 小时）

---

## Stage 1 — Agent Tools 层（**给 LLM 加工具调用**）

**核心交付**：让 BackendAgent / FrontendAgent 在 self-repair 时可以调用以下工具：

| 工具 | 输入 | 输出 | 用途 |
|---|---|---|---|
| `read_file` | path | 文件内容 | 看自己写的代码 / 看项目结构 |
| `list_files` | dir prefix | 文件列表 | 找哪些文件存在 |
| `edit_file` | path + 旧字符串 + 新字符串 | diff | 局部 patch，不是整段重抽 |
| `bash` | shell command | stdout/stderr | 跑 tsc / npm install / curl |
| `apply_patch` | unified diff 字符串 | success/fail | 同 edit_file 但更原子 |

**实现要点**：

- `app/agent/agent_tools.py` 新增 — 工具注册 + JSON Schema
- LLM 调 Anthropic `tools=` 参数（多模态走 Anthropic Messages API，已经在用）
- `BaseAgent` 子类增加 `_should_use_tools()` 钩子：默认 stage 1 仅在"修复 loop"启用，stage 2 改成总是可用
- 把工具结果存到 `context["tool_history"]`，方便 orchestrator 看 LLM 实际用了什么、失败了没

**关键文件**：

```
backend/app/agent/
  ├── agent_tools.py       (新增)
  ├── base_agent.py        (改：加 ToolHistory)
  ├── backend_agent.py     (改：注入 tool definitions)
  └── frontend_agent.py    (改：同上)
```

**退出标准**：

- 单测：mock 一个失败场景（tsc 报错），验证 agent 调 `edit_file` 修复 → tsc pass
- 手工：跑一次 todo list，agent 在 console 里能看到自己跑了 `bash: tsc --noEmit` 后改了某个文件

**预计时间**：1 周

---

## Stage 2 — Self-Repair Loop（**让失败变局部 patch**）

**核心交付**：test_agent 失败时，不再盲重抽，而是把错误信号结构化 → 喂给后端/前端 agent → LLM 局部 patch → 再跑。

**当前架构问题（来自上一版分析）**：

- `test_agent.fail` → `orchestrator.retry` → 整段 backend/frontend LLM 重抽
- 后端侥幸能跑 ≠ 前端对齐；前端侥幸对齐 ≠ 后端没改
- 失败信号（stderr、HTTP 404、控制台错）被吞了，没有回喂 LLM

**新流程**：

```
test_agent.run()
  ↓
[tcs + install + HTTP + 视觉]
  ↓
失败 → 收集 failure_signals[]
  ↓
failure_signals → 决定 patch 哪个 agent
  ↓
agent.run_with_tools(tools=[edit_file, bash, read_file], context={"failures": signals})
  ↓
agent 通过工具 patch → 文件写到磁盘
  ↓
回到 test_agent，再循环
loop budget: 3-5 次
```

**实现要点**：

- 把 `test_agent.VerificationResult` 加 `structured_failure: {file_path, error_kind, error_msg, suggested_action}`
- `ProjectOrchestrator._on_test_fail()` 重写：不是简单 retry，而是 `_run_repair_pass()`
- `_run_repair_pass()` 把 failure signal 喂给对应 agent，并要求 agent **先 `read_file` 看现状再 `edit_file` 改**
- loop budget 走 stage 5 的 cap，超了直接 fallback

**关键文件**：

```
backend/app/agent/
  ├── test_agent.py               (改：structured_failure)
  ├── project_orchestrator.py     (改：_on_test_fail → _run_repair_pass)
  ├── agent_tools.py              (依赖 stage 1)
  └── retry.py                    (改：loop_with_budget 而不是 fixed cap)
```

**退出标准**：

- 手工在 todo list / 时钟项目上，故意改坏一处（缺 package.json / fetch URL 错），看 agent 是否自动修
- 成功率提升验证：在已知 ~30% 失败的项目上重跑，自动修复通过率 ≥ 70%
- 总迭代次数 ≤ 5

**典型 bug 案例（project 3 实际出现，2026-07-05 用户报告）**：

- 用户输入"做时钟"，前端却写出 todo list UI（addItem/toggleItem/removeItem）
- 后端只有 `/time /health /preferences/:key`，前端却调 addItem 时没 API
- 结果：UI 看似能 add，刷新即丢——**端到端 schema 完全不对齐**
- Self-repair 应做：检测到 `addItem` 没对应 endpoint 时，让前端 agent 改用 `localStorage`，或让后端 agent 补 `/todos` 路由

**预计时间**：1 周

---

## Stage 3 — Visual Loop（**多模态 LLM 评视觉**）

**核心交付**：用多模态 LLM（Claude Sonnet 4.6 或 GPT-4o）看截图，判断"行不行"，不行就让前端 agent 改样式。

**实现要点**：

- `_run_visual_phase()` 已在 `test_agent.py` 用 playwright + pHash——保留作为 fast filter
- 加一个新 phase `_run_multimodal_visual_phase()`：
  - playwright 截图（已有）
  - 把图 + "这是 todo list 项目，请判断是否符合正常 todo app 视觉规范（框框、间距、配色、按钮、删除按钮、空状态、loading）" 喂给 Sonnet 4.6
  - LLM 返回结构化评分 {score: 0-10, issues: [...]}
  - score < 7 → 触发前端 agent 修样式（prompt 含 issues 列表）
  - 最多 2 次（避免无限循环）
- LLM provider 选 Sonnet 4.6（Anthropic 协议，OPC 已经在用，比 minimax M3 视觉好得多）
- 注意：minimax M3 当前没有视觉能力，**这个 stage 需要换 LLM provider 配置**——在前端 agent 调视觉时切到 anthropic

**关键文件**：

```
backend/app/agent/
  ├── test_agent.py               (改：加 multimodal visual phase)
  ├── frontend_agent.py           (改：visual issues 修复工具)
  └── app/agent/llm.py            (改：multi-provider routing)
```

**退出标准**：

- 故意生成一个无 CSS 的 page.tsx，agent 自动加 Tailwind className 直到视觉 ≥ 7/10
- 视觉过线率（手工评判 5 个项目）≥ 80%

**预计时间**：3-5 天

---

## Stage 4 — Template Seed（**高频模板作种子，并行推进**）

**核心交付**：3-5 个高频需求的"预制可运行模板"，作为 agent 的初始文件集，而不是从 0 写。

**并行推进**：这个 stage 跟 stage 1-3 同时做，不阻塞主路径。

**选模板标准**：用户已经测过且失败的同类需求。

- [ ] `todo-app-v2` — CRUD + localStorage（已有 `fallback_template/`，但太简陋）
- [ ] `landing-page-v1` — 营销页 + 联系方式 + 配色可调
- [ ] `dashboard-v1` — 卡片网格 + 简单图表（纯前端，不依赖后端）
- [ ] `form-v1` — 表单 + 校验 + 提交状态
- [ ] `calculator-v1` — 用户测试成功的那个基础上补强（加框框 + 排版 + 历史记录）

每个模板自带：
- 完整代码（人工 review + playwright 套件）
- `slots.json`（LLM 可填的可变字段）
- 默认配色 / 文案
- 自带 e2e（playwright 跑通即视为成功）

**实现要点**：

- `app/agent/intent_router.py`（新建）：LLM 分类"用户想做哪种"
  - 命中模板 → 把模板文件 + slot info 作为初始 context
  - 没命中 → 0 → 1 写代码（走通用 self-repair loop）
- `app/agent/template_loader.py`：从 `app/templates/<id>/` 加载初始 files
- LLM 仅做 slot 填充（已有模板的情况下不再写代码）

**关键文件**：

```
backend/
  ├── app/
  │   ├── templates/
  │   │   ├── todo-app-v2/
  │   │   ├── landing-page-v1/
  │   │   ├── dashboard-v1/
  │   │   ├── form-v1/
  │   │   └── calculator-v1/
  │   ├── agent/
  │   │   ├── intent_router.py     (新增)
  │   │   └── template_loader.py   (新增)
```

**退出标准**：

- 5 个模板各自的 `playwright e2e` 都通过
- 用户提"做一个 todo list"时，intent_router 命中 → 直接走模板分支 → 端到端 ≤ 30s 通过

**预计时间**：1 周（含 review；可分散到 stage 1-3 之间做）

---

## Stage 5 — Loop Budget & Rollback（**安全网**）

**核心交付**：整体 cap + 不同信号类型的优先级，避免无限循环。

**实现要点**：

- 整个项目生命周期内 "loop 总次数 ≤ 5"
- 每类信号有 retry 权重：
  - tsc / install 错：3 次（同错误停）
  - HTTP / port 错：2 次
  - 视觉：2 次（剩下 budget 给功能）
- 超过就进 fallback（已有 `level1_fallback_start` / `level2_fallback_start`）
- 失败信号汇总 → `_format_report()` 输出更结构化的报告（用户能看到"卡在哪类信号")

**关键文件**：

```
backend/app/agent/
  ├── retry.py            (改：分层 budget)
  ├── fallback.py         (改：失败信号汇总到 report)
  └── test_agent.py       (改：失败信号分类)
```

**退出标准**：

- 循环检测：故意构造 always-fail 的输入，验证 5 次 cap → fallback
- 没有跑超过 5 次的合法项目被错误截断

**预计时间**：1 天

---

## 验收标准（**整个升级完成时**）

| 指标 | baseline（现状） | 目标 |
|---|---|---|
| 端到端成功率（手工测 10 个不同需求） | ~5%（20+ 次 1 次成功） | ≥ 75% |
| 视觉过线率（多模态 LLM 自评 ≥ 7/10） | ~10% | ≥ 80% |
| 单项目耗时 | 不收敛（retry loop） | ≤ 8 分钟 |
| LLM 调用次数 / 项目 | 6-12 次常 retry 到 20+ | ≤ 10 次（命中模板 ≤ 3 次） |
| 用户操作 | 反复调试 / 重做 | 一句话→看到能跑的 |

---

## 风险 & Rollback

| 风险 | 处理 |
|---|---|
| self-repair loop 不收敛 / 死循环 | loop budget cap + 同错误连续两次就停 |
| 多模态 LLM 成本高 | 仅在视觉 phase 用 Sonnet 4.6，其余阶段继续用 minimax M3 |
| 模板拖 stage 1-3 进度 | 模板独立仓库，并行推进，stage 4 不阻塞主路径 |
| miniMax M3 没有视觉能力 → stage 3 失败 | 已经预留 fallback：纯视觉降级为 pHash diff 检测 |
| miniredis / Redis 进程问题 | 现在 backend 已经直接走 background_task；不必依赖 Redis |
| Windows uvicorn orphan | `pkill` 时严格按 command line 限定 `\opc\` 路径（见 [[feedback-no-kill-sibling-projects]]） |

**回滚策略**：每个 stage 独立 commit；任意 stage 失败不影响前一个 stage 已有的修复。`git revert <stage_tag>` 即可回退。

---

## 执行顺序（**串行依赖关系**）

```
Stage 0 (基础恢复)                    [今天]
  └→ Stage 1 (Agent Tools)           [1 周后]
       └→ Stage 2 (Self-Repair)       [2 周后]
            └→ Stage 3 (Visual Loop)  [3 周后]
                 └→ Stage 5 (Budget & Rollback) [3 周+1 天]
  
Stage 4 (Template Seed)              [与 Stage 1-3 并行]
```

---

## 下一步

- 用户确认 stage 0 任务清单无误
- 我开始 stage 0：拉 backend / frontend / 修 verify 端口 / 修 `_materialize` bytes bug
- 完成 stage 0 后，pull request 或 commit 标注 `stage-0`，进入 stage 1
