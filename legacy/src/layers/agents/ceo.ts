import { BaseAgent } from './base.js';
import { State, Action } from '../../types/index.js';
import Prisma from '../tools/prisma-client.js';
import { extractCodeBlock } from '../tools/index.js';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const { PrismaClient } = Prisma;
const prisma = new PrismaClient();

// ========== 安全配置 ==========
const FILE_READ_CONFIG = {
  maxFileSizeKB: 50, // 单个文件最大50KB
  blacklistPaths: [
    '.env',
    '.env.local',
    '.env.development',
    '.env.production',
    'node_modules',
    '.git',
    'dist',
    'build',
    'coverage',
    '.next',
    '.cache'
  ],
  blacklistExtensions: [
    '.log',
    '.swp',
    '.swo',
    '.tmp'
  ]
};

// 项目根目录（向上找直到找到package.json）
function findProjectRoot(startDir: string): string {
  let current = startDir;
  while (true) {
    if (fs.existsSync(path.join(current, 'package.json'))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return startDir; // 没找到，用起始目录
    }
    current = parent;
  }
}

const PROJECT_ROOT = findProjectRoot(path.resolve(__dirname, '../../../'));

// CEO决策类型
type CeoDecision =
  | { type: 'ignore'; reason: string }
  | { type: 'acknowledge'; reply: string }
  | { type: 'adjust_project'; changes: string }
  | { type: 'halt_project'; reason: string }
  | { type: 'wait_for_stage'; reply: string };

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

export class CeoAgent extends BaseAgent {
  userIdea: string;
  private pendingMessages: Array<{ content: string; senderId?: string }> = [];
  private currentStage: string = 'planning';
  private hasStarted: boolean = false;
  private processedMessages: Set<string> = new Set();
  private chatHistory: ChatMessage[] = []; // 对话记忆

  // 飞书模式回调
  onStartProject?: (userIdea: string) => void;
  onStopProject?: () => void;
  onReply?: (message: string) => void;

  constructor(projectId: string, userIdea: string = '') {
    super(projectId, 'ceo');
    this.userIdea = userIdea;
  }

  // ========== 覆盖BaseAgent的系统prompt ==========
  protected override getSystemPrompt(): string {
    return `你是一位专业的技术项目CEO（首席执行官），拥有10年以上的互联网产品开发经验。

## 你的核心职责

1. **需求挖掘与分析**：深入理解用户的真实需求，而不是只看表面描述
2. **技术方案评估**：能判断技术选型的合理性，识别潜在风险
3. **项目规划**：制定合理的开发计划，设置清晰的里程碑
4. **团队协调**：协调PM、Frontend、Backend、Test、Ops等角色
5. **质量把控**：确保最终交付的产品质量符合预期
6. **用户沟通**：用专业、友好的方式与用户沟通

## 你的专业能力

### 需求分析框架
- 区分"需要"vs"想要"：区分核心功能和锦上添花的功能
- 识别隐含需求：用户没说但实际需要的功能
- 评估可行性：基于技术能力和时间成本评估

### 技术栈认知
- **前端**：Next.js、React、Tailwind CSS、Vue等主流框架的适用场景
- **后端**：Express、FastAPI、Django等框架的优劣势
- **数据库**：SQLite、PostgreSQL、MySQL的适用场景
- **部署**：Docker、云服务的基本概念

### 风险识别
- 技术风险：技术方案是否存在不确定性
- 时间风险：给定时间内能否完成
- 质量风险：哪些地方容易出问题

## 你的沟通风格

- **专业但不傲慢**：用用户能理解的语言解释技术问题
- **主动引导**：帮助用户把模糊想法细化成可执行方案
- **诚实直接**：遇到困难时不回避，但同时给出替代方案
- **有问必答**：用户的问题都认真对待，即使答案是"我不知道"

## 你在OPC系统中的角色

你管理着5个Agent团队成员：
- **PM**：产品经理，写PRD文档
- **Frontend**：前端工程师，写React/Next.js代码
- **Backend**：后端工程师，写Express API
- **Test**：测试工程师，检查代码质量
- **Ops**：运维工程师，准备部署配置

你不仅是传声筒，更是他们的领导者！
`;
  }

  // 接收用户私聊消息
  receiveUserMessage(content: string, senderId?: string) {
    const msgId = `${Date.now()}-${Math.random()}`;
    if (!this.processedMessages.has(content)) {
      console.log(`[CEO] 收到用户消息: ${content}`);
      this.pendingMessages.push({ content, senderId });
      this.processedMessages.add(content);
      // 记录到对话历史
      this.chatHistory.push({
        role: 'user',
        content,
        timestamp: Date.now()
      });
    }
  }

  async perceive(): Promise<State> {
    // 扫描本地项目状态
    const projectFiles = this.scanLocalProject();
    const memorySummary = await this.loadMemorySummary();

    return {
      projectId: this.projectId,
      status: 'idle' as any,
      userIdea: this.userIdea,
      pendingMessages: this.pendingMessages.length,
      currentStage: this.currentStage,
      projectFiles,
      memorySummary
    };
  }

  async reason(state: State): Promise<Action> {
    if (this.pendingMessages.length > 0) {
      return { type: 'PROCESS_USER_MESSAGE', payload: this.pendingMessages[0] };
    }

    return { type: 'WAIT', payload: null };
  }

  async act(action: Action) {
    if (action.type === 'START_PROJECT') {
      await this.sendMessage('pm', `用户需求：${action.payload}`);
      await this.sendMessage(null, `大家好！新项目：${action.payload}，PM先来分析需求！`);
      this.currentStage = 'planning';
      this.hasStarted = true;
      return;
    }

    if (action.type === 'PROCESS_USER_MESSAGE') {
      const msg = action.payload;

      // 先检查是否是命令
      const commandResult = this.parseCommand(msg.content);
      if (commandResult) {
        await this.handleCommand(commandResult);
        this.pendingMessages.shift();
        return;
      }

      // 用LLM智能回复
      const reply = await this.generateReplyWithLLM(msg.content);
      if (this.onReply) {
        this.onReply(reply);
      }
      // 记录到对话历史
      this.chatHistory.push({
        role: 'assistant',
        content: reply,
        timestamp: Date.now()
      });

      // 从队列移除
      this.pendingMessages.shift();
      return;
    }
  }

  // ========== 安全检查 ==========
  private isPathAllowed(filePath: string): boolean {
    const normalized = path.normalize(filePath);
    const relative = path.relative(PROJECT_ROOT, normalized);

    // 禁止访问项目根目录之外的文件
    if (relative.startsWith('..')) {
      return false;
    }

    // 检查黑名单路径
    for (const blacklist of FILE_READ_CONFIG.blacklistPaths) {
      if (relative.includes(blacklist) || normalized.includes(blacklist)) {
        return false;
      }
    }

    // 检查黑名单扩展名
    const ext = path.extname(normalized).toLowerCase();
    if (FILE_READ_CONFIG.blacklistExtensions.includes(ext)) {
      return false;
    }

    return true;
  }

  // ========== 读取文件内容（带安全限制） ==========
  async readFile(filePath: string): Promise<{ success: boolean; content?: string; error?: string }> {
    try {
      const fullPath = path.resolve(PROJECT_ROOT, filePath);

      // 安全检查
      if (!this.isPathAllowed(fullPath)) {
        console.warn(`[CEO] 拒绝访问受限文件: ${filePath}`);
        return { success: false, error: '无法访问此文件（安全限制）' };
      }

      if (!fs.existsSync(fullPath)) {
        return { success: false, error: '文件不存在' };
      }

      const stats = fs.statSync(fullPath);
      if (!stats.isFile()) {
        return { success: false, error: '路径不是文件' };
      }

      // 检查文件大小
      const sizeKB = stats.size / 1024;
      if (sizeKB > FILE_READ_CONFIG.maxFileSizeKB) {
        return { success: false, error: `文件过大 (${sizeKB.toFixed(1)}KB)，超过限制 ${FILE_READ_CONFIG.maxFileSizeKB}KB` };
      }

      const content = fs.readFileSync(fullPath, 'utf-8');
      console.log(`[CEO] 已读取文件: ${filePath} (${sizeKB.toFixed(1)}KB)`);
      return { success: true, content };
    } catch (e) {
      console.warn(`[CEO] 读取文件失败 ${filePath}:`, e);
      return { success: false, error: String(e) };
    }
  }

  // ========== 扫描本地项目文件 ==========
  private scanLocalProject(): any {
    try {
      // 1. 扫描OPC项目本身的关键文件
      const opcFiles = this.listKeyProjectFiles(PROJECT_ROOT);

      // 2. 扫描生成的项目
      const projectsDir = path.resolve(PROJECT_ROOT, 'generated-projects');
      let generatedProjects: any[] = [];

      if (fs.existsSync(projectsDir)) {
        generatedProjects = fs.readdirSync(projectsDir)
          .filter(p => fs.statSync(path.join(projectsDir, p)).isDirectory())
          .map(projectId => {
            const projectPath = path.join(projectsDir, projectId);
            const hasPRD = fs.existsSync(path.join(projectPath, 'PRD.md'));
            const hasDeployment = fs.existsSync(path.join(projectPath, 'DEPLOYMENT.md'));
            const files = this.listProjectFiles(projectPath, 10);
            return { projectId, hasPRD, hasDeployment, files };
          });
      }

      return {
        opcProject: {
          root: PROJECT_ROOT,
          keyFiles: opcFiles
        },
        hasGeneratedProjects: generatedProjects.length > 0,
        generatedProjects
      };
    } catch (e) {
      console.warn('[CEO] 扫描项目失败:', e);
      return { opcProject: null, hasGeneratedProjects: false, generatedProjects: [], error: String(e) };
    }
  }

  // ========== 列出项目的关键文件 ==========
  private listKeyProjectFiles(projectRoot: string): string[] {
    const keyFiles: string[] = [];
    const candidates = [
      'package.json',
      'PLAN.md',
      'README.md',
      'prisma/schema.prisma',
      'tsconfig.json'
    ];

    for (const candidate of candidates) {
      const fullPath = path.join(projectRoot, candidate);
      if (fs.existsSync(fullPath)) {
        keyFiles.push(candidate);
      }
    }

    // 检查src目录结构
    const srcDir = path.join(projectRoot, 'src');
    if (fs.existsSync(srcDir)) {
      const walk = (dir: string, prefix: string = 'src') => {
        try {
          const entries = fs.readdirSync(dir);
          for (const entry of entries) {
            if (keyFiles.length >= 30) return; // 最多30个文件
            const fullPath = path.join(dir, entry);
            const relativePath = `${prefix}/${entry}`;

            if (fs.statSync(fullPath).isDirectory()) {
              if (!FILE_READ_CONFIG.blacklistPaths.includes(entry)) {
                walk(fullPath, relativePath);
              }
            } else {
              const ext = path.extname(entry);
              if (['.ts', '.tsx', '.js', '.jsx', '.json', '.md'].includes(ext)) {
                keyFiles.push(relativePath);
              }
            }
          }
        } catch (e) {
          // 忽略
        }
      };
      walk(srcDir);
    }

    return keyFiles;
  }

  private listProjectFiles(projectPath: string, limit: number): string[] {
    try {
      const files: string[] = [];
      const walk = (dir: string, prefix: string = '') => {
        if (files.length >= limit) return;
        const entries = fs.readdirSync(dir);
        for (const entry of entries) {
          if (files.length >= limit) break;
          const fullPath = path.join(dir, entry);
          const relativePath = prefix ? `${prefix}/${entry}` : entry;
          if (fs.statSync(fullPath).isDirectory()) {
            if (!FILE_READ_CONFIG.blacklistPaths.includes(entry)) {
              walk(fullPath, relativePath);
            }
          } else {
            files.push(relativePath);
          }
        }
      };
      walk(projectPath);
      return files;
    } catch (e) {
      return [];
    }
  }

  // ========== 加载记忆摘要 ==========
  private async loadMemorySummary(): Promise<any> {
    try {
      const recentSkills = await prisma.skillMemory.findMany({
        take: 5,
        orderBy: { createdAt: 'desc' }
      });
      const recentWorkflows = await prisma.workflowTemplate.findMany({
        take: 3,
        orderBy: { createdAt: 'desc' }
      });
      return {
        skillCount: recentSkills.length,
        recentSkills: recentSkills.map((s: any) => ({ category: s.category, title: s.title })),
        workflowCount: recentWorkflows.length,
        recentWorkflows: recentWorkflows.map((w: any) => ({ name: w.name, category: w.category }))
      };
    } catch (e) {
      return { error: String(e) };
    }
  }

  // ========== 分析用户请求，确定需要读取哪些文件 ==========
  private async analyzeAndReadRelevantFiles(userMessage: string): Promise<{ files: Array<{ path: string; content: string }>; summary: string }> {
    const lowerMsg = userMessage.toLowerCase();
    const filesToRead: string[] = [];
    const projectScan = this.scanLocalProject();

    // 检查是否提到具体文件
    if (lowerMsg.includes('package.json')) {
      filesToRead.push('package.json');
    }
    if (lowerMsg.includes('plan.md')) {
      filesToRead.push('PLAN.md');
    }
    if (lowerMsg.includes('readme')) {
      filesToRead.push('README.md');
    }
    if (lowerMsg.includes('prisma') || lowerMsg.includes('schema')) {
      filesToRead.push('prisma/schema.prisma');
    }

    // 检查是否提到生成的项目
    if (projectScan.hasGeneratedProjects && (lowerMsg.includes('todo') || lowerMsg.includes('生成') || lowerMsg.includes('项目'))) {
      const todoProject = projectScan.generatedProjects.find((p: any) => p.projectId.toLowerCase().includes('todo'));
      if (todoProject) {
        filesToRead.push(`generated-projects/${todoProject.projectId}/PRD.md`);
        filesToRead.push(`generated-projects/${todoProject.projectId}/README.md`);
        // 读取几个关键源码文件
        for (const file of todoProject.files) {
          if (file.endsWith('.ts') || file.endsWith('.tsx') || file.endsWith('.js')) {
            filesToRead.push(`generated-projects/${todoProject.projectId}/${file}`);
            if (filesToRead.length >= 5) break;
          }
        }
      }
    }

    // 读取文件
    const readFiles: Array<{ path: string; content: string }> = [];
    const errors: string[] = [];

    for (const filePath of filesToRead.slice(0, 8)) { // 最多读取8个文件
      const result = await this.readFile(filePath);
      if (result.success && result.content) {
        // 对大内容进行截断
        let content = result.content;
        if (content.length > 2000) {
          content = content.substring(0, 2000) + '\n...[内容已截断]';
        }
        readFiles.push({ path: filePath, content });
      } else if (result.error) {
        errors.push(`${filePath}: ${result.error}`);
      }
    }

    let summary = '';
    if (readFiles.length > 0) {
      summary = `已读取 ${readFiles.length} 个文件: ${readFiles.map(f => f.path).join(', ')}`;
    }
    if (errors.length > 0) {
      summary += `\n部分文件读取失败: ${errors.join('; ')}`;
    }

    return { files: readFiles, summary };
  }

  private async generateReplyWithLLM(userMessage: string): Promise<string> {
    // 先分析并读取相关文件
    const { files: readFiles, summary: fileReadSummary } = await this.analyzeAndReadRelevantFiles(userMessage);

    // 构建上下文
    const statusInfo = !this.hasStarted
      ? '目前没有进行中的项目'
      : this.currentStage === 'done'
      ? '项目已完成，代码在 generated-projects 文件夹'
      : `项目进行中，当前阶段：${this.currentStage}`;

    const recentHistory = this.chatHistory.slice(-15); // 保留最近15条，给专家更多上下文
    const projectScan = this.scanLocalProject();
    const memorySummary = await this.loadMemorySummary();

    const prompt = `作为专业的技术项目CEO，请回复用户的消息。

## 当前系统状态
- 项目状态：${statusInfo}
- 用户最初需求：${this.userIdea || '无'}
- 当前阶段：${this.currentStage}
- ${fileReadSummary || '未读取额外文件'}

## 本地项目概览
${projectScan.opcProject ? `
OPC项目关键文件: ${projectScan.opcProject.keyFiles.join(', ')}
` : ''}
${projectScan.hasGeneratedProjects ? `
已发现 ${projectScan.generatedProjects.length} 个历史项目：
${projectScan.generatedProjects.map((p: any) => `- ${p.projectId}${p.hasPRD ? ' [有PRD]' : ''}${p.hasDeployment ? ' [有部署文档]' : ''}
  文件: ${p.files.join(', ') || '无'}`).join('\n')}
` : '暂无历史项目'}

## 已读取的文件内容
${readFiles.length > 0 ? readFiles.map(f => `
--- ${f.path} ---
${f.content}
`).join('\n') : '无'}

## 团队记忆摘要
- 技能库：${memorySummary.skillCount || 0} 条技巧
- 工作流模板：${memorySummary.workflowCount || 0} 个
${memorySummary.recentSkills && memorySummary.recentSkills.length > 0 ? `
最近技能：
${memorySummary.recentSkills.map((s: any) => `- [${s.category}] ${s.title}`).join('\n')}
` : ''}

## 最近对话历史
${recentHistory.map(m => `${m.role === 'user' ? '用户' : '你'}: ${m.content}`).join('\n')}

## 可用命令
- /start [想法] - 启动新项目
- /stop - 停止当前项目
- /status - 查看状态
- /help - 帮助
- /read [文件路径] - 读取项目文件（可选功能）

## 用户消息
${userMessage}

## 请你思考并回复

请先分析用户意图，然后给出专业的回复。你的回复应该：

1. **如果用户只是闲聊**：友好回应，展现专业素养
2. **如果用户问项目状态**：详细说明当前进度，包括已完成的和下一步计划
3. **如果用户提新需求**：
   - 先用你的专业角度分析这个需求
   - 给出你的建议（技术选型、功能优先级、时间预估）
   - 最后建议用 /start 启动项目
4. **如果用户提修改需求**：
   - 先确认你理解他的意思
   - 说明你会如何协调团队调整
   - 如果项目已完成，说明你会如何安排修改
5. **如果用户问问题**：用你的专业知识耐心解答
6. **如果用户提到了某个项目的问题**（如"todo用不了"）：
   - 先查看已读取的文件内容
   - 分析问题所在
   - 给出具体的修复建议
   - 告诉用户你可以安排团队修复

## 回复要求
- 回复长度控制在100-500字
- 保持专业、友好的语气
- 如果需要用户澄清，请用开放性问题引导
- 不要太机械，要像真人一样
- 如果发现了具体问题，直接指出来并给出解决方案

只返回回复内容，不要其他格式。`;

    try {
      const response = await this.callLLM(prompt, {
        temperature: 0.7,
        maxTokens: 1000 // 增加token数，因为现在有文件内容
      });
      return extractCodeBlock(response) || response.trim();
    } catch (error) {
      console.warn('[CEO] LLM调用失败，用默认回复:', error);
      return this.getFallbackReply(userMessage);
    }
  }

  private getFallbackReply(userMessage: string): string {
    const lower = userMessage.toLowerCase();
    if (lower.includes('你好') || lower.includes('hi') || lower.includes('hello')) {
      return '你好！我是专业的技术项目CEO。很高兴能帮你！我可以帮你分析需求、规划项目、协调团队。有什么想法想聊聊吗？可以用 /start [你的想法] 来启动项目~';
    }
    if (lower.includes('项目') && lower.includes('怎么样')) {
      if (this.currentStage === 'done') {
        return '项目已经完成啦！代码在 generated-projects 文件夹里。你可以看看，有任何修改需求随时告诉我~';
      }
      return `项目还在进行中，当前阶段：${this.currentStage}。请在飞书群里看大家的工作进度，有任何问题随时问我！`;
    }
    if (lower.includes('能做') || lower.includes('可以做') || lower.includes('怎么做')) {
      return '当然可以！说说你的想法，我来帮你分析技术方案、预估时间、规划项目~';
    }
    return '收到！我是你的项目CEO，有任何想法都可以跟我聊。如果想启动新项目，用 /start [你的想法] 就可以啦。其他问题随时问我~';
  }

  updateStage(stage: string) {
    this.currentStage = stage;
  }

  // ========== 命令解析（飞书模式） ==========
  private parseCommand(content: string) {
    const clean = content.trim().toLowerCase();

    if (clean.startsWith('/start ')) {
      const idea = content.trim().substring(6).trim();
      return { type: 'start', idea: idea || '做一个demo应用' };
    }
    if (clean.startsWith('/read ')) {
      const filePath = content.trim().substring(5).trim();
      return { type: 'read', filePath };
    }
    if (clean === '/stop' || clean === '停止') {
      return { type: 'stop' };
    }
    if (clean === '/status' || clean === '状态') {
      return { type: 'status' };
    }
    if (clean === '/help' || clean === '帮助') {
      return { type: 'help' };
    }

    return null;
  }

  private async handleCommand(cmd: any) {
    switch (cmd.type) {
      case 'start':
        console.log(`[CEO] 命令: 启动项目 - ${cmd.idea}`);
        const startMsg = `好的！正在启动项目：${cmd.idea}。请在飞书群里观看团队协作过程！`;
        if (this.onReply) this.onReply(startMsg);
        this.chatHistory.push({ role: 'assistant', content: startMsg, timestamp: Date.now() });

        this.userIdea = cmd.idea;
        this.currentStage = 'planning';
        this.hasStarted = true;

        if (this.onStartProject) {
          this.onStartProject(cmd.idea);
        } else {
          await this.sendMessage('pm', `用户需求：${cmd.idea}`);
          await this.sendMessage(null, `大家好！新项目：${cmd.idea}，PM先来分析需求！`);
        }
        break;

      case 'read':
        console.log(`[CEO] 命令: 读取文件 - ${cmd.filePath}`);
        const result = await this.readFile(cmd.filePath);
        let readMsg = '';
        if (result.success && result.content) {
          const preview = result.content.length > 500
            ? result.content.substring(0, 500) + '\n...[内容过长，仅显示前500字符]'
            : result.content;
          readMsg = `✅ 已读取文件: ${cmd.filePath}\n\n\`\`\`\n${preview}\n\`\`\``;
        } else {
          readMsg = `❌ 读取失败: ${result.error}`;
        }
        if (this.onReply) this.onReply(readMsg);
        this.chatHistory.push({ role: 'assistant', content: readMsg, timestamp: Date.now() });
        break;

      case 'stop':
        console.log('[CEO] 命令: 停止项目');
        const stopMsg = '好的，正在停止项目...';
        if (this.onReply) this.onReply(stopMsg);
        this.chatHistory.push({ role: 'assistant', content: stopMsg, timestamp: Date.now() });

        if (this.onStopProject) {
          this.onStopProject();
        }
        await this.sendMessage(null, '项目已停止！');

        this.currentStage = 'idle';
        this.hasStarted = false;
        break;

      case 'status':
        console.log('[CEO] 命令: 查看状态');
        const projectScan = this.scanLocalProject();
        let statusMsg = '';
        if (!this.hasStarted) {
          statusMsg = '暂无进行中的项目。';
          if (projectScan.hasGeneratedProjects) {
            statusMsg += `\n\n历史项目：${projectScan.generatedProjects.map((p: any) => p.projectId).join(', ')}`;
          }
          if (projectScan.opcProject) {
            statusMsg += `\n\nOPC项目关键文件：${projectScan.opcProject.keyFiles.slice(0, 10).join(', ')}`;
          }
          statusMsg += '\n\n发送 /start [想法] 来启动新项目！\n发送 /read [文件路径] 来查看项目文件';
        } else if (this.currentStage === 'done') {
          statusMsg = '项目已完成！你可以在 generated-projects 文件夹查看生成的代码。有修改需求随时告诉我~';
        } else {
          statusMsg = `项目进行中！\n当前阶段：${this.currentStage}\n最初需求：${this.userIdea}`;
        }
        if (this.onReply) this.onReply(statusMsg);
        this.chatHistory.push({ role: 'assistant', content: statusMsg, timestamp: Date.now() });
        break;

      case 'help':
        const helpMsg = `🤖 OPC CEO 帮助

**命令列表**：
• /start [想法] - 启动新项目
• /read [文件路径] - 读取项目文件
• /stop - 停止当前项目
• /status - 查看当前状态
• /help - 显示帮助

**我能帮你做什么**：
• 分析你的需求，给出专业建议
• 协调团队完成项目开发
• 回答你关于技术选型的问题
• 查看历史项目，根据已有代码做调整
• 读取项目文件来了解当前状态

**安全说明**：
• 我只能读取项目目录内的文件
• 敏感文件（.env等）和大文件无法读取
• 我不会修改任何文件，但可以让团队成员来修改

有问题随时跟我聊！`;
        if (this.onReply) this.onReply(helpMsg);
        console.log('[CEO] 命令: 帮助');
        break;
    }
  }

  async handleError(error: Error): Promise<boolean> {
    console.error('[CEO] 处理错误:', error);
    return true;
  }

  async isDone(): Promise<boolean> {
    return false;
  }
}
