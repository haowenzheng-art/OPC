// Standalone Playwright e2e for the todo-app-v2 fallback template.
//
// Run:
//   1. cd fallback_template/e2e && npm install
//   2. Start backend (cd ../backend && npm install && npx tsx src/index.ts) on port 3001
//   3. Start frontend (cd ../frontend && npm install && npm run dev) on port 3000
//   4. node e2e.mjs
//
// What it covers:
//   - Initial load shows empty state
//   - Adding a todo via the form
//   - Toggle complete → line-through applied
//   - Filter tabs switch the visible list
//   - Delete removes a todo
//   - Clear completed wipes completed entries
//   - Reload preserves todos (sqlite is durable)
//
// Exits 0 on all-pass, 1 on any failure.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import process from 'node:process';

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:3000';
const ARTIFACTS = './artifacts';

const results = [];
let screenshotIdx = 0;

async function shot(page, label) {
  screenshotIdx += 1;
  const path = `${ARTIFACTS}/step-${String(screenshotIdx).padStart(2, '0')}-${label}.png`;
  await page.screenshot({ path, fullPage: true });
  return path;
}

async function expect(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`  ✓ ${name}`);
  } catch (err) {
    results.push({ name, ok: false, error: err instanceof Error ? err.message : String(err) });
    console.log(`  ✗ ${name}: ${err instanceof Error ? err.message : err}`);
  }
}

async function waitFor(url, timeoutMs = 60_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return;
    } catch {
      // not ready yet
    }
    await sleep(500);
  }
  throw new Error(`timeout waiting for ${url}`);
}

async function main() {
  console.log('Waiting for frontend + backend…');
  await waitFor(FRONTEND);
  await waitFor('http://localhost:3001/health');

  console.log('Launching chromium…');
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('  [pageerror]', e.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log('  [console.error]', msg.text());
  });

  await page.goto(FRONTEND);

  // === test phase ===
  console.log('\n[1] initial load');
  await expect('page renders empty state', async () => {
    await page.waitForSelector('[data-testid="todo-input"]', { timeout: 10_000 });
    await page.waitForSelector('[data-testid="empty"]', { timeout: 5_000 });
  });

  console.log('\n[2] add three todos');
  const titles = ['Write spec', 'Review PR', 'Deploy to staging'];
  for (const title of titles) {
    await page.fill('[data-testid="todo-input"]', title);
    await page.click('[data-testid="add-btn"]');
    await page.waitForFunction(
      (t) => Array.from(document.querySelectorAll('li')).some((li) => li.textContent && li.textContent.includes(t)),
      title,
      { timeout: 5_000 }
    );
  }
  await expect('all three todos visible', async () => {
    for (const t of titles) {
      const found = await page.locator('li', { hasText: t }).count();
      if (found === 0) throw new Error(`todo "${t}" not rendered`);
    }
  });
  await shot(page, 'added');

  console.log('\n[3] toggle complete');
  const reviewPr = page.locator('li', { hasText: 'Review PR' }).first();
  await reviewPr.locator('input[type="checkbox"]').check();
  await expect('"Review PR" gets line-through', async () => {
    await page.waitForFunction(
      () => {
        const items = Array.from(document.querySelectorAll('li'));
        const li = items.find((el) => el.textContent && el.textContent.includes('Review PR'));
        return li && !!li.querySelector('span.line-through');
      },
      null,
      { timeout: 3_000 }
    );
  });

  console.log('\n[4] filter active');
  await page.click('[data-testid="filter-active"]');
  await expect('completed todo hidden under "active"', async () => {
    const txt = await page.locator('body').textContent();
    if (txt && txt.includes('Review PR')) {
      // Review PR is the one we marked complete, it must NOT appear
      throw new Error('Review PR (completed) still showing under active filter');
    }
  });
  await expect('count shows 2 items left', async () => {
    const count = await page.locator('[data-testid="count"]').textContent();
    if (!count || !count.includes('2')) throw new Error(`expected "2 items left" got "${count}"`);
  });

  console.log('\n[5] filter completed → then back to all');
  await page.click('[data-testid="filter-completed"]');
  await expect('only completed visible', async () => {
    const count = await page.locator('li').count();
    if (count !== 1) throw new Error(`expected 1 completed, got ${count}`);
  });
  await page.click('[data-testid="filter-all"]');

  console.log('\n[6] delete');
  const deployItem = page.locator('li', { hasText: 'Deploy to staging' }).first();
  await deployItem.locator('[data-testid^="delete-"]').click();
  await expect('deploy todo removed', async () => {
    const txt = await page.locator('body').textContent();
    if (txt && txt.includes('Deploy to staging')) throw new Error('Deploy still present after delete');
  });

  console.log('\n[7] clear completed');
  await page.click('[data-testid="clear-completed"]');
  await expect('no completed todos left', async () => {
    await page.click('[data-testid="filter-completed"]');
    await page.waitForSelector('[data-testid="empty"]', { timeout: 3_000 });
  });
  await shot(page, 'after-clear');

  console.log('\n[8] reload preserves data (sqlite durability)');
  await page.click('[data-testid="filter-all"]');
  await page.reload();
  await expect('"Write spec" still present after reload', async () => {
    await page.waitForFunction(
      () => document.body && document.body.textContent && document.body.textContent.includes('Write spec'),
      null,
      { timeout: 10_000 }
    );
  });

  // === summary ===
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${'-'.repeat(40)}\n${results.length - failed.length}/${results.length} checks passed\n`);
  for (const r of results) {
    console.log(`  ${r.ok ? '✓' : '✗'} ${r.name}${r.error ? ' — ' + r.error : ''}`);
  }

  await browser.close();
  process.exit(failed.length === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error('e2e crashed:', err);
  process.exit(1);
});
