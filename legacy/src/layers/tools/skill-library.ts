import Prisma from './prisma-client.js';
import { SkillMemoryData, SkillCategory, SkillContent } from '../../types/index.js';

export type { SkillCategory };

const { PrismaClient } = Prisma;
const prisma = new PrismaClient();

export async function saveSkill(
  category: SkillCategory,
  title: string,
  problem: string,
  solution: string,
  codeExample?: string,
  tags?: string[]
): Promise<string> {
  const content: SkillContent = { problem, solution, codeExample };

  const skill = await prisma.skillMemory.create({
    data: {
      category,
      title,
      content: JSON.stringify(content),
      tags: tags ? tags.join(',') : '',
      successCount: 1
    }
  });

  return skill.id;
}

export async function findSkillsByError(error: Error, limit: number = 5): Promise<SkillMemoryData[]> {
  const errorMsg = error.message.toLowerCase();
  let category: SkillCategory = 'general';

  if (errorMsg.includes('file') || errorMsg.includes('write') || errorMsg.includes('read') || errorMsg.includes('ENOENT') || errorMsg.includes('path') || errorMsg.includes('exists')) {
    category = 'file_operations';
  } else if (errorMsg.includes('tool') || errorMsg.includes('mcp') || errorMsg.includes('api') || errorMsg.includes('request') || errorMsg.includes('fetch')) {
    category = 'tool_use';
  } else if (errorMsg.includes('debug') || errorMsg.includes('error') || errorMsg.includes('fail') || errorMsg.includes('exception') || errorMsg.includes('crash')) {
    category = 'debugging';
  } else if (errorMsg.includes('route') || errorMsg.includes('endpoint') || errorMsg.includes('rest') || errorMsg.includes('api') || errorMsg.includes('404') || errorMsg.includes('500')) {
    category = 'api_design';
  } else if (errorMsg.includes('component') || errorMsg.includes('react') || errorMsg.includes('ui') || errorMsg.includes('render') || errorMsg.includes('jsx')) {
    category = 'component_design';
  } else if (errorMsg.includes('test') || errorMsg.includes('jest') || errorMsg.includes('vitest') || errorMsg.includes('assert') || errorMsg.includes('expect')) {
    category = 'testing';
  } else if (errorMsg.includes('deploy') || errorMsg.includes('build') || errorMsg.includes('docker') || errorMsg.includes('npm') || errorMsg.includes('install')) {
    category = 'deployment';
  }

  // 提取关键词进行更精确匹配
  const keywords = extractKeywords(errorMsg);

  const skills = await prisma.skillMemory.findMany({
    where: {
      OR: [
        { category },
        { tags: { contains: category } },
        ...keywords.slice(0, 5).map(k => ({ tags: { contains: k } })),
        ...keywords.slice(0, 3).map(k => ({ title: { contains: k } })),
        ...keywords.slice(0, 3).map(k => ({ content: { contains: k } }))
      ]
    },
    orderBy: { successCount: 'desc' },
    take: limit
  });

  // 去重并排序（优先考虑匹配度高的）
  const scoredResults = skills.map((s: any) => ({
    skill: {
      category: s.category as SkillCategory,
      title: s.title,
      content: JSON.parse(s.content),
      tags: s.tags ? s.tags.split(',') : []
    },
    _score: calculateMatchScore(s, errorMsg, keywords)
  }));

  scoredResults.sort((a: { _score: number }, b: { _score: number }) => b._score - a._score);

  return scoredResults.map((r: { skill: SkillMemoryData }) => r.skill);
}

// 简单关键词提取
function extractKeywords(text: string): string[] {
  const stopWords = ['的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', '那', 'error', 'failed', 'cannot', 'could', 'not', 'can\'t', 'when', 'while', 'for', 'with'];
  const words = text.toLowerCase().split(/[\s,.，。！!?？':"(){}[\]\-\\/]+/);
  return words.filter(w => w.length > 1 && !stopWords.includes(w)).slice(0, 15);
}

// 计算匹配分数
function calculateMatchScore(skill: any, query: string, keywords: string[]): number {
  let score = 0;
  const content = JSON.stringify(skill.content).toLowerCase();
  const title = skill.title.toLowerCase();
  const tags = (skill.tags || '').toLowerCase();

  // successCount权重
  score += (skill.successCount || 0) * 2;

  // 关键词匹配
  for (const keyword of keywords) {
    if (title.includes(keyword)) score += 5;
    if (content.includes(keyword)) score += 3;
    if (tags.includes(keyword)) score += 4;
  }

  // 优先考虑auto-saved的（因为是实际解决过的）
  if (tags.includes('auto-saved')) score += 10;

  return score;
}

export async function incrementSkillUsage(skillId: string): Promise<void> {
  await prisma.skillMemory.update({
    where: { id: skillId },
    data: {
      successCount: { increment: 1 }
    }
  });
}

export async function getAllSkills(): Promise<SkillMemoryData[]> {
  const skills = await prisma.skillMemory.findMany({
    orderBy: { successCount: 'desc' }
  });

  return skills.map((s: any) => ({
    category: s.category as SkillCategory,
    title: s.title,
    content: JSON.parse(s.content),
    tags: s.tags ? s.tags.split(',') : []
  }));
}

export async function seedDefaultSkills(): Promise<void> {
  const existing = await prisma.skillMemory.count();
  if (existing > 0) return;

  const defaultSkills = [
    {
      category: 'file_operations' as SkillCategory,
      title: '文件路径分隔符处理',
      problem: 'Windows使用\\而Unix使用/，导致路径不匹配',
      solution: '使用path.join()或统一替换为/进行比较',
      codeExample: 'const normalizePath = (p: string) => p.replace(/\\\\/g, \'/\');',
      tags: ['file', 'path', 'windows', 'unix']
    },
    {
      category: 'debugging' as SkillCategory,
      title: 'JSON解析错误处理',
      problem: 'JSON.parse失败导致程序崩溃',
      solution: '使用try-catch包裹，提供默认值',
      codeExample: 'try { data = JSON.parse(str); } catch { data = defaultData; }',
      tags: ['json', 'parse', 'error', 'try-catch']
    },
    {
      category: 'api_design' as SkillCategory,
      title: '统一API响应格式',
      problem: 'API响应格式不统一，前端难以处理',
      solution: '使用{success: boolean, data?: T, error?: string}格式',
      codeExample: 'res.json({ success: true, data: result });',
      tags: ['api', 'response', 'rest', 'format']
    },
    {
      category: 'testing' as SkillCategory,
      title: '先检查文件结构再测试',
      problem: '直接测试代码但文件缺失导致失败',
      solution: '先验证package.json等核心文件是否存在',
      codeExample: 'if (!fs.existsSync(\'package.json\')) { /* 报错 */ }',
      tags: ['test', 'file', 'validation']
    }
  ];

  for (const skill of defaultSkills) {
    await saveSkill(
      skill.category,
      skill.title,
      skill.problem,
      skill.solution,
      skill.codeExample,
      skill.tags
    );
  }
}
