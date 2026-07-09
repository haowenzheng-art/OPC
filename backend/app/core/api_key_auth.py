"""API Key authentication dependency.

支持两种认证方式:
1. Bearer JWT (前端用户)
2. X-API-Key header (程序化访问)

如果 X-API-Key 存在, 优先用 API key 认证.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, CurrentUser
from app.db.session import get_db
from app.models import Organization, User
from app.services.api_keys import verify_api_key


async def get_current_user_or_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """认证: 优先 API key, 否则 JWT.

    返回 User (如果 API key 认证, 也带上 organization 关联)
    """
    if x_api_key:
        result = await verify_api_key(db, x_api_key)
        if result is None:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": 'ApiKey realm="API"'},
            )
        user, org, _api_key_obj = result
        # 把 org 关联到 user 对象, 方便后续使用
        user.organization = org
        user.organization_id = org.id
        return user

    if not authorization or not authorization.startswith("Bearer "):
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication. Provide either Authorization: Bearer <jwt> or X-API-Key header.",
            headers={"WWW-Authenticate": 'Bearer realm="API"'},
        )

    token = authorization.removeprefix("Bearer ").strip()
    # 复用现有 JWT 验证逻辑
    from app.core.security import decode_token
    from sqlalchemy import select
    try:
        payload = decode_token(token)
    except Exception:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == int(user_id_str)))
    user = result.scalar_one_or_none()
    if user is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# 类型别名, 用于 endpoint 签名
CurrentUserOrApiKey = Annotated[User, Depends(get_current_user_or_api_key)]
