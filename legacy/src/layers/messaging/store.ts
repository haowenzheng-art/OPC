import Prisma from '../tools/prisma-client.js';
const { PrismaClient } = Prisma;

const prisma = new PrismaClient();

// 消息存储
export async function saveMessage(projectId: string, fromAgent: string, toAgent: string | null, type: string, content: string) {
  return await prisma.message.create({
    data: {
      projectId,
      fromAgent,
      toAgent,
      type,
      content
    }
  });
}

// 获取项目所有消息
export async function getProjectMessages(projectId: string) {
  return await prisma.message.findMany({
    where: { projectId },
    orderBy: { createdAt: 'asc' }
  });
}

// 获取给某个Agent的消息
export async function getMessagesForAgent(projectId: string, agentRole: string) {
  return await prisma.message.findMany({
    where: {
      projectId,
      toAgent: agentRole
    },
    orderBy: { createdAt: 'asc' }
  });
}
