# OPC 项目 Phase 1-10 手动测试全流程

## 目录
1. [测试前准备](#测试前准备)
2. [Phase 1-5 基础功能测试](#phase-1-5-基础功能测试)
3. [Phase 6-8 LLM集成测试](#phase-6-8-llm集成测试)
4. [Phase 9-10 深度优化测试](#phase-9-10-深度优化测试)
5. [飞书模式完整测试](#飞书模式完整测试)
6. [问题排查指南](#问题排查指南)

---

## 测试前准备

### 1.1 环境检查清单

- [ ] Node.js 版本 >= 18
- [ ] npm install 已执行完成
- [ ] .env 文件已配置（参考 .env.example）
- [ ] Prisma 数据库已初始化
- [ ] LLM API 配置正确

### 1.2 快速环境配置

```bash
# 1. 安装依赖
npm install

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 LLM API 配置

# 3. 初始化数据库
npx prisma generate
npx prisma db push

# 4. 编译测试
npm run build
```

### 1.3 验证配置文件

检查 `.env` 包含以下内容：
```env
# LLM API配置（必须）
LLM_API_BASE=https://api.example.com
LLM_API_KEY=your_api_key_here
API_FORMAT=anthropic  # 或 openai
ANTHROPIC_VERSION=2023-06-01

# 运行模式
OPC_MODE=cli  # 或 feishu

# 飞书配置（飞书模式需要）
# FEISHU_CEO_APP_ID=...
# FEISHU_CEO_APP_SECRET=...
# ...
```

---

## Phase 1-5 基础功能测试

### Test 1.1: CLI模式启动验证

**目标**: 验证项目能正常启动，基础架构工作

**步骤**:
```bash
# 1. 启动CLI模式
npm run dev

# 2. 观察控制台输出
```

**预期结果**:
- [ ] 控制台显示 "=== OPC 多Agent系统 ==="
- [ ] 显示运行模式 "cli"
- [ ] 无错误抛出
- [ ] 项目开始自动运行演示

---

### Test 1.2: 数据库读写验证

**目标**: 验证 Prisma 数据库连接正常

**步骤**:
```typescript
// 创建测试文件 test-db.ts
import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();

async function testDb() {
  // 测试创建项目
  const project = await prisma.project.create({
    data: {
      id: 'test-project-' + Date.now(),
      userIdea: '测试项目',
      status: 'idle'
    }
  });
  console.log('✅ 创建项目成功:', project.id);

  // 测试读取项目
  const found = await prisma.project.findUnique({
    where: { id: project.id }
  });
  console.log('✅ 读取项目成功:', found?.userIdea);

  // 测试创建消息
  const msg = await prisma.message.create({
    data: {
      id: 'test-msg-' + Date.now(),
      projectId: project.id,
      fromAgent: 'test',
      toAgent: null,
      type: 'text',
      content: '测试消息',
      createdAt: new Date()
    }
  });
  console.log('✅ 创建消息成功:', msg.id);

  await prisma.$disconnect();
  console.log('\n🎉 所有数据库测试通过！');
}

testDb().catch(console.error);
```

运行:
```bash
npx tsx test-db.ts
```

**预期结果**:
- [ ] 创建项目成功
- [ ] 读取项目成功
- [ ] 创建消息成功
- [ ] 无数据库连接错误

---

### Test 1.3: 消息总线验证

**目标**: 验证 Agent 之间能正常收发消息

**步骤**:
```typescript
// 创建测试文件 test-message-bus.ts
import { sendMessage, onMessage } from './src/layers/messaging/bus.js';

const testProjectId = 'test-bus-' + Date.now();

console.log('📡 测试消息总线...\n');

// 1. 注册监听
const unsubscribe = onMessage((msg) => {
  console.log(`✅ 收到消息: [${msg.fromAgent}] -> [${msg.toAgent || '群聊'}]`);
  console.log(`   内容: ${msg.content}\n`);
});

// 2. 发送测试消息
await sendMessage(testProjectId, 'PM', 'Frontend', 'text', '请开始写代码');
await sendMessage(testProjectId, 'Frontend', 'PM', 'text', '收到，开始工作');
await sendMessage(testProjectId, 'Backend', null, 'text', 'API设计完成');

// 3. 清理
unsubscribe();

console.log('🎉 消息总线测试完成！');
```

运行:
```bash
npx tsx test-message-bus.ts
```

**预期结果**:
- [ ] 监听器能收到所有消息
- [ ] 消息内容正确传递
- [ ] fromAgent/toAgent 字段正确

---

### Test 1.4: 状态机流转验证

**目标**: 验证项目生命周期状态正确流转

**步骤**:
```typescript
// 创建测试文件 test-state-machine.ts
import { ProjectStateMachine } from './src/layers/orchestration/project-machine.js';

console.log('🔄 测试状态机流转...\n');

const machine = new ProjectStateMachine('测试状态机', 'test-sm-' + Date.now());

const logState = (step: string) => {
  console.log(`${step} -> 当前状态: ${machine.getState().value}`);
};

logState('初始状态');

machine.start();
logState('调用 start()');

machine.prdDone('测试 PRD');
logState('调用 prdDone()');

machine.frontendDone();
logState('调用 frontendDone()');

machine.backendDone();
logState('调用 backendDone()');

machine.testsPass();
logState('调用 testsPass()');

machine.deployed('http://localhost:3000');
logState('调用 deployed()');

machine.learningDone();
logState('调用 learningDone()');

// 验证最终状态
console.log('\n📊 最终上下文:', machine.getContext());

console.log('\n🎉 状态机测试完成！');
```

运行:
```bash
npx tsx test-state-machine.ts
```

**预期结果**:
- [ ] 初始状态: idle
- [ ] start() -> planning
- [ ] prdDone() -> developing
- [ ] frontendDone() -> developing (等待后端)
- [ ] backendDone() -> testing
- [ ] testsPass() -> deploying
- [ ] deployed() -> learning
- [ ] learningDone() -> done

---

### Test 1.5: BaseAgent 基础能力验证

**目标**: 验证 BaseAgent 的感知-推理-行动循环

**检查项**:
- [ ] Agent 能保存记忆 (`saveMemory()`)
- [ ] Agent 能检索记忆 (`getMemories()`)
- [ ] Agent 能发送消息 (`sendMessage()`)
- [ ] Agent 能接收消息 (`handleMessage()`)
- [ ] `useHardcodedMode` 开关工作正常

---

## Phase 6-8 LLM集成测试

### Test 2.1: LLM API 连接测试

**目标**: 验证 llm-client.ts 能正常调用 LLM

**步骤**:
```typescript
// 创建测试文件 test-llm.ts
import { chat, chatStream } from './src/layers/tools/llm-client.js';

console.log('🤖 测试 LLM API 连接...\n');

try {
  // 测试1: 普通对话
  console.log('Test 1: 普通对话...');
  const response = await chat([
    { role: 'user', content: '用一句话介绍你自己' }
  ]);
  console.log('✅ LLM 响应:', response.content.substring(0, 100) + '...\n');

  // 测试2: 流式对话（如果可用）
  console.log('Test 2: 流式对话...');
  let fullContent = '';
  for await (const chunk of chatStream([
    { role: 'user', content: '说"你好"' }
  ])) {
    fullContent += chunk.content;
    process.stdout.write(chunk.content);
  }
  console.log('\n✅ 流式响应完成:', fullContent);

  console.log('\n🎉 LLM API 测试通过！');
} catch (error) {
  console.error('❌ LLM API 测试失败:', error);
  throw error;
}
```

运行:
```bash
npx tsx test-llm.ts
```

**预期结果**:
- [ ] 普通对话返回正常响应
- [ ] 流式对话逐字返回（如果支持）
- [ ] 无 API 连接错误

---

### Test 2.2: PM Agent LLM 测试

**目标**: 验证 PM 能使用 LLM 生成 PRD

**检查项**:
```bash
# 运行 CLI 模式，观察 PM 的行为
npm run dev
```

观察 PM 的输出:
- [ ] PM 能理解用户需求"简单待办清单"
- [ ] PM 使用 LLM 生成 PRD（不是硬编码模板）
- [ ] PRD 包含: 产品概述、功能列表、数据模型、用户流程
- [ ] PRD 保存到记忆系统

---

### Test 2.3: Frontend Agent LLM 测试

**目标**: 验证 Frontend 能读 PRD 并生成代码

**检查项**:
- [ ] Frontend 接收 PRD
- [ ] Frontend 使用 LLM 分析 PRD
- [ ] Frontend 生成 Next.js 页面代码
- [ ] 代码使用正确的 API 路径: `http://localhost:3001/api/...`
- [ ] 数据模型使用正确的字段: `id: number, text: string`

---

### Test 2.4: Backend Agent LLM 测试

**目标**: 验证 Backend 能读 PRD 并生成代码 + API 规范

**检查项**:
- [ ] Backend 接收 PRD
- [ ] Backend 使用 LLM 分析 PRD
- [ ] Backend 生成 Express + TypeScript 代码
- [ ] Backend 发布详细的 API 规范（数据模型 + 端点 + 示例）
- [ ] API 规范包含 `updatedAt` 字段

---

### Test 2.5: A2A 交互测试

**目标**: 验证 Frontend 等待 Backend API 规范

**检查流程**:
1. Backend 先启动，解析 PRD
2. Backend 发布 API 规范消息
3. Frontend 等待收到 API 规范
4. Frontend 才开始生成代码
5. Frontend 的 LLM prompt 中包含 Backend 的 API 规范

---

### Test 2.6: Test Agent LLM 测试

**目标**: 验证 Test 能分析代码质量

**检查项**:
- [ ] Test 读取生成的前端和后端代码
- [ ] Test 使用 LLM 分析代码质量
- [ ] Test 生成测试报告
- [ ] 报告包含: 文件结构检查、代码质量评分、改进建议

---

### Test 2.7: Ops Agent LLM 测试

**目标**: 验证 Ops 能生成部署文档

**检查项**:
- [ ] Ops 读取项目结构
- [ ] Ops 生成 DEPLOYMENT.md
- [ ] Ops 生成 .env.example 文件
- [ ] DEPLOYMENT.md 包含: 启动步骤、常见问题排查
- [ ] Ops 提示会自动安装 npm 依赖

---

## Phase 9-10 深度优化测试

### Test 3.1: CEO Agent 文件读取测试

**目标**: 验证 CEO 能安全读取项目文件

**步骤**:
```typescript
// 创建测试文件 test-ceo-files.ts
import { CeoAgent } from './src/layers/agents/ceo.js';

console.log('👨‍💼 测试 CEO 文件读取能力...\n');

const ceo = new CeoAgent('test-ceo-' + Date.now(), '测试项目');

// 测试1: 读取允许的文件
console.log('Test 1: 读取 package.json...');
try {
  const content = await ceo.readFile('package.json');
  console.log('✅ 读取成功，长度:', content.length, 'bytes');
} catch (e) {
  console.error('❌ 读取失败:', e);
}

// 测试2: 安全机制 - 禁止读取 .env
console.log('\nTest 2: 尝试读取 .env (应该被禁止)...');
try {
  await ceo.readFile('.env');
  console.error('❌ 安全漏洞: 应该禁止读取 .env');
} catch (e) {
  console.log('✅ 安全机制正常: 禁止读取敏感文件');
}

// 测试3: 安全机制 - 禁止读取 node_modules
console.log('\nTest 3: 尝试读取 node_modules (应该被禁止)...');
try {
  await ceo.readFile('node_modules/package.json');
  console.error('❌ 安全漏洞: 应该禁止读取 node_modules');
} catch (e) {
  console.log('✅ 安全机制正常: 禁止访问 node_modules');
}

// 测试4: 智能分析
console.log('\nTest 4: 智能分析相关文件...');
const files = await ceo.analyzeAndReadRelevantFiles('看看项目的package.json');
console.log('✅ 智能读取的文件:', Object.keys(files));

console.log('\n🎉 CEO 文件读取测试完成！');
```

运行:
```bash
npx tsx test-ceo-files.ts
```

**预期结果**:
- [ ] 能正常读取 package.json、PLAN.md 等文件
- [ ] 禁止读取 .env、node_modules、.git 等
- [ ] 智能分析能根据用户消息读取相关文件

---

### Test 3.2: Skill Library 学习测试

**目标**: 验证 Agent 能从错误中学习

**检查项**:
- [ ] Agent 遇到错误时调用 `handleErrorWithSkills()`
- [ ] Skill Library 检索相关经验
- [ ] 成功解决问题后调用 `saveSuccessExperience()`
- [ ] 新保存的 skill 包含问题描述、解决方案、代码示例
- [ ] 下次调用 LLM 时自动注入相关 skills

---

### Test 3.3: 前后端数据结构统一测试

**目标**: 验证 Todo 模型前后端一致

**检查规范**:
```typescript
// Todo 统一规范
interface Todo {
  id: number;           // 数字ID，自增
  text: string;         // 内容字段用 text，不用 title
  completed: boolean;   // 完成状态
  createdAt: Date;      // 创建时间
  updatedAt: Date;      // 更新时间
}
```

**验证步骤**:
1. 运行完整流程
2. 检查生成的后端代码:
   - [ ] Todo 模型包含 `updatedAt`
   - [ ] API 响应返回正确格式
3. 检查生成的前端代码:
   - [ ] 使用 `text` 字段，不是 `title`
   - [ ] 使用 `id: number`，不是 `id: string`
   - [ ] API 请求发送 `{ text: '...' }`，不是 `{ title: '...' }`

---

### Test 3.4: Workflow Template 保存测试

**目标**: 验证工作流模板正确保存

**检查项**:
- [ ] 项目完成后进入 `learning` 状态
- [ ] Orchestrator 收集各 Agent 的 `filesWritten`
- [ ] WorkflowTemplate 保存到数据库
- [ ] Template 包含: 各 Agent 的 steps、生成的文件列表
- [ ] 新项目启动时能检索匹配的模板

---

### Test 3.5: 流式响应支持测试

**目标**: 验证 LLM 流式响应（如果 API 支持）

**检查项**:
- [ ] `chatStream()` 函数正常工作
- [ ] 返回 SSE 格式的 chunk
- [ ] 正确解析 content 字段
- [ ] token 使用统计正确

---

### Test 3.6: 飞书消息重试机制测试

**目标**: 验证网络错误时自动重试

**检查项**:
- [ ] `sendWithRetry()` 函数存在
- [ ] 失败时最多重试 3 次
- [ ] 使用指数退避延迟
- [ ] 最终失败时抛出错误

---

## 飞书模式完整测试

### Test 4.1: 飞书模式启动测试

**前置条件**:
- 已创建 6 个飞书应用（CEO/PM/Frontend/Backend/Test/Ops）
- 已配置 `.env.feishu` 文件
- 已创建飞书群聊，获取 GROUP_CHAT_ID

**步骤**:
```bash
# 1. 配置飞书环境
cp .env.feishu.example .env.feishu
# 编辑 .env.feishu，填入所有飞书凭证

# 2. 启动飞书模式
npm run dev:feishu
```

**预期结果**:
- [ ] Express 服务启动，监听配置的端口
- [ ] 控制台显示 "飞书模式已启动，等待用户私聊CEO指令"
- [ ] 无配置错误

---

### Test 4.2: CEO 私聊命令测试

**测试步骤**:

1. **私聊 CEO 发送 /help**
   - 预期: CEO 用 LLM 自然回复帮助信息
   - 预期: 显示可用命令列表

2. **私聊 CEO 发送 /start 做一个待办清单**
   - 预期: CEO 确认需求
   - 预期: 项目开始运行
   - 预期: 群聊中看到各 Agent 消息

3. **私聊 CEO 发送 /status**
   - 预期: CEO 报告当前项目状态

4. **私聊 CEO 发送 /read package.json**
   - 预期: CEO 读取并分析文件内容

5. **私聊 CEO 发送 /stop**
   - 预期: CEO 停止当前项目

---

### Test 4.3: 群聊观察测试

**观察群聊中的消息**:
- [ ] PM 发送 PRD 相关消息
- [ ] Backend 发送 API 设计消息
- [ ] Backend 发布详细 API 规范
- [ ] Frontend 等待 API 规范后开始工作
- [ ] Frontend 发送进度更新
- [ ] Test 发送测试报告
- [ ] Ops 发送部署信息
- [ ] CEO 发送协调消息
- [ ] 消息格式正确（Agent名称加粗，特殊符号正确显示）

---

### Test 4.4: 用户消息边界测试

**验证用户群聊消息被忽略**:
- [ ] 在群聊中发送任意消息
- [ ] 系统忽略该消息（UserMessageFilter 工作）
- [ ] 只有私聊 CEO 的消息被处理

---

## 完整端到端测试流程

### Test 5.1: CLI模式完整运行测试

**目标**: 从开始到结束验证完整流程

**步骤**:
```bash
# 1. 清理旧数据（可选）
rm -rf generated-projects/*
rm -rf prisma/dev.db*

# 2. 重新初始化数据库
npx prisma db push

# 3. 启动完整流程
npm run dev

# 4. 全程观察，约2-5分钟
```

**验证清单**:

| 阶段 | 检查项 | 状态 |
|-----|--------|------|
| **启动** | 系统正常启动，无错误 | ☐ |
| **Planning** | PM 生成高质量 PRD | ☐ |
| **Planning** | PM 保存记忆 | ☐ |
| **Developing** | Backend 先启动 | ☐ |
| **Developing** | Backend 发布 API 规范 | ☐ |
| **Developing** | Frontend 等待 API 规范 | ☐ |
| **Developing** | Frontend 按 API 规范生成代码 | ☐ |
| **Developing** | Frontend 使用 `http://localhost:3001/api/...` | ☐ |
| **Developing** | 前后端数据结构统一（text 字段） | ☐ |
| **Developing** | 两个 Agent 都保存记忆 | ☐ |
| **Testing** | Test 分析代码质量 | ☐ |
| **Testing** | Test 生成测试报告 | ☐ |
| **Deploying** | Ops 生成 DEPLOYMENT.md | ☐ |
| **Deploying** | Ops 生成 .env.example | ☐ |
| **Deploying** | Ops 提示会安装依赖 | ☐ |
| **Learning** | 工作流模板保存 | ☐ |
| **Learning** | Skills 保存（如果有） | ☐ |
| **Done** | 项目标记完成 | ☐ |
| **产出** | generated-projects/ 下有完整项目 | ☐ |

---

### Test 5.2: 生成项目验证测试

**目标**: 验证生成的项目能实际运行

**步骤**:
```bash
# 1. 进入生成的项目目录
cd generated-projects/[你的项目ID]

# 2. 查看项目结构
ls -la

# 3. 检查关键文件
ls -la frontend/
ls -la backend/
cat DEPLOYMENT.md
cat frontend/.env.example
cat backend/.env.example

# 4. 按 DEPLOYMENT.md 尝试启动（可选）
# cd backend && npm install && npm run dev
# cd ../frontend && npm install && npm run dev
```

**检查项**:
- [ ] frontend/ 目录存在，包含 Next.js 项目
- [ ] backend/ 目录存在，包含 Express 项目
- [ ] DEPLOYMENT.md 内容完整、步骤清晰
- [ ] 前后端都有 .env.example
- [ ] 前端 package.json 有正确的 scripts
- [ ] 后端 package.json 有正确的 scripts

---

## 问题排查指南

### 常见问题 1: TypeScript 编译错误

**症状**: `npm run build` 失败

**排查步骤**:
```bash
# 1. 查看详细错误
npm run build

# 2. 检查 tsconfig.json 配置
cat tsconfig.json

# 3. 常见修复:
# - 确保所有文件使用 .js 扩展名导入（ES module）
# - 确保类型定义正确
# - 运行 npx tsc --noEmit 查看所有错误
```

---

### 常见问题 2: LLM API 连接失败

**症状**: Agent 调用 LLM 超时或报错

**排查步骤**:
```bash
# 1. 检查 .env 配置
cat .env | grep LLM_API

# 2. 测试 API 连接
curl -X POST $LLM_API_BASE/v1/messages \
  -H "x-api-key: $LLM_API_KEY" \
  -H "anthropic-version: $ANTHROPIC_VERSION" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet","max_tokens":100,"messages":[{"role":"user","content":"hi"}]}'

# 3. 检查 llm-client.ts 配置
# 确认 API_FORMAT 设置正确（anthropic 或 openai）
```

---

### 常见问题 3: 数据库错误

**症状**: Prisma 相关错误

**排查步骤**:
```bash
# 1. 重新生成客户端
npx prisma generate

# 2. 重新推送 schema
npx prisma db push

# 3. 检查数据库文件
ls -la prisma/

# 4. 查看 Prisma schema
cat prisma/schema.prisma
```

---

### 常见问题 4: 飞书 Webhook 验证失败

**症状**: 飞书后台配置时验证失败

**排查步骤**:
- 确认服务器公网可访问
- 检查端口配置正确
- 确认 VERIFICATION_TOKEN 匹配
- 检查服务器日志看具体错误

---

## 测试报告模板

### 测试执行记录

| 测试项 | 测试时间 | 结果 | 测试人员 | 备注 |
|--------|---------|------|---------|------|
| Test 1.1 CLI启动 | | ☐ | | |
| Test 1.2 数据库 | | ☐ | | |
| Test 1.3 消息总线 | | ☐ | | |
| Test 1.4 状态机 | | ☐ | | |
| Test 2.1 LLM连接 | | ☐ | | |
| Test 2.2 PM LLM | | ☐ | | |
| Test 2.3 Frontend LLM | | ☐ | | |
| Test 2.4 Backend LLM | | ☐ | | |
| Test 2.5 A2A交互 | | ☐ | | |
| Test 2.6 Test Agent | | ☐ | | |
| Test 2.7 Ops Agent | | ☐ | | |
| Test 3.1 CEO文件读取 | | ☐ | | |
| Test 3.2 Skill学习 | | ☐ | | |
| Test 3.3 数据结构统一 | | ☐ | | |
| Test 3.4 Workflow保存 | | ☐ | | |
| Test 4.1 飞书启动 | | ☐ | | |
| Test 4.2 CEO命令 | | ☐ | | |
| Test 4.3 群聊观察 | | ☐ | | |
| Test 5.1 完整端到端 | | ☐ | | |
| Test 5.2 生成项目验证 | | ☐ | | |

### 总体评估

- [ ] 所有测试通过 🎉
- [ ] 部分测试通过，问题已记录
- [ ] 阻塞性问题需要先解决

---

## 快速开始测试脚本

创建 `run-all-tests.sh`:
```bash
#!/bin/bash
echo "🧪 OPC 项目完整测试开始..."

# 1. 基础检查
echo ""
echo "1️⃣  检查环境..."
node --version
npm --version

# 2. 编译测试
echo ""
echo "2️⃣  编译检查..."
npm run build

if [ $? -ne 0 ]; then
  echo "❌ 编译失败，请先修复TypeScript错误"
  exit 1
fi

echo "✅ 编译通过！"

# 3. 运行单元测试
echo ""
echo "3️⃣  运行单元测试..."
npm run test:unit

# 4. 提示进行手动测试
echo ""
echo "4️⃣  手动测试指南（按顺序执行）:"
echo "   - 运行 'npm run dev' 测试CLI模式完整流程"
echo "   - 参考 MANUAL_TESTING_GUIDE.md 进行详细测试"
echo ""
echo "🧪 自动化测试完成！"
```

---

## 总结

本测试指南覆盖了 Phase 1-10 的所有核心功能:

1. ✅ **Phase 1-5**: 基础架构、数据库、消息总线、状态机
2. ✅ **Phase 6-8**: LLM 集成、各 Agent 智能、A2A 交互
3. ✅ **Phase 9-10**: CEO 文件读取、Skill 学习、数据统一、Workflow 保存
4. ✅ **飞书模式**: 6个 Bot、命令处理、群聊展示

建议按照本指南顺序执行测试，确保系统达到 100% 满意状态！
