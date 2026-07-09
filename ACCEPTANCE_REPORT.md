# OPC 上线前验收报告

> 日期: 2026-07-08
> 项目: OPC (One Prompt Creates) — 企业级, 计划上线 + 收费
> 主线路径: B (LLM 自由生成 + 多层防御) + 模板快路径 (Stage 4)
> 本次范围: P0 (基础) + P1 (用户可见) + P2 (Stage 3+4 框架)

---

## 一句话总结

P0-P2 全套合并清单**已实现核心** (Stage 0/1/2/3/4 框架 + 用户可见错误 + LLM 成本控制 + 进程清理), 85 个单测全过, 端到端 self-repair 真值已校验。**P2-3 视觉 loop 完整集成 + P2-4 模板文件实际填充**留待后续 PR (设计文档就绪)。

**【2026-07-09 校准】当前优先级已变**:
- 上线收费 = 前提 = 所有功能流程跑通 + 工程稳定性 + 任务完成率 + 用户体验
- 当前没实现"项目稳定跑通", 所以**先不上线**, 专注 1. 任务完成率 2. 用户体验 3. 工程稳定性
- **P2-3/P2-4 完整集成 暂缓** — 不再做"为上线准备"的事, 先把基本流程跑通
- 下一步重点: 真实 e2e 跑通率、用户提交后看到什么、卡在哪、怎么知道进度

## 完成情况 (按 Stage)

| Stage | 内容 | 状态 | 单测 |
|---|---|---|---|
| Stage 0 | 基础恢复 (backend/frontend/e2e) | ✅ | 已有 |
| Stage 1 | Agent tools (read/edit/bash) | ✅ | 12 (test_agent_tools.py) |
| Stage 2 | Self-Repair Loop + **端到端真值校验** | ✅ **真值** | 7 (test_self_repair.py) + 1 (verify_stage2_repair.py) |
| Stage 3 | Visual Loop (多模态 LLM 评估) | 🚧 框架 | 12 (test_visual_evaluator.py) |
| Stage 4 | Template Seed (5 模板 intent router) | 🚧 框架 | 24 (test_intent_router.py) |
| P0-1 | Stage 2 真值校验 | ✅ | (verify_stage2_repair.py) |
| P0-2 | dev_server teardown 二次保险 | ✅ | 5 (test_dev_server_cleanup.py) |
| P1-3 | 用户可见错误反馈 + state_machine bug fix | ✅ | 10 (test_user_visible_errors.py) |
| P1-4 | LLM 成本控制 (cost log + Lite + hard limit) | ✅ | 13 (test_cost_tracker.py) |
| **总计** | | | **85 passed, 2 skipped** |

## 核心交付物

### 代码 (新增/修改)
- `backend/app/agent/cost_tracker.py` — LLM 成本跟踪 (P1-4)
- `backend/app/agent/projects/visual_evaluator.py` — 多模态 LLM 视觉评估 (P2-3)
- `backend/app/agent/projects/intent_router.py` — 5 模板意图路由 (P2-4)
- `backend/app/services/dev_server.py` — 加 `kill_orphan_on_port()` (P0-2)
- `backend/app/agent/project_orchestrator.py` — 加 cost_tracker / state_machine ERROR 优先级 (P1-3/4)
- `backend/app/agent/llm.py` — 自动 record 到 CostTracker (P1-4)
- `backend/app/agent/projects/test_agent.py` — 加 4xx/5xx 监听 (Stage 2)
- `backend/app/agent/projects/{frontend,backend}_agent.py` — repair prompt 强化 (Stage 2)
- `backend/app/worker/opc_tasks.py` — 用户友好 error (P1-3)
- `backend/app/api/v1/projects.py` — `ProjectResponse.error` 字段 (P1-3)

### 文档
- `STAGE2_SELF_REPAIR.md` — Stage 2 完整记录
- `STAGE3_VISUAL.md` — Stage 3 设计 + 验收标准
- `STAGE4_TEMPLATES.md` — Stage 4 设计 + 模板目录结构
- `SESSION_NOTES.md` — **主进度索引** (实时更新)
- `verify_stage2_repair.py` — 端到端真值校验脚本 (可手动跑)

### 单测
- `backend/tests/agent/test_cost_tracker.py` (13)
- `backend/tests/agent/test_dev_server_cleanup.py` (5+1 skip)
- `backend/tests/agent/test_intent_router.py` (24)
- `backend/tests/agent/test_self_repair.py` (7)
- `backend/tests/agent/test_user_visible_errors.py` (10)
- `backend/tests/agent/test_visual_evaluator.py` (12)
- `backend/tests/agent/test_agent_tools.py` (12, 已有)

**总计 85 passed, 2 skipped**

## 真值校验 (Stage 2)

`verify_stage2_repair.py` 跑通完整端到端:
- 故意构造路由错配 (backend POST /foo, frontend GET /api/v1/foo)
- TestAgent 真抓 on_response 4xx → http_404 signal
- FrontendAgent.repair_with_tools 调真实 LLM → 改 fetch URL
- 重跑 TestAgent → passed

```
Stage 2 self-repair 在真实 LLM + 真实项目下确实能修对路由错配
  - http_404 signal:       ✓
  - LLM repair 成功:       ✓ (tools_used=11)
  - 文件真改对:            ✓
  - 重跑 TestAgent pass:   ✓
```

修复过程中暴露并修复的真问题:
1. `success` 判定假阴性 (LLM 改对文件但没明说 REPAIRED)
2. LLM 重复调 tsc (8 iteration 用 5 次) — prompt 加限制
3. **state_machine ERROR event 优先级 bug** — elif 链导致从 developing 状态发不出 ERROR

## 未完成项 (后续 PR)

| 项目 | 工作量 | 优先级 | 设计文档 |
|---|---|---|---|
| Stage 3 完整集成 (orchestrator 用 multimodal evaluator + 切 anthropic) | 1-2 天 | 高 | STAGE3_VISUAL.md |
| Stage 4 模板文件实际填充 (5 模板, 各自带 e2e) | 5-7 天 | 中 | STAGE4_TEMPLATES.md |
| Stage 4 template_loader + slot filling + orchestrator 集成 | 1-2 天 | 中 | STAGE4_TEMPLATES.md |
| 模板多语言 (i18n) | 3 天 | 低 | - |
| 视觉 prompt 调优 (基于真实生成 case) | 1 周 | 低 | - |

## 用户已明确的偏好 (从 memory + 这次对话)

- **企业级方向**: 计划上线 + 收费, 工程化稳定性是硬指标 ✅ 已对齐
- **每完成 stage 主动 commit + push GitHub**: 等 gh auth 后立即做
- **真值校验**: 不只看"测试通过", 要主动注入 case 验证修复 ✅ 已对齐
- **工作风格**: 拍板快, 不喜欢选择题, 关注工程基建

## 阻塞项

### 1. gh auth (用户操作)
- 用户需跑 `gh auth login` (OAuth device flow)
- 完成后: git init → gh repo create → git add → commit → push
- 预计: 5 分钟 (用户操作) + 2 分钟 (我执行)

### 2. Stage 3/4 完整实现 (2-3 周工作量)
- 见 "未完成项" 表格
- **建议**: 拆成 3 个 PR, 每个 PR 有自己的设计 + 单测 + 验收

## 下一步 (按用户拍板)

我现在**等**:
1. **用户 gh auth login** — 立刻 commit + push 全部 P0-P2
2. **用户决定**: P2-3 完整集成 vs P2-4 模板文件 vs 验收 e2e × 3 (Stage 2 真值 + Stage 4 intent)

按你的标准 (上线收费级), 建议**先 push 当前所有 stage 完整代码**到 GitHub, 拿到一个 stable base, 再分 PR 推进 Stage 3/4 完整实现。
