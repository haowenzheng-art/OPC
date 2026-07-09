import { BaseAgent } from './base.js';
import { State, Action, AgentRole } from '../../types/index.js';
import { generateProjectStructure, writeProjectFile, extractCodeBlock, listProjectFiles } from '../tools/index.js';

interface Page {
  name: string;
  description: string;
}

interface Component {
  name: string;
  description: string;
}

export class FrontendAgent extends BaseAgent {
  prd: string = '';
  done: boolean = false;
  started: boolean = false;
  waitingForApiSpec: boolean = true;
  apiSpecification: string = '';
  pages: Page[] = [];
  components: Component[] = [];
  filesWritten: string[] = [];

  constructor(projectId: string, prd: string = '') {
    super(projectId, 'frontend');
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
      return { type: 'START_FRONTEND', payload: this.prd };
    }
    if (this.started && this.waitingForApiSpec) {
      // 检查是否收到了API规范
      if (this.apiSpecification) {
        this.waitingForApiSpec = false;
        await this.sendMessage(null, '✅ 收到API规范！开始生成代码...');
      } else {
        return { type: 'WAIT', payload: '等待后端API规范...' };
      }
    }
    if (this.started && !this.done && !this.waitingForApiSpec) {
      if (this.pages.length > 0) {
        const page = this.pages.shift()!;
        return { type: 'WRITE_PAGE', payload: page };
      }
      if (this.components.length > 0) {
        const component = this.components.shift()!;
        return { type: 'WRITE_COMPONENT', payload: component };
      }
      return { type: 'FINISH', payload: null };
    }
    return { type: 'WAIT', payload: null };
  }

  async act(action: Action) {
    if (action.type === 'START_FRONTEND') {
      this.started = true;
      await this.sendMessage(null, '收到PRD！等待后端API规范...');

      // 先解析PRD，同时等待API规范
      if (this.useHardcodedMode()) {
        await new Promise(r => setTimeout(r, 500));
        this.parsePRD(action.payload);
      } else {
        try {
          await this.sendMessage(null, '正在用AI分析PRD...');
          await this.parsePRDWithLLM(action.payload);
        } catch (error) {
          console.warn('[Frontend] LLM failed, falling back to hardcoded:', error);
          await this.sendMessage(null, 'AI暂时不可用，使用模板模式...');
          this.parsePRD(action.payload);
        }
      }

      generateProjectStructure(this.projectId, 'nextjs');

      this.recordAction('START_FRONTEND');
      await this.saveMemory({
        action: '启动前端开发，创建项目结构',
        observation: 'PRD分析完成，项目类型: Next.js',
        insight: '需要生成' + (this.pages.length + this.components.length) + '个文件',
        timestamp: Date.now()
      }, 6);
    }

    if (action.type === 'WRITE_PAGE') {
      const page = action.payload as Page;
      await this.writePage(page);
      this.recordAction('WRITE_PAGE:' + page.name);
      await this.saveMemory({
        action: '编写页面: ' + page.name,
        observation: '页面描述: ' + page.description,
        insight: '使用React函数组件 + Tailwind样式',
        timestamp: Date.now()
      }, 5);
    }

    if (action.type === 'WRITE_COMPONENT') {
      const component = action.payload as Component;
      await this.writeComponent(component);
      this.recordAction('WRITE_COMPONENT:' + component.name);
      await this.saveMemory({
        action: '编写组件: ' + component.name,
        observation: '组件描述: ' + component.description,
        insight: '保持组件简单且可复用',
        timestamp: Date.now()
      }, 5);
    }

    if (action.type === 'FINISH') {
      this.done = true;
      const files = listProjectFiles(this.projectId);
      await this.sendMessage(null, `前端代码完成！共生成 ${files.length} 个文件:\n${files.slice(0, 10).map(f => '  • ' + f).join('\n')}${files.length > 10 ? `\n  ... 还有 ${files.length - 10} 个文件` : ''}`);

      this.recordAction('FINISH_FRONTEND');
      await this.saveMemory({
        action: '前端开发完成',
        observation: '共生成' + files.length + '个文件',
        insight: '文件写入成功，可以进入测试阶段',
        timestamp: Date.now()
      }, 7);

      await this.reportToCEO({ type: 'frontend_done', files });
    }
  }

  private async parsePRDWithLLM(prd: string): Promise<void> {
    const prompt = `
根据以下PRD，提取需要创建的页面和组件：

PRD:
${prd}

请以JSON格式输出，格式如下：
{
  "pages": [
    {"name": "Home", "description": "首页描述"},
    {"name": "About", "description": "关于页面描述"}
  ],
  "components": [
    {"name": "Header", "description": "头部组件"},
    {"name": "Footer", "description": "底部组件"}
  ]
}

只返回JSON，不要其他文字。
`;

    try {
      const response = await this.callLLM(prompt, { temperature: 0.3 });
      const jsonStr = extractCodeBlock(response) || response;
      const parsed = JSON.parse(jsonStr);

      this.pages = parsed.pages || [];
      this.components = parsed.components || [];

      if (this.pages.length === 0) {
        this.pages.push({ name: 'Home', description: '应用首页' });
      }
    } catch {
      this.parsePRD(prd);
    }
  }

  private parsePRD(prd: string) {
    this.pages = [];
    this.components = [];

    if (prd.includes('待办') || prd.includes('Todo')) {
      this.pages.push({ name: 'Home', description: '待办清单首页，显示所有待办事项' });
      this.components.push({ name: 'TodoList', description: '显示待办事项列表' });
      this.components.push({ name: 'TodoInput', description: '添加新待办的输入框' });
    } else {
      this.pages.push({ name: 'Home', description: '应用首页' });
      this.components.push({ name: 'Header', description: '页面头部组件' });
      this.components.push({ name: 'Footer', description: '页面底部组件' });
    }
  }

  private async writePage(page: Page) {
    await this.sendMessage(null, `正在写页面: ${page.name}...`);

    let content = '';

    if (this.useHardcodedMode()) {
      content = this.generateHardcodedPage(page);
    } else {
      try {
        content = await this.generatePageWithLLM(page);
      } catch {
        content = this.generateHardcodedPage(page);
      }
    }

    const filePath = page.name === 'Home' ? 'app/page.tsx' : `app/${page.name.toLowerCase()}/page.tsx`;
    writeProjectFile(this.projectId, filePath, content);
    this.filesWritten.push(filePath);
    await this.sendMessage(null, `✓ 页面 ${page.name} 已完成`);
  }

  private async generatePageWithLLM(page: Page): Promise<string> {
    const apiSpec = this.apiSpecification || '后端API规范尚未收到，使用默认的Todo API。';

    const prompt = `
根据以下需求，编写一个Next.js 14页面组件（使用app router）：

页面名称: ${page.name}
页面描述: ${page.description}
PRD概要: ${this.prd.substring(0, 200)}

## 后端API规范（必须严格遵守）
${apiSpec}

## 重要要求
1. 必须严格按照上面的API规范来调用接口
2. 必须使用完整URL：http://localhost:3001/...（不要相对路径）
3. 必须使用API规范中定义的TypeScript类型
4. 必须按照API规范中的请求/响应格式来处理数据
5. 使用React函数组件 + TypeScript
6. 使用Tailwind CSS样式
7. 美观、现代的UI设计
8. 包含必要的状态管理（useState）和useEffect（用于获取数据）
9. 使用 'use client' 指令（需要客户端交互）

只返回代码，不要其他文字。
`;
    const response = await this.callLLM(prompt, { temperature: 0.7, maxTokens: 2048 });
    const code = extractCodeBlock(response) || response;
    return code;
  }

  private generateHardcodedPage(page: Page): string {
    if (page.name === 'Home' && (this.prd.includes('待办') || this.prd.includes('Todo'))) {
      return `'use client';

import { useState, useEffect } from 'react';
import TodoList from '@/components/TodoList';
import TodoInput from '@/components/TodoInput';

interface Todo {
  id: number;
  text: string;
  completed: boolean;
  createdAt: string;
  updatedAt: string;
}

export default function Home() {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    fetchTodos();
  }, []);

  const fetchTodos = async () => {
    try {
      const res = await fetch('http://localhost:3001/api/todos');
      if (res.ok) {
        const data = await res.json();
        setTodos(data.data || data);
      }
    } catch (e) {
      console.error('Failed to fetch todos:', e);
    } finally {
      setIsLoading(false);
    }
  };

  const addTodo = async (text: string) => {
    try {
      const res = await fetch('http://localhost:3001/api/todos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      if (res.ok) {
        const data = await res.json();
        setTodos([data.data || data, ...todos]);
      }
    } catch (e) {
      console.error('Failed to add todo:', e);
    }
  };

  const toggleTodo = async (id: number, completed: boolean) => {
    try {
      const res = await fetch(\`http://localhost:3001/api/todos/\${id}\`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ completed: !completed })
      });
      if (res.ok) {
        const data = await res.json();
        setTodos(todos.map(t => t.id === id ? (data.data || data) : t));
      }
    } catch (e) {
      console.error('Failed to update todo:', e);
    }
  };

  const deleteTodo = async (id: number) => {
    try {
      const res = await fetch(\`http://localhost:3001/api/todos/\${id}\`, {
        method: 'DELETE'
      });
      if (res.ok) {
        setTodos(todos.filter(t => t.id !== id));
      }
    } catch (e) {
      console.error('Failed to delete todo:', e);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 p-8">
      <div className="max-w-2xl mx-auto">
        <h1 className="text-4xl font-bold text-gray-800 mb-8 text-center">
          待办清单
        </h1>
        <TodoInput onAdd={addTodo} />
        {isLoading ? (
          <div className="text-center py-12">加载中...</div>
        ) : (
          <TodoList todos={todos} onToggle={toggleTodo} onDelete={deleteTodo} />
        )}
      </div>
    </div>
  );
}
`;
    }
    return `export default function ${page.name}() {
  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <h1 className="text-3xl font-bold text-gray-800">${page.name}</h1>
      <p className="mt-4 text-gray-600">${page.description}</p>
    </div>
  );
}
`;
  }

  private async writeComponent(component: Component) {
    await this.sendMessage(null, `正在写组件: ${component.name}...`);

    let content = '';

    if (this.useHardcodedMode()) {
      content = this.generateHardcodedComponent(component);
    } else {
      try {
        content = await this.generateComponentWithLLM(component);
      } catch {
        content = this.generateHardcodedComponent(component);
      }
    }

    const filePath = `components/${component.name}.tsx`;
    writeProjectFile(this.projectId, filePath, content);
    this.filesWritten.push(filePath);
    await this.sendMessage(null, `✓ 组件 ${component.name} 已完成`);
  }

  private async generateComponentWithLLM(component: Component): Promise<string> {
    const prompt = `
根据以下需求，编写一个React组件：

组件名称: ${component.name}
组件描述: ${component.description}

要求：
- 使用TypeScript
- 使用Tailwind CSS样式
- 简洁、可复用
- 包含必要的类型定义

只返回代码，不要其他文字。
`;
    const response = await this.callLLM(prompt, { temperature: 0.7, maxTokens: 1024 });
    const code = extractCodeBlock(response) || response;
    return code;
  }

  private generateHardcodedComponent(component: Component): string {
    if (component.name === 'TodoList') {
      return `'use client';

interface Todo {
  id: number;
  text: string;
  completed: boolean;
}

interface TodoListProps {
  todos: Todo[];
  onToggle: (id: number) => void;
  onDelete: (id: number) => void;
}

export default function TodoList({ todos, onToggle, onDelete }: TodoListProps) {
  if (todos.length === 0) {
    return (
      <div className="mt-8 text-center text-gray-500">
        <p className="text-lg">还没有待办事项</p>
        <p className="mt-2">添加一个开始吧!</p>
      </div>
    );
  }

  return (
    <ul className="mt-6 space-y-3">
      {todos.map(todo => (
        <li key={todo.id} className="flex items-center justify-between bg-white rounded-lg shadow-sm p-4 hover:shadow-md transition-shadow">
          <button
            onClick={() => onToggle(todo.id)}
            className={\`flex items-center gap-3 flex-1 \${todo.completed ? 'text-gray-400' : 'text-gray-800'}\`}
          >
            <div className={\`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors \${todo.completed ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 hover:border-blue-400'}\`}>
              {todo.completed && '✓'}
            </div>
            <span className={todo.completed ? 'line-through' : ''}>{todo.text}</span>
          </button>
          <button
            onClick={() => onDelete(todo.id)}
            className="ml-4 text-red-500 hover:text-red-700 px-3 py-1 rounded hover:bg-red-50"
          >
            删除
          </button>
        </li>
      ))}
    </ul>
  );
}
`;
    }
    if (component.name === 'TodoInput') {
      return `'use client';

import { useState } from 'react';

interface TodoInputProps {
  onAdd: (text: string) => void;
}

export default function TodoInput({ onAdd }: TodoInputProps) {
  const [input, setInput] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim()) {
      onAdd(input.trim());
      setInput('');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex gap-3">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="添加新的待办事项..."
        className="flex-1 px-4 py-3 rounded-lg border border-gray-200 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      <button
        type="submit"
        disabled={!input.trim()}
        className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        添加
      </button>
    </form>
  );
}
`;
    }
    if (component.name === 'Header') {
      return `export default function Header() {
  return (
    <header className="bg-white shadow-sm border-b border-gray-200">
      <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">OPC App</h1>
        <nav className="flex gap-4">
          <a href="/" className="text-gray-600 hover:text-blue-600">首页</a>
        </nav>
      </div>
    </header>
  );
}
`;
    }
    if (component.name === 'Footer') {
      return `export default function Footer() {
  return (
    <footer className="bg-gray-50 border-t border-gray-200 mt-auto">
      <div className="max-w-6xl mx-auto px-4 py-6 text-center text-gray-500">
        <p>Generated by OPC Agent System</p>
      </div>
    </footer>
  );
}
`;
    }
    return this.createGenericComponent(component.name, component.description);
  }

  private createGenericComponent(name: string, description: string): string {
    return `interface ${name}Props {
  children?: React.ReactNode;
}

export default function ${name}({ children }: ${name}Props) {
  return (
    <div className="p-4">
      {children || <p>${description}</p>}
    </div>
  );
}
`;
  }

  async handleError(error: Error): Promise<boolean> {
    return await this.handleErrorWithSkills(error);
  }

  // 处理收到的消息，特别是来自Backend的API规范
  protected override handleMessage(from: AgentRole, content: string) {
    if (from === 'backend' && content.includes('API_SPECIFICATION')) {
      // 提取API规范
      const spec = content.replace('API_SPECIFICATION:\n', '');
      this.apiSpecification = spec;
      console.log('[Frontend] 收到API规范:', spec.substring(0, 200) + '...');
    }
    // 调用父类方法处理普通消息
    super.handleMessage(from, content);
  }

  async isDone(): Promise<boolean> {
    return this.done;
  }
}
