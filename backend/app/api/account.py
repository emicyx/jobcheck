from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE, get_current_user
from app.core.security import verify_password
from app.db.database import get_db
from app.db.models import User

router = APIRouter(prefix="/account", tags=["account"])


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.delete("")
def delete_account(
    payload: DeleteAccountIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "密码错误")
    db.delete(user)
    db.commit()
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
