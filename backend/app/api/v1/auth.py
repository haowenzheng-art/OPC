"""认证 API 端点.

POST /api/v1/auth/register  {email, password}          → {user, access_token, refresh_token}
POST /api/v1/auth/login      form: username, password   → {access_token, refresh_token}  (Swagger Authorize 按钮)
POST /api/v1/auth/refresh    {refresh_token}           → {access_token, refresh_token}
GET  /api/v1/auth/me          Bearer                    → 当前用户信息
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    CurrentUser,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.user import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime


class RegisterResponse(BaseModel):
    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _make_tokens(user: User) -> tuple[str, str]:
    """为 user 签发 access + refresh. 返回 (access, refresh)."""
    return (
        create_access_token(user.id, user.email, user.role),
        create_refresh_token(user.id, user.email, user.role),
    )


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id, email=user.email, role=user.role, created_at=user.created_at
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """注册新用户. 首个用户自动设为 admin (便于初始化), 其后为 member."""
    existing = await db.execute(select(User).where(User.email == request.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered",
        )

    # 用户表为空 → 首个用户设 admin, 便于初始化
    count_stmt = select(User)
    user_count = len((await db.execute(count_stmt)).scalars().all())
    role = "admin" if user_count == 0 else "member"

    user = User(
        email=request.email,
        password_hash=hash_password(request.password),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access, refresh = _make_tokens(user)
    return RegisterResponse(
        user=_user_to_response(user),
        access_token=access,
        refresh_token=refresh,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """密码登录. form.username 字段传 email (FastAPI OAuth2 form 只有 username)."""
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(form.password, user.password_hash):
        # 不区分 "用户不存在" 和 "密码错误", 防止枚举攻击
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access, refresh = _make_tokens(user)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """用 refresh token 换新 access + refresh. 旧 refresh 一次性使用 (无状态, 靠短期 access 兜底)."""
    try:
        payload = decode_token(request.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(payload.get("sub", ""))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access, refresh = _make_tokens(user)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.get("/me", response_model=UserResponse)
async def me(current: CurrentUser):
    return _user_to_response(current)
