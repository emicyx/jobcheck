from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AppStatusHistory, Application, Tag, User
from app.domain import statuses
from app.schemas.application import ApplicationCreate, ApplicationUpdate


class TagNotFound(Exception):
    pass


def _resolve_tags(db: Session, user: User, tag_ids: list[int]) -> list[Tag]:
    if not tag_ids:
        return []
    tags = list(db.scalars(select(Tag).where(Tag.user_id == user.id, Tag.id.in_(tag_ids))))
    if len(tags) != len(set(tag_ids)):
        raise TagNotFound("包含不存在或不属于当前用户的标签")
    return tags


def create_application(db: Session, user: User, data: ApplicationCreate) -> Application:
    tags = _resolve_tags(db, user, data.tag_ids)
    app_row = Application(
        user_id=user.id,
        source="manual",
        company=data.company.strip(),
        job_title=data.job_title.strip(),
        department=data.department,
        work_location=data.work_location,
        applied_at=data.applied_at,
        batch=data.batch,
        current_status=data.current_status,
        raw_status_text=data.raw_status_text,
        note=data.note,
        confidence="manual",
        tags=tags,
    )
    app_row.history.append(
        AppStatusHistory(
            from_status=None,
            to_status=data.current_status,
            raw_status_text=data.raw_status_text or statuses.label(data.current_status),
        )
    )
    db.add(app_row)
    db.commit()
    db.refresh(app_row)
    return app_row


def update_application(db: Session, app_row: Application, data: ApplicationUpdate) -> Application:
    provided = data.model_fields_set

    for field in ("company", "job_title", "department", "work_location", "applied_at", "batch", "note"):
        if field in provided and getattr(data, field) is not None:
            value = getattr(data, field)
            setattr(app_row, field, value.strip() if isinstance(value, str) else value)
    # 允许显式清空的可空文本字段
    for field in ("raw_status_text", "note"):
        if field in provided:
            setattr(app_row, field, getattr(data, field))

    if "tag_ids" in provided and data.tag_ids is not None:
        app_row.tags = _resolve_tags(db, app_row.user, data.tag_ids)

    if "current_status" in provided and data.current_status is not None:
        old = app_row.current_status
        new = data.current_status
        if new != old:
            app_row.current_status = new
            app_row.history.append(
                AppStatusHistory(
                    from_status=old,
                    to_status=new,
                    raw_status_text=data.raw_status_text or statuses.label(new),
                )
            )

    db.commit()
    db.refresh(app_row)
    return app_row
