from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from core.models import get_db, hash_password
from core.notifier import _invalidate_cache, send_notification

from .auth import require_user

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", summary="Get settings")
async def get_settings(user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT key, value FROM settings")
    await db.close()
    all_settings = {r["key"]: r["value"] for r in rows}

    notify_keys = [
        "DINGTALK_WEBHOOK", "FEISHU_WEBHOOK", "WECOM_WEBHOOK",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "BARK_DEVICE_KEY",
        "RESEND_API_KEY", "RESEND_FROM", "MAIL_TO",
    ]
    notifications = {k: all_settings.get(f"notify_{k}", "") for k in notify_keys}

    result = {"notifications": notifications}
    if user.get("is_admin"):
        result["system"] = {
            "schedule_time": all_settings.get("global_schedule_time", "21:00"),
            "jitter_minutes": int(all_settings.get("global_jitter_minutes", "30")),
            "send_gap_min": int(all_settings.get("global_send_gap_min", "6")),
            "send_gap_max": int(all_settings.get("global_send_gap_max", "12")),
            "max_friends_per_run": int(all_settings.get("global_max_friends_per_run", "20")),
            "daily_limit": int(all_settings.get("global_daily_limit", "50")),
            "rate_limit_cooldown": int(all_settings.get("global_rate_limit_cooldown", "45")),
            "retry_delay": int(all_settings.get("global_retry_delay", "45")),
            "allow_registration": all_settings.get("global_allow_registration", "true") == "true",
        }
    return result


@router.post("/settings", summary="Update settings")
async def update_settings(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    db = await get_db()

    notify_keys = [
        "DINGTALK_WEBHOOK", "FEISHU_WEBHOOK", "WECOM_WEBHOOK",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "BARK_DEVICE_KEY",
        "RESEND_API_KEY", "RESEND_FROM", "MAIL_TO",
    ]
    for k in notify_keys:
        if k in data:
            await db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                (f"notify_{k}", str(data[k]), str(data[k])),
            )

    if user.get("is_admin"):
        sys_keys = [
            "schedule_time", "jitter_minutes", "send_gap_min", "send_gap_max",
            "max_friends_per_run", "daily_limit", "rate_limit_cooldown",
            "retry_delay", "allow_registration",
        ]
        for k in sys_keys:
            if k in data:
                await db.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                    (f"global_{k}", str(data[k]), str(data[k])),
                )
        if data.get("admin_pass"):
            await db.execute(
                "UPDATE users SET password_hash=? WHERE username='admin'",
                (hash_password(data["admin_pass"]),),
            )

    await db.commit()
    await db.close()
    _invalidate_cache()
    return {"success": True}


@router.post("/notify/test", summary="Test notification")
async def test_notify(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    channel = data.get("channel", "all")
    title = data.get("title", "Test Notification")
    content = data.get("content", "This is a test from Douyin Spark Fusion")
    _invalidate_cache()
    results = await send_notification(f"[{channel}] {title}", content)
    return {"success": True, "results": str(results)}


@router.get("/stats", summary="Dashboard stats")
async def dashboard_stats(user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    uid = user["id"]
    if user["is_admin"]:
        ac = await db.execute_fetchall("SELECT COUNT(*) as c FROM accounts")
        tk = await db.execute_fetchall("SELECT COUNT(*) as c FROM tasks WHERE is_active=1")
        fr = await db.execute_fetchall("SELECT COUNT(*) as c FROM friends")
        td = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM logs WHERE status='success' AND date(created_at)=date('now')"
        )
    else:
        ac = await db.execute_fetchall("SELECT COUNT(*) as c FROM accounts WHERE user_id=?", (uid,))
        tk = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND is_active=1", (uid,)
        )
        fr = await db.execute_fetchall("SELECT COUNT(*) as c FROM friends WHERE user_id=?", (uid,))
        td = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM logs WHERE user_id=? AND status='success' AND date(created_at)=date('now')",
            (uid,),
        )
    await db.close()
    return {
        "accounts": ac[0]["c"] if ac else 0,
        "tasks": tk[0]["c"] if tk else 0,
        "friends": fr[0]["c"] if fr else 0,
        "today_sent": td[0]["c"] if td else 0,
    }
