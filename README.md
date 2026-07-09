# OPC - One Prompt Creates

**OPC** is an enterprise-grade, multi-agent system that turns a one-sentence idea into a runnable application.

Describe what you want, and OPC's AI agents (PM, Frontend, Backend, Test, Ops, CEO) collaborate to generate:

- A complete Product Requirements Document (PRD)
- Next.js frontend code
- Express + TypeScript backend code
- Database schema
- Tests and deployment configuration
- A ready-to-run Docker Compose setup

## Architecture

```
opc/
├── backend/          Python/FastAPI + PostgreSQL + Redis + Celery
├── frontend/         Next.js + Tailwind CSS + shadcn/ui
├── docker-compose.yml
└── legacy/           Original TypeScript prototype (archived)
```

## Tech Stack

**Backend**
- Python 3.11+
- FastAPI
- SQLAlchemy + asyncpg + PostgreSQL
- Celery + Redis
- Anthropic SDK (Claude-compatible APIs)
- Alembic migrations

**Frontend**
- Next.js 16
- TypeScript
- Tailwind CSS v4
- shadcn/ui

## Quick Start

### Prerequisites

- Docker + Docker Compose
- Or local: Python 3.11+, Node.js 20+, PostgreSQL, Redis

### Docker Compose (Recommended)

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and add your LLM API key

docker compose up --build
```

Then open http://localhost:3000

### Local Development

**Backend**

```bash
cd backend
cp .env.example .env
# Edit .env with your LLM API key

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .

# Start PostgreSQL and Redis, then run migrations
alembic upgrade head

# Terminal 1: API server
uvicorn app.main:app --reload --port 8000

# Terminal 2: Celery worker
celery -A app.worker.celery_app worker --loglevel=info
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

## Usage

1. Register an account at `/register`
2. Go to Dashboard and click "New Project"
3. Describe your idea, e.g.:
   > "I want a simple todo app where users can create tasks, mark them complete, and filter by status."
4. OPC will create a project and run AI agents asynchronously
5. Refresh the project page to see generated files, PRD, code, and deployment instructions

## Project Status

This is the enterprise rewrite of the original OPC prototype. The legacy TypeScript version is archived in `legacy/`.

Current capabilities:
- [x] Multi-agent project generation (CEO, PM, Frontend, Backend, Test, Ops)
- [x] FastAPI backend with PostgreSQL, Redis, Celery
- [x] Next.js frontend with auth, dashboard, project studio
- [x] Local file storage for generated artifacts
- [x] JWT authentication
- [x] Organization-based multi-tenancy scaffold

In progress:
- [ ] Stripe subscription integration
- [ ] Usage-based credits and billing
- [ ] Real deployment to Vercel/Railway/Docker hosts
- [ ] Real-time WebSocket/SSE project status updates
- [ ] Template marketplace

## License

MIT
