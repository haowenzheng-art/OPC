import { describe, it, expect, vi, beforeEach } from 'vitest';
import { OpsAgent } from '../../../src/layers/agents/ops.js';
import * as tools from '../../../src/layers/tools/file-system.js';
import * as messaging from '../../../src/layers/messaging/bus.js';

vi.mock('../../../src/layers/messaging/bus.js');
vi.mock('../../../src/layers/tools/file-system.js', () => ({
  listProjectFiles: vi.fn().mockReturnValue(['package.json']),
  writeProjectFile: vi.fn()
}));

describe('OpsAgent', () => {
  let ops: OpsAgent;

  beforeEach(() => {
    ops = new OpsAgent('test-proj-1');
    vi.clearAllMocks();
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
  });

  it('should create deployment configuration', async () => {
    await ops.run();
    expect(tools.writeProjectFile).toHaveBeenCalledWith(
      'test-proj-1',
      'DEPLOYMENT.md',
      expect.stringContaining('部署信息')
    );
  });

  it('should report deploy done to CEO', async () => {
    await ops.run();
    expect(messaging.sendToAgent).toHaveBeenCalledWith(
      'test-proj-1',
      'ops',
      'ceo',
      expect.stringContaining('deploy_done')
    );
  });

  it('should mark done when deployment complete', async () => {
    await ops.run();
    expect(await ops.isDone()).toBe(true);
  });

  it('should initialize with correct projectId', () => {
    const newOps = new OpsAgent('proj-2');
    expect(newOps.projectId).toBe('proj-2');
  });
});
