# OPC 架构 Sketch — 两条路径对比

> 目的：解决"20 次成功 1 次"的根本症结，列出两条可行路，让用户决定往哪边走。

---

## 当前架构（baseline）— 已知失败模式

```
用户一句话
  └→ CEO 分析 → PM 写 PRD
        └→ Design 设计
              └→ Contract 预测 (LLM 估端点)
                    └→ Backend Agent + Frontend Agent  ←  LLM 自由生成完整 TS 文件
                          └→ Test Agent (verify)   ←  全靠 grep 字符串推断
                                └→ Ops 部署 / Learning
```

**每一步成功率（约数）：**

| 步骤 | LLM 类型任务 | 单步成功率 |
|---|---|---|
| PM 写 PRD | LLM 强项 | ~85% |
| Design 出设计 | LLM 中等 | ~70% |
| Contract 预测 6 endpoint | LLM 中等 | ~70% |
| Backend 生成完整 TS 代码 | LLM **弱项** | ~50% |
| Frontend 生成完整 TSX | LLM **弱项** | ~50% |
| Test 静态/动态/视觉/交互 | LLM 中等偏弱 | ~40% |

**串行相乘 ≈ 3%**。retry 是"再投一次 LLM"，期望值不变。

**核心症结：** LLM 自由生成完整代码是它最不擅长的事，被当成主力任务用。
这跟 devin/Claude Code 同款——但 devin 的目标用户是工程师（能修错），OPC 目标用户是非工程师。

---

## 路径 A — 模板 + LLM 填空（推荐）

### 设计原则

- **代码 = 模板库**（人写的，100% 可运行、100% 视觉过线、100% 自带验证）
- **LLM = 意图理解 + slot 填充**（"做哪个模板 + 改什么变量"）
- **不再让 LLM 自由生成 TSX/TS 代码**

### 数据流

```
用户一句话
  └→ IntentAgent            (LLM: 选模板 + 提取 slot 值)
        └→ SlotResolver     (非 LLM: 校验 slot 是否合法)
              └→ TemplateEngine (非 LLM: 把模板 + slot 拼成最终项目)
                    └→ SelfVerify (非 LLM: 跑模板自带的 playwright 套件)
                          └→ Preview + 交付

模板库：100 个预制模板
  └ 每个模板自带：代码骨架、playwright 验证脚本、默认配色、可填 slot 列表

LLM 调用次数：1 次（IntentAgent）
```

### Agent 角色变化

| 角色 | 旧版本 | 新版本 |
|---|---|---|
| CEO | LLM 分析意图 | **删除**（被 IntentAgent 吸收） |
| PM | LLM 写 PRD | 改为 LLM **填 slot**（短输出、100% 结构化） |
| Design | LLM 设计 UI | **删除**（模板自带设计） |
| Backend Agent | LLM 自由生成 backend TS | **删除**（模板自带 backend） |
| Frontend Agent | LLM 自由生成 frontend TSX | **删除**（模板自带 frontend） |
| Test Agent | LLM + grep + HTTP 探活 | **改为模板自带 playwright**（LLM 不参与） |
| Ops | 默认生成 docker-compose | **保留**（不变） |

**LLM 在整个 pipeline 里只出现 1 次。**

### Slot 设计的示例

```jsonc
// 模板: "todo-app-v1"
{
  "template_id": "todo-app-v1",
  "slots": {
    "title":        { "type": "string", "default": "My Todos" },
    "primary_color":{ "type": "color",  "default": "#3b82f6" },
    "fields":       { "type": "schema", "schema": "todo_fields_v1" },
    "row_layout":   { "type": "enum",   "values": ["card", "row", "compact"] }
  }
}
```

LLM 输出 → 转成上面这个 JSON → 模板引擎消费 → 完整项目。

### 文件系统变化（最小手术式）

```
opc/
├── backend/
│   ├── app/
│   │   ├── agent/
│   │   │   ├── intent_agent.py        (LLM, 唯一)
│   │   │   ├── slot_resolver.py       (非 LLM)
│   │   │   ├── template_engine.py     (非 LLM)
│   │   │   └── (删) backend/frontend/test/design_agent.py
│   │   └── templates/
│   │       ├── todo-app-v1/           (代码 + playwright 套件 + slots.json)
│   │       ├── calculator-v1/
│   │       ├── clock-v1/
│   │       └── ... (100 个)
│   └── tests/
│       └── test_template_renders.py   (模板各自独立测试)
```

### 预期成功率

- IntentAgent 分类意图 + 提取 slot：**~92%**（LLM 强项）
- SlotResolver 校验：**~99%**（确定性逻辑）
- TemplateEngine 渲染：**100%**（纯字符串替换）
- 模板自带 playwright 探活：**100%**（人写的测试）

**整体成功率 ≈ 90%**（瓶颈是 LLM 理解意图，用户输入越清晰越好）。

### 工作量估算

- 模板库首批 10 个（覆盖 80% 常见需求：todo、CRUD、看板、表单、落地页、博客、电商、计算器、时钟、聊天）—— **2 周**
- IntentAgent + SlotResolver + TemplateEngine 骨架 —— **1 周**
- 现有项目的 20+ 次积累作为反向数据，沉淀模板适配 → 同步进行

### 优势 / 劣势

| + | - |
|---|---|
| 成功率从 3% → 90% | 需要先建模板库（一次性成本） |
| 视觉质量直接由设计师把控 | 模板库外需求（比如"做个 ATM 取款机"）当前不支持 |
| test 套件自带，无需 LLM | 模板更新慢，新框架/新组件需要重做模板 |
| LLM 调用 1 次→成本骤降 | 迁移期需要双轨，运营压力 |
| 跟 "用户不会写代码" 目标用户一致 | 需要 LLM 与模板 ABI 严格定义 |

---

## 路径 B — 现状改进（结构化 schema + 增量 retry）

### 设计原则

- **保留 LLM 自由生成**（不推翻现有架构）
- **加 schema 约束**：前后端 agent 必须输出结构化 manifest，不靠 grep 猜
- **retry 改粒度**：单文件 / 单路径失败时局部 patch，而不是整段重抽

### 数据流（变化部分用 ★ 标记）

```
用户一句话
  └→ PM 写 PRD
        └→ Design 出设计
              └→ Contract 预测 (LLM)
                    └→ ★ Backend Agent 输出 manifest.json + 文件列表
                    └→ ★ Frontend Agent 输出 manifest.json + 文件列表
                              └→ ★ Test Agent 直接解析 manifest（不靠 grep）
                                    └→ Ops
```

### Agent 角色变化

| 角色 | 变化 |
|---|---|
| Backend Agent | 输出末尾加一份 `manifest.json`：`{endpoint_to_file_map, imports, deps, entry}` |
| Frontend Agent | 同上 + `{fetch_call_to_contract_url_map, components, state_stores}` |
| Test Agent | **直接消费 manifest**（已有结构化数据，不用 grep） |
| Retry 策略 | 失败时只 patch 受影响的文件，不重抽整个 backend/frontend |

### 预期成功率

- 结构化 manifest 解决 `frontend fetch URLs not in api_contract` 这类 grep 猜不到的问题：**+15%**
- 增量 patch 减少"一个文件错就整段重抽"：**+10%**
- 视觉退化阈值 8 仍是天花板、LLM 写代码错仍是大概率 → **整体上限 ~40-50%**

### 工作量估算

- manifest schema 定义 + 强制输出提示词：**3 天**
- Test Agent 改用 manifest：**3 天**
- 增量 retry 重写 orchestrator **RetryState 逻辑：** **1 周**
- 全套集成测试：**3 天**

### 优势 / 劣势

| + | - |
|---|---|
| 工作量小（~3 周） | **天花板仍然 40-50%**，用户痛点没解决 |
| 不推翻现有架构 | 视觉质量不会自动变好 |
| LLM 能力强的话效果可接受 | 仍依赖 LLM 自由写代码，长期仍会撞 c‌apability 上限 |
| 短期能交付一些"差不多行"的项目 | 不符合"非工程师直接交付"的产品目标 |

---

## 关键差异表（决策用）

| 维度 | 路径 A（模板） | 路径 B（schema 约束） |
|---|---|---|
| 成功率（端到端交付） | **~90%** | ~40-50% |
| 视觉质量 | 设计师把控，**持续 100%** | 仍 LLM 决定，**参差** |
| LLM 调用次数 | 1 | 6-12 |
| 改动量 | 大（2-3 周） | 小（2-3 周） |
| 跟产品目标契合度 | **完全契合** | 仍是"AI 写代码"思路 |
| 长期天花板 | 受 slot schema 限制 | 受 LLM 能力限制（当前 5% 自由代码成功率） |
| 失败时用户感受 | "我没表达清楚"（可补救） | "OPC 又抽风了"（不可补救） |

---

## 我的建议

走 **路径 A**。

理由：
1. 你的目标用户是非工程师，路径 B 的"参差"对他们是体验灾难，路径 A 是单一体验。
2. 路径 B 是"在烂路上补轮胎"，路径 A 是"换条路"——后者一旦建成就比前者永远好。
3. 模板库可以分批建（先做 10 个验证 ROI，再扩）。
4. 切路径 A 时**已有路径 B 的代码可以复用**——前端 Next.js 模板、后端 Express 模板、playwright 验证脚手架都还能用，只是把"LLM 填充文件"换成"模板 + LLM 填 slot"。

下一步看你拍板：
- 选 A → 我写路径 A 的实现 plan + 第一批 10 个模板的目录骨架
- 选 B → 我直接动手改 manifest schema + retry 粒度
- 都先不动 → 这文档存档
