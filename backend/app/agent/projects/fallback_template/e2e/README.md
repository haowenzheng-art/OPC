# todo-app-v2 — End-to-End Tests

Standalone Playwright script that boots the fallback template and exercises the full UI flow.

## What's covered

1. Initial load shows empty state.
2. Add three todos via the form (each persists across renders).
3. Toggle "complete" → CSS `line-through` applied.
4. Filter `active` hides completed todos + counter shows correct remaining.
5. Filter `completed` shows only completed todos.
6. Delete removes the item from DOM.
7. Clear completed empties the completed list.
8. Reload preserves todos (sqlite durability check).

## Run it

```bash
cd fallback_template/e2e
npm install                                  # playwright deps only
npm run build --prefix ../backend            # make sure backend builds clean
cd ../backend && npx tsc && npx prisma generate
node ../backend/src/index.ts &               # backend on :3001
BACKEND_PORT=3001 npm run dev --prefix ../frontend &   # frontend on :3000
node e2e.mjs                                 # run script
```

Or use the convenience script from the repo root:

```bash
python fallback_template/e2e/run_all.py
```

## Output

- Console summary in `1/8` format.
- PNG screenshots in `artifacts/step-NN-*.png` after each major phase.
- Exit code: `0` on all-pass, `1` on any failure.
