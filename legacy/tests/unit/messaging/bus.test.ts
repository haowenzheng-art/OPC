import { describe, it, expect, vi, beforeEach } from 'vitest';
import { sendToGroup, sendToAgent, onMessage } from '../../../src/layers/messaging/bus.js';

vi.mock('../../../src/layers/messaging/store.js', () => ({
  saveMessage: vi.fn().mockResolvedValue(undefined)
}));

describe('MessageBus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should send message to group', async () => {
    await expect(sendToGroup('proj-1', 'pm', 'Hello group')).resolves.not.toThrow();
  });

  it('should send private message to agent', async () => {
    await expect(sendToAgent('proj-1', 'pm', 'ceo', 'Hello CEO')).resolves.not.toThrow();
  });

  it('should allow subscribing to messages', () => {
    const callback = vi.fn();
    onMessage(callback);
    expect(callback).toBeDefined();
  });

  it('should send group message with empty content', async () => {
    await expect(sendToGroup('proj-1', 'pm', '')).resolves.not.toThrow();
  });

  it('should send agent message with empty content', async () => {
    await expect(sendToAgent('proj-1', 'pm', 'ceo', '')).resolves.not.toThrow();
  });
});
