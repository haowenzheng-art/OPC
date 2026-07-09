import express from 'express';
import bodyParser from 'body-parser';
import * as lark from '@larksuiteoapi/node-sdk';
import { handleUserMessage, formatAgentName } from './bot.js';
import { setupGroupBridge } from './group-bridge.js';

// 全局CEO引用
let ceoAgentRef: any = null;
let lastSenderId: string | null = null;

export function setCeoAgent(ceo: any) {
  ceoAgentRef = ceo;
}

// 暴露飞书客户端用于直接回复
export function getLarkClient(role: string) {
  return larkClients[role as keyof typeof larkClients];
}

// 暴露获取最后发送者ID
export function getLastSenderId() {
  return lastSenderId;
}

const app = express();

// Agent角色定义
type AgentRole = 'pm' | 'frontend' | 'backend' | 'test' | 'ops' | 'ceo';

// 单个Bot配置
interface BotConfig {
  appId: string;
  appSecret: string;
  verificationToken?: string;
  encryptKey?: string;
}

// 完整配置
export interface FeishuMultiBotConfig {
  bots: Record<AgentRole, BotConfig>;
  groupChatId: string;
  port: number;
}

// 6个飞书客户端实例（普通API调用）
const larkClients: Partial<Record<AgentRole, lark.Client>> = {};
let config: FeishuMultiBotConfig | null = null;

// ========== 飞书服务初始化 ==========
export function startFeishuServer(feishuConfig: FeishuMultiBotConfig) {
  config = feishuConfig;

  // 初始化6个飞书SDK客户端（用于发送消息）
  for (const role of ['pm', 'frontend', 'backend', 'test', 'ops', 'ceo'] as AgentRole[]) {
    const botConfig = config.bots[role];
    if (botConfig && botConfig.appId && botConfig.appSecret) {
      larkClients[role] = new lark.Client({
        appId: botConfig.appId,
        appSecret: botConfig.appSecret,
      });
      console.log(`[飞书Bot] ${formatAgentName(role)} 已初始化`);
    }
  }

  // 验证CEO Bot必需配置（因为要接收用户私聊）
  if (!larkClients.ceo || !config.bots.ceo) {
    throw new Error('CEO Bot 必须配置！');
  }

  // 启动长连接监听事件（只对CEO Bot）
  startWebSocketEvent(config.bots.ceo);

  // 中间件
  app.use(bodyParser.json({
    verify: (req, res, buf) => {
      (req as any).rawBody = buf;
    },
  }));

  // 健康检查
  app.get('/health', (req, res) => {
    res.json({ status: 'ok', service: 'opc-feishu-multi-bot' });
  });

  // 启动HTTP服务（主要做健康检查）
  app.listen(config!.port, () => {
    console.log(`[飞书Bot] 服务已启动，监听端口 ${config!.port}`);
    console.log(`[飞书Bot] 群聊ID: ${config!.groupChatId}`);
    console.log(`[飞书Bot] ✅  长连接模式已启用，无需公网地址！`);
    console.log(`[飞书Bot] ℹ️  请在飞书后台设置事件订阅方式为「使用长连接接收事件」`);
  });

  // 设置群聊桥接（传6个客户端映射）
  setupGroupBridge(config.groupChatId, larkClients);

  return { app, larkClients };
}

// ========== 长连接监听事件 ==========
function startWebSocketEvent(ceoBotConfig: BotConfig) {
  if (!ceoBotConfig.appId || !ceoBotConfig.appSecret) return;

  console.log('[飞书Bot] 启动长连接监听事件...');

  const baseConfig = {
    appId: ceoBotConfig.appId,
    appSecret: ceoBotConfig.appSecret
  };

  // 创建长连接客户端
  const wsClient = new lark.WSClient({
    ...baseConfig,
    loggerLevel: lark.LoggerLevel.info
  });

  // 启动长连接
  wsClient.start({
    eventDispatcher: new lark.EventDispatcher({
      encryptKey: ceoBotConfig.encryptKey
    }).register({
      'im.message.receive_v1': async (data) => {
        console.log('[飞书Bot] 收到消息事件:', data);
        await handleMessageEventFromWebSocket(data);
      }
    })
  });

  console.log('[飞书Bot] 长连接已启动，等待事件...');
}

// ========== 处理长连接消息事件 ==========
async function handleMessageEventFromWebSocket(data: any) {
  const message = data?.message;
  const sender = data?.sender;

  if (!message || !sender) return;

  // 忽略机器人自己发送的消息（sender_type == 'app'）
  if (sender.sender_type === 'app') {
    console.log('[飞书Bot] 忽略机器人自己的消息');
    return;
  }

  const chatType = message.chat_type;
  const content = parseMessageContent(message.content);
  const senderId = sender.sender_id?.open_id;

  if (!content) return;

  // 保存最后发送者，用于回复
  lastSenderId = senderId;
  console.log(`[飞书Bot] 收到消息: ${chatType} - ${content} (sender: ${senderId})`);

  // 交给边界逻辑处理
  handleUserMessage({
    content,
    chatType: chatType === 'p2p' ? 'p2p' : 'group',
    senderId
  });
}

// ========== 直接回复用户私聊 ==========
export async function replyToUser(content: string) {
  if (!lastSenderId || !larkClients.ceo) {
    console.warn('[飞书Bot] 无法回复：缺少senderId或CEO客户端');
    return;
  }

  try {
    await larkClients.ceo.im.message.create({
      params: { receive_id_type: 'open_id' },
      data: {
        receive_id: lastSenderId,
        msg_type: 'text',
        content: JSON.stringify({ text: content }),
      },
    });
    console.log(`[飞书Bot] CEO已回复用户: ${content}`);
  } catch (err) {
    console.error('[飞书Bot] 回复失败:', err);
  }
}

// ========== 工具函数 ==========
function parseMessageContent(contentStr: string): string | null {
  try {
    const content = JSON.parse(contentStr);
    if (content.text) return content.text;
    if (content.elements) {
      return content.elements
        .map((e: any) => e.text?.content || '')
        .filter(Boolean)
        .join(' ');
    }
    return null;
  } catch {
    return null;
  }
}

// ========== 带重试机制的消息发送 ==========
async function sendWithRetry(
  sendFn: () => Promise<any>,
  maxRetries: number = 3,
  retryDelayMs: number = 1000
): Promise<any> {
  let lastError: any = null;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      return await sendFn();
    } catch (error) {
      lastError = error;
      console.warn(`[飞书Bot] 发送失败 (尝试 ${attempt}/${maxRetries}):`, error);

      if (attempt < maxRetries) {
        await new Promise(r => setTimeout(r, retryDelayMs * attempt));
      }
    }
  }

  throw lastError;
}

// ========== 指定角色发送消息到飞书 ==========
export async function sendAgentMessageToFeishu(
  chatId: string,
  agentRole: AgentRole,
  content: string
) {
  const client = larkClients[agentRole];
  if (!client) {
    console.warn(`[飞书Bot] ${formatAgentName(agentRole)} 未配置，用CEO Bot代替`);
    if (!larkClients.ceo) throw new Error('CEO Bot not initialized');
    await sendWithClient(larkClients.ceo, chatId, agentRole, content);
    return;
  }

  await sendWithClient(client, chatId, agentRole, content);
}

// ========== 构建富文本卡片消息 + 发送 ==========
async function sendWithClient(client: lark.Client, chatId: string, agentRole: AgentRole, content: string) {
  const agentName = formatAgentName(agentRole);
  const richContent = buildRichTextMessage(agentName, agentRole, content);

  await sendWithRetry(async () => {
    await client.im.message.create({
      params: { receive_id_type: 'chat_id' },
      data: {
        receive_id: chatId,
        msg_type: 'post',
        content: JSON.stringify(richContent),
      },
    });
  });
}

// ========== 构建富文本卡片消息 ==========
function buildRichTextMessage(agentName: string, agentRole: string, content: string) {
  // 把消息按行分割，构建更可读的格式
  const lines = content.split('\n').filter(Boolean);

  const elements = [];
  elements.push({ tag: 'md', text: `**${agentName}**` });

  if (lines.length > 0) {
    elements.push({ tag: 'text', text: '\n\n', style: [] });
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.startsWith('✅') || line.startsWith('❌') || line.startsWith('⚠️') || line.startsWith('ℹ️') || line.startsWith('🎯') || line.startsWith('🚀')) {
        elements.push({ tag: 'md', text: line });
      } else {
        elements.push({ tag: 'text', text: line, style: [] });
      }
      if (i < lines.length - 1) {
        elements.push({ tag: 'text', text: '\n', style: [] });
      }
    }
  }

  return {
    zh_cn: {
      title: '',
      content: [elements]
    }
  };
}
