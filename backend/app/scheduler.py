"""轮询调度：APScheduler 每分钟扫描到期绑定，门户级限速，失败退避。"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import Binding, Portal

logger = logging.getLogger("jobcheck.scheduler")

MAX_SYNC_PER_TICK = 5
FAILURE_PAUSE_THRESHOLD = 5


def run_due_syncs() -> int:
    """同步所有到期绑定（同步函数，由调度器与测试直接调用）。"""
    now_utc = _utcnow()
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(Binding)
                .where(Binding.status == "active", Binding.next_check_at.is_not(None))
                .where(Binding.next_check_at <= now_utc)
                .order_by(Binding.next_check_at)
                .limit(MAX_SYNC_PER_TICK * 3)
            )
        )

        done = 0
        for binding in due:
            if done >= MAX_SYNC_PER_TICK:
                break
            portal: Portal | None = binding.portal
            if portal is None or not portal.enabled:
                continue
            # 门户级限速：同一门户两次轮询间隔不小于配置值
            if portal.last_polled_at and (now_utc - portal.last_polled_at).total_seconds() < settings.portal_min_interval_seconds:
                binding.next_check_at = now_utc + timedelta(seconds=settings.portal_min_interval_seconds + 10)
                db.commit()
                continue
            _sync_one(db, binding)
            done += 1
        return done


def _sync_one(db, binding: Binding) -> None:
    from app.adapters import AdapterError, SessionInvalidError
    from app.services.sync import sync_binding

    try:
        summary = sync_binding(db, binding)
        logger.info(
            "binding %s synced: fetched=%s created=%s updated=%s",
            binding.id, summary.get("fetched"), summary.get("created"), summary.get("updated"),
        )
    except SessionInvalidError as e:
        binding.status = "expired"
        binding.last_error = f"登录态失效: {e}"
        binding.next_check_at = None
        db.commit()
        logger.info("binding %s expired: %s", binding.id, e)
    except AdapterError as e:
        binding.consecutive_failures += 1
        binding.last_error = f"同步失败: {e}"
        if binding.consecutive_failures >= FAILURE_PAUSE_THRESHOLD:
            binding.status = "paused"
            binding.next_check_at = None
        else:
            # 指数退避：10min * 2^n，封顶 6h
            backoff = min(10 * (2 ** binding.consecutive_failures), 360)
            binding.next_check_at = _utcnow() + timedelta(minutes=backoff)
        db.commit()
        logger.warning("binding %s adapter error: %s", binding.id, e)
    except Exception as e:  # noqa: BLE001 调度循环不能因单条绑定崩掉
        db.rollback()
        logger.exception("binding %s unexpected error", binding.id)
        binding.next_check_at = _utcnow() + timedelta(minutes=30)
        db.commit()


def _utcnow():
    from app.services.bindings import utcnow

    return utcnow()


def start_scheduler(app) -> None:
    """在 FastAPI lifespan 中启动；RUN_SCHEDULER=0 或测试环境可关闭。"""
    if not settings.scheduler_enabled:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("apscheduler 未安装，轮询调度未启动")
        return

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: asyncio.get_event_loop().run_in_executor(None, run_due_syncs),
        "interval",
        seconds=settings.scheduler_tick_seconds,
        id="jobcheck-poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("轮询调度已启动（每 %ss）", settings.scheduler_tick_seconds)


def stop_scheduler(app) -> None:
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.shutdown(wait=False)
