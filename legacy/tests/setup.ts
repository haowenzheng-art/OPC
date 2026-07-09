import { beforeAll, afterAll, vi } from 'vitest';

beforeAll(() => {
  process.env.USE_LLM = 'false';
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterAll(() => {
  vi.restoreAllMocks();
});
