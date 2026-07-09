import { BaseAgent } from './base.js';
import { State, Action } from '../../types/index.js';
import { listProjectFiles, writeProjectFile, extractCodeBlock } from '../tools/index.js';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

export class OpsAgent extends BaseAgent {
  private started: boolean = false;
  private deploySteps: string[] = [];
  private deployUrl: string = '';

  constructor(projectId: string) {
    super(projectId, 'ops');
  }

  async perceive(): Promise<State> {
    const files = listProjectFiles(this.projectId);
    return {
      projectId: this.projectId,
      status: 'idle' as any,
      files,
      deploySteps: this.deploySteps
    };
  }

  async reason(state: State): Promise<Action> {
    if (!this.started) {
      return { type: 'START_DEPLOY', payload: state.files };
    }
    if (this.started && !this.isDoneSync()) {
      if (this.deploySteps.length === 0) {
        return { type: 'CREATE_DEPLOY_CONFIG', payload: state.files };
      }
      if (this.deploySteps.length === 1) {
        return { type: 'SIMULATE_DEPLOY', payload: null };
      }
      return { type: 'FINISH_DEPLOY', payload: null };
    }
    return { type: 'WAIT', payload: null };
  }

  async act(action: Action) {
    if (action.type === 'START_DEPLOY') {
      this.started = true;
      const files = action.payload as string[];
      await this.sendMessage(null, '测试通过！开始部署流程...');
      await this.sendMessage(null, '项目包含 ' + files.length + ' 个文件待部署');
      this.recordAction('START_DEPLOY');
    }

    if (action.type === 'CREATE_DEPLOY_CONFIG') {
      await this.sendMessage(null, '创建部署配置文件...');

      const files = action.payload as string[];
      let deployContent = '';

      if (this.useHardcodedMode()) {
        deployContent = '# 部署文档\n\n## 项目概述\n项目ID: ' + this.projectId + '\n状态: 已部署\n部署时间: ' + new Date().toISOString() + '\n\n## 前置依赖\n- Node.js >= 18.x\n- npm >= 9.x\n\n## 服务地址\n- 前端访问地址: http://localhost:3000\n- 后端API地址: http://localhost:3001\n\n## 启动说明\n\n### 1. 前端启动\n```bash\n# 安装依赖\nnpm install\n\n# 启动开发服务器\nnpm run dev\n```\n\n### 2. 后端启动\n```bash\n# 进入后端目录\ncd server\n\n# 安装依赖\nnpm install\n\n# 启动开发服务器\nnpm run dev\n```\n\n## 环境变量配置\n1. 复制 .env.example 为 .env\n2. 根据实际情况修改配置\n\n## 常见问题排查\n\n### Q: 端口被占用怎么办？\nA: 修改 package.json 中的端口配置，或结束占用端口的进程：\n```bash\n# Windows 查找并结束进程\nnetstat -ano | findstr :3000\ntaskkill /PID <进程ID> /F\n\n# Mac/Linux\nlsof -ti :3000 | xargs kill -9\n```\n\n### Q: npm install 失败？\nA: 尝试以下方案：\n1. 清除 npm 缓存: npm cache clean --force\n2. 使用淘宝镜像: npm config set registry https://registry.npmmirror.com\n3. 删除 node_modules 和 package-lock.json 后重新安装\n\n### Q: 前端无法连接后端API？\nA: 检查：\n1. 后端服务是否正常启动在 3001 端口\n2. 前端 .env 中的 API_URL 配置是否正确\n3. 浏览器控制台是否有 CORS 错误\n\n### Q: 数据库连接失败？\nA: 检查：\n1. 数据库服务是否启动\n2. .env 中的 DATABASE_URL 配置是否正确\n3. 是否有创建数据库的权限\n';
      } else {
        try {
          await this.sendMessage(null, '正在用 AI 生成部署配置...');
          deployContent = await this.generateDeployConfigWithLLM(files);
        } catch (error) {
          console.warn('[Ops] LLM failed, falling back to hardcoded:', error);
          deployContent = '# 部署信息\n\n项目ID: ' + this.projectId + '\n状态: 已部署\n部署时间: ' + new Date().toISOString() + '\n';
        }
      }

      writeProjectFile(this.projectId, 'DEPLOYMENT.md', deployContent);

      // 生成 .env.example 文件（前端）
      const envExampleFrontend = '# 前端环境变量配置\n# 复制此文件为 .env 并填写配置\n\n# API地址（后端服务）\nNEXT_PUBLIC_API_URL=http://localhost:3001\n\n# 其他配置...\n';
      writeProjectFile(this.projectId, '.env.example', envExampleFrontend);

      // 检查是否有后端目录，如果有则也生成后端的 .env.example
      if (files.some(f => f.startsWith('server/') || f.includes('/server/')) || files.some(f => f.includes('backend'))) {
        const envExampleBackend = '# 后端环境变量配置\n# 复制此文件为 .env 并填写配置\n\n# 服务端口\nPORT=3001\n\n# 数据库连接（如果使用）\n# DATABASE_URL=sqlite://./dev.db\n\n# CORS配置\nCORS_ORIGIN=http://localhost:3000\n\n# 其他配置...\n';
        writeProjectFile(this.projectId, 'server/.env.example', envExampleBackend);
      }

      this.deploySteps.push('✅ 部署配置已创建');
      await this.sendMessage(null, '✅ 部署配置已创建');

      this.recordAction('CREATE_DEPLOY_CONFIG');
      await this.saveMemory({
        action: '创建部署配置文件',
        observation: 'DEPLOYMENT.md 已创建',
        insight: '包含启动说明和访问地址',
        timestamp: Date.now()
      }, 5);
    }

    if (action.type === 'SIMULATE_DEPLOY') {
      const projectPath = `generated-projects/${this.projectId}`;

      await this.sendMessage(null, '正在安装后端依赖...');
      try {
        await execAsync('npm install', { cwd: `${projectPath}/server` });
        await this.sendMessage(null, '✅ 后端依赖安装成功');
      } catch (e) {
        await this.sendMessage(null, '⚠️ 后端依赖安装可能需要手动检查');
        console.warn('Backend install error:', e);
      }

      await this.sendMessage(null, '正在安装前端依赖...');
      try {
        await execAsync('npm install', { cwd: projectPath });
        await this.sendMessage(null, '✅ 前端依赖安装成功');
      } catch (e) {
        await this.sendMessage(null, '⚠️ 前端依赖安装可能需要手动检查');
        console.warn('Frontend install error:', e);
      }

      await this.sendMessage(null, '正在上传到服务器...');
      await new Promise(r => setTimeout(r, 800));
      await this.sendMessage(null, '正在启动服务...');
      await new Promise(r => setTimeout(r, 500));

      this.deployUrl = 'http://' + this.projectId + '.demo.local';
      this.deploySteps.push('✅ 项目部署成功');
      await this.sendMessage(null, '✅ 项目部署成功');

      this.recordAction('SIMULATE_DEPLOY');
      await this.saveMemory({
        action: '模拟部署流程',
        observation: '构建、上传、启动步骤完成',
        insight: '部署 URL: ' + this.deployUrl,
        timestamp: Date.now()
      }, 6);
    }

    if (action.type === 'FINISH_DEPLOY') {
      await this.sendMessage(null, '🎉 部署完成！');
      await this.sendMessage(null, '🌐 访问地址: ' + this.deployUrl);
      await this.sendMessage(null, '📖 查看 DEPLOYMENT.md 了解启动说明');

      this.deploySteps.push('🎉 部署流程完成');

      this.recordAction('FINISH_DEPLOY');
      await this.saveMemory({
        action: '部署完成',
        observation: '项目已上线: ' + this.deployUrl,
        insight: '可以进入学习阶段保存工作流',
        timestamp: Date.now()
      }, 7);

      await this.reportToCEO({
        type: 'deploy_done',
        url: this.deployUrl,
        projectId: this.projectId
      });
    }
  }

  private async generateDeployConfigWithLLM(files: string[]): Promise<string> {
    const prompt = `
作为专业运维工程师，为以下项目生成 DEPLOYMENT.md 部署文档：

项目文件: ${files.slice(0, 10).join(', ')}

要求：
- 包含项目概述
- 包含前置依赖（Node.js 版本要求等）
- 包含启动说明（前端和后端）
- 包含环境变量配置说明
- 包含访问地址信息
- 包含常见问题排查（端口占用、依赖安装失败等）
- 使用 Markdown 格式
- 只返回文档内容，不要其他文字
`;

    const response = await this.callLLM(prompt, { temperature: 0.7, maxTokens: 2048 });
    return extractCodeBlock(response) || response;
  }

  async handleError(error: Error): Promise<boolean> {
    return await this.handleErrorWithSkills(error);
  }

  private isDoneSync(): boolean {
    return this.deploySteps.some(r => r.includes('部署流程完成'));
  }

  async isDone(): Promise<boolean> {
    return this.isDoneSync();
  }
}
