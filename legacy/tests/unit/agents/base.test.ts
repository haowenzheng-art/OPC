import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BaseAgent } from '../../../src/layers/agents/base';
import { State, Action, AgentRole } from '../../../src/types';
import * as messaging from '../../../src/layers/messaging/bus';

vi.mock('../../../src/layers/messaging/bus');

class TestAgent extends BaseAgent {
  private done = false;
  public actions: Action[] = [];
  public perceptions: State[] = [];

  async perceive(): Promise<State> {
    const state = { projectId: this.projectId, status: 'idle' as any };
    this.perceptions.push(state);
    return state;
  }

  async reason(state: State): Promise<Action> {
    if (this.done) return { type: 'WAIT', payload: null };
    return { type: 'TEST_ACTION', payload: 'test' };
  }

  async act(action: Action): Promise<void> {
    this.actions.push(action);
    this.done = true;
  }

  async handleError(error: Error): Promise<boolean> {
    return true;
  }

  async isDone(): Promise<boolean> {
    return this.done;
  }
}

describe('BaseAgent', () => {
  let agent: TestAgent;

  beforeEach(() => {
    agent = new TestAgent('test-proj-1', 'pm' as AgentRole);
    vi.clearAllMocks();
    vi.mocked(messaging.sendToAgent).mockResolvedValue(undefined);
    vi.mocked(messaging.sendToGroup).mockResolvedValue(undefined);
  });

  it('should initialize with correct projectId and role', () => {
    expect(agent.projectId).toBe('test-proj-1');
    expect(agent.role).toBe('pm');
  });

  it('should run through perceive-reason-act cycle', async () => {
    await agent.run();

    expect(agent.perceptions.length).toBeGreaterThan(0);
    expect(agent.actions.length).toBeGreaterThan(0);
  });

  it('should complete when isDone returns true', async () => {
    await agent.run();
    expect(await agent.isDone()).toBe(true);
  });

  it('should handle errors and continue if handleError returns true', async () => {
    const testError = new Error('Test error');
    vi.spyOn(agent, 'perceive').mockRejectedValueOnce(testError);
    const handleErrorSpy = vi.spyOn(agent, 'handleError').mockResolvedValue(true);

    await agent.run();

    expect(handleErrorSpy).toHaveBeenCalledWith(testError);
  });

  it('should be stoppable', () => {
    agent.stop();
    expect(agent.isRunning).toBe(false);
  });

  it('should send message to agent', async () => {
    await agent.sendMessage('ceo', 'Hello CEO');

    expect(messaging.sendToAgent).toHaveBeenCalledWith(
      'test-proj-1',
      'pm',
      'ceo',
      'Hello CEO'
    );
  });

  it('should send message to group', async () => {
    await agent.sendMessage(null, 'Hello group');

    expect(messaging.sendToGroup).toHaveBeenCalledWith(
      'test-proj-1',
      'pm',
      'Hello group'
    );
  });
});
