from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.db.models import Portal, User

router = APIRouter(prefix="/portals", tags=["portals"])


class PortalOut(BaseModel):
    id: int
    name: str
    company: str
    provider_key: str
    domains: list[str]
    enabled: bool
    verified: bool
    note: str | None

    model_config = {"from_attributes": True}


class IdentifyIn(BaseModel):
    url: str = Field(min_length=4, max_length=500)


@router.get("", response_model=list[PortalOut])
def list_portals(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return list(db.scalars(select(Portal).where(Portal.enabled.is_(True)).order_by(Portal.id)))


@router.post("/identify", response_model=PortalOut | None)
def identify(payload: IdentifyIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    host = urlparse(payload.url if "//" in payload.url else "https://" + payload.url).netloc.lower()
    if not host:
        raise HTTPException(400, "无法从链接中解析出域名")
    # 未启用的门户也返回（前端据此显示「已识别、配置生成中」），启用的优先
    portals = list(db.scalars(select(Portal).order_by(Portal.enabled.desc(), Portal.id)))
    for portal in portals:
        for domain in portal.domains or []:
            if domain and domain.lower() in host:
                return portal
    return None
