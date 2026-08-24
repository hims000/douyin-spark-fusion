from __future__ import annotations

import asyncio
import functools
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from core.automation import message_hash, run_send_task
from core.config import settings
from core.models import get_db
from core.scheduler import (
    has_rate_limit_cooldown,
    schedule_auto_retry,
    schedule_rate_limit_cooldown,
)

from .auth import app_error, require_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
_executor = ThreadPoolExecutor(max_workers=5)


def _validate_cron_expr(expr: str) -> bool:
    from apscheduler.triggers.cron import CronTrigger

    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    try:
        CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
        return True
    except Exception:
        return False


def _validate_input(value: str, label: str, min_len: int = 1, max_len: int = 500) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, f"{label} 必须是字符串")
    if len(value.strip()) < min_len:
        raise HTTPException(400, f"{label} 至少需要 {min_len} 个字符")
    if len(value) > max_len:
        raise HTTPException(400, f"{label} 不能超过 {max_len} 个字符")
    return value.strip()


@router.get("", summary="List tasks")
async def list_tasks(user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if user["is_admin"]:
        rows = await db.execute_fetchall("SELECT * FROM tasks ORDER BY id DESC")
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM tasks WHERE user_id=? ORDER BY id DESC", (user["id"],)
        )
    await db.close()
    return [dict(r) for r in rows]


@router.post("", summary="Create task")
async def create_task(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    account_id = int(data.get("account_id", 0))
    friend_name = _validate_input(data.get("friend_name", ""), "好友名称", min_len=1, max_len=100)
    message = str(data.get("message", ""))[:5000]
    message_type = str(data.get("message_type", "text"))
    cron_expr = str(data.get("cron_expr", "0 9 * * *"))

    if not _validate_cron_expr(cron_expr):
        raise HTTPException(400, "cron 表达式格式无效")

    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")

    cursor = await db.execute(
        "INSERT INTO tasks(account_id, user_id, friend_name, message, message_type, cron_expr) VALUES(?,?,?,?,?,?)",
        (account_id, user["id"], friend_name, message, message_type, cron_expr),
    )
    await db.commit()
    task_id = cursor.lastrowid
    await db.close()
    return {"id": task_id}


@router.put("/{task_id}", summary="Update task")
async def update_task(task_id: int, req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM tasks WHERE id=? AND user_id=?", (task_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")

    account_id = int(data.get("account_id", 0))
    friend_name = str(data.get("friend_name", ""))[:100]
    message = str(data.get("message", ""))[:5000]
    message_type = str(data.get("message_type", "text"))
    cron_expr = str(data.get("cron_expr", "0 9 * * *"))
    is_active = int(data.get("is_active", 1))

    if not _validate_cron_expr(cron_expr):
        raise HTTPException(400, "cron 表达式格式无效")

    await db.execute(
        "UPDATE tasks SET account_id=?, friend_name=?, message=?, message_type=?, cron_expr=?, is_active=? WHERE id=?",
        (account_id, friend_name, message, message_type, cron_expr, is_active, task_id),
    )
    await db.commit()
    await db.close()
    return {"success": True}


@router.delete("/{task_id}", summary="Delete task")
async def delete_task(task_id: int, user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM tasks WHERE id=? AND user_id=?", (task_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    await db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    await db.commit()
    await db.close()
    return {"success": True}


@router.post("/{task_id}/run", summary="Run task now")
async def run_task_now(task_id: int, user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    else:
        row = await db.execute_fetchall("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        await db.close()
        raise app_error("TASK_001", 404, "任务不存在")

    task = dict(row[0])
    account_id = task["account_id"]

    if not user["is_admin"]:
        acc = await db.execute_fetchall(
            "SELECT id, cookies, storage_state FROM accounts WHERE id=? AND user_id=?",
            (account_id, user["id"]),
        )
    else:
        acc = await db.execute_fetchall(
            "SELECT id, cookies, storage_state FROM accounts WHERE id=?", (account_id,)
        )
    if not acc:
        await db.close()
        raise app_error("ACCT_001", 404, "账号不存在")

    account = dict(acc[0])
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
            friend_name=task["friend_name"],
            message=task["message"],
            message_type=task.get("message_type", "text"),
            dry_run=False,
            cookies=cookies,
            storage_state=storage_state,
        ),
    )

    db = await get_db()
    await db.execute(
        "UPDATE tasks SET last_run=? WHERE id=?", (datetime.now().isoformat(), task_id)
    )
    await db.execute(
        "INSERT INTO logs(account_id, task_id, user_id, friend_name, status, message, reason) VALUES(?,?,?,?,?,?,?)",
        (account_id, task_id, user["id"], task["friend_name"], "success" if success else "error", reason, reason),
    )
    await db.commit()

    if success:
        msg_hash = message_hash(task["friend_name"], task["message"], task.get("message_type", "text"))
        await db.execute(
            "INSERT INTO history(account_id, user_id, friend_name, message, message_hash, status) VALUES(?,?,?,?,?,?)",
            (account_id, user["id"], task["friend_name"], task["message"], msg_hash, "success"),
        )
        await db.commit()

    await db.close()

    if "限流" in reason or "频繁" in reason:
        schedule_rate_limit_cooldown(settings.rate_limit_cooldown_minutes)
        schedule_auto_retry(task["friend_name"], task["message"], cookies, storage_state)

    return {"success": success, "code": "SEND_001" if not success else None, "message": reason}


@router.post("/run-all", summary="Run all tasks")
async def run_all_tasks(user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if user["is_admin"]:
        rows = await db.execute_fetchall("SELECT * FROM tasks WHERE is_active=1")
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM tasks WHERE is_active=1 AND user_id=?", (user["id"],)
        )
    await db.close()

    results = []
    for task in rows:
        task_dict = dict(task)
        try:
            account_id = task_dict["account_id"]
            db2 = await get_db()
            if not user["is_admin"]:
                acc = await db2.execute_fetchall(
                    "SELECT id, cookies, storage_state FROM accounts WHERE id=? AND user_id=?",
                    (account_id, user["id"]),
                )
            else:
                acc = await db2.execute_fetchall(
                    "SELECT id, cookies, storage_state FROM accounts WHERE id=?", (account_id,)
                )
            await db2.close()

            if not acc:
                results.append({"task_id": task_dict["id"], "success": False, "message": "账号不存在"})
                continue

            account = dict(acc[0])
            cookies = json.loads(account.get("cookies", "[]"))
            storage_state_raw = account.get("storage_state", "")
            storage_state = json.loads(storage_state_raw) if storage_state_raw else None

            if has_rate_limit_cooldown():
                results.append({"task_id": task_dict["id"], "success": False, "message": "限流冷却中"})
                continue

            loop = asyncio.get_running_loop()
            success, reason = await loop.run_in_executor(
                _executor,
                functools.partial(
                    run_send_task,
                    friend_name=task_dict["friend_name"],
                    message=task_dict["message"],
                    message_type=task_dict.get("message_type", "text"),
                    dry_run=False,
                    cookies=cookies,
                    storage_state=storage_state,
                ),
            )

            db3 = await get_db()
            await db3.execute(
                "UPDATE tasks SET last_run=? WHERE id=?", (datetime.now().isoformat(), task_dict["id"])
            )
            await db3.execute(
                "INSERT INTO logs(account_id, task_id, user_id, friend_name, status, message, reason) VALUES(?,?,?,?,?,?,?)",
                (account_id, task_dict["id"], user["id"], task_dict["friend_name"], "success" if success else "error", reason, reason),
            )
            await db3.commit()
            await db3.close()

            results.append({"task_id": task_dict["id"], "success": success, "message": reason})

            if "限流" in reason or "频繁" in reason:
                schedule_rate_limit_cooldown(settings.rate_limit_cooldown_minutes)
                break
        except Exception as e:
            results.append({"task_id": task_dict.get("id", 0), "success": False, "message": str(e)})

    return {"results": results}
