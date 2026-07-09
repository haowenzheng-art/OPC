// 边界逻辑：用户消息过滤器
// 规则：
// 1. 用户在群聊中说话 → 完全忽略（不影响Agent工作）
// 2. 用户私聊CEO → 分析后决定是否影响项目

export type ChatType = 'group' | 'p2p';
export type MessageSource = 'user' | 'agent' | 'system';

export interface UserMessage {
  id: string;
  content: string;
  chatType: ChatType;
  chatId: string;
  senderId?: string;
  senderName?: string;
  timestamp: Date;
}

export interface FilterResult {
  shouldIgnore: boolean;
  reason?: string;
  action?: 'forward_to_ceo' | 'reply_polite' | 'none';
  replyContent?: string;
}

// 礼貌的忽略回复（可选）
const POLITE_REPLIES = [
  '收到！我在专注工作中，有重要事请私聊CEO~',
  '好的！团队正在努力中，重大调整请联系CEO~',
  '明白了！有新需求请私聊CEO告诉我~'
];

export class UserMessageFilter {
  private projectId: string;
  private ignoreGroupMessages: boolean = true;
  private enablePoliteReply: boolean = false;

  constructor(projectId: string) {
    this.projectId = projectId;
  }

  filter(message: UserMessage): FilterResult {
    // 规则1：群聊消息 → 忽略
    if (message.chatType === 'group') {
      return this.handleGroupMessage(message);
    }

    // 规则2：私聊消息 → 让CEO处理
    if (message.chatType === 'p2p') {
      return this.handlePrivateMessage(message);
    }

    // 默认：忽略
    return { shouldIgnore: true, reason: '未知的聊天类型' };
  }

  private handleGroupMessage(message: UserMessage): FilterResult {
    console.log(`[边界逻辑] 群聊消息已忽略: "${message.content.substring(0, 30)}..."`);

    if (this.enablePoliteReply) {
      const reply = POLITE_REPLIES[Math.floor(Math.random() * POLITE_REPLIES.length)];
      return {
        shouldIgnore: true,
        reason: '群聊消息不影响Agent工作',
        action: 'reply_polite',
        replyContent: reply
      };
    }

    return {
      shouldIgnore: true,
      reason: '群聊消息不影响Agent工作',
      action: 'none'
    };
  }

  private handlePrivateMessage(message: UserMessage): FilterResult {
    console.log(`[边界逻辑] CEO收到私聊消息: "${message.content.substring(0, 50)}..."`);

    // 分析消息内容
    const analysis = this.analyzeMessage(message.content);

    return {
      shouldIgnore: false,
      reason: 'CEO私聊消息需要处理',
      action: 'forward_to_ceo',
      replyContent: analysis.autoReply
    };
  }

  private analyzeMessage(content: string) {
    const lowerContent = content.toLowerCase();

    // 判断消息类型
    const isProjectRelated = this.isProjectRelated(lowerContent);
    const isUrgent = this.isUrgent(lowerContent);
    const isChatting = this.isJustChatting(lowerContent);

    let autoReply = '收到你的消息！我来处理一下...';

    if (isChatting) {
      autoReply = '哈哈好的！我先让团队继续工作，有空再跟你聊~';
    } else if (isUrgent) {
      autoReply = '收到紧急消息！我立即评估是否需要调整当前工作...';
    } else if (isProjectRelated) {
      autoReply = '收到项目相关消息！我等当前阶段完成后处理...';
    }

    return {
      isProjectRelated,
      isUrgent,
      isChatting,
      autoReply
    };
  }

  private isProjectRelated(content: string): boolean {
    const keywords = [
      '需求', '需求变更', '改一下', '修改', '调整',
      '功能', '加个', '新加', '补充',
      '暂停', '停止', '取消',
      '重新', '重来', '从头来'
    ];
    return keywords.some(kw => content.includes(kw));
  }

  private isUrgent(content: string): boolean {
    const keywords = [
      '紧急', '快', '立刻', '马上', '赶紧',
      '十万火急', '急', '重要'
    ];
    return keywords.some(kw => content.includes(kw));
  }

  private isJustChatting(content: string): boolean {
    const keywords = [
      '你好', '哈喽', 'hi', 'hello',
      '在吗', '在么', '忙吗',
      '谢谢', '感谢', '好的',
      '哈哈', '呵呵', '赞', '厉害'
    ];
    // 如果短消息且包含这些词，视为闲聊
    return content.length < 20 && keywords.some(kw => content.includes(kw));
  }

  // 配置选项
  setIgnoreGroupMessages(value: boolean) {
    this.ignoreGroupMessages = value;
  }

  setEnablePoliteReply(value: boolean) {
    this.enablePoliteReply = value;
  }
}

// 单例模式：全局用户消息过滤器
let globalFilter: UserMessageFilter | null = null;

export function getGlobalFilter(projectId?: string): UserMessageFilter {
  if (!globalFilter && projectId) {
    globalFilter = new UserMessageFilter(projectId);
  }
  if (!globalFilter) {
    throw new Error('需要先初始化globalFilter，传入projectId');
  }
  return globalFilter;
}

export function resetGlobalFilter() {
  globalFilter = null;
}
