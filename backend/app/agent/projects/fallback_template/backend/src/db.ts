// Prisma client singleton — shared by routes + index.ts.
// On boot, push schema to sqlite if the DB file is missing (idempotent).
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PrismaClient } from '@prisma/client';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const projectRoot = resolve(__dirname, '..');

// Resolve the DB file path. Default to ./data.db in project root if no env override.
// Set process.env.DATABASE_URL *before* spawning `prisma db push`, since `prisma`
// reads it from schema.prisma's `env("DATABASE_URL")`.
const dbUrl = `file:${resolve(projectRoot, 'data.db')}`;
process.env.DATABASE_URL = process.env.DATABASE_URL ?? dbUrl;

// `prisma db push` is idempotent — safe to call on every boot. We only run it
// when the DB file is missing so we don't pay the migration cost every restart.
const dbFilePath = resolve(projectRoot, 'data.db');
if (!existsSync(dbFilePath)) {
  const push = spawnSync('npx', ['--no-install', 'prisma', 'db', 'push', '--accept-data-loss', '--skip-generate'], {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  });
  if (push.status !== 0) {
    console.error('prisma db push failed');
    process.exit(1);
  }
}

export const prisma = new PrismaClient({
  log: process.env.NODE_ENV === 'production' ? ['error'] : ['warn', 'error'],
});
