import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PMTools, FETools, BETools, QATools } from '../../../../src/layers/tools/mcp-client';

vi.mock('child_process', () => ({
  exec: vi.fn(),
  promisify: vi.fn().mockImplementation(() => vi.fn())
}));

describe('MCP Client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('PMTools', () => {
    it('应该有extractRequirements方法', () => {
      expect(PMTools.extractRequirements).toBeDefined();
    });

    it('应该有decomposeAndAssign方法', () => {
      expect(PMTools.decomposeAndAssign).toBeDefined();
    });

    it('应该有getProjectStatus方法', () => {
      expect(PMTools.getProjectStatus).toBeDefined();
    });

    it('应该有updateTaskStatus方法', () => {
      expect(PMTools.updateTaskStatus).toBeDefined();
    });

    it('应该有estimateEffort方法', () => {
      expect(PMTools.estimateEffort).toBeDefined();
    });
  });

  describe('FETools', () => {
    it('应该有generateComponent方法', () => {
      expect(FETools.generateComponent).toBeDefined();
    });

    it('应该有styleTailwind方法', () => {
      expect(FETools.styleTailwind).toBeDefined();
    });

    it('应该有createPageRoute方法', () => {
      expect(FETools.createPageRoute).toBeDefined();
    });

    it('应该有lintFix方法', () => {
      expect(FETools.lintFix).toBeDefined();
    });

    it('应该有generateTest方法', () => {
      expect(FETools.generateTest).toBeDefined();
    });
  });

  describe('BETools', () => {
    it('应该有createApiRoute方法', () => {
      expect(BETools.createApiRoute).toBeDefined();
    });

    it('应该有databaseMigration方法', () => {
      expect(BETools.databaseMigration).toBeDefined();
    });

    it('应该有generateValidationSchema方法', () => {
      expect(BETools.generateValidationSchema).toBeDefined();
    });

    it('应该有generateApiDocs方法', () => {
      expect(BETools.generateApiDocs).toBeDefined();
    });
  });

  describe('QATools', () => {
    it('应该有runE2ETest方法', () => {
      expect(QATools.runE2ETest).toBeDefined();
    });

    it('应该有generateUnitTest方法', () => {
      expect(QATools.generateUnitTest).toBeDefined();
    });

    it('应该有smokeTestApi方法', () => {
      expect(QATools.smokeTestApi).toBeDefined();
    });
  });
});
