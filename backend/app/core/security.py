"""认证核心: argon2 密码哈希 + JWT 签发/校验 + FastAPI 依赖.

设计决策:
- argon2 而非 bcrypt: argon2 是 OWASP 推荐, 抗 GPU/ASIC 攻击更强.
- JWT 用 HS256 对称签名: 单机部署够用; 若多实例/服务间共享, 再换 RS256 非对称.
- access 15min / refresh 7day: 短 access 减少被盗窗口, 长 refresh 兼顾 UX.
- refresh token 不存 DB: 无状态 JWT, 撤销靠短期 access; 若要强撤销, 再加 blacklist.
- get_current_user 每次请求都查 DB: 牺牲一点性能换 role/email 变更即时生效.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.user import User

# argon2 默认参数已经足够安全 (time_cost=3, memory_cost=65536 KiB, parallelism=4)
# 不自定义, 跟库默认走, 减少踩坑.
_hasher = PasswordHasher()

# tokenUrl 指向 token 端点, 用于 OpenAPI Authorize 按钮. 端点在 auth.py 实现.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _create_token(
    user_id: int, email: str, role: str, token_type: TokenType, expires_delta: timedelta
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        # jti (JWT ID): 让每次签发的 token 唯一, 即使 payload 相同 (同秒内签发).
        # 主要用于未来做 token 撤销/blacklist, 也方便测试区分.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, email: str, role: str) -> str:
    return _create_token(
        user_id,
        email,
        role,
        "access",
        timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )


def create_refresh_token(user_id: int, email: str, role: str) -> str:
    return _create_token(
        user_id,
        email,
        role,
        "refresh",
        timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def decode_token(token: str) -> dict:
    """解码并验证 JWT. 过期/签名错误抛 JWTError, 由调用方决定 HTTP 状态码."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI 依赖: 从 Bearer token 解析 user_id, 查 DB 返回 User.

    失败统一返回 401, 错误信息区分场景方便前端处理.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
    except JWTError:
        raise credentials_exc

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise credentials_exc

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise credentials_exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exc

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*allowed_roles: str):
    """角色守卫依赖. require_role('admin') 或 require_role('admin', 'member').

    用法: @router.get('/', dependencies=[Depends(require_role('admin'))])
    或:   def endpoint(user: CurrentUser = Depends(require_role('admin'))): ...
    """

    async def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not allowed; requires one of {allowed_roles}",
            )
        return user

    return _check
