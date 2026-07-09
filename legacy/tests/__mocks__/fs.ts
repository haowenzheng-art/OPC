import { vi } from 'vitest';

const mockFiles: Map<string, string> = new Map();

export const existsSync = vi.fn((path: string) => mockFiles.has(path));
export const mkdirSync = vi.fn();
export const writeFileSync = vi.fn((path: string, content: string) => {
  mockFiles.set(path, content);
});
export const readFileSync = vi.fn((path: string) => mockFiles.get(path) || '');
export const readdirSync = vi.fn().mockReturnValue([]);

export function resetMockFiles() {
  mockFiles.clear();
}

export default {
  existsSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
  resetMockFiles
};
