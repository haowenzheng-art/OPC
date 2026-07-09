import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ProjectStateMachine } from '../../src/layers/orchestration/project-machine';

describe('Full Workflow Integration', () => {
  let machine: ProjectStateMachine;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  it('should go through complete project lifecycle', async () => {
    machine = new ProjectStateMachine('测试待办应用', 'test-proj-1');

    expect(machine.getState().value).toBe('idle');

    machine.start();
    expect(machine.getState().value).toBe('planning');

    machine.prdDone('Test PRD');
    expect(machine.getState().value).toBe('developing');

    machine.frontendDone();
    machine.backendDone();
    expect(machine.getState().value).toBe('testing');

    machine.testsPass();
    expect(machine.getState().value).toBe('deploying');

    machine.deployed('http://example.com');
    expect(machine.getState().value).toBe('learning');

    machine.learningDone();
    expect(machine.getState().value).toBe('done');

    const context = machine.getContext();
    expect(context.prd).toBe('Test PRD');
    expect(context.frontendCodeReady).toBe(true);
    expect(context.backendCodeReady).toBe(true);
    expect(context.testsPassed).toBe(true);
    expect(context.deployUrl).toBe('http://example.com');
  });

  it('should handle partial completion correctly', () => {
    machine = new ProjectStateMachine('Test', 'proj1');
    machine.start();
    machine.prdDone('PRD');

    expect(machine.getState().value).toBe('developing');

    machine.frontendDone();
    expect(machine.getState().value).toBe('developing');

    machine.backendDone();
    expect(machine.getState().value).toBe('testing');
  });
});
