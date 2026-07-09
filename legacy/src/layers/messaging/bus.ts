import { saveMessage } from './store.js';
import { EventEmitter } from 'events';

const eventBus = new EventEmitter();

// 发送消息到群聊
export async function sendToGroup(projectId: string, fromAgent: string, content: string) {
  await saveMessage(projectId, fromAgent, null, 'text', content);
  eventBus.emit('message', { projectId, fromAgent, toAgent: null, content });
}

// 发送私聊消息
export async function sendToAgent(projectId: string, fromAgent: string, toAgent: string, content: string) {
  await saveMessage(projectId, fromAgent, toAgent, 'text', content);
  eventBus.emit('message', { projectId, fromAgent, toAgent, content });
}

// 监听消息
export function onMessage(callback: (msg: { projectId: string, fromAgent: string, toAgent: string | null, content: string }) => void) {
  eventBus.on('message', callback);
}
