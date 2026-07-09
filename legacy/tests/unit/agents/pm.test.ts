import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PMAgent } from '../../../src/layers/agents/pm.js';
import * as messaging from '../../../src/layers/messaging/bus.js';

vi.mock('../../../src/layers/messaging/bus.js');

describe('PMAgent', () => {
  let pm: PMAgent;
  const testIdea = '测试待办应用';

  beforeEach(() => {
    pm = new PMAgent('test-proj-1', testIdea);
    vi.clearAllMocks();
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
  });

  it('should generate PRD based on user idea', async () => {
    await pm.run();

    expect(pm.prdWritten).toBe(true);
    expect(pm.prdContent).toContain(testIdea);
    expect(pm.prdContent).toContain('功能概述');
    expect(pm.prdContent).toContain('技术方案');
  });

  it('should send PRD to group', async () => {
    await pm.run();

    expect(messaging.sendToGroup).toHaveBeenCalled();
  });

  it('should report to CEO when done', async () => {
    await pm.run();

    expect(messaging.sendToAgent).toHaveBeenCalledWith(
      'test-proj-1',
      'pm',
      'ceo',
      expect.stringContaining('prd_done')
    );
  });

  it('should return PRD content via getPrdContent', async () => {
    await pm.run();
    const prd = pm.getPrdContent();

    expect(prd).toBe(pm.prdContent);
  });

  it('should initialize with correct projectId and userIdea', () => {
    expect(pm.projectId).toBe('test-proj-1');
    expect((pm as any).userIdea).toBe(testIdea);
  });

  it('should return true from isDone after run', async () => {
    await pm.run();
    const done = await pm.isDone();
    expect(done).toBe(true);
  });
});
