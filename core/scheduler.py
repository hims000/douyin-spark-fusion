from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .automation import run_send_task
from .config import load_config

logger = logging.getLogger("fusion-spark")
TZ = "Asia/Shanghai"

_scheduler: BackgroundScheduler | None = None
_run_func: Callable | None = None


def _daily_job() -> None:
    cfg = load_config()
    jitter = max(0, int(cfg.get("jitter_minutes", 30) or 30))
    if jitter:
        delay = random.uniform(0, jitter * 60)
        logger.info("随机延迟 %.0f 秒后开始发送（抖动窗口 %s 分钟）", delay, jitter)
        time.sleep(delay)
    if _run_func:
        _run_func()


def configure(run_func: Callable) -> None:
    global _scheduler, _run_func
    _run_func = run_func
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=TZ)
        _scheduler.start()
    apply_schedule()


def apply_schedule() -> None:
    if _scheduler is None:
        return
    cfg = load_config()
    schedule_time = cfg.get("schedule_time", "21:00")
    try:
        hh, mm = schedule_time.split(":")
    except ValueError:
        hh, mm = "21", "00"
    _scheduler.add_job(
        _daily_job,
        CronTrigger(hour=int(hh), minute=int(mm), timezone=TZ),
        id="daily_send",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info("定时任务已更新：每天 %s:%s (%s)", hh, mm, TZ)


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_send")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def schedule_retry(run_func: Callable, delay_minutes: int = 45) -> None:
    if _scheduler is None:
        return
    if _scheduler.get_job("retry_send"):
        return
    run_at = datetime.now() + timedelta(minutes=delay_minutes)
    _scheduler.add_job(
        run_func,
        DateTrigger(run_date=run_at, timezone=TZ),
        id="retry_send",
        replace_existing=True,
    )
    logger.info("已安排 %s 分钟后自动补发本次失败的好友", delay_minutes)


def schedule_auto_retry(friend_name: str, message: str, cookies: list, storage_state: dict | None = None):
    """Schedule a retry 45 minutes after rate limiting."""
    if _scheduler is None:
        return
    run_at = datetime.now() + timedelta(minutes=45)
    job_id = f"retry_{friend_name}_{int(datetime.now().timestamp())}"
    _scheduler.add_job(
        lambda: run_send_task(friend_name=friend_name, message=message, cookies=cookies, storage_state=storage_state),
        'date',
        run_date=run_at,
        id=job_id,
    )
    logger.info("Scheduled retry for %s at %s", friend_name, run_at)


def cancel_retry() -> None:
    if _scheduler and _scheduler.get_job("retry_send"):
        _scheduler.remove_job("retry_send")
        logger.info("已取消待执行的补发任务")


def has_rate_limit_cooldown() -> bool:
    if _scheduler is None:
        return False
    return _scheduler.get_job("rate_limit_cooldown") is not None


def schedule_rate_limit_cooldown(minutes: int = 45) -> None:
    if _scheduler is None:
        return
    _scheduler.add_job(
        lambda: logger.info("限流冷却时间已过，可以恢复发送"),
        DateTrigger(run_date=datetime.now() + timedelta(minutes=minutes), timezone=TZ),
        id="rate_limit_cooldown",
        replace_existing=True,
    )
    logger.warning("触发限流冷却，%s 分钟内不发送", minutes)


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
