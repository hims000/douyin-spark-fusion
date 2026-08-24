from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from core.automation import (
    generate_ai_message,
    message_hash,
    render_template,
    run_send_task,
)
from core.models import get_db
from core.scheduler import has_rate_limit_cooldown, schedule_rate_limit_cooldown

from .auth import app_error, require_user

router = APIRouter(prefix="/api/messages", tags=["messages"])
_executor = ThreadPoolExecutor(max_workers=5)


def _validate_input(value: str, label: str, min_len: int = 1, max_len: int = 500) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, f"{label} 必须是字符串")
    if len(value.strip()) < min_len:
        raise HTTPException(400, f"{label} 至少需要 {min_len} 个字符")
    if len(value) > max_len:
        raise HTTPException(400, f"{label} 不能超过 {max_len} 个字符")
    return value.strip()


@router.post("/send", summary="Send message")
async def send_message(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    account_id = data.get("account_id", 0)
    friend_name = _validate_input(data.get("friend_name", ""), "好友名称", min_len=1, max_len=100)
    message = str(data.get("message", ""))[:5000]
    message_type = str(data.get("message_type", "text"))
    dry_run = bool(data.get("dry_run", False))
    image_path = str(data.get("image_path", ""))[:500]
    sticker_name = str(data.get("sticker_name", ""))[:100]

    if message_type not in ("text", "image", "sticker", "random"):
        raise HTTPException(400, "不支持的消息类型")

    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id, cookies, storage_state FROM accounts WHERE id=? AND user_id=?",
            (account_id, user["id"]),
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    else:
        row = await db.execute_fetchall(
            "SELECT id, cookies, storage_state FROM accounts WHERE id=?", (account_id,)
        )
    if not row:
        await db.close()
        raise app_error("ACCT_001", 404, "账号不存在")

    account = dict(row[0])
    await db.close()

    cookies = json.loads(account.get("cookies", "[]"))
    storage_state_raw = account.get("storage_state", "")
    storage_state = json.loads(storage_state_raw) if storage_state_raw else None

    if not cookies and not storage_state:
        raise app_error("ACCT_002", 400, "请先配置登录凭据")

    if has_rate_limit_cooldown():
        raise app_error("RATE_001", 429, "当前处于限流冷却期，请稍后再试")

    loop = asyncio.get_running_loop()
    success, reason = await loop.run_in_executor(
        _executor,
        lambda: run_send_task(
            friend_name=friend_name,
            message=message,
            message_type=message_type,
            image_path=image_path,
            sticker_name=sticker_name,
            dry_run=dry_run,
            cookies=cookies,
            storage_state=storage_state,
        ),
    )

    db = await get_db()
    await db.execute(
        "INSERT INTO logs(account_id, user_id, friend_name, status, message, reason) VALUES(?,?,?,?,?,?)",
        (account_id, user["id"], friend_name, "success" if success else "error", reason, reason),
    )
    await db.commit()

    if success:
        msg_hash = message_hash(friend_name, message, message_type)
        await db.execute(
            "INSERT INTO history(account_id, user_id, friend_name, message, message_hash, status) VALUES(?,?,?,?,?,?)",
            (account_id, user["id"], friend_name, message, msg_hash, "success"),
        )
        await db.commit()

    await db.close()

    if "限流" in reason or "频繁" in reason:
        schedule_rate_limit_cooldown(45)

    return {"success": success, "code": "SEND_001" if not success else None, "message": reason}


@router.post("/preview", summary="Preview message template")
async def preview_template(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    template = str(data.get("template", ""))[:5000]
    use_ai = bool(data.get("use_ai", False))
    friend_name = str(data.get("friend", ""))
    spark_days = int(data.get("spark_days", 100))
    context = {
        "account": str(data.get("account", "")),
        "friend": friend_name,
        "yiyan": str(data.get("yiyan", "人生苦短，及时行乐")),
        "from": str(data.get("from", "一言")),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "weekday": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][datetime.now().weekday()],
        "spark_days": str(spark_days),
    }
    if use_ai and friend_name:
        ai_message = generate_ai_message(friend_name, spark_days)
        rendered = ai_message
    else:
        rendered = render_template(template, context)
    return {"rendered": rendered, "context": context}


@router.get("/history", summary="Message history")
async def message_history_endpoint(
    friend_name: str = "",
    limit: int = 50,
    user: dict[str, Any] = Depends(require_user),
):
    db = await get_db()
    if user["is_admin"]:
        if friend_name:
            rows = await db.execute_fetchall(
                "SELECT * FROM history WHERE friend_name=? ORDER BY created_at DESC LIMIT ?",
                (friend_name, limit),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM history ORDER BY created_at DESC LIMIT ?", (limit,)
            )
    else:
        if friend_name:
            rows = await db.execute_fetchall(
                "SELECT h.* FROM history h JOIN accounts a ON h.account_id=a.id WHERE h.friend_name=? AND a.user_id=? ORDER BY h.created_at DESC LIMIT ?",
                (friend_name, user["id"], limit),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT h.* FROM history h JOIN accounts a ON h.account_id=a.id WHERE a.user_id=? ORDER BY h.created_at DESC LIMIT ?",
                (user["id"], limit),
            )
    await db.close()
    return [dict(r) for r in rows]
