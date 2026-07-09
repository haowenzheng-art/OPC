import express from 'express';
import cors from 'cors';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import routes from './routes.js';
import { prisma } from './db.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const projectRoot = resolve(__dirname, '..');

// Ensure prisma client is generated before any query runs.
// Without this, fresh `npm install && npm run dev` would crash on first query.
const prismaClientGenerated = resolve(projectRoot, 'node_modules/.prisma/client/index.d.ts');
if (!existsSync(prismaClientGenerated)) {
  const gen = spawnSync('npx', ['--no-install', 'prisma', 'generate'], {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  });
  if (gen.status !== 0) {
    console.error('prisma generate failed');
    process.exit(1);
  }
}

const app = express();
app.use(cors());
app.use(express.json());

app.get('/health', (_req, res) => {
  res.json({ ok: true });
});

app.use('/api/v1', routes);

const PORT = Number(process.env.PORT) || 3001;
const server = app.listen(PORT, () => {
  console.log(`[fallback-backend] listening on http://localhost:${PORT}`);
});

prisma
  .$connect()
  .then(() => {
    console.log('[fallback-backend] prisma connected');
  })
  .catch((err: unknown) => {
    console.error('[fallback-backend] prisma connect failed:', err);
    server.close(() => process.exit(1));
  });
