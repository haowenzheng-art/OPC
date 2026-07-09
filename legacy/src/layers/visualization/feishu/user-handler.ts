import { UserMessageFilter } from '../../boundary/userMessageFilter.js';

// 全局引用
let messageFilter: UserMessageFilter | null = null;
let ceoHandler: ((msg: string, senderId?: string) => void) | null = null;

// ========== 初始化用户消息处理器 ==========
export function setupUserHandler(projectId: string) {
  messageFilter = new UserMessageFilter(projectId);
  console.log('[用户消息处理] 已初始化');
}

// ========== 处理来自飞书的用户消息 ==========
export async function handleFeishuUserMessage(msg: {
  content: string;
  chatType: 'group' | 'p2p';
  senderId?: string;
}) {
  if (!messageFilter) {
    console.warn('[用户消息处理] 未初始化，使用默认设置');
    messageFilter = new UserMessageFilter('default_project');
  }

  const filterResult = messageFilter.filter({
    id: 'msg-' + Date.now(),
    content: msg.content,
    chatType: msg.chatType,
    chatId: 'chat-' + Date.now(),
    senderId: msg.senderId,
    timestamp: new Date()
  });

  if (filterResult.shouldIgnore) {
    console.log(`[用户消息处理] 忽略${msg.chatType}消息: ${msg.content}`);
    return { action: 'ignore' as const, reply: filterResult.replyContent };
  }

  if (filterResult.action === 'forward_to_ceo') {
    console.log(`[用户消息处理] 转发给CEO: ${msg.content}`);

    // 解析命令
    const command = parseCommand(msg.content);

    if (ceoHandler) {
      ceoHandler(msg.content, msg.senderId);
    }

    return {
      action: 'forward_to_ceo' as const,
      command,
      reply: filterResult.replyContent
    };
  }

  return { action: 'ignore' as const };
}

// ========== 命令解析 ==========
function parseCommand(content: string) {
  const cleanContent = content.trim().toLowerCase();

  // 启动项目
  if (cleanContent.startsWith('/start') || cleanContent.startsWith('开始')) {
    const idea = cleanContent.replace(/^\/start\s*/, '').replace(/^开始\s*/, '').trim();
    return { type: 'start', idea: idea || '做一个demo应用' };
  }

  // 停止项目
  if (cleanContent === '/stop' || cleanContent === '停止') {
    return { type: 'stop' };
  }

  // 查看状态
  if (cleanContent === '/status' || cleanContent === '状态') {
    return { type: 'status' };
  }

  // 帮助
  if (cleanContent === '/help' || cleanContent === '帮助') {
    return { type: 'help' };
  }

  // 普通对话
  return { type: 'chat', content };
}

// ========== 设置CEO消息处理器 ==========
export function setCeoHandler(handler: (msg: string, senderId?: string) => void) {
  ceoHandler = handler;
}

// ========== 获取帮助文本 ==========
export function getHelpText() {
  return `
🤖 **OPC Agent系统 帮助**

**命令列表：**
• \`/start [想法]\` - 启动一个新项目
• \`/stop\` - 停止当前项目
• \`/status\` - 查看项目状态
• \`/help\` - 显示帮助

**使用说明：**
• 在群聊中说话会被忽略
• 私聊CEO可以控制项目

**示例：**
• \`/start 做一个待办应用\`
• \`/start 一个博客系统\`
`;
}
