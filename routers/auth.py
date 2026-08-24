from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from core.config import settings
from core.models import get_db, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    confirm_password: str = ""
    invite_code: str = ""


def _validate_input(value: str, label: str, min_len: int = 1, max_len: int = 500) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, f"{label} 必须是字符串")
    if len(value.strip()) < min_len:
        raise HTTPException(400, f"{label} 至少需要 {min_len} 个字符")
    if len(value) > max_len:
        raise HTTPException(400, f"{label} 不能超过 {max_len} 个字符")
    return value.strip()


def app_error(code: str, status_code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


async def _session_lookup(token: str) -> dict[str, Any]:
    if not token:
        return {}
    db = await get_db()
    row = await db.execute_fetchall("SELECT * FROM sessions WHERE token=?", (token,))
    await db.close()
    if row:
        s = dict(row[0])
        return {"id": s["user_id"], "username": s["username"], "is_admin": bool(s["is_admin"])}
    return {}


async def get_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    if not credentials:
        return {}
    return await _session_lookup(credentials.credentials)


async def require_user(user: dict[str, Any] = Depends(get_user)) -> dict[str, Any]:
    if not user:
        raise app_error("AUTH_002", 401, "请先登录")
    return user


async def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise app_error("AUTH_003", 403, "需要管理员权限")
    return user


async def _create_session(user_id: int, username: str, is_admin: bool) -> tuple[str, dict[str, Any]]:
    token = secrets.token_hex(32)
    user_data = {"id": user_id, "username": username, "is_admin": is_admin}
    db = await get_db()
    await db.execute(
        "INSERT INTO sessions(token, user_id, username, is_admin) VALUES(?,?,?,?)",
        (token, user_id, username, 1 if is_admin else 0),
    )
    await db.commit()
    await db.close()
    return token, user_data


@router.post("/login", summary="User login")
async def login(req: LoginRequest):
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT * FROM users WHERE username=?", (req.username,)
    )
    await db.close()
    if not row:
        raise app_error("AUTH_001", 401, "用户名或密码错误")
    user = dict(row[0])
    if not verify_password(req.password, user["password_hash"]):
        raise app_error("AUTH_001", 401, "用户名或密码错误")
    token, user_data = await _create_session(user["id"], user["username"], bool(user["is_admin"]))
    return {"token": token, "user": user_data}


@router.post("/register", summary="User registration")
async def register(req: RegisterRequest):
    if not settings.allow_registration:
        raise app_error("AUTH_003", 403, "注册功能已关闭")
    username = _validate_input(req.username, "用户名", min_len=2, max_len=50)
    password = _validate_input(req.password, "密码", min_len=4, max_len=100)
    if req.password != req.confirm_password:
        raise HTTPException(400, "两次密码不一致")
    db = await get_db()
    existing = await db.execute_fetchall(
        "SELECT id FROM users WHERE username=?", (username,)
    )
    if existing:
        await db.close()
        raise HTTPException(400, "用户名已存在")

    from core.models import get_setting

    invite_only = (await get_setting("global_invite_only", "false")).lower() == "true"
    if invite_only:
        if not req.invite_code:
            await db.close()
            raise HTTPException(400, "需要邀请码才能注册")
        code_row = await db.execute_fetchall(
            "SELECT id, is_used FROM invite_codes WHERE code=?", (req.invite_code.upper(),)
        )
        if not code_row:
            await db.close()
            raise HTTPException(400, "邀请码无效")
        if code_row[0]["is_used"]:
            await db.close()
            raise HTTPException(400, "邀请码已被使用")

    await db.execute(
        "INSERT INTO users(username, password_hash) VALUES(?,?)",
        (username, hash_password(password)),
    )
    await db.commit()
    user_id = (await db.execute_fetchall("SELECT last_insert_rowid() as id"))[0]["id"]

    if invite_only:
        await db.execute(
            "UPDATE invite_codes SET is_used=1, used_by=? WHERE code=?",
            (user_id, req.invite_code.upper()),
        )
        await db.commit()

    await db.close()
    token, user_data = await _create_session(user_id, username, False)
    return {"token": token, "user": user_data}


@router.get("/me", summary="Get current user")
async def me(user: dict[str, Any] = Depends(require_user)):
    return {"user": user}


@router.post("/logout", summary="User logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials and credentials.credentials:
        db = await get_db()
        await db.execute("DELETE FROM sessions WHERE token=?", (credentials.credentials,))
        await db.commit()
        await db.close()
    return {"success": True}
