import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CeoAgent } from '../../../src/layers/agents/ceo.js';
import * as messaging from '../../../src/layers/messaging/bus.js';

vi.mock('../../../src/layers/messaging/bus.js');

describe('CeoAgent', () => {
  let ceo: CeoAgent;

  beforeEach(() => {
    ceo = new CeoAgent('test-proj-1', '测试项目');
    vi.clearAllMocks();
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
  });

  it('should initialize with correct projectId and userIdea', () => {
    expect(ceo.projectId).toBe('test-proj-1');
    expect((ceo as any).userIdea).toBe('测试项目');
  });

  it('should recognize halt commands', () => {
    const result = (ceo as any).isHaltCommand('暂停项目');
    expect(result).toBe(true);
  });

  it('should recognize chat messages', () => {
    const result = (ceo as any).isJustChatting('你好');
    expect(result).toBe(true);
  });

  it('should update stage correctly', () => {
    ceo.updateStage('developing');
    expect((ceo as any).currentStage).toBe('developing');
  });

  it('should receive user messages', () => {
    ceo.receiveUserMessage('这是一个消息', 'user123');
    const pending = (ceo as any).pendingMessages;
    expect(pending.length).toBe(1);
  });

  it('should not recognize non-halt commands', () => {
    const result = (ceo as any).isHaltCommand('继续开发');
    expect(result).toBe(false);
  });

  it('should not recognize non-chat messages', () => {
    const result = (ceo as any).isJustChatting('prd_done');
    expect(result).toBe(false);
  });

  it('should return false from isDone initially', async () => {
    expect(await ceo.isDone()).toBe(false);
  });

  it('should handle known halt command variations', () => {
    expect((ceo as any).isHaltCommand('停止')).toBe(true);
    expect((ceo as any).isHaltCommand('暂停')).toBe(true);
  });

  it('should handle known chat phrases', () => {
    expect((ceo as any).isJustChatting('你好')).toBe(true);
    expect((ceo as any).isJustChatting('hi')).toBe(true);
    expect((ceo as any).isJustChatting('hello')).toBe(true);
  });

  it('should initialize with userIdea as undefined if not provided', () => {
    const newCeo = new CeoAgent('proj-2');
    expect(newCeo.projectId).toBe('proj-2');
  });

  it('should receive and store multiple user messages', () => {
    ceo.receiveUserMessage('消息1', 'user1');
    ceo.receiveUserMessage('消息2', 'user2');
    const pending = (ceo as any).pendingMessages;
    expect(pending.length).toBe(2);
  });
});
