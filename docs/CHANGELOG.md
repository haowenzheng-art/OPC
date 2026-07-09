# OPC 更新日志

## v0.5.0 — 2026-07-10 · Marketing + GitHub Open Source

### 新增
- 营销官网 `marketing-site/index.html` —— 5 sections, Apple 风格
- 官网 favicon (`marketing-site/favicon.svg`)
- 产品级 README.md (重写为产品介绍页)
- `docs/PRD.md` 产品需求文档
- `docs/screenshots/` 7 张官网截图

### 优化
- README 顶部加 logo + hero screenshot
- 文档互链: README ↔ PRD ↔ STAGE*.md ↔ BASELINE_RESULT

---

## v0.4.0 — Stage 4 · Templates

### 新增
- 模板 fallback 框架 (`backend/app/agent/templates/`)
- 电商 / 招聘 / 营销三个验证过的模板骨架

详见 [STAGE4_TEMPLATES.md](../STAGE4_TEMPLATES.md)

---

## v0.3.0 — Stage 3 · Visual Generation

### 新增
- Designer Agent 输出 `design_spec.json` (色板 / 字体 / 间距 token)
- Frontend Agent 强制读 `design_spec.json` 再写组件

详见 [STAGE3_VISUAL.md](../STAGE3_VISUAL.md)

---

## v0.2.0 — Stage 2 · Self-Repair Loop

### 新增
- `FailureSignal` 数据结构 + 信号分类器
- `ProjectOrchestrator._run_repair_pass()` + 文件同步/重载
- `BackendAgent.repair_with_tools()` / `FrontendAgent.repair_with_tools()`
- `ToolRegistry` (read_file / list_files / edit_file / bash / apply_patch)
- retry budget = 3
- 单测覆盖 7 个真实失败场景, **7/7 PASS**

### 修复
- test_agent 加 Playwright `response` 监听器, 把 4xx/5xx 收成 signal
- 修复 frontend fetch URL 错配被吞的 bug
- repair prompt 强化: 强制先读 contract, 改 fetch URL 必须逐字等于 `endpoints[*].full`
- 修复 Python f-string `\${...}` 转义 bug

详见 [STAGE2_SELF_REPAIR.md](../STAGE2_SELF_REPAIR.md)

---

## v0.1.0 — Stage 0/1 · Baseline + Cost Control

### 新增
- 真值测量 (Playwright + 真实 HTTP, 不用 mock)
- `cost_tracker.py` LLM 调用成本记录
- 失败反馈链路
- 多 Agent 编排 (CEO / PM / Designer / Frontend / Backend / Test / Ops)
- FastAPI 后端 + PostgreSQL + Redis + Celery
- Next.js 前端 + Auth + Dashboard + Project Studio

详见 [BASELINE_RESULT_NEW1.md](../BASELINE_RESULT_NEW1.md)

---

## 版本约定

- **Major (v1.x)** —— 上线收费就绪
- **Minor (v0.x)** —— 阶段完成 / 新能力
- **Patch (v0.x.y)** —— bug 修复 / 文档更新