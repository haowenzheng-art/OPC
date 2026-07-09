import Prisma from '../tools/prisma-client.js';
const { PrismaClient } = Prisma;
import { State, Action, AgentRole, AgentMemoryContent } from '../../types/index.js';
import { sendToAgent, sendToGroup, onMessage } from '../messaging/bus.js';
import { LLMClient, llmClient, Message } from '../tools/index.js';
import { findSkillsByError, saveSkill, SkillCategory } from '../tools/skill-library.js';

const prisma = new PrismaClient();

export abstract class BaseAgent {
  projectId: string;
  role: AgentRole;
  isRunning: boolean = false;
  protected memories: AgentMemoryContent[] = [];
  protected recentActions: string[] = [];
  protected llm: LLMClient;
  protected conversationHistory: Message[] = [];
  protected pendingQuestions: Map<string, { from: AgentRole; question: string; resolve: (answer: string) => void }> = new Map();
  protected receivedMessages: { from: AgentRole; content: string }[] = [];

  constructor(projectId: string, role: AgentRole) {
    this.projectId = projectId;
    this.role = role;
    this.llm = llmClient;
  }

  protected getSystemPrompt(): string {
    const roleDescriptions: Record<AgentRole, string> = {
      pm: '你是一个专业的产品经理。根据用户需求，编写清晰、完整的PRD文档，包括功能概述、核心功能、技术方案、页面结构等。',
      frontend: '你是一个专业的前端工程师。根据PRD，使用Next.js + Tailwind CSS编写高质量的React组件和页面。',
      backend: '你是一个专业的后端工程师。根据PRD，使用Express + TypeScript + SQLite编写后端API和数据模型。',
      test: '你是一个专业的测试工程师。检查生成的代码，编写测试用例，生成测试报告。',
      ops: '你是一个专业的运维工程师。编写部署配置，生成Dockerfile，提供部署说明。',
      ceo: '你是一个项目协调员。管理整个项目流程，协调各个Agent的工作。',
    };
    return roleDescriptions[this.role] || '你是一个AI助手。';
  }

  protected async callLLM(userPrompt: string, options?: { temperature?: number; maxTokens?: number }, relatedSkills?: any[]): Promise<string> {
    if (!this.llm.isConfigured()) {
      throw new Error('LLM not configured');
    }

    const systemPrompt = this.getSystemPrompt();

    const recentMemories = await this.loadMemory();
    const memoryPrompt = recentMemories.length > 0
      ? `\n\n最近的工作记录:\n${recentMemories.map((m: any) => {
          try {
            const parsed = JSON.parse(m.content) as AgentMemoryContent;
            return `- ${parsed.observation}`;
          } catch {
            return `- ${m.content}`;
          }
        }).join('\n')}`
      : '';

    // 主动加载相关经验
    let skillsToUse = relatedSkills || [];
    if (!relatedSkills) {
      try {
        skillsToUse = await this.findRelevantSkillsForTask(userPrompt);
      } catch {
        skillsToUse = [];
      }
    }

    const skillPrompt = skillsToUse.length > 0
      ? `\n\n相关经验技巧（请参考这些经验来更好地完成任务）:\n${skillsToUse.map((s, i) => `${i + 1}. ${s.title}\n   问题: ${s.content.problem}\n   方案: ${s.content.solution}${s.content.codeExample ? `\n   代码: ${s.content.codeExample}` : ''}`).join('\n\n')}`
      : '';

    const messagesPrompt = this.receivedMessages.length > 0
      ? `\n\n收到的其他Agent消息:\n${this.receivedMessages.map(m => `[${m.from}] ${m.content}`).join('\n')}`
      : '';

    const fullUserPrompt = userPrompt + memoryPrompt + skillPrompt + messagesPrompt;

    const response = await this.llm.chatWithSystem(systemPrompt, fullUserPrompt, options);

    this.conversationHistory.push(
      { role: 'user', content: fullUserPrompt },
      { role: 'assistant', content: response }
    );

    return response;
  }

  // 根据当前任务主动查找相关经验
  protected async findRelevantSkillsForTask(taskPrompt: string): Promise<any[]> {
    try {
      const keywords = this.extractKeywords(taskPrompt);
      const allSkills = await prisma.skillMemory.findMany({
        where: {
          OR: [
            { tags: { contains: this.role } },
            ...keywords.slice(0, 5).map(k => ({ tags: { contains: k } })),
            ...keywords.slice(0, 5).map(k => ({ title: { contains: k } }))
          ]
        },
        orderBy: { successCount: 'desc' },
        take: 3
      });

      return allSkills.map((s: any) => ({
        category: s.category,
        title: s.title,
        content: JSON.parse(s.content),
        tags: s.tags ? s.tags.split(',') : []
      }));
    } catch {
      return [];
    }
  }

  // 简单的关键词提取
  private extractKeywords(text: string): string[] {
    const stopWords = ['的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这'];
    const words = text.toLowerCase().split(/[\s,.，。！!?？]+/);
    return words.filter(w => w.length > 1 && !stopWords.includes(w)).slice(0, 10);
  }

  // 成功完成任务后主动保存经验
  protected async saveSuccessExperience(
    title: string,
    problemContext: string,
    solution: string,
    codeExample?: string,
    tags: string[] = []
  ): Promise<void> {
    try {
      const category = this.inferCategoryFromContent(title + ' ' + problemContext + ' ' + solution);
      await saveSkill(
        category,
        title,
        problemContext,
        solution,
        codeExample,
        [this.role, 'success', ...tags]
      );
      console.log(`[${this.role}] 成功经验已保存: ${title}`);
    } catch {
      // 保存失败不影响主流程
    }
  }

  private inferCategoryFromContent(content: string): SkillCategory {
    const lower = content.toLowerCase();
    if (lower.includes('file') || lower.includes('write') || lower.includes('read') || lower.includes('path')) {
      return 'file_operations';
    } else if (lower.includes('tool') || lower.includes('mcp') || lower.includes('api')) {
      return 'tool_use';
    } else if (lower.includes('component') || lower.includes('react') || lower.includes('ui')) {
      return 'component_design';
    } else if (lower.includes('route') || lower.includes('endpoint') || lower.includes('rest')) {
      return 'api_design';
    } else if (lower.includes('test') || lower.includes('jest') || lower.includes('vitest')) {
      return 'testing';
    } else if (lower.includes('deploy') || lower.includes('build') || lower.includes('docker')) {
      return 'deployment';
    }
    return 'general';
  }

  protected useHardcodedMode(): boolean {
    // 如果USE_LLM=false，或者在测试环境下，使用硬编码模式
    if (process.env.USE_LLM === 'false' || process.env.NODE_ENV === 'test') {
      return true;
    }
    try {
      return !this.llm.isConfigured();
    } catch {
      return true;
    }
  }

  async run() {
    this.isRunning = true;
    this.setupMessageListener();
    console.log(`[${this.role}] 开始工作...`);

    while (this.isRunning && !(await this.isDone())) {
      try {
        const state = await this.perceive();
        const action = await this.reason(state);

        if (action.type === 'WAIT') {
          await new Promise(r => setTimeout(r, 1000));
          continue;
        }

        await this.act(action);

        await new Promise(r => setTimeout(r, 500));
      } catch (error) {
        console.error(`[${this.role}] 遇到错误:`, error);
        const handled = await this.handleError(error as Error);
        if (!handled) {
          await this.reportToCEO({ type: 'stuck', error: String(error) });
          break;
        }
      }
    }

    if (this.isRunning) {
      await this.reportToCEO({ type: 'done' });
      console.log(`[${this.role}] 工作完成！`);
    }
  }

  stop() {
    this.isRunning = false;
  }

  abstract perceive(): Promise<State>;
  abstract reason(state: State): Promise<Action>;
  abstract act(action: Action): Promise<void>;
  abstract handleError(error: Error): Promise<boolean>;
  abstract isDone(): Promise<boolean>;

  async sendMessage(to: AgentRole | null, content: string) {
    if (to) {
      await sendToAgent(this.projectId, this.role, to, content);
    } else {
      await sendToGroup(this.projectId, this.role, content);
    }
  }

  async reportToCEO(message: any) {
    if (this.role !== 'ceo') {
      await sendToAgent(this.projectId, this.role, 'ceo', JSON.stringify(message));
    }
  }

  async loadMemory() {
    const memories = await prisma.agentMemory.findMany({
      where: { projectId: this.projectId, agentRole: this.role },
      orderBy: { importance: 'desc' },
      take: 10
    });
    return memories;
  }

  async saveMemory(content: AgentMemoryContent, importance: number = 5) {
    try {
      const memoryData = {
        projectId: this.projectId,
        agentRole: this.role,
        content: JSON.stringify(content),
        importance: Math.max(0, Math.min(10, importance))
      };
      await prisma.agentMemory.create({ data: memoryData });
      this.memories.push(content);
    } catch (e) {
      console.error(`[${this.role}] 保存记忆失败:`, e);
    }
  }

  async checkSkillLibrary(error: Error) {
    const errorMsg = error.message.toLowerCase();
    let category: string = 'general';

    if (errorMsg.includes('file') || errorMsg.includes('write') || errorMsg.includes('read')) {
      category = 'file_operations';
    } else if (errorMsg.includes('tool') || errorMsg.includes('mcp')) {
      category = 'tool_use';
    } else if (errorMsg.includes('debug') || errorMsg.includes('error')) {
      category = 'debugging';
    } else if (errorMsg.includes('api') || errorMsg.includes('route')) {
      category = 'api_design';
    } else if (errorMsg.includes('test')) {
      category = 'testing';
    } else if (errorMsg.includes('deploy')) {
      category = 'deployment';
    }

    const skills = await prisma.skillMemory.findMany({
      where: {
        OR: [
          { category },
          { tags: { contains: category } }
        ]
      },
      orderBy: { successCount: 'desc' },
      take: 5
    });
    return skills;
  }

  public getRecentActions(): string[] {
    return this.recentActions;
  }

  protected recordAction(action: string) {
    this.recentActions.push(action);
  }

  // A2A: 向其他Agent提问并等待回答
  protected async askAgent(to: AgentRole, question: string): Promise<string> {
    await this.sendMessage(to, `[QUESTION] ${question}`);
    await this.saveMemory({
      action: '向其他Agent提问',
      observation: `向${to}询问: ${question}`,
      insight: 'Agent之间需要协作时应该主动询问',
      timestamp: Date.now()
    }, 4);

    return new Promise((resolve) => {
      const questionId = `${to}_${Date.now()}`;
      this.pendingQuestions.set(questionId, { from: to, question, resolve });

      // 30秒超时，返回默认答案
      setTimeout(() => {
        if (this.pendingQuestions.has(questionId)) {
          this.pendingQuestions.delete(questionId);
          resolve('对方没有回应，我将继续按默认方式处理。');
        }
      }, 30000);
    });
  }

  // A2A: 回答其他Agent的问题
  protected async answerAgent(to: AgentRole, originalQuestion: string, answer: string) {
    await this.sendMessage(to, `[ANSWER] ${originalQuestion}\n→ ${answer}`);
    await this.saveMemory({
      action: '回答其他Agent的问题',
      observation: `回答了${to}的问题: ${originalQuestion}`,
      insight: '分享知识可以帮助团队更好地协作',
      timestamp: Date.now()
    }, 3);
  }

  // 处理收到的消息
  protected setupMessageListener() {
    onMessage((msg) => {
      if (msg.projectId !== this.projectId) return;
      if (msg.toAgent !== this.role && msg.toAgent !== null) return;
      if (msg.fromAgent === this.role) return;

      const content = msg.content;

      // 处理[QUESTION]格式的消息
      if (content.startsWith('[QUESTION]')) {
        const question = content.replace('[QUESTION]', '').trim();
        this.handleAgentQuestion(msg.fromAgent as AgentRole, question);
      }
      // 处理[ANSWER]格式的消息
      else if (content.startsWith('[ANSWER]')) {
        const lines = content.split('\n');
        const question = lines[0].replace('[ANSWER]', '').trim();
        const answer = lines.slice(1).join('\n').replace('→', '').trim();
        this.handleAgentAnswer(msg.fromAgent as AgentRole, question, answer);
      }
      // 普通消息，先让子类处理，然后存入receivedMessages供LLM参考
      else {
        this.handleMessage(msg.fromAgent as AgentRole, content);
        this.receivedMessages.push({ from: msg.fromAgent as AgentRole, content });
        if (this.receivedMessages.length > 20) {
          this.receivedMessages.shift();
        }
      }
    });
  }

  // 子类可以覆盖这个方法来处理普通消息
  protected handleMessage(from: AgentRole, content: string) {
    // 默认什么都不做，子类可以覆盖
  }

  // 子类可以覆盖这个方法来处理问题
  protected async handleAgentQuestion(from: AgentRole, question: string) {
    await this.sendMessage(null, `收到${from}的问题: ${question}，我思考后会回复...`);

    try {
      const prompt = `
收到了来自${from}的问题: "${question}"

请根据你的角色，提供专业、简洁的回答。如果问题超出你的范围，请如实说明。

只返回答案，不要其他文字。
`;
      const answer = await this.callLLM(prompt, { temperature: 0.5, maxTokens: 512 });
      await this.answerAgent(from, question, answer);
    } catch {
      await this.answerAgent(from, question, '抱歉，我现在无法回答这个问题，请按默认方式处理。');
    }
  }

  protected handleAgentAnswer(from: AgentRole, question: string, answer: string) {
    // 找到对应的pending question并resolve
    for (const [id, pending] of this.pendingQuestions.entries()) {
      if (pending.from === from && pending.question === question) {
        pending.resolve(answer);
        this.pendingQuestions.delete(id);
        break;
      }
    }
  }

  // 增强版handleError: 使用Skill Library
  async handleErrorWithSkills(error: Error): Promise<boolean> {
    console.error(`[${this.role}] 遇到错误:`, error);

    // 1. 从Skill Library查找相关经验
    const relatedSkills = await findSkillsByError(error);
    if (relatedSkills.length > 0) {
      await this.sendMessage(null, `💡 找到${relatedSkills.length}条相关经验，正在参考...`);
      console.log(`[${this.role}] 应用相关经验:`, relatedSkills.map(s => s.title));
    }

    // 2. 尝试使用LLM + skills解决问题
    let solved = false;
    let solution = '继续执行，跳过有问题的步骤';
    let codeExample: string | undefined = undefined;

    try {
      if (!this.useHardcodedMode() && relatedSkills.length > 0) {
        const prompt = `
遇到了一个错误:

错误信息: ${error.message}
错误堆栈: ${error.stack || '无'}

相关经验技巧:
${relatedSkills.map((s, i) => `${i + 1}. ${s.title}\n问题: ${s.content.problem}\n解决方案: ${s.content.solution}${s.content.codeExample ? `\n代码示例: ${s.content.codeExample}` : ''}`).join('\n\n')}

请分析：
1. 这个错误的根本原因是什么？
2. 上面的经验技巧是否能帮助解决这个问题？
3. 请给出具体的解决方案（可以是代码调整、步骤调整等）

请按以下格式返回JSON：
{
  "analysis": "错误原因分析",
  "solution": "具体解决方案描述",
  "canApply": true/false,
  "codeExample": "如果有代码修复示例，请提供"
}

只返回JSON，不要其他文字。
`;
        try {
          const response = await this.llm.chatWithSystem('你是一个调试专家，帮助AI Agent分析和解决问题。', prompt, { temperature: 0.3, maxTokens: 1024 });
          const jsonStr = response.match(/\{[\s\S]*\}/)?.[0] || response;
          const result = JSON.parse(jsonStr);

          if (result.canApply) {
            solution = result.solution;
            codeExample = result.codeExample;
            await this.sendMessage(null, `🤖 根据经验分析: ${result.analysis}`);
            await this.sendMessage(null, `✅ 解决方案: ${solution}`);
          }
        } catch {
          // LLM分析失败，继续默认处理
        }
      }

      // 默认错误处理：记录并继续
      await this.sendMessage(null, `遇到问题: ${error.message}，正在尝试继续...`);
      solved = true;
    } catch {
      solved = false;
    }

    // 3. 如果问题解决了，保存为新Skill（有价值的内容）
    if (solved) {
      const category = this.getErrorCategory(error);
      try {
        // 只有当solution不是默认值时才保存
        if (solution !== '继续执行，跳过有问题的步骤' || relatedSkills.length === 0) {
          await saveSkill(
            category,
            `${this.role}解决了${error.message.substring(0, 50)}...`,
            error.message,
            solution,
            codeExample,
            [this.role, 'auto-saved', ...relatedSkills.map(s => s.title.substring(0, 20))]
          );
          console.log(`[${this.role}] 经验已保存到Skill Library: ${solution.substring(0, 50)}...`);
        }
      } catch {
        // 保存skill失败不影响主流程
      }
    }

    // 保存错误记忆
    await this.saveMemory({
      action: '遇到错误并尝试解决',
      observation: `错误: ${error.message}`,
      insight: solution,
      timestamp: Date.now()
    }, 7);

    return solved;
  }

  private getErrorCategory(error: Error): SkillCategory {
    const msg = error.message.toLowerCase();
    if (msg.includes('file') || msg.includes('write') || msg.includes('read') || msg.includes('ENOENT')) {
      return 'file_operations';
    } else if (msg.includes('tool') || msg.includes('mcp') || msg.includes('api')) {
      return 'tool_use';
    } else if (msg.includes('test')) {
      return 'testing';
    } else if (msg.includes('deploy') || msg.includes('build')) {
      return 'deployment';
    }
    return 'general';
  }
}
