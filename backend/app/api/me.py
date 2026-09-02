"""当前用户个人数据 API：看板左侧「我的数据」侧边栏的统计聚合。

区别于管理端 /api/admin/*（全站视角），这里只聚合当前用户自己的投递，
且不受看板筛选影响——侧边栏始终展示全量个人统计。
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.db.models import Application, User
from app.domain.statuses import BY_KEY, all_defs

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/stats")
def my_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """个人投递统计（只读）：总览 + 状态机顺序的流程分布。"""
    status_counts = {
        k: c
        for k, c in db.execute(
            select(Application.current_status, func.count())
            .where(Application.user_id == user.id)
            .group_by(Application.current_status)
        )
    }
    total = sum(status_counts.values())
    terminal = sum(c for k, c in status_counts.items() if BY_KEY.get(k) and BY_KEY[k].group == "terminal")

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_new = (
        db.scalar(
            select(func.count()).select_from(Application).where(
                Application.user_id == user.id, Application.created_at >= month_start
            )
        )
        or 0
    )

    return {
        "total": total,
        "in_progress": total - terminal,
        "terminal": terminal,
        "month_new": month_new,
        "by_status": [
            {"key": d["key"], "label": d["label"], "color": d["color"], "count": status_counts.get(d["key"], 0)}
            for d in all_defs()
            if status_counts.get(d["key"], 0) > 0
        ],
    }
