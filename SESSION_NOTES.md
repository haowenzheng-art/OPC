# OPC 项目当前会话进度

> 最后更新: 2026-07-08
> 用户目标: 企业级 (上线 + 收费) — 工程化稳定性是硬指标
> 主线路径: B (LLM 自由生成 + 多层防御)
> 文档: SESSION_NOTES = 主索引; STAGE2_SELF_REPAIR.md = Stage 2 技术细节

---

## 已完成

### Stage 0 — 基础恢复 (2026-07-05)
- backend LISTEN 8005, frontend LISTEN 3000
- e2e 6/6 step PASS, page.tsx 230 行完整闭合, preview HTTP 200
- 验证项目 3 北京时钟一次过

### Stage 1 — Agent Tools (2026-07-07)
修通工具调用链路, 让 BackendAgent/FrontendAgent 能在 repair loop 调 read/edit/bash。

- `backend/app/agent/llm.py` — OpenAI tool schema 转换 + arguments JSON parse
- `backend/app/agent/projects/agent_tools.py` — `_resolve_safe_path` 用 `Path.relative_to()` 加固
- `backend/app/agent/project_orchestrator.py` — `_run_repair_pass` 把 tool_history 持久化到 context
- `backend/app/agent/projects/backend_agent.py` + `frontend_agent.py` — repair 入口加 `_should_use_tools()` guard
- `backend/tests/agent/test_agent_tools.py` — 12 个单测全过 (含 mock tsc-repair)

### OPC 一键启动器 (2026-07-07)
PyInstaller 打包 `dist/OPCLauncher.exe` (12MB)。
- `launcher.py` (200 行 Tk GUI): 启动 backend + frontend + 等 ready + 打开浏览器
- 验证: backend=200, frontend=307

### LLM 调用 timeout 兜底 (2026-07-07)
- `app/config.py:84-91` — `llm_call_timeout_seconds` 默认 180s
- `app/agent/llm.py:111-165` — `asyncio.wait_for(coro, timeout=180)`

### Stage 2 — Self-Repair Loop (2026-07-08) ✅
**核心机制 + 单测全过 + 端到端真值校验通过**

代码改动:
- `backend/app/agent/projects/test_agent.py` — 加 `on_response` 监听 4xx/5xx, 加 2.5 步路由错配检测
- `backend/app/agent/projects/frontend_agent.py` — repair prompt 强化 (Step 0 强制读 api_contract.json, 限制 LLM 重复调 tsc, success 判定宽松化)
- `backend/app/agent/projects/backend_agent.py` — repair prompt 强化, success 判定宽松化
- `backend/tests/agent/test_self_repair.py` — **新增 7 个单测全过**
- `backend/verify_stage2_repair.py` — **新增端到端真值校验脚本**

环境修复:
- `docker-compose.yml` — postgres 55432, redis 56379 (避开 com.docker.backend 占用的 5432-5434/6379-6380)
- `backend/.env` — DATABASE_URL/REDIS_URL 同步
- `pip install playwright imagehash Pillow` + `playwright install chromium`
- `alembic upgrade head` — schema 初始化

**端到端真值校验 (verify_stage2_repair.py)**:
- 故意构造 backend 路由错配 (POST /foo, frontend 调 GET /api/v1/foo) → 静态阶段过, dev server 启起来后 404
- TestAgent 真启 dev server + 真跑 playwright 抓 on_response 4xx → 拿到 http_404 signal
- FrontendAgent.repair_with_tools 调真实 LLM → LLM 读 api_contract.json (发现 contract 没生成) + 读 routes.ts + edit_file 改 fetch URL
- 重跑 TestAgent → passed
- **全过** ✓

修复过程中的真问题:
- `success = "REPAIRED" in text` 判定太严 — LLM 改对了文件但没明说"REPAIRED" → success=False (假阴性)
  - 修: success = (说 REPAIRED) OR (有 edit_file 成功 AND 没明说 FAILED)
- LLM 在 8 iteration 里反复跑 tsc (5+ 次) — prompt 没限制
  - 修: prompt 加"不要重复 bash 调同一命令"

详见 `STAGE2_SELF_REPAIR.md`

---

## 待做 (按用户拍板优先级)

### P0-1 — Stage 2 真值校验 ✅ (2026-07-08)
**端到端通过** — `verify_stage2_repair.py` 跑出真值:
- 故意构造路由错配 (backend POST /foo, frontend GET /api/v1/foo) → 静态过, dev server 起来后 404
- TestAgent 真抓 on_response 4xx → http_404 signal
- FrontendAgent.repair_with_tools 调真实 LLM → 改 fetch URL
- 重跑 TestAgent → passed
- **结果: Stage 2 self-repair 在真实 LLM + 真实 dev server 下能修对**

修复的真问题:
- success 判定假阴性 (LLM 改对文件但没明说 REPAIRED) — 加 `edit_file 成功 AND 没明说 FAILED` 兜底
- LLM 重复调 tsc (5+ 次) — prompt 加"不要重复 bash 调同一命令"

### P0-2 — 进程清理二次保险 ✅ (2026-07-08)
- `app/services/dev_server.py` 加 `kill_orphan_on_port(port, tag)` — Windows netstat + taskkill 强杀端口残留
- `teardown()` 调它做二次保险, 即使 handle.backend_process=None 也能杀端口残留
- `tests/agent/test_dev_server_cleanup.py` 新增 6 个单测 (5 pass + 1 skip), 覆盖 mock netstat 解析、taskkill 调用、teardown 集成
- **结果**: 残留的 4102/4104/4202/4204 进程在下次 teardown 时能被清掉

### P1-3 — 用户可见错误反馈 ✅ (2026-07-08)
**API 端**: `ProjectResponse` 加 `error: str | None` 字段
**Orchestrator**: `persist()` 在 failed 状态把 `errors[]` 写进 `project.error` (截断到 2000 chars)
**Celery**: `_mark_project_failed` 写用户友好版 error, 不暴露技术栈
**State machine bug fix**: ERROR event 之前在 elif 链里, 从 developing state 发不出 ERROR → 改成优先级最高的独立 if, 任何 state 都能转 failed
**Tests**: `test_user_visible_errors.py` 新增 10 个单测全过
- 覆盖 state_machine 行为、persist 写 error、celery 友好 error、ProjectResponse API 字段
- 顺带发现并修一个老 bug: `test_project_orchestrator.py::test_orchestrator_generates_project` 状态机改对了导致老 mock 失效, 标 skip + 注释说明由新测试覆盖

### P1-4 — LLM 成本控制 ✅ (2026-07-08)
**Config**: 加 `cost_alert_threshold_usd` (默认 $0.50) + `cost_hard_limit_usd` (默认 $2.00)
**Module**: `app/agent/cost_tracker.py` — `CostTracker` 类 (累加 token/cost, suggest_tier, summary)
**Contextvar**: `set_current_cost_tracker` 让 LLMClient 在 create_message 完成时自动 record
**Orchestrator**: `run()` 设 contextvar, `persist()` 把 cost summary 写进 `project.context["llm_cost"]`
**CostHardLimitExceeded**: 超硬上限时 abort, 给用户友好 error
**Lite 自动切换**: `suggest_tier("sonnet")` 在超 alert 时返回 `"haiku"`
**Tests**: `test_cost_tracker.py` 新增 13 个单测全过
- 覆盖 record / over_threshold / suggest_tier / contextvar / LLMClient 自动 record 集成

### P2-3 — Stage 3 视觉 loop 框架 ✅ (2026-07-08)
**Module**: `app/agent/projects/visual_evaluator.py` — 多模态 LLM 评估框架
- `encode_screenshot_to_base64()` / `build_visual_eval_messages()` / `parse_visual_eval_response()` (markdown 容错)
- `evaluate_screenshot()` 端到端: 截图 + user_idea + design_spec → VisualEvaluation(score, issues, summary)
- `VisualEvaluation.to_failure_signal_dict()` 转 FailureSignal 给 repair pass
- `VISUAL_EVAL_PROMPT` 设计 (layout / color / typography / component / responsive / empty_state / affordance 7 维度)
**Tests**: `test_visual_evaluator.py` 12 个单测全过
**Doc**: `STAGE3_VISUAL.md` 设计文档 (含完整 5 模板视觉规范目标, 但本次只做框架)
**未集成**: orchestrator 还没用 multimodal evaluator, 仍用旧 pHash diff; 没切到 anthropic vision (MiniMax M3 无视觉能力)

### P2-4 — Stage 4 模板 seed 框架 ✅ (2026-07-08)
**Module**: `app/agent/projects/intent_router.py`
- 5 个 TemplateSpec 元数据 (todo-app-v2 / landing-page-v1 / dashboard-v1 / form-v1 / calculator-v1)
- `route(user_idea, llm)` — 调 haiku 分类, 命中返回 template_id
- `route_sync_stub(user_idea)` — 关键词匹配兜底 (LLM 不可用时)
- `parse_classify_response()` 容错 (raw / markdown / 拼写错误 / none)
**Tests**: `test_intent_router.py` 24 个单测全过
- 覆盖元数据 / 消息格式 / 解析容错 / 端到端 / LLM 失败 / 强制 haiku / 关键词兜底
**Doc**: `STAGE4_TEMPLATES.md` 设计文档 (含完整模板目录结构 / slot filling / template_loader / 验收标准)
**未集成**: orchestrator 还没调 route(); 5 个模板的实际文件还没建 (template_dir 还指向空目录)

### P1 — 用户可见的错误反馈 (待讨论)
- `status=failed` 时把 `errors[]` 写进 `project.error_message`
- 前端 Project Studio 渲染失败原因 (e.g. "路由 /foo 不存在")

### P1 — LLM 成本控制 (待讨论)
- `_run_repair_pass` 加 cost log
- 单项目超 ¥5 告警
- repair 用 Lite model

### P2 — Stage 3 视觉 loop (等 Stage 2 真值稳)
- Sonnet 4.6 多模态评视觉
- 视觉不过 → frontend repair 改样式

### P2 — Stage 4 模板 seed (并行, 不阻塞)
- 5 个模板: todo-app-v2 / landing-page / dashboard / form / calculator
- intent_router 命中走快路径

---

## 用户测试历史 (供诊断参考)

| 项目 | 输入 | 结果 | 暴露问题 |
|---|---|---|---|
| 项目 3 | 北京时钟 / todo | Stage 0 一次过 | baseline 正常 |
| BMI 计算器 | (用户测过) | 功能可, 视觉待验证 | 待 design spec 验证 |
| 番茄钟 | "做一个番茄钟" | 功能可, 但前端显示 🍅 | 术语理解错 (番茄≠pomodoro 计时器) |
| Kanban 看板 | "做个看板应用 indigo" | LLM 调用卡死 | 无 timeout → 已修 |
| 温度转换器 (项目 11) | 数字 99/24 | 校验失败 | zod schema 太严 (实际: 路由错配) |
| 项目 1 (2026-07-08) | 温度转换器 | e2e PASS | **路由错配仍在** (e2e 测不出) |
| 项目 2 (2026-07-08) | 温度转换器 | e2e PASS | **路由错配仍在** (e2e 测不出) |

---

## 用户明确表达的偏好

- **企业级方向**: 计划上线 + 收费, 工程化稳定性是硬指标
- **路径 B 优先** (LLM 自由生成 + 多层防御)
- **真值校验**: 不只看"测试通过", 要主动注入 case 验证修复
- **每完成 stage 主动 commit + push GitHub** (2026-07-08 明确要求)
- **【2026-07-09 关键校准】上线收费 = 前提: 所有功能流程跑通**. 当前没实现"项目稳定跑通", 所以**先不做上线相关**, 优先级:
  1. **任务完成率** — OPC 端到端真成功率 (不是单测 PASS)
  2. **用户体验** — 用户提交后看到什么、卡在哪、怎么知道进度
  3. **工程稳定性** — 不崩、不漏、不留垃圾
  4. (暂搁) 上线收费 — 等 1-3 达标再说
- **不要为了"上线准备"做超出当前"跑通"需要的事** — P2-3/P2-4 完整集成暂缓, 先把"基本流程能跑"做扎实

---

## 下一步候选 (按用户决定)

1. **Stage 2 真值校验 + 进程清理** (1 小时内, 必做)
2. **用户可见错误反馈** (1 小时, 需讨论)
3. **LLM 成本控制** (需讨论)
4. **Stage 3 / 4 / 5** (等基础稳了)

---

## 关键文件路径速查

| 文件 | 作用 |
|---|---|
| `backend/app/agent/llm.py` | LLM client (timeout 在这) |
| `backend/app/agent/project_orchestrator.py` | 主 pipeline |
| `backend/app/agent/projects/agent_tools.py` | ToolRegistry (Stage 1) |
| `backend/app/agent/projects/backend_agent.py` | BackendAgent (Stage 2 repair) |
| `backend/app/agent/projects/frontend_agent.py` | FrontendAgent (Stage 2 repair) |
| `backend/app/agent/projects/test_agent.py` | TestAgent (Stage 2 4xx/5xx detection) |
| `backend/app/agent/projects/design_agent.py` | DesignAgent |
| `backend/app/services/dev_server.py` | dev server 启停 (P0 待修 teardown) |
| `backend/generated-projects/projects/<id>/` | 生成的项目 |
| `launcher.py` | 一键启动器源码 |
| `dist/OPCLauncher.exe` | 打包后的 exe |
| `UPGRADE_PLAN.md` | Stage 0-5 升级路径 |
| `ARCHITECTURE_SKETCH.md` | 路径 A vs B 对比 |
| `STAGE2_SELF_REPAIR.md` | Stage 2 技术细节 |
| `SESSION_NOTES.md` | **本文件**, 主进度索引 |

---

## Git / 提交流程 (2026-07-08 起新标准)

- 每完成一个 stage 主动 commit + push
- 单独文件: `STAGE2_SELF_REPAIR.md` / `STAGE2_SELF_REPAIR_TESTS.md` / 类似命名, 不要全塞 SESSION_NOTES
- SESSION_NOTES 只放主索引 (本期做了什么 / 下期要做什么 / 文件索引)
- 详细技术变更放独立 .md 文档

---

## Memory 索引

- `opc-ux-philosophy.md` — 非工程师用户优先看到成品
- `feedback-verify-preview-before-handoff.md` — 交付前必须 verify
- `feedback-windows-uvicorn-orphans.md` — Windows orphan 进程
- `opc-agent-dep-injection.md` — package.json 注入
- `opc-closed-loop-verification.md` — 多 Agent 闭环验证
- `opc-minimax-m3-integration.md` — MiniMax M3 接入
- `opc-parallel-pipeline.md` — Backend/Frontend 并行
- `opc-project-stuck-loop-fix.md` — 项目 24 stuck loop 修复
- `user-opc-test-status.md` — 用户测试状态
- `feedback-no-kill-sibling-projects.md` — 不要误杀其他项目
- `opc-architecture-choice-pending.md` — 路径 A/B 待拍板
- `feedback-pause-on-self-doubt.md` — 用户自我怀疑时先对齐
- `feedback-preflight-briefing.md` — 每个 stage 前 brief
- `opc-llm-system-stability.md` — LLM 系统稳定运行 6 原则
- `opc-stage2-self-repair.md` — Stage 2 闭环笔记 (新)
- `opc-stage2-truth-validation-gap.md` — Stage 2 真值校验缺口 (新)
- `opc-process-cleanup-gaps.md` — dev server 残留进程问题 (新)