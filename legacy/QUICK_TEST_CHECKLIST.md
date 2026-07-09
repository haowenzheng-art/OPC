# OPC Phase 1-10 快速测试检查清单

## 🚀 5分钟冒烟测试

### 第一步：环境检查 (1分钟)
- [ ] `node --version` >= 18
- [ ] `npm install` 已执行
- [ ] `.env` 文件存在且配置了 LLM API
- [ ] `npx prisma db push` 已执行

### 第二步：编译测试 (1分钟)
```bash
npm run build
```
- [ ] 无 TypeScript 错误
- [ ] 编译成功完成

### 第三步：LLM 连接测试 (1分钟)
```bash
npx tsx test-llm.ts
```
- [ ] LLM API 连接正常
- [ ] 返回有效响应

### 第四步：CLI 完整流程 (2分钟)
```bash
npm run dev
```
观察控制台输出：
- [ ] 系统正常启动
- [ ] PM 生成 PRD
- [ ] Backend 发布 API 规范
- [ ] Frontend 生成代码
- [ ] Test 分析代码
- [ ] Ops 生成部署文档
- [ ] 进入 learning 状态
- [ ] 最终完成 (done)

---

## 📋 详细功能验证清单

### Phase 1-5: 基础架构
- [ ] 数据库读写正常
- [ ] 消息总线工作
- [ ] 状态机流转正确
- [ ] BaseAgent 基础能力

### Phase 6-8: LLM 集成
- [ ] PM 用 LLM 生成 PRD
- [ ] Frontend 用 LLM 分析 PRD 写代码
- [ ] Backend 用 LLM 分析 PRD 写代码 + API 规范
- [ ] Test 用 LLM 分析代码质量
- [ ] Ops 用 LLM 生成部署文档
- [ ] A2A 交互：Frontend 等待 Backend API 规范

### Phase 9-10: 深度优化
- [ ] CEO 能用 LLM 自然对话
- [ ] CEO 能安全读取项目文件
- [ ] CEO 不能读取 .env / node_modules
- [ ] Skill Library 激活（从错误学习）
- [ ] Workflow Template 保存
- [ ] 前后端数据结构统一 (id: number, text: string)
- [ ] 生成项目包含 .env.example
- [ ] DEPLOYMENT.md 内容完善

### 飞书模式 (可选)
- [ ] 6个 Bot 配置正确
- [ ] CEO 私聊命令工作 (/start, /stop, /read, /help)
- [ ] 群聊消息实时显示
- [ ] 用户群聊消息被忽略

---

## ✅ 通过标准

**最低通过标准**:
- [ ] 冒烟测试全部通过
- [ ] CLI 模式能完整运行完一个项目
- [ ] generated-projects/ 下有可用的项目输出

**理想状态**:
- [ ] 所有检查项打勾
- [ ] 飞书模式也验证通过
- [ ] 生成的项目能实际启动运行

---

## 🎯 开始测试

```bash
# 按照这个顺序执行：

# 1. 环境准备
npm install
npx prisma db push

# 2. 编译检查
npm run build

# 3. LLM 检查
npx tsx test-llm.ts

# 4. 完整运行
npm run dev

# 5. 查看生成的项目
ls -la generated-projects/
```

详细测试步骤请参考 **MANUAL_TESTING_GUIDE.md**
