# OPC Stage 4 — Template Seed (高频模板作种子)

> 日期: 2026-07-08
> 状态: 🚧 IntentRouter 框架 + 5 模板元数据 + 24 个单测就绪
> 配套: UPGRADE_PLAN.md Stage 4; SESSION_NOTES.md 2026-07-08

---

## 目标

让 5 个高频需求 (todo / landing-page / dashboard / form / calculator) **走快路径**:
- LLM 不从 0 写代码
- 直接用预验证模板 + slot filling
- 端到端 ≤ 30s (对比从 0 生成 5-10 分钟)

要解决的核心问题: OPC 在通用场景 (todo/form) 上成功率本来就高, 让这些走模板能:
- 提速 10x (30s vs 5min)
- 提高稳定性 (模板已经 pre-tested, 不会出现 LLM 随机性)
- 降低成本 (slot filling 调 1-2 次 LLM, 0→1 写代码要 6-12 次)

## 现状盘点 (2026-07-08)

✅ **已实现**:
- `app/agent/projects/intent_router.py`:
  - 5 个 TemplateSpec 元数据 (id / name / description / intent_examples / slots / template_dir)
  - `route(user_idea, llm)` — 调 haiku 分类
  - `route_sync_stub(user_idea)` — 关键词匹配兜底 (LLM 不可用时)
  - `parse_classify_response()` — 解析容错 (raw / markdown / 拼写错误 / none)
- `tests/agent/test_intent_router.py` — **24 个单测全过**
  - 覆盖模板元数据完整性、消息格式、解析容错、端到端 mock LLM、LLM 失败兜底、强制 haiku、关键词兜底

❌ **未集成到 orchestrator**:
- `ProjectOrchestrator.run()` 没调 `route()` 决定走模板还是自由生成
- 5 个模板的**实际文件**还没建 (template_dir 还指向空目录)
- template_loader (从 template_dir 加载文件) 还没写 — 现在只有 fallback_template/
- 5 个模板的 api_contract + design_spec 没配
- 5 模板各自需要 playwright e2e 验证

❌ **未做**:
- 多语言模板 (i18n) — 模板默认中文, 多语言后续 PR
- 模板版本管理 (template 改了, 用户已有项目怎么升级)
- A/B 测试 (模板 vs 自由生成, 哪个用户更喜欢)

## 完整设计 (待实现)

### 1. 模板目录结构

```
backend/app/agent/projects/
├── templates/
│   ├── todo-app-v2/
│   │   ├── api_contract.json     # 机器可读
│   │   ├── design_spec.json      # 视觉规范
│   │   ├── backend/
│   │   │   ├── package.json
│   │   │   ├── tsconfig.json
│   │   │   ├── prisma/schema.prisma
│   │   │   └── src/{index,routes}.ts
│   │   └── frontend/
│   │       ├── package.json
│   │       ├── tsconfig.json
│   │       ├── next.config.mjs
│   │       ├── tailwind.config.js
│   │       └── src/app/{layout,page}.tsx
│   ├── landing-page-v1/  (类似)
│   ├── dashboard-v1/     (类似)
│   ├── form-v1/          (类似)
│   └── calculator-v1/    (类似)
```

### 2. template_loader

```python
# 伪代码
class TemplateLoader:
    def load(self, template_id: str) -> dict[str, str]:
        """返回 {file_path: content} 跟 agent.get_files() 同格式."""
    
    def get_api_contract(self, template_id: str) -> dict | None: ...
    def get_design_spec(self, template_id: str) -> dict | None: ...
```

可以复用 `fallback.py:load_fallback_files()` 的逻辑, 但要按 template_id 找目录。

### 3. slot filling

模板文件里有些"占位符", slot filling 用 LLM 从 user_idea 提取填入:

```typescript
// templates/todo-app-v2/frontend/src/app/page.tsx
const TITLE = "{{title}}";  // ← 会被替换成 "我的待办"
```

```python
# orchestrator 集成
async def _fill_slots(self, template: TemplateSpec, user_idea: str, llm) -> dict:
    """调 LLM 提取 slots, 返回 {slot_name: value}."""
    prompt = f"从 user_idea 提取 template 字段:\n模板 slots: {template.slots}\nuser_idea: {user_idea}"
    response = await llm.create_message(messages=[...], tier="haiku")
    return parse_slots(response.content)
```

### 4. orchestrator 集成

```python
# 伪代码 - ProjectOrchestrator.run()
async def run(self):
    # Step 0: intent route
    template_id = await route(self.user_idea, self.llm)
    if template_id:
        template = get_template(template_id)
        # Step 0.5: slot filling
        slots = await self._fill_slots(template, self.user_idea, self.llm)
        # Step 1: 加载模板文件
        files = self.template_loader.load(template_id)
        # Step 1.5: replace 占位符
        files = self._apply_slots(files, slots)
        # Step 2: 跳过 generation, 直接进 test (Stage 2 self-repair 适用)
        return await self._run_test_loop(files, template.get_api_contract(), template.get_design_spec())
    else:
        # 没命中模板, 走 0→1 自由生成 (现有逻辑)
        return await self._run_full_pipeline()
```

### 5. 测试要求

每个模板需要:
- 单测: 文件加载、API contract 校验、design_spec 校验
- e2e: 跑一次端到端, 看模板生成的项目能 preview + dev server 起来
- 视觉: P2-3 multimodal evaluator 给 ≥ 7 分

## 验收标准 (整体完成时)

| 指标 | baseline (现状) | 目标 |
|---|---|---|
| 命中模板的端到端耗时 | 5-10 min | ≤ 30s |
| 模板生成项目的成功率 | 95% (现 0→1) | ≥ 99% (pre-tested) |
| 模板数量 | 0 (用 fallback_template) | 5 |
| LLM 调用次数 / 模板项目 | 6-12 次 | ≤ 3 次 (slot filling + 可选修复) |
| 模板复用率 | N/A | ≥ 40% (5 模板覆盖 40% 用户需求) |

## 模板优先级 (建议实施顺序)

1. **todo-app-v2** (1 天) — 最高频, 现有 fallback 模板略改
2. **calculator-v1** (1 天) — BMI/汇率/单位换算, 复用 todo 后端结构
3. **form-v1** (1 天) — 注册/联系/反馈, 跟 todo 类似
4. **landing-page-v1** (2 天) — 纯前端, 不用 backend, 反而简单
5. **dashboard-v1** (2 天) — 纯前端, 图表库集成 (recharts?) 工作量大

总共 ~7 天。**推荐先做 todo + calculator** (高频简单), 验证模板架构 OK 再做 dashboard (复杂)。

## 关键文件

| 文件 | 作用 |
|---|---|
| `backend/app/agent/projects/intent_router.py` | IntentRouter + 5 TemplateSpec |
| `backend/tests/agent/test_intent_router.py` | 24 个单测全过 |
| `backend/app/agent/projects/fallback.py` | 现有 fallback_template (todo) — 后续可迁移到 templates/ |
| `backend/app/agent/projects/template_loader.py` | **待新建** |
| `backend/app/agent/project_orchestrator.py` | 待集成 (run() 入口加 route + 模板分支) |
| `backend/app/agent/projects/templates/` | **待新建** 5 个模板目录 |

## 设计决策

- **为什么用 haiku 分类**: 分类任务简单, haiku 够用, 上线后每天 1000 个项目能省 ~$5
- **为什么不强制只走模板**: 模板覆盖率有限, 没命中走自由生成兜底; 不能锁死
- **为什么不在模板里写死所有文案**: 留 slot 给 LLM 填, 用户能定制 (e.g. todo 标题 / 落地页产品名)
- **为什么 template_dir 指向相对路径**: 跟随代码仓库走, 上线后 docker compose mount 同目录即可
