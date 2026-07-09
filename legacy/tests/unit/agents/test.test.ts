import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TestAgent } from '../../../src/layers/agents/test.js';
import * as tools from '../../../src/layers/tools/file-system.js';
import * as messaging from '../../../src/layers/messaging/bus.js';
import { mockPrd } from '../../fixtures/mock-prd.js';

vi.mock('../../../src/layers/messaging/bus.js');
vi.mock('../../../src/layers/tools/file-system.js', () => ({
  listProjectFiles: vi.fn().mockReturnValue([
    'package.json',
    'app/page.tsx',
    'server/src/index.ts'
  ]),
  readProjectFile: vi.fn().mockImplementation((proj, file) => {
    if (file === 'package.json') return JSON.stringify({ dependencies: { next: '14.0.0' } });
    if (file === 'app/page.tsx') return 'export default function Home() { return <div>Test</div>; }';
    if (file === 'server/src/index.ts') return 'import express from "express";';
    return null;
  }),
  writeProjectFile: vi.fn()
}));

describe('TestAgent', () => {
  let testAgent: TestAgent;

  beforeEach(() => {
    testAgent = new TestAgent('test-proj-1');
    testAgent.setPrd(mockPrd);
    vi.clearAllMocks();
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
  });

  it('should be able to set PRD', () => {
    testAgent.setPrd('New PRD');
    expect((testAgent as any).prd).toBe('New PRD');
  });

  it('should check file structure', async () => {
    await testAgent.run();
    expect(tools.listProjectFiles).toHaveBeenCalled();
  });

  it('should validate code files', async () => {
    await testAgent.run();
    expect(tools.readProjectFile).toHaveBeenCalled();
  });

  it('should mark done when testing complete', async () => {
    await testAgent.run();
    expect(await testAgent.isDone()).toBe(true);
  });

  it('should initialize with correct projectId', () => {
    const newTest = new TestAgent('proj-2');
    expect(newTest.projectId).toBe('proj-2');
  });

  it('should handle empty file list', async () => {
    vi.mocked(tools.listProjectFiles).mockReturnValueOnce([]);
    await testAgent.run();
    expect(await testAgent.isDone()).toBe(true);
  });

  it('should handle missing package.json', async () => {
    vi.mocked(tools.readProjectFile).mockReturnValueOnce(null);
    await testAgent.run();
    expect(await testAgent.isDone()).toBe(true);
  });

  it('should handle todo features in PRD', async () => {
    testAgent.setPrd('# 待办应用');
    await testAgent.run();
    expect(await testAgent.isDone()).toBe(true);
  });
});
