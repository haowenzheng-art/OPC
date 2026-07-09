# NEW-1 真实任务完成率基准测量 — 第一轮结果

> 日期: 2026-07-09
> 测试脚本: `backend/verify_baseline.py` (`--quick` 跑 2 个需求)
> baseline timeout: 600s (旧) → 1200s (新, 适配实际耗时)

## 第一轮真实结果 (查询 DB, 揭穿 baseline 误判)

| Task | pid | status | 真实结果 | baseline 报告 |
|---|---|---|---|---|
| todo (Baseline) | 13 | **done 100%** (tool_history: 1 success=True) | **成功** | timeout 600s 误判 |
| calculator-bmi | 14 | developing (tool_history: 3, frontend 1✓ 2✗) | retry 中 (2 fail 在重抽) | pending |
| todo (二轮) | 15 | testing 82% | running | running |

**真实任务完成率: 至少 1/2 通过**。 baseline 错误报告 "0/2" 是因为 600s timeout 配错。

## 关键发现 (NEW-1)

1. **OPC 端到端工作正常**: pid=13 完整跑通 planning → developing → testing → repair → testing → **done**.  
2. **Stage 2 self-repair 起作用**: pid=13 第一轮 frontend tsc JSX 错 → repair 成功 → 进测试 → 完成.  
3. **瓶颈在 test 阶段**: npm install + dev server 启动 + npm playwright = ~10 分钟.  
4. **第一轮 LLM 生成 page.tsx 闭合错误**: 静态 tsc 拦截, 但浪费 1 轮 LLM 调用.

## 修复 (2026-07-08 → 2026-07-09)

| 修复 | 状态 | commit |
|---|---|---|
| orchestrator skip_regen | ✅ | 8fff4cc |
| dev_server netstat NoneType | ✅ | 8fff4cc |
| ProjectResponse.error 暴露 | ✅ | 8fff4cc |
| CostTracker + haiku 切换 + hard limit | ✅ | 8fff4cc |
| OPC_DISABLE_BILLING=1 | ✅ | a40f63c |
| test_agent 固定 project_id 目录 (node_modules 复用) | ✅ | f3d415b |
| test_agent _clean_build_cache 不删 node_modules | ✅ | f3d415b |
| frontend_agent _generate_page_tsx 加 JSX 强约束 | ✅ | f3d415b |
| verify_baseline timeout 1200s + env override | ✅ | f3d415b |
| detach_runner.py 真正独立进程 detach | ✅ | f3d415b |

## 测试覆盖

| 模块 | 单测 | 状态 |
|---|---|---|
| test_agent_tools | 12 | ✅ |
| test_self_repair | 7 | ✅ |
| test_dev_server_cleanup | 5 | ✅ 1 skip |
| test_user_visible_errors | 10 | ✅ |
| test_cost_tracker | 13 | ✅ |
| test_visual_evaluator | 12 | ✅ |
| test_intent_router | 24 | ✅ |
| **总计** | **85 passed, 2 skipped** | |

## Git 状态

- ✅ 3 commits: `8fff4cc` (P0-P2), `a40f63c` (NEW-1), `f3d415b` (NEW-4+3+tool)
- ⏳ 远端 push 还没 (gh auth 没做)

## 后续 (按"先能跑通"优先级)

1. **跑 baseline v2 用新 backend** — 验证 NEW-4 让端到端 < 2 分钟
2. **NEW-2 用户体验** — 进度可见
3. **gh auth + push 现有工作** — 避免 session 再丢
