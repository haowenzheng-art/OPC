import { onMessage } from '../../messaging/bus.js';
import { sendAgentMessageToFeishu } from './server.js';

// 全局状态
let groupChatId: string | null = null;
let isBridgeActive = false;

// ========== 设置群聊桥接 ==========
export function setupGroupBridge(chatId: string, _clients: any) {
  groupChatId = chatId;
  console.log(`[群聊桥接] 已连接到群聊: ${chatId}`);
  startBridge();
}

// ========== 启动消息桥接 ==========
function startBridge() {
  if (isBridgeActive) return;
  isBridgeActive = true;

  // 监听MessageBus的所有消息
  onMessage(async (message) => {
    if (!groupChatId) return;

    if (message.fromAgent !== 'user') {
      try {
        await sendAgentMessageToFeishu(groupChatId, message.fromAgent as any, message.content);
        console.log(`[群聊桥接] ${message.fromAgent} → 飞书群`);
      } catch (error) {
        console.error(`[群聊桥接] 发送失败:`, error);
      }
    }
  });

  console.log('[群聊桥接] 已启动，正在监听Agent消息...');
}

// ========== 停止桥接 ==========
export function stopBridge() {
  isBridgeActive = false;
  console.log('[群聊桥接] 已停止');
}
