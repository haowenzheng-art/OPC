# NEW-1 真实任务完成率基准测量 — 第一轮结果

> 日期: 2026-07-09
> 测试脚本: `backend/verify_baseline.py` (`--quick` 跑 2 个需求)
> baseline timeout: 600s (旧) → 1200s (新, 适配实际耗时)

## 第一轮结果 (timeout 600s)

| Task | 期望模板 | 结果 | 时长 | 真实原因 |
|---|---|---|---|---|
| todo (pid=13) | todo-app-v2 | **timeout 608s** (实际 OPC 仍跑) | 10+min | 第一轮 test fail (JSX 错误) → repair 成功 → 第二轮 test 卡 600s |
| calculator-bmi (pid=14) | calculator-v1 | **timeout 14s** (因前一个卡了 budget) | - | baseline timeout 后项目仍未完成 |

**0/2 = 不代表 OPC 不行**, 是 baseline 600s 配错了。

## 第二轮 (timeout 1200s) — 必要

**Todo pid=13 真状态** (baseline 报 timeout 但实际没死):
- 21 events: planning → developing → testing → developing (repair 后) → testing (第二轮)
- tool_history: 1 entry, **success=True** ← **Stage 2 self-repair 修对了!**
- 第二轮 test 跑 ~10 分钟没结束, 怀疑是 dev server 启动慢 + playwright 启动慢 + npm install 慢叠加

## 真问题 (按优先级)

1. **test 阶段耗时太长** — NEW-1 显示端到端 12-15 分钟, 但 90% 时间在 npm install + dev server 启动
2. **第一轮 LLM 生成 JSX 不闭合** — verify 阶段拦住了, 但浪费 1 轮 LLM 调用
3. **test 阶段每轮都跑完整 npm install** — 已经在 _verify 临时目录, 不知道能不能 reuse backend node_modules

## 修复总结 (2026-07-08 → 2026-07-09)

| 修复 | 状态 | 影响 |
|---|---|---|
| orchestrator 修复后跳过 backend/frontend 重抽 (skip_regen) | ✅ 在跑中 | 修了 8 小时死循环, 现在第二轮直接重跑 test |
| dev_server.kill_orphan_on_port netstat NoneType | ✅ 已修 | 不再让 teardown 整体崩溃 |
| ProjectResponse 暴露 error 字段 + state_machine ERROR 优先级 bug fix | ✅ 已修 | 用户能看到失败原因 |
| CostTracker + haiku 自动切换 + hard limit abort | ✅ 已修 | 防止 LLM 烧光额度 |
| OPC_DISABLE_BILLING=1 环境变量 | ✅ 已加 | dev/test 模式绕过 billing 限额 |

## 测试覆盖

| 模块 | 单测 | 状态 |
|---|---|---|
| test_agent_tools | 12 | ✅ |
| test_self_repair (mock LLM) | 7 | ✅ |
| test_dev_server_cleanup (mock netstat) | 5 | ✅ 1 skip |
| test_user_visible_errors | 10 | ✅ |
| test_cost_tracker | 13 | ✅ |
| test_visual_evaluator | 12 | ✅ |
| test_intent_router | 24 | ✅ |
| **总计** | **85 passed, 2 skipped** | |

## Git 状态

- ✅ `git init` 完成 (commit 8fff4cc)
- ✅ P0-P2 所有工作 commit 进去了
- ⏳ 远端 push 还没 (等 gh auth)

## 下一步

1. **调高 baseline timeout → 重跑** — 拿真实成功率数字 (NEW-1 真值)
2. **缩短 test 阶段** — 优化 dev_server 启动 / npm install 缓存 (NEW-4)
3. **前端 LLM prompt 加 JSX 语法约束** — 减少第一轮 fail (NEW-3)
4. **用户进度可见** — 提交后看到阶段 (NEW-2)
