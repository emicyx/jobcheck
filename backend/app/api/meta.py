from fastapi import APIRouter

from app.domain import statuses
from app.schemas.application import MetaOut

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("", response_model=MetaOut)
def get_meta():
    return MetaOut(
        statuses=statuses.all_defs(),
        batches=statuses.BATCHES,
        default_status=statuses.DEFAULT_STATUS,
        default_batch=statuses.DEFAULT_BATCH,
    )
