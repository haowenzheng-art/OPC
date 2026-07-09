import Prisma from '../tools/prisma-client.js';
const { PrismaClient } = Prisma;
import { PMAgent } from '../agents/pm.js';
import { CeoAgent } from '../agents/ceo.js';
import { FrontendAgent } from '../agents/frontend.js';
import { BackendAgent } from '../agents/backend.js';
import { TestAgent } from '../agents/test.js';
import { OpsAgent } from '../agents/ops.js';
import { sendToGroup } from '../messaging/bus.js';
import { ProjectStateMachine } from './project-machine.js';
import { UserMessageFilter } from '../boundary/userMessageFilter.js';
import { setMessageFilter, setCeoAgent } from '../visualization/feishu/bot.js';
import { saveWorkflowTemplate, findMatchingTemplate } from '../tools/workflow.js';
import { seedDefaultSkills } from '../tools/skill-library.js';

const prisma = new PrismaClient();

export class ProjectOrchestrator {
  projectId: string;
  userIdea: string;
  pm: PMAgent;
  ceo: CeoAgent;
  frontend: FrontendAgent;
  backend: BackendAgent;
  test: TestAgent;
  ops: OpsAgent;
  stateMachine: ProjectStateMachine;
  messageFilter: UserMessageFilter;
  prdContent: string = '';

  constructor(userIdea: string, existingCeo?: CeoAgent) {
    this.userIdea = userIdea;
    this.projectId = 'proj_' + Date.now();

    this.pm = new PMAgent(this.projectId, userIdea);
    if (existingCeo) {
      this.ceo = existingCeo;
      this.ceo.projectId = this.projectId;
      this.ceo.userIdea = userIdea;
    } else {
      this.ceo = new CeoAgent(this.projectId, userIdea);
    }
    this.frontend = new FrontendAgent(this.projectId);
    this.backend = new BackendAgent(this.projectId);
    this.test = new TestAgent(this.projectId);
    this.ops = new OpsAgent(this.projectId);
    this.stateMachine = new ProjectStateMachine(userIdea, this.projectId);
    this.messageFilter = new UserMessageFilter(this.projectId);
  }

  async init() {
    await seedDefaultSkills();

    await prisma.project.create({
      data: {
        id: this.projectId,
        userIdea: this.userIdea,
        status: 'idle'
      }
    });

    const existingTemplate = await findMatchingTemplate(this.userIdea);
    if (existingTemplate) {
      console.log('\n[Orchestrator] 找到匹配的工作流模板:', existingTemplate.name);
      console.log('[Orchestrator] 模板描述:', existingTemplate.description);
    }

    setMessageFilter(this.messageFilter);
    setCeoAgent(this.ceo);
  }

  async start() {
    console.log('=== 项目启动 ===');
    console.log('项目ID:', this.projectId);
    console.log('用户需求:', this.userIdea);
    console.log('');

    this.stateMachine.start();
    this.ceo.run();
    this.pm.run();

    this.setupAgentListeners();
  }

  private setupAgentListeners() {
    const checkPmInterval = setInterval(async () => {
      if (await this.pm.isDone()) {
        clearInterval(checkPmInterval);
        console.log('\n[Orchestrator] PM完成了PRD！');
        const prd = await this.pm.getPrdContent();
        this.prdContent = prd;
        this.stateMachine.prdDone(prd);
        this.ceo.updateStage('developing');

        await sendToGroup(this.projectId, 'ceo', 'PRD已通过！后端先设计API，前端等待API设计完成...');

        // 先启动Backend，设计API
        this.backend.setPrd(prd);
        this.backend.run();

        this.waitForBackendApiDesign();
      }
    }, 500);
  }

  private waitForBackendApiDesign() {
    const checkBackendApiInterval = setInterval(async () => {
      // 检查Backend是否完成了API设计（已经开始写代码或者完成）
      const backendActions = this.backend.getRecentActions ? this.backend.getRecentActions() : [];
      const hasStartedWriting = backendActions.some((a: string) => a.includes('WRITE_MODEL') || a.includes('WRITE_API'));
      const isBackendDone = await this.backend.isDone();

      if (hasStartedWriting || isBackendDone) {
        clearInterval(checkBackendApiInterval);
        console.log('\n[Orchestrator] 后端API设计完成！现在启动前端...');
        await sendToGroup(this.projectId, 'ceo', '✅ 后端API设计完成！前端开始根据API设计写代码...');

        // 给Frontend设置PRD并启动
        this.frontend.setPrd(this.prdContent);
        this.frontend.run();

        this.setupDevListeners();
      }
    }, 500);
  }

  private setupDevListeners() {
    let frontendDone = false;
    let backendDone = false;

    const checkFrontendInterval = setInterval(async () => {
      if (await this.frontend.isDone()) {
        clearInterval(checkFrontendInterval);
        frontendDone = true;
        console.log('\n[Orchestrator] 前端完成！');
        this.stateMachine.frontendDone();
        checkDevCompletion();
      }
    }, 500);

    const checkBackendInterval = setInterval(async () => {
      if (await this.backend.isDone()) {
        clearInterval(checkBackendInterval);
        backendDone = true;
        console.log('\n[Orchestrator] 后端完成！');
        this.stateMachine.backendDone();
        checkDevCompletion();
      }
    }, 500);

    const checkDevCompletion = () => {
      if (frontendDone && backendDone) {
        console.log('\n[Orchestrator] 所有代码完成！开始测试...');
        this.startTestingPhase();
      }
    };
  }

  private startTestingPhase() {
    this.stateMachine.testsPass();

    this.test.setPrd(this.prdContent);
    this.test.run();

    const checkTestInterval = setInterval(async () => {
      if (await this.test.isDone()) {
        clearInterval(checkTestInterval);
        console.log('\n[Orchestrator] 测试完成！开始部署...');
        this.stateMachine.testsPass();
        this.startDeployPhase();
      }
    }, 500);
  }

  private startDeployPhase() {
    this.ops.run();

    const checkOpsInterval = setInterval(async () => {
      if (await this.ops.isDone()) {
        clearInterval(checkOpsInterval);
        console.log('\n[Orchestrator] 部署完成！进入学习阶段...');
        this.stateMachine.deployed(`http://${this.projectId}.demo.local`);
        this.startLearningPhase();
      }
    }, 500);
  }

  private async startLearningPhase() {
    console.log('[Orchestrator] 开始保存工作流模板...');

    // 收集所有生成的文件
    const allFiles: string[] = [];
    const pmFiles: string[] = []; // PM一般不生成文件
    const frontendFiles = this.frontend.filesWritten || [];
    const backendFiles = this.backend.filesWritten || [];
    const testFiles: string[] = []; // Test一般不生成文件
    const opsFiles: string[] = []; // Ops生成的配置文件

    // 合并所有文件用于保存
    allFiles.push(...frontendFiles, ...backendFiles);

    await saveWorkflowTemplate(
      this.userIdea,
      this.pm.getRecentActions ? this.pm.getRecentActions() : [],
      this.frontend.getRecentActions ? this.frontend.getRecentActions() : [],
      this.backend.getRecentActions ? this.backend.getRecentActions() : [],
      this.test.getRecentActions ? this.test.getRecentActions() : [],
      this.ops.getRecentActions ? this.ops.getRecentActions() : [],
      { pmFiles, frontendFiles, backendFiles, testFiles, opsFiles, allFiles }
    );

    console.log('[Orchestrator] 工作流模板保存成功！');

    this.stateMachine.learningDone();

    this.ceo.updateStage('done');

    await sendToGroup(this.projectId, 'ceo', '🎉 项目全部完成！工作流已保存，下次可以参考使用。');
  }

  getState() {
    return this.stateMachine.getState();
  }

  getContext() {
    return this.stateMachine.getContext();
  }
}
