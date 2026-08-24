from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from core.automation import _cookies_to_storage_state, fetch_chat_contacts
from core.models import get_db

from .auth import app_error, require_user

router = APIRouter(prefix="/api/friends", tags=["friends"])


@router.get("", summary="List friends")
async def list_friends(account_id: int = 0, user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if account_id:
        if not user["is_admin"]:
            acc = await db.execute_fetchall(
                "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
            )
            if not acc:
                await db.close()
                raise app_error("AUTH_003", 403, "无权操作")
        rows = await db.execute_fetchall(
            "SELECT * FROM friends WHERE account_id=? ORDER BY id DESC", (account_id,)
        )
    else:
        if user["is_admin"]:
            rows = await db.execute_fetchall("SELECT * FROM friends ORDER BY id DESC")
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM friends WHERE user_id=? ORDER BY id DESC", (user["id"],)
            )
    await db.close()
    return [dict(r) for r in rows]


@router.post("/sync", summary="Sync friends")
async def sync_friends(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    account_id = data.get("account_id", 0)

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
    cookies = json.loads(account.get("cookies", "[]"))
    storage_state_raw = account.get("storage_state", "")
    storage_state = json.loads(storage_state_raw) if storage_state_raw else None
    await db.close()

    if not cookies and not storage_state:
        raise app_error("ACCT_002", 400, "请先配置登录凭据")

    if not storage_state and cookies:
        storage_state = _cookies_to_storage_state(cookies)

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    _executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _executor, fetch_chat_contacts, None, storage_state
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        result = {"error": "同步超时，请检查 Cookie 是否有效"}

    if result.get("error"):
        return {"success": False, "error": result["error"]}

    db = await get_db()
    await db.execute("DELETE FROM friends WHERE account_id=?", (account_id,))
    for f in result.get("names", []):
        name = f.get("name", "")
        if not name:
            continue
        streak = f.get("streak", "")
        spark_days = 0
        if streak:
            try:
                spark_days = int("".join(c for c in streak if c.isdigit()) or "0")
            except ValueError:
                pass
        await db.execute(
            "INSERT INTO friends(account_id, user_id, name, spark_days) VALUES(?,?,?,?)",
            (account_id, user["id"], name, spark_days),
        )
    await db.commit()
    await db.close()
    return {"success": True, "count": len(result.get("names", []))}
