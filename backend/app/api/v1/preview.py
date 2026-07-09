"""Project preview API - 一键启动生成项目的本地预览.

本地开发/演示用:
- POST /api/v1/projects/{id}/preview/start
- GET  /api/v1/projects/{id}/preview/status
- POST /api/v1/projects/{id}/preview/stop
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.db.session import get_db
from app.models import Project
from app.services.preview import PreviewProcess, preview_manager

router = APIRouter(prefix="/api/v1/projects", tags=["preview"])


class PreviewResponse(BaseModel):
    project_id: int
    status: str
    preview_url: str | None
    backend_port: int | None
    frontend_port: int | None
    error: str | None
    logs: list[str]


def _preview_to_response(preview: PreviewProcess | None, project_id: int) -> PreviewResponse:
    if preview is None:
        return PreviewResponse(
            project_id=project_id,
            status="stopped",
            preview_url=None,
            backend_port=None,
            frontend_port=None,
            error=None,
            logs=[],
        )
    return PreviewResponse(
        project_id=preview.project_id,
        status=preview.status,
        preview_url=preview.preview_url,
        backend_port=preview.backend_port,
        frontend_port=preview.frontend_port,
        error=preview.error,
        logs=preview.logs[-50:],
    )


async def _get_owned_project(project_id: int, user: CurrentUser, db: AsyncSession) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/{project_id}/preview/start", response_model=PreviewResponse)
async def start_preview(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    project = await _get_owned_project(project_id, user, db)
    if project.status != "done":
        raise HTTPException(status_code=400, detail="project must be done before starting preview")
    if not project.storage_prefix:
        raise HTTPException(status_code=400, detail="project has no generated artifacts")

    try:
        preview = await preview_manager.start(project.id, project.storage_prefix)
        return _preview_to_response(preview, project.id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/preview/status", response_model=PreviewResponse)
async def get_preview_status(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    await _get_owned_project(project_id, user, db)
    preview = preview_manager.get(project_id)
    return _preview_to_response(preview, project_id)


@router.post("/{project_id}/preview/stop", response_model=PreviewResponse)
async def stop_preview(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    await _get_owned_project(project_id, user, db)
    preview = await preview_manager.stop(project_id)
    return _preview_to_response(preview, project_id)
