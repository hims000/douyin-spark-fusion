from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from core.models import get_db

from .auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", summary="List all users")
async def list_users(user: dict[str, Any] = Depends(require_admin)):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, username, is_admin, group_id, created_at FROM users ORDER BY id"
    )
    await db.close()
    return [dict(r) for r in rows]


@router.delete("/users/{user_id}", summary="Delete user")
async def delete_user(user_id: int, user: dict[str, Any] = Depends(require_admin)):
    if user_id == user["id"]:
        raise HTTPException(400, "不能删除自己")
    db = await get_db()
    await db.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM tasks WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM friends WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM logs WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM history WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM users WHERE id=?", (user_id,))
    await db.commit()
    await db.close()
    return {"success": True}


@router.get("/accounts", summary="Admin batch account list")
async def admin_accounts(user: dict[str, Any] = Depends(require_admin)):
    db = await get_db()
    rows = await db.execute_fetchall("""
        SELECT a.*, u.username as owner_username
        FROM accounts a LEFT JOIN users u ON a.user_id = u.id
        ORDER BY a.id DESC
    """)
    await db.close()
    return [dict(r) for r in rows]


@router.post("/accounts/batch-delete", summary="Batch delete accounts")
async def admin_batch_delete(req: Request, user: dict[str, Any] = Depends(require_admin)):
    data = await req.json()
    ids = data.get("account_ids", [])
    if not ids:
        raise HTTPException(400, "请选择账号")
    db = await get_db()
    placeholders = ",".join("?" * len(ids))
    await db.execute(f"DELETE FROM accounts WHERE id IN ({placeholders})", ids)
    await db.commit()
    await db.close()
    return {"success": True, "deleted": len(ids)}


@router.post("/accounts/batch-toggle", summary="Batch toggle accounts")
async def admin_batch_toggle(req: Request, user: dict[str, Any] = Depends(require_admin)):
    data = await req.json()
    ids = data.get("account_ids", [])
    active = data.get("is_active", True)
    if not ids:
        raise HTTPException(400, "请选择账号")
    db = await get_db()
    placeholders = ",".join("?" * len(ids))
    await db.execute(
        f"UPDATE accounts SET is_active=? WHERE id IN ({placeholders})", [1 if active else 0] + ids
    )
    await db.commit()
    await db.close()
    return {"success": True, "updated": len(ids)}


@router.get("/groups", summary="List user groups")
async def admin_groups(user: dict[str, Any] = Depends(require_admin)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM groups ORDER BY id")
    await db.close()
    return [dict(r) for r in rows]


@router.post("/groups", summary="Create group")
async def admin_create_group(req: Request, user: dict[str, Any] = Depends(require_admin)):
    data = await req.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "组名不能为空")
    db = await get_db()
    await db.execute("INSERT INTO groups(name) VALUES(?)", (name,))
    await db.commit()
    await db.close()
    return {"success": True}


@router.put("/groups/{group_id}", summary="Update group")
async def admin_update_group(group_id: int, req: Request, user: dict[str, Any] = Depends(require_admin)):
    data = await req.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "组名不能为空")
    db = await get_db()
    await db.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
    await db.commit()
    await db.close()
    return {"success": True}


@router.delete("/groups/{group_id}", summary="Delete group")
async def admin_delete_group(group_id: int, user: dict[str, Any] = Depends(require_admin)):
    db = await get_db()
    await db.execute("UPDATE users SET group_id=0 WHERE group_id=?", (group_id,))
    await db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    await db.commit()
    await db.close()
    return {"success": True}


@router.get("/groups/{group_id}/users", summary="List group users")
async def admin_group_users(group_id: int, user: dict[str, Any] = Depends(require_admin)):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, username, is_admin FROM users WHERE group_id=?", (group_id,)
    )
    await db.close()
    return [dict(r) for r in rows]


@router.put("/users/{user_id}/group", summary="Set user group")
async def admin_user_group(user_id: int, req: Request, user: dict[str, Any] = Depends(require_admin)):
    data = await req.json()
    group_id = data.get("group_id", 0)
    db = await get_db()
    await db.execute("UPDATE users SET group_id=? WHERE id=?", (group_id, user_id))
    await db.commit()
    await db.close()
    return {"success": True}
