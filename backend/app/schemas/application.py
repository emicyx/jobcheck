from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.statuses import BATCHES, DEFAULT_BATCH, DEFAULT_STATUS, VALID_KEYS


def _check_batch(v: str | None) -> str | None:
    if v is not None and v not in BATCHES:
        raise ValueError(f"batch 必须是 {BATCHES} 之一")
    return v


def _check_status(v: str | None) -> str | None:
    if v is not None and v not in VALID_KEYS:
        raise ValueError("无效的状态值")
    return v


class TagOut(BaseModel):
    id: int
    name: str
    color: str

    model_config = {"from_attributes": True}


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=128)
    job_title: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=128)
    work_location: str | None = Field(default=None, max_length=128)
    applied_at: date
    batch: str = Field(default=DEFAULT_BATCH)
    current_status: str = Field(default=DEFAULT_STATUS)
    raw_status_text: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=1000)
    tag_ids: list[int] = Field(default_factory=list)

    _batch_ok = field_validator("batch")(_check_batch)
    _status_ok = field_validator("current_status")(_check_status)


class ApplicationUpdate(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=128)
    job_title: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=128)
    work_location: str | None = Field(default=None, max_length=128)
    applied_at: date | None = None
    batch: str | None = None
    current_status: str | None = None
    raw_status_text: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=1000)
    tag_ids: list[int] | None = None

    _batch_ok = field_validator("batch")(_check_batch)
    _status_ok = field_validator("current_status")(_check_status)


class ApplicationOut(BaseModel):
    id: int
    source: Literal["manual", "auto"]
    company: str
    job_title: str
    department: str | None
    work_location: str | None
    applied_at: date
    batch: str
    current_status: str
    raw_status_text: str | None
    note: str | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut]

    model_config = {"from_attributes": True}


class HistoryOut(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    raw_status_text: str | None
    detected_at: datetime

    model_config = {"from_attributes": True}


class ApplicationDetail(ApplicationOut):
    history: list[HistoryOut]


class StatusMeta(BaseModel):
    key: str
    label: str
    group: str
    order: int
    color: str


class MetaOut(BaseModel):
    statuses: list[StatusMeta]
    batches: list[str]
    default_status: str
    default_batch: str
