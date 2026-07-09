# OPC Stage 3 — Visual Loop (多模态 LLM 评视觉)

> 日期: 2026-07-08
> 状态: 🚧 框架就绪, 完整 orchestrator 集成 + 5 模板视觉规范留待后续 PR
> 配套: UPGRADE_PLAN.md Stage 3; SESSION_NOTES.md 2026-07-08

---

## 目标

让 OPC 不只测"代码能跑", 还测"**视觉符合 design spec**"——用多模态 LLM (Sonnet 4.6 vision) 给截图打分, 不及格就触发 frontend agent 修样式。

要解决的核心问题: OPC 生成的页面经常**功能正确但视觉难看** (按钮无边框, 颜色不一致, 间距乱), 之前的 pHash diff 测的是"这次和上次比变化多少", **测不出"这次和 design_spec 比符合度"**。

## 现状盘点 (2026-07-08)

✅ **已实现**:
- `app/agent/projects/visual_evaluator.py` — 多模态 LLM 评估框架
  - `encode_screenshot_to_base64()` — 截图转 base64
  - `build_visual_eval_messages()` — 构造 Anthropic 多模态格式
  - `parse_visual_eval_response()` — 解析 LLM JSON 返回 (含 markdown 容错)
  - `evaluate_screenshot()` — 端到端, 调 LLM 拿 VisualEvaluation
  - `VisualEvaluation.to_failure_signal_dict()` — 转 FailureSignal 给 repair pass
- `tests/agent/test_visual_evaluator.py` — **12 个单测全过**
  - 覆盖 base64 编码 / 消息格式 / JSON 解析 / markdown 容错 / 乱码兜底 / 端到端 mock LLM / LLM 失败 graceful

❌ **未集成到 orchestrator**:
- `test_agent._run_visual_phase()` 还在用旧的 pHash diff, 没用 multimodal evaluator
- orchestrator 没接 visual failure signal → repair pass
- 没用 vision LLM (用的都是 MiniMax, 没视觉能力) — 需切到 anthropic

❌ **未做**:
- 5 个模板的 design_spec 视觉规范 (todo / landing-page / dashboard / form / calculator)
- visual prompt 微调 (基于真实失败 case)
- 多轮视觉修复 (现在只支持 1 轮: 评估 → 修 → 评估)
- 视觉基线缓存 (相同 design_spec 多次生成的视觉预期)

## 完整设计 (待实现)

### 1. 多 LLM provider 路由

当前所有 LLM 调用都用 MiniMax M3 (Anthropic 协议), 但**它没视觉能力**。需要:
- 视觉评估 (visual_evaluator) 切到 Anthropic Sonnet 4.6 (有 vision)
- 其它调用 (生成/修复) 继续用 MiniMax (便宜)

```python
# 伪代码
class MultimodalRouter:
    def get_tier_for_phase(self, phase: str) -> str:
        if phase == "visual_eval":
            return "sonnet"  # 切到 anthropic
        else:
            return "minimax"  # 走 MiniMax 便宜
```

实现方式: 在 `app/agent/llm.py` 的 `LLMClient` 加 `tier_to_provider` 映射, 或者新建一个 `MultimodalLLMClient` 类, 视觉评估用这个。

### 2. orchestrator 集成

```python
# project_orchestrator._run_develop_test_loop 改造
async def _run_visual_phase(self, ...):
    # 1. 跑现有 multimodal evaluator
    evaluation = await evaluate_screenshot(
        llm_client=self.llm,
        screenshot_path=current_png,
        user_idea=self.user_idea,
        design_spec=self.design_spec,
        tier="sonnet",  # 用 vision-capable model
    )

    # 2. 不及格 → 触发 repair pass
    if not evaluation.is_passing(threshold=settings.agent_score_threshold):
        log.warning("visual_eval_failed", project_id=..., score=evaluation.score)
        signal = evaluation.to_failure_signal_dict()
        await self._run_repair_pass("frontend", tool_root, [signal])
        repair_passes["frontend"] += 1
        # 最多 2 轮 (UPGRADE_PLAN)
        continue
    else:
        log.info("visual_eval_passed", project_id=..., score=evaluation.score)
        return None  # 通过
```

### 3. 5 模板视觉规范 (Stage 4 配合)

每个模板要自带 design_spec.json, 含完整 palette/typography/spacing/components 字段, 视觉评估按这个 spec 打分。Stage 4 模板时同步做。

### 4. 视觉 prompt 调优

初始 prompt (在 `VISUAL_EVAL_PROMPT`) 写得相对基础, 真实场景需要:
- 加 Few-shot examples (好 vs 坏截图)
- 加项目类型特定规则 (e.g. dashboard 必须有数据可视, landing-page 必须有 CTA)
- 加可量化指标 (e.g. "主按钮颜色跟 design_spec.palette.primary 一致得 1 分")

调优成本: ~50 个真实生成项目的视觉评估, 收集 1 周, 调 prompt。

### 5. 多轮修复

当前 Stage 2 repair pass 单轮 (一次修复就结束)。视觉评估可能需要 2-3 轮 (LLM 评估 → 修 → 再评估 → 再修)。需要:
- 视觉修复 budget: 2 轮
- 每轮评估结果持久化到 project.context["visual_history"]

## 验收标准 (整体完成时)

| 指标 | baseline (现状) | 目标 |
|---|---|---|
| 视觉过线率 (LLM 评 ≥ 7/10) | ~10% | ≥ 80% |
| 视觉评估调用平均耗时 | N/A | ≤ 8s (Sonnet 4.6 vision) |
| 视觉评估 token cost / 项目 | N/A | ≤ $0.05 (单次 eval) |
| 视觉修复成功率 (eval → fix → eval 通过) | N/A | ≥ 50% (2 轮内) |

## 下一步 (待用户拍板)

按工作量分:
- **最小 1 (1-2 天)**: 多 LLM provider 路由 + orchestrator 集成 + 1 个模板 (todo-app-v2) 视觉评估
- **完整 (1 周)**: 上述 + 5 模板视觉规范 + 视觉 prompt 调优 + 多轮修复

我建议先做"最小 1", 跑真实 e2e 看视觉评估对不对, 再决定要不要继续"完整"。

## 关键文件

| 文件 | 作用 |
|---|---|
| `backend/app/agent/projects/visual_evaluator.py` | 多模态 LLM 视觉评估框架 |
| `backend/tests/agent/test_visual_evaluator.py` | 12 个单测全过 |
| `backend/app/agent/projects/test_agent.py` | 待集成 (替换 pHash diff) |
| `backend/app/agent/project_orchestrator.py` | 待集成 (orchestrator 视觉修复循环) |
| `backend/app/agent/llm.py` | 待改造 (多 provider 路由) |

## 设计决策

- **为什么不用 pHash baseline**: OPC 每次生成的内容不同, baseline 概念不适用; multimodal evaluator 改成"和 design_spec 比符合度", 不靠历史
- **为什么用 Sonnet 4.6 vision 而不是 MiniMax M3 vision**: MiniMax M3 当前没 vision 能力; Sonnet 4.6 vision 业界公认最强
- **为什么视觉修复放 frontend agent**: 视觉问题主要是 CSS/样式, frontend agent 改 page.tsx 的 className 最自然
- **为什么不做完整 5 模板视觉规范**: 模板数量多, 每个都要 manual review; Stage 4 模板时再一起做
