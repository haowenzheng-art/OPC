import { describe, it, expect, beforeEach } from 'vitest';
import { ProjectStateMachine } from '../../../src/layers/orchestration/project-machine.js';

describe('ProjectStateMachine', () => {
  let machine: ProjectStateMachine;

  beforeEach(() => {
    machine = new ProjectStateMachine('测试待办应用', 'test-proj-1');
  });

  it('should start in idle state', () => {
    const state = machine.getState();
    expect(state.value).toBe('idle');
  });

  it('should transition from idle to planning on SUBMIT_IDEA', () => {
    machine.start();
    const state = machine.getState();
    expect(state.value).toBe('planning');
  });

  it('should transition from planning to developing on PRD_DONE', () => {
    machine.start();
    machine.prdDone('Test PRD');

    const state = machine.getState();
    expect(state.value).toBe('developing');
  });

  it('should transition from developing to testing when both frontend and backend done', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();

    const state = machine.getState();
    expect(state.value).toBe('testing');
  });

  it('should transition from testing to deploying on TESTS_PASS', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();
    machine.testsPass();

    const state = machine.getState();
    expect(state.value).toBe('deploying');
  });

  it('should transition from deploying to learning on DEPLOYED', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();
    machine.testsPass();
    machine.deployed('http://example.com');

    const state = machine.getState();
    expect(state.value).toBe('learning');
  });

  it('should transition from learning to done on LEARNING_DONE', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();
    machine.testsPass();
    machine.deployed('http://example.com');
    machine.learningDone();

    const state = machine.getState();
    expect(state.value).toBe('done');
  });

  it('should track context correctly', () => {
    machine.start();
    machine.prdDone('Test PRD Content');

    const context = machine.getContext();
    expect(context.prd).toBe('Test PRD Content');
  });

  it('should track user idea in context', () => {
    const context = machine.getContext();
    expect(context.userIdea).toBe('测试待办应用');
    expect(context.projectId).toBe('test-proj-1');
  });

  it('should mark frontend and backend code ready in context', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();

    let context = machine.getContext();
    expect(context.frontendCodeReady).toBe(true);
    expect(context.backendCodeReady).toBe(false);

    machine.backendDone();
    context = machine.getContext();
    expect(context.backendCodeReady).toBe(true);
  });

  it('should only mark frontend ready first', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();

    const state = machine.getState();
    expect(state.value).toBe('developing');
  });

  it('should only mark backend ready first', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.backendDone();

    const state = machine.getState();
    expect(state.value).toBe('developing');
  });

  it('should not transition without start', () => {
    machine.prdDone('Test PRD');
    const state = machine.getState();
    expect(state.value).toBe('idle');
  });

  it('should track deployUrl in context', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();
    machine.testsPass();
    machine.deployed('http://example.com');

    const context = machine.getContext();
    expect(context.deployUrl).toBe('http://example.com');
  });

  it('should not transition to done without deploy', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();
    machine.testsPass();

    const state = machine.getState();
    expect(state.value).toBe('deploying');
  });

  it('should create machine without projectId', () => {
    const newMachine = new ProjectStateMachine('测试需求');
    expect(newMachine.getContext().userIdea).toBe('测试需求');
  });

  it('should track testsPassed in context', () => {
    machine.start();
    machine.prdDone('Test PRD');
    machine.frontendDone();
    machine.backendDone();
    machine.testsPass();

    const context = machine.getContext();
    expect(context.testsPassed).toBe(true);
  });

  it('should initialize errors array', () => {
    const context = machine.getContext();
    expect(Array.isArray(context.errors)).toBe(true);
  });
});
