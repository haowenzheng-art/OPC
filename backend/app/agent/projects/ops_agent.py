"""Ops Agent - 生成部署配置和说明."""
from __future__ import annotations

from typing import Any

from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.core.logging import get_logger

log = get_logger(__name__)


class OpsAgent(ProjectAgent):
    role = "ops"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.files = context.get("files", {})
        self.deploy_url = ""

    async def perceive(self) -> AgentState:
        return AgentState(
            project_id=self.project_id,
            role=self.role,
            data={"files": list(self.files.keys())},
        )

    async def reason(self, state: AgentState) -> AgentAction:
        if "DEPLOYMENT.md" not in self.files:
            return AgentAction(type="GENERATE_OPS", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "GENERATE_OPS":
            log.info("ops_generating", project_id=self.project_id)
            self.files["docker-compose.yml"] = self._docker_compose()
            self.files["backend/Dockerfile"] = self._backend_dockerfile()
            self.files["frontend/Dockerfile"] = self._frontend_dockerfile()
            self.files["DEPLOYMENT.md"] = self._deployment_md()
            self.deploy_url = "http://localhost:3002"

            self.record_action("GENERATE_OPS")
            await self.save_memory(
                observation="生成部署配置",
                insight="docker-compose 是最小可运行的部署方式",
                importance=6,
            )
            self.mark_done()

    def _docker_compose(self) -> str:
        return """version: '3.8'

services:
  backend:
    build: ./backend
    ports:
      - \"${BACKEND_PORT:-3001}:3001\"
    environment:
      - PORT=3001
      - DATABASE_URL=file:./dev.db
    volumes:
      - ./backend:/app
      - /app/node_modules

  frontend:
    build: ./frontend
    ports:
      - \"${FRONTEND_PORT:-3002}:3000\"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:3001/api/v1
    depends_on:
      - backend
"""

    def _backend_dockerfile(self) -> str:
        return """FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

EXPOSE 3001

CMD [\"npm\", \"start\"]
"""

    def _frontend_dockerfile(self) -> str:
        return """FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

FROM node:20-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/package*.json ./
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000

CMD [\"npm\", \"start\"]
"""

    def _deployment_md(self) -> str:
        return """# 部署说明

## 本地开发

后端：

```bash
cd backend
npm install
npm run dev
```

前端：

```bash
cd frontend
npm install
npm run dev -- --port 3002
```

访问:
- 前端: http://localhost:3002
- 后端 API: http://localhost:3001/api/v1

## Docker Compose

```bash
docker compose up --build
```

默认端口:
- 前端: http://localhost:3002
- 后端 API: http://localhost:3001/api/v1

如果端口冲突，可以修改环境变量：

```bash
FRONTEND_PORT=3010 BACKEND_PORT=3011 docker compose up --build
```
"""

    def get_files(self) -> dict[str, str]:
        return self.files

    def get_deploy_url(self) -> str:
        return self.deploy_url
