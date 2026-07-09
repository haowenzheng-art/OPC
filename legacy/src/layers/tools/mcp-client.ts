import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

const MCP_SERVER_PATH = 'C:\\Users\\19802\\Desktop\\ai-company-tools';

export interface McpToolResult {
  success: boolean;
  result?: any;
  error?: string;
}

async function callMcpTool(toolName: string, args: Record<string, any>): Promise<McpToolResult> {
  try {
    const request = {
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: {
        name: toolName,
        arguments: args
      }
    };

    const command = `cd "${MCP_SERVER_PATH}"; echo '${JSON.stringify(request)}' | node dist/index.js`;
    const { stdout, stderr } = await execAsync(command, { shell: 'powershell.exe' });

    if (stderr && !stderr.includes('DeprecationWarning')) {
      console.warn('[MCP] 警告:', stderr);
    }

    let output = stdout.trim();
    let result: any;

    try {
      result = JSON.parse(output);
    } catch {
      const jsonMatch = output.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        result = JSON.parse(jsonMatch[0]);
      } else {
        result = { result: output };
      }
    }

    if (result.error) {
      return { success: false, error: result.error.message || JSON.stringify(result.error) };
    }

    return { success: true, result: result.result };
  } catch (error) {
    return { success: false, error: String(error) };
  }
}

export const PMTools = {
  async extractRequirements(sourceText: string): Promise<McpToolResult> {
    return callMcpTool('dev.pm.extract_requirements', { sourceText });
  },

  async decomposeAndAssign(requirementId: string, maxTasks: number = 10): Promise<McpToolResult> {
    return callMcpTool('dev.pm.decompose_and_assign', { requirementId, maxTasks });
  },

  async getProjectStatus(): Promise<McpToolResult> {
    return callMcpTool('dev.pm.get_project_status', {});
  },

  async updateTaskStatus(taskId: string, newStatus: string): Promise<McpToolResult> {
    return callMcpTool('dev.pm.update_task_status', { taskId, newStatus });
  },

  async estimateEffort(description: string): Promise<McpToolResult> {
    return callMcpTool('dev.pm.estimate_effort', { requirementDescription: description });
  }
};

export const FETools = {
  async generateComponent(componentName: string, props?: string, framework: string = 'react'): Promise<McpToolResult> {
    const args: any = { componentName, framework };
    if (props) args.props = props;
    return callMcpTool('dev.fe.generate_component', args);
  },

  async styleTailwind(description: string, variant?: string): Promise<McpToolResult> {
    const args: any = { description };
    if (variant) args.variant = variant;
    return callMcpTool('dev.fe.style_tailwind', args);
  },

  async createPageRoute(pageName: string, router: string = 'nextjs', dataSource?: string): Promise<McpToolResult> {
    const args: any = { pageName, router };
    if (dataSource) args.dataSource = dataSource;
    return callMcpTool('dev.fe.create_page_route', args);
  },

  async lintFix(code: string): Promise<McpToolResult> {
    return callMcpTool('dev.fe.lint_fix', { code });
  },

  async generateTest(targetName: string, testType: string = 'unit'): Promise<McpToolResult> {
    return callMcpTool('dev.fe.generate_test', { targetName, testType });
  }
};

export const BETools = {
  async createApiRoute(entityName: string, methods?: string[], basePath: string = '/api/v1'): Promise<McpToolResult> {
    const args: any = { entityName, basePath };
    if (methods) args.methods = methods;
    return callMcpTool('dev.be.create_api_route', args);
  },

  async databaseMigration(tableName: string, fields: string, dialect: string = 'sqlite'): Promise<McpToolResult> {
    return callMcpTool('dev.be.database_migration', { tableName, fields, dialect });
  },

  async generateValidationSchema(entityName: string, fields: string, library: string = 'zod'): Promise<McpToolResult> {
    return callMcpTool('dev.be.generate_validation_schema', { entityName, fields, library });
  },

  async generateApiDocs(entityName: string, methods?: string[]): Promise<McpToolResult> {
    const args: any = { entityName };
    if (methods) args.methods = methods;
    return callMcpTool('dev.be.generate_api_docs', args);
  }
};

export const QATools = {
  async runE2ETest(testCases: string): Promise<McpToolResult> {
    return callMcpTool('dev.qa.run_e2e_test', { testCases });
  },

  async generateUnitTest(targetCode: string, testFramework: string = 'jest'): Promise<McpToolResult> {
    return callMcpTool('dev.qa.generate_unit_test', { targetCode, testFramework });
  },

  async smokeTestApi(endpoints: string): Promise<McpToolResult> {
    return callMcpTool('dev.qa.smoke_test_api', { endpoints });
  }
};
