import { onMessage } from '../../messaging/bus.js';
import { UserMessageFilter, UserMessage } from '../../boundary/userMessageFilter.js';
import { startFeishuServer, FeishuMultiBotConfig } from './server.js';
import { setupUserHandler, handleFeishuUserMessage, setCeoHandler, getHelpText } from './user-handler.js';

// Agent角色映射
const AGENT_NAMES: Record<string, string> = {
  'pm': '产品经理',
  'frontend': '前端工程师',
  'backend': '后端工程师',
  'test': '测试工程师',
  'ops': '运维工程师',
  'ceo': 'CEO'
};

// 全局引用（运行时设置）
let messageFilter: UserMessageFilter | null = null;
let ceoAgentRef: any = null;
let mode: 'cli' | 'feishu' = 'cli';

// ========== CLI可视化模式 ==========
function startCliBot() {
  mode = 'cli';
  console.log('=== OPC Agent群聊 (CLI模式) ===');
  console.log('(用户群聊消息会被忽略，只有Agent消息显示)');
  console.log('');
  console.log('[边界逻辑] 已启用：');
  console.log('  • 用户在群聊说话 → 忽略');
  console.log('  • 用户私聊CEO → 由CEO处理');
  console.log('');

  // 监听Agent消息并在CLI显示
  onMessage((msg) => {
    if (msg.fromAgent !== 'user') {
      const agentName = AGENT_NAMES[msg.fromAgent] || msg.fromAgent;
      console.log(`\n[${agentName}]: ${msg.content}`);
    }
  });
}

// ========== 飞书Bot模式 ==========
function startFeishuBot(config: FeishuMultiBotConfig & { projectId?: string }) {
  mode = 'feishu';
  console.log('=== OPC Agent群聊 (飞书模式) ===');

  // 初始化用户消息处理器
  setupUserHandler(config.projectId || 'feishu_project');

  // 启动飞书服务
  startFeishuServer(config);

  // 设置CEO处理器
  setCeoHandler((msg, senderId) => {
    if (ceoAgentRef) {
      ceoAgentRef.receiveUserMessage(msg, senderId);
    }
  });

  return mode;
}

// ========== 边界逻辑：处理用户消息 ==========
export function handleUserMessage(msg: {
  content: string;
  chatType: 'group' | 'p2p';
  senderId?: string;
}) {
  if (mode === 'feishu') {
    // 飞书模式下由server.ts调用handler
    handleFeishuUserMessage(msg);
    return;
  }

  // CLI模式
  if (!messageFilter) {
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
    console.log(`[边界逻辑] 用户${msg.chatType === 'group' ? '群聊' : '私聊'}消息已忽略: ${msg.content}`);
    if (filterResult.action === 'reply_polite' && filterResult.replyContent) {
      console.log(`[自动回复]: ${filterResult.replyContent}`);
    }
    return;
  }

  if (filterResult.action === 'forward_to_ceo') {
    console.log(`[边界逻辑] 转发给CEO: ${msg.content}`);
    if (ceoAgentRef) {
      ceoAgentRef.receiveUserMessage(msg.content, msg.senderId);
    }
    if (filterResult.replyContent) {
      console.log(`[CEO回复]: ${filterResult.replyContent}`);
    }
  }
}

export function formatAgentName(role: string) {
  return AGENT_NAMES[role] || role;
}

export function setMessageFilter(filter: UserMessageFilter) {
  messageFilter = filter;
}

export function setCeoAgent(ceo: any) {
  ceoAgentRef = ceo;
}

export function startBot(options?: {
  mode?: 'cli' | 'feishu';
  feishuConfig?: FeishuMultiBotConfig;
}) {
  if (options?.mode === 'feishu' && options.feishuConfig) {
    startFeishuBot(options.feishuConfig);
  } else {
    startCliBot();
  }
}

export { getHelpText };

