import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  ensureProjectDir,
  writeProjectFile,
  readProjectFile,
  generateProjectStructure,
  extractCodeBlock
} from '../../../src/layers/tools/file-system.js';
import * as fs from 'fs';
import * as path from 'path';

vi.mock('fs');

describe('FileSystemTools', () => {
  const testProjId = 'test-proj-1';

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fs.existsSync).mockReturnValue(true);
    vi.mocked(fs.mkdirSync).mockImplementation(() => '');
    vi.mocked(fs.writeFileSync).mockImplementation(() => {});
    vi.mocked(fs.readFileSync).mockImplementation(() => 'content');
  });

  it('should ensure project directory exists', () => {
    vi.mocked(fs.existsSync).mockReturnValue(false);

    ensureProjectDir(testProjId);

    expect(fs.mkdirSync).toHaveBeenCalled();
  });

  it('should not create directory if it already exists', () => {
    vi.mocked(fs.existsSync).mockReturnValue(true);

    ensureProjectDir(testProjId);

    expect(fs.mkdirSync).not.toHaveBeenCalled();
  });

  it('should write project files', () => {
    writeProjectFile(testProjId, 'test.txt', 'content');

    expect(fs.writeFileSync).toHaveBeenCalled();
  });

  it('should write project files with empty content', () => {
    writeProjectFile(testProjId, 'test.txt', '');

    expect(fs.writeFileSync).toHaveBeenCalled();
  });

  it('should read project files', () => {
    vi.mocked(fs.readFileSync).mockReturnValue('test content');

    const result = readProjectFile(testProjId, 'test.txt');

    expect(result).toBe('test content');
  });

  it('should extract code blocks from markdown', () => {
    const markdown = '```typescript\nconst x = 1;\n```';
    const code = extractCodeBlock(markdown);

    expect(code).toBe('const x = 1;');
  });

  it('should return content as-is if no code block', () => {
    const markdown = 'just plain text';
    const code = extractCodeBlock(markdown);

    expect(code).toBe('just plain text');
  });

  it('should extract code blocks with different language tags', () => {
    const markdown = '```javascript\nlet x = 1;\n```';
    const code = extractCodeBlock(markdown);

    expect(code).toBe('let x = 1;');
  });

  it('should generate project structure', () => {
    vi.mocked(fs.readFileSync).mockReturnValue(JSON.stringify({
      name: 'test',
      devDependencies: {}
    }));
    const writeSpy = vi.mocked(fs.writeFileSync);

    generateProjectStructure(testProjId, 'nextjs');

    expect(writeSpy).toHaveBeenCalled();
  });
});
