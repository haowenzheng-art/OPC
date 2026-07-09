"""OPC Project API - 创建、查询、下载项目."""
from __future__ import annotations

from typing import Any
import socket

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.plans import CREDITS_PER_PROJECT
from app.core.security import CurrentUser
from app.db.session import get_db
from app.models import Artifact, Organization, Project, User
from app.services.billing import (
    InsufficientCreditsError,
    PlanLimitExceededError,
    check_and_deduct_credits,
    check_plan_limits,
)
from app.worker.opc_tasks import generate_project_task

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ============ Schemas ============

class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4096)
    user_idea: str = Field(min_length=1, max_length=4096)


class ProjectResponse(BaseModel):
    id: int
    organization_id: int
    user_id: int
    name: str
    description: str | None
    user_idea: str | None
    status: str
    error: str | None  # 用户可见的失败原因 (status=failed 时填)
    deploy_url: str | None
    credits_used: int
    context: dict | None
    created_at: str
    updated_at: str
    completed_at: str | None

    model_config = ConfigDict(from_attributes=True)


class ArtifactResponse(BaseModel):
    id: int
    path: str
    type: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)


# ============ Helpers ============

def _is_redis_available() -> bool:
    try:
        with socket.create_connection(("localhost", 6379), timeout=0.5):
            return True
    except OSError:
        return False


def _project_to_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        organization_id=project.organization_id,
        user_id=project.user_id,
        name=project.name,
        description=project.description,
        user_idea=project.user_idea,
        status=project.status,
        error=project.error,
        deploy_url=project.deploy_url,
        credits_used=project.credits_used,
        context=project.context,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
    )


async def _ensure_user_has_org(user: User, db: AsyncSession) -> int:
    """如果用户还没有组织, 自动创建一个 free 组织并赋予初始 credits."""
    if user.organization_id:
        return user.organization_id
    from app.models import Organization
    from app.core.plans import get_plan_limits
    limits = get_plan_limits("free")
    org = Organization(
        name=f"Org of {user.email}",
        plan="free",
        credits_balance=limits.monthly_credits,
        monthly_credits=limits.monthly_credits,
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    user.organization_id = org.id
    await db.commit()
    return org.id


# ============ Endpoints ============

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_project(
    req: ProjectCreateRequest,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """创建项目并异步启动生成任务."""
    org_id = await _ensure_user_has_org(user, db)
    org = await db.get(Organization, org_id)  # type: ignore[arg-type]
    if org is None:
        raise HTTPException(status_code=500, detail="organization not found")

    # NEW-1 fix: 测试/开发环境完全绕过 billing (plan limits + credits check).
    # 不然 baseline/e2e 测试会因为 free 计划 3 个/月限额根本跑不起来.
    # 上线 (生产) 必须 OPC_DISABLE_BILLING=0 或不设置.
    import os as _os
    if _os.environ.get("OPC_DISABLE_BILLING", "0") == "1":
        log.info(
            "billing_bypassed",
            org_id=org_id,
            user_id=user.id,
            msg="OPC_DISABLE_BILLING=1, 跳过 plan limit + credits check (dev/test 模式)",
        )
    else:
        # 1. 检查 plan 月度限制
        try:
            await check_plan_limits(db, org, project_creation=True)
        except PlanLimitExceededError as e:
            raise HTTPException(status_code=403, detail=e.message)

        # 2. 检查并扣减 credits
        try:
            await check_and_deduct_credits(db, org, CREDITS_PER_PROJECT, project_increment=True)
        except InsufficientCreditsError as e:
            raise HTTPException(
                status_code=402,
                detail=f"Insufficient credits: required {e.required}, available {e.available}. Upgrade your plan.",
            )

    project = Project(
        organization_id=org_id,
        user_id=user.id,
        name=req.name,
        description=req.description,
        user_idea=req.user_idea,
        status="idle",
        credits_used=CREDITS_PER_PROJECT,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # 启动生成任务：优先 Celery；Redis/Celery 不可用时使用 FastAPI background task 兜底
    if _is_redis_available():
        try:
            generate_project_task.delay(project.id, req.user_idea)
        except Exception as e:
            log.warning("celery_unavailable_fallback_background_task", error=str(e), project_id=project.id)
            from app.worker.opc_tasks import _generate_project_async
            background_tasks.add_task(_generate_project_async, project.id, req.user_idea, "")
    else:
        log.warning("redis_unavailable_fallback_background_task", project_id=project.id)
        from app.worker.opc_tasks import _generate_project_async
        background_tasks.add_task(_generate_project_async, project.id, req.user_idea, "")

    # 审计日志
    from app.services.audit import record_audit
    await record_audit(
        db,
        org_id=org_id,
        user_id=user.id,
        action="project.created",
        resource_type="project",
        resource_id=str(project.id),
        payload={"name": project.name, "credits_used": CREDITS_PER_PROJECT},
    )

    log.info(
        "project_created",
        project_id=project.id,
        user_id=user.id,
        org_id=org_id,
        credits_used=CREDITS_PER_PROJECT,
    )
    return _project_to_response(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 20,
) -> list[ProjectResponse]:
    """列出当前用户组织的项目."""
    if not user.organization_id:
        return []
    result = await db.execute(
        select(Project)
        .where(Project.organization_id == user.organization_id)
        .order_by(Project.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    projects = result.scalars().all()
    return [_project_to_response(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project not found")
    return _project_to_response(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除项目和所有 artifacts."""
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project not found")
    await db.execute(delete(Artifact).where(Artifact.project_id == project_id))
    await db.delete(project)
    await db.commit()


@router.get("/{project_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactResponse]:
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project not found")
    result = await db.execute(
        select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.path)
    )
    artifacts = result.scalars().all()
    return [
        ArtifactResponse(
            id=a.id,
            path=a.path,
            type=a.type,
            created_at=a.created_at.isoformat(),
        )
        for a in artifacts
    ]


@router.get("/{project_id}/artifacts/{artifact_id}")
async def get_artifact_content(
    project_id: int,
    artifact_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project not found")
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != project_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"path": artifact.path, "content": artifact.content or ""}


# ============ E2E Test (Stage 1 后续) ============

class E2ECheckResultSchema(BaseModel):
    name: str
    passed: bool
    message: str
    duration_sec: float
    stderr: str | None = None


class E2ETestResultSchema(BaseModel):
    passed: bool
    duration_sec: float
    checks: list[E2ECheckResultSchema]
    preview_url: str | None = None


@router.post(
    "/{project_id}/test/run",
    response_model=E2ETestResultSchema,
    summary="对已生成的项目跑端到端检查 (tsc + package.json + preview HTTP 200)",
)
async def run_project_e2e_test(
    project_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> E2ETestResultSchema:
    """对已存在的项目跑 3 个 e2e check:

      1. tsc --noEmit (frontend)
      2. backend imports 是否都被 package.json 覆盖
      3. 启 dev server (backend + frontend) probe HTTP 200

    同步阻塞 2-5 分钟。返回结构化结果给前端展示。
    """
    from app.services.project_e2e import run_project_e2e_check

    project = await db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="project not found")

    log.info(
        "e2e_test_api_start",
        project_id=project_id,
        user_id=user.id,
        org_id=user.organization_id,
    )
    result = await run_project_e2e_check(project_id)
    log.info(
        "e2e_test_api_done",
        project_id=project_id,
        passed=result.passed,
        duration_sec=round(result.duration_sec, 1),
    )
    return E2ETestResultSchema(**result.to_dict())
