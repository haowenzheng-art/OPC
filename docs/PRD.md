# OPC 产品需求文档 (PRD)

> 版本: v1.0 · 日期: 2026-07-10 · 状态: Active Development
> 一句话: **One Person Company → One Prompt Creates**

---

## 1. 产品定位

### 1.1 目标用户

| 用户类型 | 痛点 | OPC 给的价值 |
|---|---|---|
| **独立开发者 / 创业者** | 一个人要干全栈, 想法经常卡在后端 / 测试 / 部署 | 一句话拿到可运行 MVP |
| **小团队 Lead** | 招不到全栈, 工期被单个瓶颈卡死 | 让 1 个人 + OPC = 一个工程团队 |
| **企业内部创新团队** | 业务想法 → 上线 demo, 走流程要几周 | 一杯咖啡时间拿到 prototype + PRD |
| **外包 / 乙方** | 客户 brief → 出方案 / 出 mock, 重复劳动 | 输入 brief 直接拿到初版方案 |

### 1.2 核心价值主张

> **"一句话 → 一个真能跑的全栈项目"**

不是聊天记录, 不是 mock, 不是 lorem ipsum 占位 —— 是**磁盘上的可运行项目**:
- `frontend/` 能 `npm run dev`
- `backend/` 能 `uvicorn app.main:app`
- 数据库迁移能跑
- 测试能 pass
- Docker Compose 能拉起来

### 1.3 反向定位 (我们不做什么)

- ❌ 不是聊天机器人 —— 没有"再问一句"循环, 一次性交付
- ❌ 不是低代码平台 —— 不暴露表单拖拽, 你写一句自然语言就够了
- ❌ 不是 Figma 替代品 —— 视觉生成是为了**写代码**, 不是为了导 PNG
- ❌ 不是 IDE 替代品 —— 你不会在 OPC 里写代码, 你只输入 prompt

---

## 2. 用户故事

### 2.1 主流程: 一句话生成

> **作为** 一位独立开发者
> **我想要** 在 Dashboard 上输入一句产品描述
> **以便于** 在 5 分钟内拿到一个能跑的项目

**验收标准**:
- [ ] 输入框接受自然语言, 长度 1-500 字
- [ ] 点击 "Create" 后 5 秒内进入项目详情页
- [ ] 详情页显示任务进度条 (CEO → PM → Designer → Frontend → Backend → Test)
- [ ] 每个 Agent 完成后追加一行 trace, 类似直播弹幕
- [ ] 全部完成后显示 "Open project" 按钮 + 本地路径
- [ ] 失败的 Agent 显示红色 ❌ + "Self-repairing..." 状态

### 2.2 二次迭代

> **作为** 拿到初版项目的用户
> **我想要** 在不离开页面的情况下追加需求
> **以便于** 增量改进而不是重新生成

**验收标准**:
- [ ] 项目详情页有 "Add requirement" 输入框
- [ ] 追加需求只触发受影响的 Agent (改文案 → Designer + Frontend; 加 API → Backend)
- [ ] 上一个 run 的工件保留, 新 run 在 `out/<project>/v2/`
- [ ] 版本可对比 (diff view)

### 2.3 自修闭环

> **作为** 一位不信任 LLM 的开发者
> **我想要** 看到 OPC 在自修, 而不是吞错
> **以便于** 我能信任它产出的代码

**验收标准**:
- [ ] 每个 Agent 失败时, 状态栏显示 `<Agent> failed: <reason>`
- [ ] 自修过程可见: "Reading <file>... Editing <file>... Re-running tests..."
- [ ] 自修最多 3 轮, 超限明确报错 "Could not auto-repair after 3 attempts"
- [ ] 所有失败信号记录在 `FailureSignal[]`, 用户可点开看详情

---

## 3. 功能模块

### 3.1 Agent 编排系统 (核心)

| Agent | 职责 | 输入 | 输出 |
|---|---|---|---|
| **Orchestrator** | 拆 prompt, 派单, 收验 | 一句话 prompt | task list + 调度顺序 |
| **CEO** | 把控产品方向 / 优先级 | prompt + user persona | 产品 vision + MVP scope |
| **PM** | 把 vision 拆成 PRD | vision + user stories | PRD.md |
| **Designer** | 视觉规范 + 屏幕列表 | PRD | design_spec.json + screens.json |
| **Frontend** | 写 React 组件 + 路由 | design_spec + api_contract | Next.js 代码 |
| **Backend** | 写 API + schema | PRD + api_contract | FastAPI / Express 代码 |
| **Test** | 真实验证 | 全部工件 | 测试报告 + FailureSignal[] |
| **Ops** | 部署配置 | 全部工件 | Dockerfile + docker-compose.yml |

**关键文件** (真理之源):
- `api_contract.json` — 前后端 API 约定, Frontend 和 Backend 都强制先读
- `design_spec.json` — 视觉规范, Frontend 强制先读
- `tasks.json` — Orchestrator 派单状态, 含 `acceptance_criteria`

### 3.2 Self-Repair Loop (Stage 2)

详见 [STAGE2_SELF_REPAIR.md](../STAGE2_SELF_REPAIR.md)。

**触发条件**:
- Test Agent 跑出 HTTP 4xx/5xx
- TypeScript 编译失败
- 浏览器 console error
- 后端 API 路由缺失

**修复机制**:
- Orchestrator 解析 `FailureSignal`
- 调用对应 Agent 的 `repair_with_tools()`
- Agent 使用工具局部 patch (read_file / edit_file / bash)
- 改完回到 Test Agent 重跑

**降级链**:
```
LLM repair → retry budget = 3 → 失败 → fallback 模板 → 仍失败 → 报错给用户
```

### 3.3 真值校验

| 层级 | 工具 | 校验内容 |
|---|---|---|
| 类型层 | TypeScript tsc / Python mypy | 类型契约 |
| 契约层 | api_contract.json 对比 | URL / 参数 / 返回类型一致 |
| 接口层 | 真实 HTTP 调用 (axios / httpx) | 200 OK, schema match |
| UI 层 | Playwright headless | 页面渲染 / 控制台无错 / 关键交互可用 |
| E2E 层 | Playwright + smoke test | 关键 user flow 跑通 |

### 3.4 成本控制

- 每次 LLM 调用记录: model / tokens_in / tokens_out / cost_usd
- 项目级预算: 默认 $0.50 / run, 可调
- 超出预算自动降级:
  1. Claude Opus → Sonnet → Haiku
  2. 仍超 → fallback 到验证过的模板
- Self-Repair 阶段不重新生成 Agent context, 只传 diff + 失败信号

### 3.5 用户系统 (MVP)

- 注册 / 登录 (邮箱 + JWT)
- Organization 多租户 scaffold
- Dashboard: 项目列表 + 状态
- Project Studio: 单项目详情 + trace + 工件预览
- (规划中) Stripe 订阅 + 配额计费

---

## 4. 非功能性需求

### 4.1 性能

- Prompt → 项目详情页: **< 5s** (任务入队)
- 单次完整生成: **< 5 分钟** (普通 CRUD app)
- Dashboard 列表加载: **< 200ms**

### 4.2 可靠性

- Celery worker crash → 任务自动重试 (max 3)
- Self-Repair 失败 → fallback 模板, 不静默
- LLM 调用超时 (30s) → 自动 retry, 最多 2 次
- 所有失败信号写 log + 通知用户

### 4.3 可观测

- 每个 Agent 调用 trace 进 DB (`agent_runs` table)
- trace 字段: agent / start_at / end_at / tokens / cost / status / failure_signals
- 用户在 Project Studio 可见

### 4.4 安全

- LLM API key 只在 backend 端, 永不出前端
- JWT 鉴权, 路由级 RBAC
- 生成的代码 sandbox 隔离 (目前是本地磁盘, 未来 Docker 容器隔离)
- 上线前审计: prompt / 输出 / 修复路径全部可追

---

## 5. 架构决策记录 (ADR)

### ADR-001: 多层防御 > 单一强模型

**Context**: LLM 自由生成代码不可靠, 但模板化又牺牲灵活性。

**Decision**: LLM 生成 + 类型契约 + 真实验证 + 自修 + 模板降级, 五层叠加。

**Consequence**:
- ✅ 灵活度保留 (LLM 写大部分代码)
- ✅ 出错能自修 (Stage 2)
- ⚠️ 复杂度高, 5 个文件互相依赖, 需要严格的接口约定

### ADR-002: 异步任务队列 (Celery) > 同步阻塞

**Context**: 一次生成可能要 3-5 分钟, 同步会超时 + 用户体验差。

**Decision**: API 立即返回 task_id, 前端轮询 / WebSocket 拿进度。

**Consequence**:
- ✅ 用户体验好 (立即看到进度)
- ✅ 可水平扩展 (多 worker)
- ⚠️ 需要额外基础设施 (Redis)

### ADR-003: 测试用真 Playwright + 真实 HTTP

**Context**: 之前用 mock 数据验证, 假阳性高。

**Decision**: Test Agent 跑真 Playwright (headless Chromium) + 真实 HTTP 请求。

**Consequence**:
- ✅ 校验是"真"的 (前端 fetch 调错 URL → 真的 404 → 真触发 Self-Repair)
- ⚠️ 测试耗时增加 (~10s/run)
- ⚠️ CI 需要装 Chromium

---

## 6. 路线图 (按优先级)

| 阶段 | 目标 | 状态 |
|---|---|---|
| **Stage 0** | 基线: 真值测量 + 关键发现 | ✅ Done |
| **Stage 1** | 错误反馈 + 成本控制 + 视觉/模板框架 | ✅ Done |
| **Stage 2** | Self-Repair Loop (前后端路由错配自修) | ✅ Done (核心 + 7 单测) |
| **Stage 3** | 视觉生成 + 设计 token 化 | ✅ Done (框架) |
| **Stage 4** | 模板 fallback + 模板市场 scaffold | ✅ Done (框架) |
| **Stage 5** | 营销官网 + GitHub 开源 | ✅ Done (本 commit) |
| **Stage 6** | WebSocket 实时进度 + 多项目并发 | 🔜 Next |
| **Stage 7** | Stripe 订阅 + 配额计费 | 🔜 After MVP 跑通 |
| **Stage 8** | 真部署集成 (Vercel + Railway) | 🔜 |
| **Stage 9** | 模板市场 (用户发布 / 复用) | 🔜 |

> "上线收费" 是 Stage 7 的事。在那之前, 优先级是 **任务完成率** > **用户体验** > **工程稳定性**。

---

## 7. 成功指标

### 7.1 北极星指标
**Monthly Successful Projects Generated** — 一个月内用户成功拿到 "能跑的项目" 的次数。

### 7.2 关键指标

| 指标 | 当前 (Baseline) | 目标 (Stage 6 完成) |
|---|---|---|
| 端到端成功率 (e2e PASS / total runs) | ~40% | ≥ 80% |
| Self-Repair 触发成功率 (修好 / 触发) | ~30% | ≥ 70% |
| 平均生成耗时 (普通 CRUD app) | ~8 min | < 4 min |
| 单 run 平均成本 (USD) | ~$0.80 | < $0.30 |
| 用户完成首次生成的留存 | TBD | ≥ 50% (7d) |

### 7.3 反指标 (要避免)

- ❌ 静默吞错 (失败一定要让用户知道)
- ❌ 用 mock 数据冒充真功能 (不存在的 API 不能返回 200)
- ❌ 把"测试 PASS"当"功能 PASS" (必须 e2e 真实验证)

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| LLM API 不稳定 | 整个流程崩 | 重试 + 多 provider fallback |
| Self-Repair 死循环 | token 烧光 + 用户等待 | retry budget = 3, 超限报错 |
| 模板 fallback 看起来像"假数据" | 信任危机 | 明确标注 "Generated by template vX" |
| 成本失控 | 烧钱 | 预算 + 自动降级 + 用户可调 |
| 安全: LLM 写恶意代码 | 上线后门 | 静态扫描 + (未来) sandbox 隔离 |

---

## 9. 附录

### 9.1 术语表

| 术语 | 含义 |
|---|---|
| **Prompt** | 用户输入的一句话产品描述 |
| **Orchestrator** | 调度者 Agent, 拆 prompt + 派单 + 验收 |
| **FailureSignal** | 失败信号结构 (kind / msg / suggested_action / agent) |
| **Stage** | OPC 发展阶段 (Stage 0-9) |
| **Truth Validation** | 真值校验 (用 Playwright + 真实 HTTP, 不用 mock) |
| **Self-Repair** | 自修 (Stage 2 引入, 局部 patch 而非全量重抽) |
| **Fallback Template** | 失败时降级用的验证过模板 |

### 9.2 相关文档

- [STAGE2_SELF_REPAIR.md](../STAGE2_SELF_REPAIR.md) — 自修机制细节
- [STAGE3_VISUAL.md](../STAGE3_VISUAL.md) — 视觉生成
- [STAGE4_TEMPLATES.md](../STAGE4_TEMPLATES.md) — 模板 fallback
- [BASELINE_RESULT_NEW1.md](../BASELINE_RESULT_NEW1.md) — 基线真值测量
- [UPGRADE_PLAN.md](../UPGRADE_PLAN.md) — 总路线图
- [ACCEPTANCE_REPORT.md](../ACCEPTANCE_REPORT.md) — 验收报告

### 9.3 修订记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-07-10 | v1.0 | 初版, 配合官网发布 + GitHub 开源 |