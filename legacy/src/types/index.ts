// 消息类型
export interface Message {
  id: string;
  projectId: string;
  fromAgent: string;
  toAgent: string | null;
  type: 'text' | 'file' | 'action';
  content: string;
  createdAt: Date;
}

// 任务类型
export interface Task {
  id: string;
  projectId: string;
  assignee: string;
  description: string;
  status: 'todo' | 'in_progress' | 'done' | 'blocked';
  priority: 'low' | 'medium' | 'high';
  createdAt: Date;
}

// 项目状态
export type ProjectStatus = 'idle' | 'planning' | 'developing' | 'testing' | 'deploying' | 'learning' | 'done';

// Agent角色
export type AgentRole = 'pm' | 'frontend' | 'backend' | 'test' | 'ops' | 'ceo';

// 基础状态接口
export interface State {
  projectId: string;
  status: ProjectStatus;
  [key: string]: any;
}

// 行动接口
export interface Action {
  type: string;
  payload: any;
}

// ==================== 记忆系统类型 ====================

// Agent记忆内容
export interface AgentMemoryContent {
  action: string;        // 做了什么
  observation: string;   // 观察到什么
  insight: string;       // 有什么发现/感悟
  timestamp: number;
}

// 工作流步骤
export interface WorkflowStep {
  action: string;
  description: string;
  filesGenerated?: string[];
}

// Agent工作流记录
export interface AgentWorkflow {
  agentRole: AgentRole;
  steps: WorkflowStep[];
  filesGenerated: string[];
  insights: string[];
}

// 工作流模板（完整）
export interface WorkflowTemplateData {
  name: string;
  description: string;
  category: string;
  complexity: 'simple' | 'medium' | 'complex';
  pmSteps: WorkflowStep[];
  frontendSteps: WorkflowStep[];
  backendSteps: WorkflowStep[];
  testSteps: WorkflowStep[];
  opsSteps: WorkflowStep[];
}

// 技巧分类
export type SkillCategory =
  | 'file_operations'
  | 'tool_use'
  | 'debugging'
  | 'api_design'
  | 'component_design'
  | 'testing'
  | 'deployment'
  | 'general';

// 技巧内容
export interface SkillContent {
  problem: string;      // 遇到的问题
  solution: string;     // 解决方案
  codeExample?: string; // 代码示例
}

// 技巧记忆（用于保存和检索）
export interface SkillMemoryData {
  category: SkillCategory;
  title: string;
  content: SkillContent;
  tags: string[];
}
