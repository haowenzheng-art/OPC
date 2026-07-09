import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BackendAgent } from '../../../src/layers/agents/backend.js';
import * as tools from '../../../src/layers/tools/file-system.js';
import * as messaging from '../../../src/layers/messaging/bus.js';

vi.mock('../../../src/layers/messaging/bus.js');
vi.mock('../../../src/layers/tools/file-system.js', () => ({
  ensureProjectDir: vi.fn(),
  writeProjectFile: vi.fn(),
  listProjectFiles: vi.fn().mockReturnValue(['server/package.json'])
}));

describe('BackendAgent', () => {
  let backend: BackendAgent;

  beforeEach(() => {
    backend = new BackendAgent('test-proj-1');
    backend.setPrd('# 测试待办应用');
    vi.clearAllMocks();
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
  });

  it('should be able to set PRD', () => {
    backend.setPrd('New PRD');
    expect((backend as any).prd).toBe('New PRD');
  });

  it('should create backend project structure', async () => {
    await backend.run();
    expect(tools.writeProjectFile).toHaveBeenCalled();
  });

  it('should mark done when all files are written', async () => {
    await backend.run();
    expect(await backend.isDone()).toBe(true);
  });

  it('should initialize with correct projectId', () => {
    const newBackend = new BackendAgent('proj-2');
    expect(newBackend.projectId).toBe('proj-2');
  });

  it('should handle todo features in PRD', async () => {
    backend.setPrd('# 待办应用');
    await backend.run();
    expect(await backend.isDone()).toBe(true);
  });
});
