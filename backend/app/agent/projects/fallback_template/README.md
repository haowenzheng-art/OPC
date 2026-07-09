# todo-app-v2 — Fallback Template (Level 2)

Pre-validated todo app loaded by `fallback.load_fallback_files()` when the
agent pipeline is fully stuck. Guarantees the user always gets *something that runs*.

## Layout

```
fallback_template/
├── backend/                     # Express + Prisma + SQLite + zod
│   ├── api_contract.json        # machine-readable REST contract (PUT /todos/:id)
│   ├── package.json             # @prisma/client + zod + express
│   ├── prisma/schema.prisma     # sqlite file db, single Todo model
│   ├── src/index.ts             # express + cors + /health + mount /api/v1
│   ├── src/db.ts                # prisma client + auto `prisma db push` on first boot
│   └── src/routes.ts            # GET/POST/PUT/DELETE /todos
├── frontend/                    # Next.js 14 App Router + Tailwind + TypeScript
│   ├── package.json             # next 14.2 + react 18 + tailwind 3.4
│   └── src/app/page.tsx         # full UI: filter, line-through, localStorage fallback
├── e2e/                         # standalone Playwright smoke
│   ├── e2e.mjs                  # 9 checks (add/toggle/filter/delete/clear/reload)
│   ├── run_all.py               # one-shot: install + tsc + boot + e2e
│   └── README.md                # detailed run instructions
├── design_spec.json             # palette/typography/spacing for Design Agent
├── slots.json                   # LLM-fillable UI text (Stage 4 use)
└── .gitignore                   # node_modules / .next / *.db / package-lock.json
```

## Run the template from scratch

```bash
cd backend && npm install
PORT=3001 npx tsx src/index.ts            # boots express + auto-creates data.db

cd ../frontend && npm install
NEXT_PUBLIC_API_URL=http://localhost:3001/api/v1 npm run dev
# open http://localhost:3000
```

## Run the full validation

```bash
cd e2e
npm install
npx playwright install chromium-headless-shell   # one-time
python run_all.py                                # install + tsc + boot + 9 e2e checks
```

Exit code 0 ⇒ every step (install / tsc / build / e2e) passed.

## Behavior contract (what `load_fallback_files()` returns)

Only files under `backend/` and `frontend/` are picked up by the current
`load_fallback_files()`. `design_spec.json` and `slots.json` at the root are
documentation for future Stage 4 — agent LLM will read them; runtime doesn't load them yet.

## Local storage fall-back

The frontend keeps an `LS_KEY = "fallback-todo-app:v1"` mirror in `localStorage`.
If `GET /api/v1/todos` fails on initial load, the page renders the local copy and
shows an "Offline mode" banner. New mutations while offline stay local and
re-sync when the backend comes back. Test it by stopping the backend server
mid-session — the page keeps working.
