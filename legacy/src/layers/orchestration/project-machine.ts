export interface ProjectContext {
  projectId: string;
  userIdea: string;
  prd: string | null;
  frontendCodeReady: boolean;
  backendCodeReady: boolean;
  testsPassed: boolean;
  deployUrl: string | null;
  errors: string[];
}

export type ProjectEvent =
  | { type: 'SUBMIT_IDEA'; idea: string }
  | { type: 'PRD_DONE'; prd: string }
  | { type: 'FRONTEND_DONE' }
  | { type: 'BACKEND_DONE' }
  | { type: 'TESTS_PASS' }
  | { type: 'TESTS_FAIL'; error: string }
  | { type: 'DEPLOYED'; url: string }
  | { type: 'LEARNING_DONE' }
  | { type: 'ERROR'; error: string }
  | { type: 'RETRY' };

export class ProjectStateMachine {
  private currentState: string = 'idle';
  private context: ProjectContext;
  private previousState: string = 'idle';

  constructor(userIdea: string, projectId?: string) {
    this.context = {
      projectId: projectId || 'proj_' + Date.now(),
      userIdea,
      prd: null,
      frontendCodeReady: false,
      backendCodeReady: false,
      testsPassed: false,
      deployUrl: null,
      errors: []
    };
  }

  start() {
    console.log('[状态机] 启动，初始状态: idle');
    if (this.context.userIdea) {
      this.send({ type: 'SUBMIT_IDEA', idea: this.context.userIdea });
    }
  }

  send(event: ProjectEvent) {
    const oldState = this.currentState;

    switch (this.currentState) {
      case 'idle':
        if (event.type === 'SUBMIT_IDEA') {
          this.context.userIdea = event.idea;
          this.currentState = 'planning';
          console.log('[状态机] idle -> planning');
        }
        break;

      case 'planning':
        if (event.type === 'PRD_DONE') {
          this.context.prd = event.prd;
          this.currentState = 'developing';
          console.log('[状态机] planning -> developing');
        }
        break;

      case 'developing':
        if (event.type === 'FRONTEND_DONE') {
          this.context.frontendCodeReady = true;
        } else if (event.type === 'BACKEND_DONE') {
          this.context.backendCodeReady = true;
        }
        if (this.context.frontendCodeReady && this.context.backendCodeReady) {
          this.currentState = 'testing';
          console.log('[状态机] developing -> testing');
        }
        break;

      case 'testing':
        if (event.type === 'TESTS_PASS') {
          this.context.testsPassed = true;
          this.currentState = 'deploying';
          console.log('[状态机] testing -> deploying');
        } else if (event.type === 'TESTS_FAIL') {
          this.currentState = 'developing';
          console.log('[状态机] testing -> developing (修复bug)');
        }
        break;

      case 'deploying':
        if (event.type === 'DEPLOYED') {
          this.context.deployUrl = event.url;
          this.currentState = 'learning';
          console.log('[状态机] deploying -> learning (保存工作流)');
        }
        break;

      case 'learning':
        if (event.type === 'LEARNING_DONE') {
          this.currentState = 'done';
          console.log('[状态机] learning -> done (完成!)');
        }
        break;
    }

    this.previousState = oldState;
  }

  getState() {
    return { value: this.currentState, context: this.context };
  }

  getContext() {
    return this.context;
  }

  prdDone(prd: string) {
    this.send({ type: 'PRD_DONE', prd });
  }

  frontendDone() {
    this.send({ type: 'FRONTEND_DONE' });
  }

  backendDone() {
    this.send({ type: 'BACKEND_DONE' });
  }

  testsPass() {
    this.send({ type: 'TESTS_PASS' });
  }

  testsFail(error: string) {
    this.send({ type: 'TESTS_FAIL', error });
  }

  deployed(url: string) {
    this.send({ type: 'DEPLOYED', url });
  }

  learningDone() {
    this.send({ type: 'LEARNING_DONE' });
  }
}
