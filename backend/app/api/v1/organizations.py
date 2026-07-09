"""OPC Organization / Team API - 团队管理、成员邀请、RBAC.

当前简化版:
- owner: 组织所有者 (创建组织的人)
- admin: 可邀请成员、管理项目
- member: 创建/编辑项目
- viewer: 只读

成员邀请流程:
1. owner/admin 调 POST /api/v1/organizations/invitations 创建邀请
2. 邀请邮件发送 (当前先返回 invitation token, 后期接邮件服务)
3. 被邀请人用 token 完成注册/加入组织
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.db.session import get_db
from app.models import Organization, User

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


# ============ Schemas ============

class OrganizationResponse(BaseModel):
    id: int
    name: str
    plan: str
    credits_balance: int
    monthly_credits: int
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class MemberResponse(BaseModel):
    id: int
    email: str
    role: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class InvitationRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")


class InvitationResponse(BaseModel):
    id: int
    email: str
    role: str
    token: str
    expires_at: str


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(pattern="^(owner|admin|member|viewer)$")


# ============ Helpers ============

def _require_org_owner_or_admin(user: User) -> None:
    """权限检查: 只有 owner/admin 可执行管理操作."""
    if user.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{user.role}' not allowed; requires owner or admin",
        )


async def _ensure_user_has_org(user: User, db: AsyncSession) -> int:
    """同步 _ensure_user_has_org 逻辑 (与 billing.py 一致)."""
    if user.organization_id:
        return user.organization_id
    from app.core.plans import get_plan_limits
    limits = get_plan_limits("free")
    org = Organization(
        name=f"Org of {user.email}",
        plan="free",
        credits_balance=limits.monthly_credits,
        monthly_credits=limits.monthly_credits,
    )
    db.add(org)
    # 同步: 把 user.role 升级为 owner (创建组织的人)
    user.role = "owner"
    db.add(user)
    await db.commit()
    await db.refresh(org)
    user.organization_id = org.id
    await db.commit()
    return org.id


# ============ Endpoints ============

@router.get("/current", response_model=OrganizationResponse)
async def get_current_org(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    """获取当前用户的组织信息."""
    org_id = await _ensure_user_has_org(user, db)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=500, detail="organization not found")
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        plan=org.plan,
        credits_balance=org.credits_balance,
        monthly_credits=org.monthly_credits,
        created_at=org.created_at.isoformat(),
    )


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[MemberResponse]:
    """列出组织所有成员."""
    if not user.organization_id:
        return []
    result = await db.execute(
        select(User).where(User.organization_id == user.organization_id).order_by(User.id)
    )
    members = result.scalars().all()
    return [
        MemberResponse(
            id=m.id,
            email=m.email,
            role=m.role,
            created_at=m.created_at.isoformat(),
        )
        for m in members
    ]


@router.post("/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    req: InvitationRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvitationResponse:
    """创建成员邀请.

    流程 (简化版):
    1. 生成 token + 过期时间 (7 天)
    2. 返回 token 供 owner 通过邮件/链接分享给被邀请人
    3. 被邀请人用 token 调用 POST /api/v1/auth/register?invitation_token=xxx 加入

    后期集成邮件服务后, 这里自动发邮件.
    """
    _require_org_owner_or_admin(user)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="user has no organization")

    # 检查是否已是组织成员
    result = await db.execute(
        select(User).where(User.email == req.email).where(User.organization_id == user.organization_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="user already in organization")

    # 生成 token
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=7)

    # 当前简化: 不存 DB, 直接返回 (后期加 invitations 表)
    log.info(
        "invitation_created",
        org_id=user.organization_id,
        invited_email=req.email,
        role=req.role,
        expires_at=expires_at.isoformat(),
    )
    return InvitationResponse(
        id=0,  # 占位, 后期接 invitations 表后填真实 id
        email=req.email,
        role=req.role,
        token=token,
        expires_at=expires_at.isoformat(),
    )


@router.put("/members/{member_id}/role", response_model=MemberResponse)
async def update_member_role(
    member_id: int,
    req: UpdateMemberRoleRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MemberResponse:
    """更新成员角色. 仅 owner 可执行."""
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Only owner can change member roles")
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="user has no organization")

    member = await db.get(User, member_id)
    if member is None or member.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="member not found")

    # 不能把自己降级
    if member.id == user.id and req.role != "owner":
        raise HTTPException(status_code=400, detail="cannot demote yourself")

    member.role = req.role
    await db.commit()
    await db.refresh(member)

    return MemberResponse(
        id=member.id,
        email=member.email,
        role=member.role,
        created_at=member.created_at.isoformat(),
    )


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """移除成员. 仅 owner/admin 可执行."""
    _require_org_owner_or_admin(user)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="user has no organization")

    member = await db.get(User, member_id)
    if member is None or member.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="member not found")

    if member.role == "owner":
        raise HTTPException(status_code=400, detail="cannot remove owner")

    # 把成员移出组织 (不删除用户, 仅断开)
    member.organization_id = None
    member.role = "member"
    await db.commit()

    log.info("member_removed", org_id=user.organization_id, member_id=member_id)
