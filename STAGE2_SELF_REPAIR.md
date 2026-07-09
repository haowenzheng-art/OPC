# OPC Stage 2 — Self-Repair Loop

> 日期: 2026-07-08
> 状态: ✅ 核心机制 + 单测全过 + e2e 跑通 (随机样本未踩错配, repair 未实际触发)
> 配套: UPGRADE_PLAN.md Stage 2; SESSION_NOTES.md 2026-07-08

---

## 目标

让 OPC 在 LLM 生成代码**第一次出错时**自动用工具局部 patch,不再盲重抽整个 backend/frontend。
要解决的核心 bug: **前后端路由错配** (前端 fetch URL 跟 backend 实际路由对不上 → 404 → 用户报告"校验失败")。

## 现状盘点 (2026-07-07 之前已完成)

- `FailureSignal` 数据结构 (file_path / error_kind / error_msg / suggested_action / agent)
- `_extract_signals_from_errors()` 信号分类器
- `ProjectOrchestrator._run_repair_pass()` + `_sync_files_to_disk` / `_reload_files_from_disk`
- `BackendAgent.repair_with_tools()` / `FrontendAgent.repair_with_tools()`
- `ToolRegistry`: read_file / list_files / edit_file / bash / apply_patch
- `retry budget`: repair_passes[agent] < 3 才走 repair

## 这次新增 / 修复 (2026-07-08)

### 1. test_agent 交互阶段补 4xx/5xx signal 链路

`backend/app/agent/projects/test_agent.py`:
- 在 `_run_interaction_phase` 加 `page.on("response", on_response)` 监听器,把 backend 4xx/5xx 收成 `api_4xx_5xx[]`
- 在 api_calls 检查后加 2.5 步: 路由错配检测 → 输出 `VerificationResult(failed_agent="frontend", failure_signals=[http_404])`
- **关键: 之前前端 fetch 调错 URL 被吞掉,现在能被 orchestrator 看到并触发 frontend repair pass**

### 2. repair_with_tools prompt 强化

`backend/app/agent/projects/frontend_agent.py` + `backend_agent.py`:
- 强制 Step 0: `read_file("backend/api_contract.json")` — LLM 不再靠记忆
- 明确告诉 LLM 改 fetch URL 时, new_text 必须**逐字**等于 `endpoints[*].full`
- 明确 "不要重写 page.tsx 整文件", "不要改 design_spec", "不要新增 import"
- 修 backend 时也要先读 contract 再改 routes.ts
- 修了一处 f-string 的 `\`${...}\`` 转义 bug (Python f-string 会把 `${API}` 当变量, 用 `\${{API}}` 转义)

### 3. 单测覆盖闭环 (7/7 PASS)

`backend/tests/agent/test_self_repair.py`:
- `test_extract_signals_http_404_routes_to_frontend` — 信号分类准确性
- `test_extract_signals_http_500_routes_to_backend` — 500 路由给 backend
- `test_extract_signals_tsc_routes_to_frontend` — tsc 错路由
- `test_extract_signals_console_error_routes_to_frontend` — JS 运行时错
- `test_frontend_agent_repair_fix_fetch_url` — **项目 11 真实场景 mock 复现**: broken page.tsx 调错 URL → mock LLM 走 read_contract/read_page/edit → 文件真的改对
- `test_backend_agent_repair_add_missing_endpoint` — backend 缺 endpoint → mock LLM 加路由
- `test_frontend_agent_repair_returns_failed_when_llm_gives_up` — LLM 主动放弃 → repair success=False, orchestrator 可走 fallback

### 4. 环境修复

- `docker-compose.yml`: postgres 端口 5433→55432, redis 6379→56379 (避开 com.docker.backend 占用的 5432-5434/6379-6380)
- `backend/.env`: DATABASE_URL/REDIS_URL 同步更新
- Docker Desktop 启动, postgres/redis 容器就绪
- alembic 迁移: `alembic upgrade head` ✅
- `pip install playwright imagehash Pillow` + `playwright install chromium`

## 实测结果

### 项目 1 / 项目 2 跑通 e2e

- **项目 1**: HTTP 200 / tsc PASS / preview running → `[PASS]`
- **项目 2**: HTTP 200 / tsc PASS / preview running → `[PASS]`

### 重要观察: 路由错配 bug **依然存在**,但 e2e 测不到

- **项目 1 page.tsx:46**: `fetch(\`${API}/api/v1/history\`, { method: 'POST' })` ← 但 backend 实际 `POST /convert` / `GET /history`
- **项目 2 page.tsx:49**: `fetch(\`${API}/api/v1/temperature/history\`, { method: 'POST' })` ← 但 backend 实际 `POST /temperature/convert` / `GET /temperature/history`

两个项目前端都用 POST 调 history 端点,都是 404。但 e2e 只检查 page HTTP 200, 没点 form 提交, 所以测不出。

**Stage 2 self-repair 的真值校验需要: 让 test_agent interaction 阶段真去点 form → 4xx → repair 触发。** 之前的 interaction 阶段没跑是因为 playwright 没装, 现在装上了, 但实测时**LLM 又恰好写对了, repair 未被触发**。

### "卡了一下午" 的真相

- 4 次 e2e 跑下来每次 5-17 分钟 (LLM 调用 6+ 次 + npm install 2 次)
- 期间还穿插了 Docker Desktop 启动 / Postgres 配置 / alembic 迁移
- **没有真卡住**, 是耗时操作堆叠 + 对话间隔长

## 当前缺口 & 下一步

| Gap | 修法 | 优先级 |
|---|---|---|
| interaction 阶段经常没真触发 (LLM 走运 / Playwright launch 慢) | 给 interaction 阶段加更激进的触发: 任意 backend 4xx/5xx 立刻 fail | P0 |
| 残留 dev server 进程 (4102/4104/4202/4204) 没清 | 在 test_agent.teardown 阶段强制 kill `localhost:4100-4400` 上自己起的进程 | P1 |
| 主动验证 Stage 2 真值 | 写一个 fixture: 故意把 backend `/convert` 改名为 `/foo`, 跑一次 e2e, 看 self-repair 能否改回 | P0 |
| Stage 3 视觉 loop | UPGRADE_PLAN 计划, 等 Stage 2 验收过再做 | P2 |
| Stage 4 模板 seed | 跟 Stage 1-3 并行 | P2 |

## 建议下一步 (等你拍板)

**A. 主动注入错配验证 Stage 2 真值** (~30 分钟)
   - 写一个集成测试, 故意改坏 backend route
   - 跑 e2e, 看 repair pass 是否被触发, 文件是否被改对
   - 如果通过, Stage 2 算"端到端可观测地工作"

**B. 推进 Stage 3 视觉 loop** (~3-5 天, UPGRADE_PLAN 已有)
   - Sonnet 4.6 多模态评视觉
   - 视觉不过 → 触发 frontend repair 改样式

**C. 收拾残局 + 补 Stage 4 模板 seed** (~1 周)
   - 清 dev server 残留
   - todo-app / landing-page / dashboard / form / calculator 5 个模板
   - intent_router 命中模板就快路径

我建议先 **A** (30 分钟, 给出 Stage 2 闭环的真值), 再决定 B 还是 C。

## 关键文件速查

| 文件 | 改动 |
|---|---|
| `backend/app/agent/projects/test_agent.py` | + `on_response` 监听, + 2.5 步 4xx/5xx 检测 |
| `backend/app/agent/projects/frontend_agent.py` | repair prompt 强化 (Step 0 读 contract, fetch URL 严㸼匹配) |
| `backend/app/agent/projects/backend_agent.py` | repair prompt 强化 (Step 0 读 contract) |
| `backend/tests/agent/test_self_repair.py` | 新增, 7 个单测覆盖 signal+repair 闭环 |
| `docker-compose.yml` | postgres 55432, redis 56379 |
| `backend/.env` | DATABASE_URL/REDIS_URL 更新 |

## 验收参考

```bash
# 单测
.venv\Scripts\python.exe -m pytest tests/agent/test_self_repair.py -v
# → 7 passed in 2.20s

# e2e
.venv\Scripts\python.exe verify_preview_e2e.py --idea "做一个温度转换器..."
# → ALL CHECKS PASSED (但路由错配 bug 仍在, e2e 不覆盖)
```