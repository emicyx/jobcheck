from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.db.models import Application, Tag, User
from app.schemas.application import ApplicationCreate, ApplicationDetail, ApplicationOut, ApplicationUpdate
from app.services import applications as app_service

router = APIRouter(prefix="/applications", tags=["applications"])


def _own_application(db: Session, user: User, app_id: int) -> Application:
    row = db.get(Application, app_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "投递记录不存在")
    return row


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    company: str | None = Query(default=None, description="公司名子串"),
    batch: str | None = None,
    tag_id: int | None = None,
    source: str | None = Query(default=None, pattern="^(manual|auto)$"),
    status_key: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="公司/岗位/部门关键词"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Application).where(Application.user_id == user.id)
    if company:
        stmt = stmt.where(Application.company.ilike(f"%{company}%"))
    if batch:
        stmt = stmt.where(Application.batch == batch)
    if source:
        stmt = stmt.where(Application.source == source)
    if status_key:
        stmt = stmt.where(Application.current_status == status_key)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Application.company.ilike(like)
            | Application.job_title.ilike(like)
            | Application.department.ilike(like)
        )
    if tag_id is not None:
        stmt = stmt.join(Application.tags).where(Tag.id == tag_id)
        stmt = stmt.where(Tag.user_id == user.id)
    stmt = stmt.order_by(Application.updated_at.desc())
    return list(db.scalars(stmt))


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return app_service.create_application(db, user, payload)
    except app_service.TagNotFound as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/{app_id}", response_model=ApplicationDetail)
def get_application(
    app_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _own_application(db, user, app_id)


@router.patch("/{app_id}", response_model=ApplicationOut)
def update_application(
    app_id: int,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _own_application(db, user, app_id)
    try:
        return app_service.update_application(db, row, payload)
    except app_service.TagNotFound as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/{app_id}")
def delete_application(
    app_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _own_application(db, user, app_id)
    db.delete(row)
    db.commit()
    return {"ok": True}
