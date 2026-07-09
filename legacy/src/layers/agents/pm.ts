import { BaseAgent } from './base.js';
import { State, Action } from '../../types/index.js';
import Prisma from '../tools/prisma-client.js';

const { PrismaClient } = Prisma;
const prisma = new PrismaClient();

export class PMAgent extends BaseAgent {
  prdWritten: boolean = false;
  userIdea: string = '';
  prdContent: string = '';
  started: boolean = false;

  constructor(projectId: string, userIdea: string) {
    super(projectId, 'pm');
    this.userIdea = userIdea;
  }

  async perceive(): Promise<State> {
    return {
      projectId: this.projectId,
      status: 'idle' as any,
      userIdea: this.userIdea
    };
  }

  async reason(state: State): Promise<Action> {
    if (!this.started) {
      return { type: 'WRITE_PRD', payload: state.userIdea };
    }
    return { type: 'WAIT', payload: null };
  }

  async act(action: Action) {
    if (action.type === 'WRITE_PRD' && !this.started) {
      this.started = true;
      await this.sendMessage(null, '收到需求！正在写PRD...');

      if (this.useHardcodedMode()) {
        await new Promise(r => setTimeout(r, 1000));
        this.prdContent = this.generateHardcodedPRD(action.payload);
      } else {
        try {
          await this.sendMessage(null, '正在用AI分析需求...');
          this.prdContent = await this.generatePRDWithLLM(action.payload);
        } catch (error) {
          console.warn('[PM] LLM failed, falling back to hardcoded:', error);
          await this.sendMessage(null, 'AI暂时不可用，使用模板模式...');
          this.prdContent = this.generateHardcodedPRD(action.payload);
        }
      }

      await this.sendMessage(null, `PRD写好了！\n\n${this.prdContent}`);
      this.prdWritten = true;

      this.recordAction('WRITE_PRD');
      await this.saveMemory({
        action: '完成PRD撰写',
        observation: '用户需求: ' + action.payload,
        insight: '采用标准的Next.js+Express技术栈',
        timestamp: Date.now()
      }, 8);

      await this.reportToCEO({ type: 'prd_done', prd: this.prdContent });
    }
  }

  private async generatePRDWithLLM(userIdea: string): Promise<string> {
    const prompt = `
用户需求: "${userIdea}"

请根据这个需求，编写一份完整的产品需求文档(PRD)，格式如下：

# [项目名称]

## 功能概述
简要描述这个项目是做什么的

## 核心功能
列出3-5个核心功能点

## 技术方案
- 前端: Next.js + Tailwind CSS
- 后端: Express + TypeScript + SQLite

## 页面结构
- 首页: ...
- [其他页面]: ...

## 数据模型
描述需要存储的数据结构

请用Markdown格式输出，语言使用中文。
`;
    return await this.callLLM(prompt, { temperature: 0.7, maxTokens: 2048 });
  }

  private generateHardcodedPRD(userIdea: string): string {
    return `# ${userIdea}

## 功能概述
这是一个简单的${userIdea}应用。

## 核心功能
1. 基础UI界面
2. 简单的数据存储

## 技术方案
- 前端: Next.js + Tailwind
- 后端: Express + SQLite

## 页面结构
- 首页: 展示主要内容
`;
  }

  getPrdContent(): string {
    return this.prdContent;
  }

  async handleError(error: Error): Promise<boolean> {
    console.error('[PM] 处理错误:', error);
    return true;
  }

  async isDone(): Promise<boolean> {
    return this.prdWritten;
  }
}
