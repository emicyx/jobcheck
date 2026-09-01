from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.db.models import Tag, User
from app.schemas.application import TagOut
from app.schemas.tag import TagCreate, TagUpdate

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return list(db.scalars(select(Tag).where(Tag.user_id == user.id).order_by(Tag.id)))


def _own_tag(db: Session, user: User, tag_id: int) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None or tag.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "标签不存在")
    return tag


@router.post("", response_model=TagOut, status_code=201)
def create_tag(payload: TagCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = payload.name.strip()
    if db.scalar(select(Tag).where(Tag.user_id == user.id, Tag.name == name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "同名标签已存在")
    tag = Tag(user_id=user.id, name=name, color=payload.color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.patch("/{tag_id}", response_model=TagOut)
def update_tag(
    tag_id: int,
    payload: TagUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tag = _own_tag(db, user, tag_id)
    provided = payload.model_fields_set
    if "name" in provided and payload.name is not None:
        name = payload.name.strip()
        dup = db.scalar(select(Tag).where(Tag.user_id == user.id, Tag.name == name, Tag.id != tag_id))
        if dup:
            raise HTTPException(status.HTTP_409_CONFLICT, "同名标签已存在")
        tag.name = name
    if "color" in provided and payload.color is not None:
        tag.color = payload.color
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tag = _own_tag(db, user, tag_id)
    db.delete(tag)
    db.commit()
    return {"ok": True}
