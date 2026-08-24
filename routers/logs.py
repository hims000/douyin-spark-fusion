from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from core.models import get_db

from .auth import require_user

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", summary="List logs")
async def list_logs(limit: int = 50, user: dict[str, Any] = Depends(require_user)):
    limit = min(max(limit, 1), 500)
    db = await get_db()
    if user["is_admin"]:
        rows = await db.execute_fetchall(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM logs WHERE user_id=? ORDER BY id DESC LIMIT ?", (user["id"], limit)
        )
    await db.close()
    return [dict(r) for r in rows]
