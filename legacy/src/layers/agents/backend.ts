import { BaseAgent } from './base.js';
import { State, Action, AgentRole } from '../../types/index.js';
import { ensureProjectDir, writeProjectFile, listProjectFiles, extractCodeBlock } from '../tools/index.js';

interface ApiRoute {
  entity: string;
  methods: string[];
  path: string;
}

interface DataModel {
  name: string;
  fields: string;
}

export class BackendAgent extends BaseAgent {
  prd: string = '';
  done: boolean = false;
  started: boolean = false;
  apiRoutes: ApiRoute[] = [];
  dataModels: DataModel[] = [];
  filesWritten: string[] = [];

  constructor(projectId: string, prd: string = '') {
    super(projectId, 'backend');
    this.prd = prd;
  }

  setPrd(prd: string) {
    this.prd = prd;
  }

  async perceive(): Promise<State> {
    return {
      projectId: this.projectId,
      status: 'idle' as any,
      prd: this.prd,
      filesWritten: this.filesWritten
    };
  }

  async reason(state: State): Promise<Action> {
    if (!this.started && this.prd) {
      return { type: 'START_BACKEND', payload: this.prd };
    }
    if (this.started && !this.done) {
      if (this.dataModels.length > 0) {
        const model = this.dataModels.shift()!;
        return { type: 'WRITE_MODEL', payload: model };
      }
      if (this.apiRoutes.length > 0) {
        const route = this.apiRoutes.shift()!;
        return { type: 'WRITE_API', payload: route };
      }
      return { type: 'FINISH', payload: null };
    }
    return { type: 'WAIT', payload: null };
  }

  async act(action: Action) {
    if (action.type === 'START_BACKEND') {
      this.started = true;
      await this.sendMessage(null, '收到PRD！开始分析并设计API...');

      if (this.useHardcodedMode()) {
        await new Promise(r => setTimeout(r, 500));
        this.parsePRD(action.payload);
      } else {
        try {
          await this.sendMessage(null, '正在用AI分析PRD...');
          await this.parsePRDWithLLM(action.payload);
        } catch (error) {
          console.warn('[Backend] LLM failed, falling back to hardcoded:', error);
          await this.sendMessage(null, 'AI暂时不可用，使用模板模式...');
          this.parsePRD(action.payload);
        }
      }

      // 生成详细的API规范并发布给Frontend
      await this.publishApiSpecification();

      await this.createProjectStructure();

      await this.sendMessage(null, '项目结构已创建，开始写代码...');

      this.recordAction('START_BACKEND');
      await this.saveMemory({
        action: '启动后端开发，创建项目结构',
        observation: 'PRD分析完成，技术栈: Express + TypeScript',
        insight: '需要生成' + (this.dataModels.length + this.apiRoutes.length) + '个文件',
        timestamp: Date.now()
      }, 6);
    }

    if (action.type === 'WRITE_MODEL') {
      const model = action.payload as DataModel;
      await this.writeModel(model);
      this.recordAction('WRITE_MODEL:' + model.name);
      await this.saveMemory({
        action: '定义数据模型: ' + model.name,
        observation: '模型字段: ' + model.fields,
        insight: '保持数据模型简洁，支持扩展',
        timestamp: Date.now()
      }, 5);
    }

    if (action.type === 'WRITE_API') {
      const route = action.payload as ApiRoute;
      await this.writeApiRoute(route);
      this.recordAction('WRITE_API:' + route.path);
      await this.saveMemory({
        action: '编写API路由: ' + route.path,
        observation: '支持方法: ' + route.methods.join(', '),
        insight: 'RESTful设计，统一响应格式',
        timestamp: Date.now()
      }, 5);
    }

    if (action.type === 'FINISH') {
      this.done = true;
      const files = listProjectFiles(this.projectId);
      await this.sendMessage(null, `后端代码完成！共生成 ${files.length} 个文件:\n${files.slice(0, 10).map(f => '  • ' + f).join('\n')}${files.length > 10 ? `\n  ... 还有 ${files.length - 10} 个文件` : ''}`);

      this.recordAction('FINISH_BACKEND');
      await this.saveMemory({
        action: '后端开发完成',
        observation: '共生成' + files.length + '个文件',
        insight: 'API完整，可以进入测试阶段',
        timestamp: Date.now()
      }, 7);

      await this.reportToCEO({ type: 'backend_done', files });
    }
  }

  private async parsePRDWithLLM(prd: string): Promise<void> {
    const prompt = `
根据以下PRD，提取需要的数据模型和API路由：

PRD:
${prd}

请以JSON格式输出，格式如下：
{
  "dataModels": [
    {"name": "Todo", "fields": "id: number; text: string; completed: boolean; createdAt: Date;"}
  ],
  "apiRoutes": [
    {"entity": "todos", "methods": ["GET", "POST", "PUT", "DELETE"], "path": "/api/todos"}
  ]
}

只返回JSON，不要其他文字。
`;

    try {
      const response = await this.callLLM(prompt, { temperature: 0.3 });
      const jsonStr = extractCodeBlock(response) || response;
      const parsed = JSON.parse(jsonStr);

      this.dataModels = parsed.dataModels || [];
      this.apiRoutes = parsed.apiRoutes || [];

      if (this.dataModels.length === 0) {
        this.dataModels.push({
          name: 'Item',
          fields: 'id: number; name: string; description?: string;'
        });
        this.apiRoutes.push({
          entity: 'items',
          methods: ['GET', 'POST'],
          path: '/api/items'
        });
      }
    } catch {
      this.parsePRD(prd);
    }
  }

  private parsePRD(prd: string) {
    this.apiRoutes = [];
    this.dataModels = [];

    if (prd.includes('待办') || prd.includes('Todo')) {
      this.dataModels.push({
        name: 'Todo',
        fields: 'id: number; text: string; completed: boolean; createdAt: Date; updatedAt: Date;'
      });

      this.apiRoutes.push({
        entity: 'todos',
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        path: '/api/todos'
      });
    } else {
      this.dataModels.push({
        name: 'Item',
        fields: 'id: number; name: string; description?: string; createdAt: Date; updatedAt: Date;'
      });

      this.apiRoutes.push({
        entity: 'items',
        methods: ['GET', 'POST'],
        path: '/api/items'
      });
    }
  }

  private async createProjectStructure() {
    ensureProjectDir(this.projectId);

    writeProjectFile(this.projectId, 'server/package.json', JSON.stringify({
      name: `opc-server-${this.projectId}`,
      version: '0.1.0',
      scripts: {
        dev: 'tsx watch src/index.ts',
        build: 'tsc',
        start: 'node dist/index.js'
      },
      dependencies: {
        express: '^4.18.2',
        cors: '^2.8.5'
      },
      devDependencies: {
        '@types/express': '^4.17.21',
        '@types/cors': '^2.8.17',
        '@types/node': '^20.9.0',
        tsx: '^4.5.0',
        typescript: '^5.2.2'
      }
    }, null, 2));
    this.filesWritten.push('server/package.json');

    writeProjectFile(this.projectId, 'server/tsconfig.json', JSON.stringify({
      compilerOptions: {
        target: 'ES2020',
        module: 'commonjs',
        lib: ['ES2020'],
        strict: true,
        esModuleInterop: true,
        skipLibCheck: true,
        forceConsistentCasingInFileNames: true,
        outDir: './dist',
        rootDir: './src'
      },
      include: ['src/**/*'],
      exclude: ['node_modules']
    }, null, 2));
    this.filesWritten.push('server/tsconfig.json');

    // Use hardcoded index.ts for stability
    writeProjectFile(this.projectId, 'server/src/index.ts', `import express from 'express';
import cors from 'cors';
import todoRoutes from './routes/todos';

const app = express();
const PORT = process.env.PORT || 3001;

app.use(cors());
app.use(express.json());

app.use('/api/todos', todoRoutes);

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', message: 'OPC Backend is running!' });
});

app.listen(PORT, () => {
  console.log(\`Server running on http://localhost:\${PORT}\`);
});
`);
    this.filesWritten.push('server/src/index.ts');

    // Use hardcoded store for stability
    writeProjectFile(this.projectId, 'server/src/store/index.ts', `import { Todo } from '../types';

let todos: Todo[] = [];
let nextId = 1;

export const todoStore = {
  getAll: () => todos,
  getById: (id: number) => todos.find(t => t.id === id),
  create: (data: any): Todo => {
    const todo: Todo = {
      id: nextId++,
      ...data,
      createdAt: new Date(),
      updatedAt: new Date()
    } as any;
    todos.push(todo);
    return todo;
  },
  update: (id: number, updates: Partial<Todo>): Todo | null => {
    const index = todos.findIndex(t => t.id === id);
    if (index === -1) return null;
    todos[index] = { ...todos[index], ...updates, updatedAt: new Date() } as any;
    return todos[index];
  },
  delete: (id: number): boolean => {
    const index = todos.findIndex(t => t.id === id);
    if (index === -1) return false;
    todos.splice(index, 1);
    return true;
  }
};
`);
    this.filesWritten.push('server/src/store/index.ts');

    await this.sendMessage(null, '✓ 后端项目结构已创建');
  }

  private async writeModel(model: DataModel) {
    await this.sendMessage(null, `正在定义数据模型: ${model.name}...`);

    let typesContent = '';
    if (this.useHardcodedMode()) {
      typesContent = this.generateHardcodedTypes(model);
    } else {
      try {
        typesContent = await this.generateTypesWithLLM(model);
      } catch {
        typesContent = this.generateHardcodedTypes(model);
      }
    }

    writeProjectFile(this.projectId, 'server/src/types/index.ts', typesContent);
    this.filesWritten.push('server/src/types/index.ts');

    await new Promise(r => setTimeout(r, 300));
    await this.sendMessage(null, `✓ 数据模型 ${model.name} 已定义`);
  }

  private async generateTypesWithLLM(model: DataModel): Promise<string> {
    const prompt = `
根据以下数据模型定义，生成 TypeScript 类型定义文件：

数据模型: ${model.name}
字段: ${model.fields}

要求：
- 生成完整的 TypeScript 接口
- 包含 Create 和 Update 的输入类型（Pick/Partial）
- 使用 'export interface'
- 只返回代码，不要其他文字

示例格式：
export interface ModelName {
  id: number;
  field1: string;
  field2: boolean;
  createdAt: Date;
}

export type CreateModelNameInput = Pick<ModelName, 'field1' | 'field2'>;
export type UpdateModelNameInput = Partial<Pick<ModelName, 'field1' | 'field2'>>;
`;
    const response = await this.callLLM(prompt, { temperature: 0.5, maxTokens: 1024 });
    const code = extractCodeBlock(response) || response;
    return code;
  }

  private generateHardcodedTypes(model: DataModel): string {
    if (model.name === 'Todo') {
      return `export interface Todo {
  id: number;
  text: string;
  completed: boolean;
  createdAt: Date;
  updatedAt: Date;
}

export type CreateTodoInput = Pick<Todo, 'text'>;
export type UpdateTodoInput = Partial<Pick<Todo, 'text' | 'completed'>>;
`;
    }
    return `export interface ${model.name} {
  ${model.fields}
}

export type Create${model.name}Input = Partial<Omit<${model.name}, 'id' | 'createdAt'>>;
export type Update${model.name}Input = Partial<Omit<${model.name}, 'id' | 'createdAt'>>;
`;
  }

  private async writeApiRoute(route: ApiRoute) {
    await this.sendMessage(null, `正在写API路由: ${route.path}...`);

    let routeContent = '';
    if (this.useHardcodedMode()) {
      routeContent = this.generateHardcodedRoute(route);
    } else {
      try {
        routeContent = await this.generateRouteWithLLM(route);
      } catch {
        routeContent = this.generateHardcodedRoute(route);
      }
    }

    writeProjectFile(this.projectId, `server/src/routes/${route.entity}.ts`, routeContent);
    this.filesWritten.push(`server/src/routes/${route.entity}.ts`);

    await this.sendMessage(null, `✓ API路由 ${route.path} 已完成`);
  }

  private async generateRouteWithLLM(route: ApiRoute): Promise<string> {
    const entityName = route.entity.charAt(0).toUpperCase() + route.entity.slice(0, -1); // todos -> Todo
    const prompt = `
根据以下 API 路由定义，生成 Express + TypeScript 的路由代码：

实体: ${route.entity}
支持方法: ${route.methods.join(', ')}
路径: ${route.path}

要求：
- 使用 express.Router()
- 统一响应格式: { success: boolean, data?: any, error?: string }
- 从 '../types' 导入类型，从 '../store' 导入 store
- store 应该是一个对象，包含 getAll, getById, create, update, delete 方法
- 包含基本的参数验证
- 只返回代码，不要其他文字
`;
    try {
      const response = await this.callLLM(prompt, { temperature: 0.6, maxTokens: 2048 });
      const code = extractCodeBlock(response) || response;
      return code;
    } catch {
      return this.generateHardcodedRoute(route);
    }
  }

  private generateHardcodedRoute(route: ApiRoute): string {
    if (route.entity === 'todos') {
      return `import express from 'express';
import { todoStore } from '../store';
import { CreateTodoInput, UpdateTodoInput } from '../types';

const router = express.Router();

router.get('/', (req, res) => {
  const todos = todoStore.getAll();
  res.json({ success: true, data: todos });
});

router.get('/:id', (req, res) => {
  const id = parseInt(req.params.id);
  const todo = todoStore.getById(id);
  if (!todo) {
    return res.status(404).json({ success: false, error: 'Todo not found' });
  }
  res.json({ success: true, data: todo });
});

router.post('/', (req, res) => {
  const { text }: CreateTodoInput = req.body;
  if (!text?.trim()) {
    return res.status(400).json({ success: false, error: 'Text is required' });
  }
  const todo = todoStore.create(text.trim());
  res.status(201).json({ success: true, data: todo });
});

router.put('/:id', (req, res) => {
  const id = parseInt(req.params.id);
  const updates: UpdateTodoInput = req.body;
  const todo = todoStore.update(id, updates);
  if (!todo) {
    return res.status(404).json({ success: false, error: 'Todo not found' });
  }
  res.json({ success: true, data: todo });
});

router.delete('/:id', (req, res) => {
  const id = parseInt(req.params.id);
  const deleted = todoStore.delete(id);
  if (!deleted) {
    return res.status(404).json({ success: false, error: 'Todo not found' });
  }
  res.json({ success: true, message: 'Todo deleted' });
});

export default router;
`;
    }
    return `import express from 'express';
const router = express.Router();

router.get('/', (req, res) => {
  res.json({ success: true, data: [] });
});

router.post('/', (req, res) => {
  res.status(201).json({ success: true, data: req.body });
});

export default router;
`;
  }

  async handleError(error: Error): Promise<boolean> {
    return await this.handleErrorWithSkills(error);
  }

  // 生成详细的API规范并发布给Frontend
  private async publishApiSpecification() {
    await this.sendMessage(null, '📋 正在生成API规范...');

    let apiSpec = '';

    if (this.apiRoutes.length > 0 && this.dataModels.length > 0) {
      // 构建详细的API规范
      apiSpec = this.buildDetailedApiSpec();
    } else {
      // 默认的Todo API规范
      apiSpec = this.buildDefaultTodoApiSpec();
    }

    // 发送给Frontend（通过消息总线）
    await this.sendMessage('frontend', `API_SPECIFICATION:\n${apiSpec}`);
    await this.sendMessage(null, '✅ API规范已发布给Frontend！');

    // 同时在群里发一个摘要
    const summary = this.apiRoutes.length > 0
      ? `API端点：${this.apiRoutes.map(r => `${r.methods.join('/')} http://localhost:3001${r.path}`).join(', ')}`
      : `API端点：GET/POST/PUT/DELETE http://localhost:3001/api/todos`;

    await this.sendMessage(null, `📡 后端API信息：${summary}，前端请用完整URL调用！`);
  }

  // 构建详细的API规范
  private buildDetailedApiSpec(): string {
    let spec = `## 后端API规范
基础URL: http://localhost:3001
统一响应格式: { success: boolean, data?: any, error?: string }

## 数据模型
`;

    for (const model of this.dataModels) {
      spec += `\n### ${model.name}
\`\`\`typescript
${model.fields.includes('id:') ? '' : 'id: number;\n'}${model.fields}
\`\`\`
`;
    }

    spec += `\n## API端点\n`;

    for (const route of this.apiRoutes) {
      const entityName = route.entity.charAt(0).toUpperCase() + route.entity.slice(0, -1);
      spec += `\n### ${route.path}\n`;

      for (const method of route.methods) {
        spec += `- ${method} ${route.path}\n`;

        if (method === 'POST' || method === 'PUT') {
          spec += `  请求体: Partial<${entityName}>\n`;
        }
        if (method === 'GET' && !route.path.includes(':')) {
          spec += `  响应: { success: true, data: ${entityName}[] }\n`;
        }
        if (method === 'GET' && route.path.includes(':')) {
          spec += `  响应: { success: true, data: ${entityName} }\n`;
        }
      }
    }

    spec += `\n## 前端调用示例
\`\`\`typescript
// 获取列表
const res = await fetch('http://localhost:3001${this.apiRoutes[0]?.path || '/api/todos'}');
const { success, data } = await res.json();

// 创建
const createRes = await fetch('http://localhost:3001${this.apiRoutes[0]?.path || '/api/todos'}', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ /* 字段 */ })
});
\`\`\`
`;

    return spec;
  }

  // 默认的Todo API规范
  private buildDefaultTodoApiSpec(): string {
    return `## 后端API规范
基础URL: http://localhost:3001
统一响应格式: { success: boolean, data?: any, error?: string }

## 数据模型

### Todo
\`\`\`typescript
interface Todo {
  id: number;
  text: string;
  completed: boolean;
  createdAt: Date;
  updatedAt: Date;
}
\`\`\`

## API端点

### /api/todos
- GET /api/todos
  响应: { success: true, data: Todo[] }
- POST /api/todos
  请求体: { text: string }
  响应: { success: true, data: Todo }
- GET /api/todos/:id
  响应: { success: true, data: Todo }
- PUT /api/todos/:id
  请求体: { text?: string; completed?: boolean }
  响应: { success: true, data: Todo }
- DELETE /api/todos/:id
  响应: { success: true }

## 前端调用示例
\`\`\`typescript
// 获取列表
const res = await fetch('http://localhost:3001/api/todos');
const { success, data } = await res.json();

// 创建
const createRes = await fetch('http://localhost:3001/api/todos', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: 'Buy milk' })
});
const newTodo = (await createRes.json()).data;

// 更新
await fetch(\`http://localhost:3001/api/todos/\${newTodo.id}\`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ completed: true })
});
\`\`\`
`;
  }

  // 回答Frontend关于API的问题
  protected override async handleAgentQuestion(from: AgentRole, question: string) {
    if (from === 'frontend' && (question.includes('API') || question.includes('api') || question.includes('端点'))) {
      await this.sendMessage(null, `🤝 收到Frontend的API询问...`);

      const apiInfo = this.buildDetailedApiSpec();
      await this.answerAgent(from, question, apiInfo);
    } else {
      await super.handleAgentQuestion(from, question);
    }
  }

  async isDone(): Promise<boolean> {
    return this.done;
  }
}
