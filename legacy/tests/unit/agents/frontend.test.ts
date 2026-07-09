import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FrontendAgent } from '../../../src/layers/agents/frontend.js';
import * as tools from '../../../src/layers/tools/file-system.js';
import * as messaging from '../../../src/layers/messaging/bus.js';

vi.mock('../../../src/layers/messaging/bus.js');
vi.mock('../../../src/layers/tools/file-system.js', () => ({
  generateProjectStructure: vi.fn(),
  writeProjectFile: vi.fn(),
  listProjectFiles: vi.fn().mockReturnValue(['package.json', 'app/page.tsx'])
}));

describe('FrontendAgent', () => {
  let frontend: FrontendAgent;

  beforeEach(() => {
    frontend = new FrontendAgent('test-proj-1');
    frontend.setPrd('# 测试待办应用');
    vi.clearAllMocks();
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
  });

  it('should be able to set PRD', () => {
    frontend.setPrd('New PRD');
    expect((frontend as any).prd).toBe('New PRD');
  });

  it('should generate project structure', async () => {
    await frontend.run();
    expect(tools.generateProjectStructure).toHaveBeenCalledWith('test-proj-1', 'nextjs');
  });

  it('should write page and component files', async () => {
    await frontend.run();
    expect(tools.writeProjectFile).toHaveBeenCalled();
  });

  it('should mark done when all files are written', async () => {
    await frontend.run();
    expect(await frontend.isDone()).toBe(true);
  });

  it('should initialize with correct projectId', () => {
    const newFrontend = new FrontendAgent('proj-2');
    expect(newFrontend.projectId).toBe('proj-2');
  });

  it('should handle todo features in PRD', async () => {
    frontend.setPrd('# 待办应用');
    await frontend.run();
    expect(await frontend.isDone()).toBe(true);
  });
});
