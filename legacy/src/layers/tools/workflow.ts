import Prisma from './prisma-client.js';
import { WorkflowTemplateData, AgentRole, WorkflowStep } from '../../types/index.js';

const { PrismaClient } = Prisma;
const prisma = new PrismaClient();

export async function saveWorkflowTemplate(
  userIdea: string,
  pmActions: string[],
  frontendActions: string[],
  backendActions: string[],
  testActions: string[],
  opsActions: string[],
  fileMap: {
    pmFiles: string[],
    frontendFiles: string[],
    backendFiles: string[],
    testFiles: string[],
    opsFiles: string[],
    allFiles: string[]
  }
): Promise<string> {
  const category = inferCategory(userIdea);
  const complexity = inferComplexity(userIdea);
  const name = generateTemplateName(userIdea, category);

  const template = await prisma.workflowTemplate.create({
    data: {
      name,
      description: `用于${userIdea}类项目的工作流模板`,
      category,
      complexity,
      pmSteps: JSON.stringify(actionsToSteps(pmActions, fileMap.pmFiles)),
      frontendSteps: JSON.stringify(actionsToSteps(frontendActions, fileMap.frontendFiles)),
      backendSteps: JSON.stringify(actionsToSteps(backendActions, fileMap.backendFiles)),
      testSteps: JSON.stringify(actionsToSteps(testActions, fileMap.testFiles)),
      opsSteps: JSON.stringify(actionsToSteps(opsActions, fileMap.opsFiles)),
      usageCount: 1,
      lastUsed: new Date()
    }
  });

  return template.id;
}

export async function findMatchingTemplate(userIdea: string): Promise<WorkflowTemplateData | null> {
  const category = inferCategory(userIdea);

  const templates = await prisma.workflowTemplate.findMany({
    where: { category },
    orderBy: [
      { usageCount: 'desc' },
      { lastUsed: 'desc' }
    ],
    take: 3
  });

  if (templates.length === 0) {
    return null;
  }

  const bestMatch = templates[0];

  await prisma.workflowTemplate.update({
    where: { id: bestMatch.id },
    data: {
      usageCount: { increment: 1 },
      lastUsed: new Date()
    }
  });

  return {
    name: bestMatch.name,
    description: bestMatch.description,
    category: bestMatch.category,
    complexity: bestMatch.complexity as any,
    pmSteps: JSON.parse(bestMatch.pmSteps || '[]'),
    frontendSteps: JSON.parse(bestMatch.frontendSteps || '[]'),
    backendSteps: JSON.parse(bestMatch.backendSteps || '[]'),
    testSteps: JSON.parse(bestMatch.testSteps || '[]'),
    opsSteps: JSON.parse(bestMatch.opsSteps || '[]')
  };
}

export async function getAllTemplates(): Promise<WorkflowTemplateData[]> {
  const templates = await prisma.workflowTemplate.findMany({
    orderBy: { usageCount: 'desc' }
  });

  return templates.map((t: any) => ({
    name: t.name,
    description: t.description,
    category: t.category,
    complexity: t.complexity as any,
    pmSteps: JSON.parse(t.pmSteps || '[]'),
    frontendSteps: JSON.parse(t.frontendSteps || '[]'),
    backendSteps: JSON.parse(t.backendSteps || '[]'),
    testSteps: JSON.parse(t.testSteps || '[]'),
    opsSteps: JSON.parse(t.opsSteps || '[]')
  }));
}

function inferCategory(userIdea: string): string {
  const idea = userIdea.toLowerCase();
  if (idea.includes('todo') || idea.includes('待办') || idea.includes('任务')) {
    return 'todo';
  } else if (idea.includes('blog') || idea.includes('博客') || idea.includes('文章')) {
    return 'blog';
  } else if (idea.includes('shop') || idea.includes('电商') || idea.includes('购物')) {
    return 'ecommerce';
  } else if (idea.includes('chat') || idea.includes('聊天') || idea.includes('消息')) {
    return 'chat';
  } else if (idea.includes('dashboard') || idea.includes('看板') || idea.includes('管理')) {
    return 'dashboard';
  } else if (idea.includes('landing') || idea.includes('着陆页') || idea.includes('官网')) {
    return 'landing-page';
  }
  return 'general';
}

function inferComplexity(userIdea: string): 'simple' | 'medium' | 'complex' {
  const wordCount = userIdea.split(/\s+/).length;
  if (wordCount <= 3) return 'simple';
  if (wordCount <= 10) return 'medium';
  return 'complex';
}

function generateTemplateName(userIdea: string, category: string): string {
  const prefix = {
    'todo': '待办清单',
    'blog': '博客系统',
    'ecommerce': '电商平台',
    'chat': '聊天应用',
    'dashboard': '管理看板',
    'landing-page': '着陆页',
    'general': '通用项目'
  }[category] || '通用项目';

  return `${prefix}模板`;
}

function actionsToSteps(actions: string[], files: string[] = []): WorkflowStep[] {
  if (actions.length === 0) return [];

  // 如果没有步骤但有文件，创建一个虚拟步骤
  if (actions.length === 0 && files.length > 0) {
    return [{
      action: '生成项目文件',
      description: '生成项目所需的文件',
      filesGenerated: files
    }];
  }

  // 把文件分配给各个步骤（简单策略：所有文件放到第一个步骤）
  return actions.map((action, i) => ({
    action,
    description: `步骤 ${i + 1}: ${action}`,
    filesGenerated: i === 0 ? files : []
  }));
}
