from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import load_session_token
from app.db.database import get_db
from app.db.models import User

SESSION_COOKIE = "jc_session"


def get_current_user(
    jc_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not jc_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    user_id = load_session_token(jc_session)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话已过期，请重新登录")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在")
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user
