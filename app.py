from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import secrets
import smtplib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from core.automation import (
    check_login,
    fetch_chat_contacts,
    message_hash,
    render_template,
    run_send_task,
)
from core.config import (
    load_config,
    save_config,
    settings,
)
from core.metrics import get_memory_usage, get_metrics
from core.models import (
    get_db,
    hash_password,
    init_db,
)
from core.notifier import send_notification
from core.scheduler import (
    has_rate_limit_cooldown,
    schedule_auto_retry,
    schedule_rate_limit_cooldown,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fusion-spark")

security = HTTPBearer(auto_error=False)
_sessions: dict[str, dict[str, Any]] = {}

_rate_limit_store: dict[str, list[float]] = {}
_executor = ThreadPoolExecutor(max_workers=2)


def app_error(code: str, status_code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def _check_rate_limit(key: str, max_requests: int = 60, window: int = 60) -> bool:
    now = time.time()
    if key not in _rate_limit_store:
        _rate_limit_store[key] = []
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window]
    if len(_rate_limit_store[key]) >= max_requests:
        return True
    _rate_limit_store[key].append(now)
    return False


async def get_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    if not credentials:
        return {}
    return _sessions.get(credentials.credentials, {})


async def require_user(user: dict[str, Any] = Depends(get_user)) -> dict[str, Any]:
    if not user:
        raise app_error("AUTH_002", 401, "请先登录")
    return user


async def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise app_error("AUTH_003", 403, "需要管理员权限")
    return user


def _validate_cron_expr(expr: str) -> bool:
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    try:
        CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
        return True
    except Exception:
        return False


def _validate_input(
    value: str, label: str, min_len: int = 1, max_len: int = 500
) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, f"{label} 必须是字符串")
    if len(value.strip()) < min_len:
        raise HTTPException(400, f"{label} 至少需要 {min_len} 个字符")
    if len(value) > max_len:
        raise HTTPException(400, f"{label} 不能超过 {max_len} 个字符")
    return value.strip()


def _send_email_notification(subject: str, body: str) -> None:
    if not settings.email_smtp_host or not settings.email_user:
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.email_user
        msg["To"] = settings.email_to
        with smtplib.SMTP_SSL(
            settings.email_smtp_host, settings.email_smtp_port, timeout=10
        ) as server:
            server.login(settings.email_user, settings.email_pass)
            server.send_message(msg)
        logger.info("邮件通知已发送")
    except Exception as e:
        logger.warning("邮件发送失败: %s", e)


def _send_dingtalk_notification(title: str, text: str) -> None:
    if not settings.dingtalk_webhook or not settings.dingtalk_secret:
        return
    try:
        import base64
        import hmac
        import urllib.request
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        timestamp = str(int(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{settings.dingtalk_secret}".encode()
        signature = base64.b64encode(
            hmac.new(
                settings.dingtalk_secret.encode("utf-8"), string_to_sign, hashlib.sha256
            ).digest()
        ).decode()

        parsed = urlsplit(settings.dingtalk_webhook)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query.extend((("timestamp", timestamp), ("sign", signature)))
        signed_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

        payload = json.dumps(
            {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": text},
                "at": {"isAtAll": False},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            signed_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("errcode") != 0:
            logger.warning("钉钉通知失败: %s", result.get("errmsg", ""))
        else:
            logger.info("钉钉通知已发送")
    except Exception as e:
        logger.warning("钉钉通知发送失败: %s", e)


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Douyin Spark Fusion API",
    description="Self-hosted douyin spark automation service. Automatically send messages to maintain friendship streaks on Douyin.",
    version="1.0.0",
    openapi_tags=[
        {"name": "health", "description": "Health check endpoints"},
        {"name": "auth", "description": "User authentication (register/login/token)"},
        {
            "name": "accounts",
            "description": "Douyin account management (CRUD + cookie upload)",
        },
        {"name": "friends", "description": "Friend list sync and management"},
        {"name": "messages", "description": "Message sending and template preview"},
        {"name": "tasks", "description": "Scheduled task management (CRUD cron jobs)"},
        {"name": "logs", "description": "System logs and history"},
        {"name": "settings", "description": "System settings and configuration"},
        {"name": "admin", "description": "Admin-only endpoints (user management)"},
    ],
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def app_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(client_ip, max_requests=120, window=60):
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_001", "message": "请求过于频繁，请稍后再试"},
        )
    return await call_next(request)


@app.get("/metrics")
async def metrics():
    return Response(content=get_metrics(), media_type="text/plain")


@app.get(
    "/api/health",
    tags=["health"],
    summary="Health check",
    description="Returns system health status including uptime and version",
)
async def health_check():
    db_status = "ok"
    try:
        db = await get_db()
        await db.execute_fetchall("SELECT 1")
        await db.close()
    except Exception:
        db_status = "error"

    playwright_status = "ok"
    try:
        import importlib

        importlib.import_module("playwright")
    except Exception:
        playwright_status = "error"

    return {
        "status": "ok",
        "timestamp": datetime.now().astimezone().isoformat(),
        "version": "1.0.0",
        "db": db_status,
        "playwright": playwright_status,
        "memory_mb": round(get_memory_usage(), 1),
    }


@app.get(
    "/api/health/ready",
    tags=["health"],
    summary="Readiness check",
    description="Returns readiness state checking DB and Playwright availability",
)
async def health_ready():
    db_ok = True
    try:
        db = await get_db()
        await db.execute_fetchall("SELECT 1")
        await db.close()
    except Exception:
        db_ok = False

    playwright_ok = True
    try:
        import importlib

        importlib.import_module("playwright")
    except Exception:
        playwright_ok = False

    ready = db_ok and playwright_ok
    return {
        "status": "ready" if ready else "not ready",
        "db": "ok" if db_ok else "error",
        "playwright": "ok" if playwright_ok else "error",
    }


@app.get(
    "/api/health/live",
    tags=["health"],
    summary="Liveness check",
    description="Simple liveness probe",
)
async def health_live():
    return {"status": "ok"}


# ── Auth ────────────────────────────────────────────────────


@app.post(
    "/api/auth/login",
    tags=["auth"],
    summary="User login",
    description="Authenticate with username and password to receive a session token",
)
async def login(req: LoginRequest):
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT * FROM users WHERE username=?", (req.username,)
    )
    await db.close()
    if not row:
        raise app_error("AUTH_001", 401, "用户名或密码错误")
    user = dict(row[0])
    if user["password_hash"] != hash_password(req.password):
        raise app_error("AUTH_001", 401, "用户名或密码错误")
    token = secrets.token_hex(32)
    _sessions[token] = {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
    }
    return {"token": token, "user": _sessions[token]}


@app.post(
    "/api/auth/register",
    tags=["auth"],
    summary="User registration",
    description="Create a new user account if registration is enabled",
)
async def register(req: RegisterRequest):
    if not settings.allow_registration:
        raise app_error("AUTH_003", 403, "注册功能已关闭")
    username = _validate_input(req.username, "用户名", min_len=2, max_len=50)
    password = _validate_input(req.password, "密码", min_len=4, max_len=100)
    db = await get_db()
    existing = await db.execute_fetchall(
        "SELECT id FROM users WHERE username=?", (username,)
    )
    if existing:
        await db.close()
        raise HTTPException(400, "用户名已存在")
    await db.execute(
        "INSERT INTO users(username, password_hash) VALUES(?,?)",
        (username, hash_password(password)),
    )
    await db.commit()
    user_id = (await db.execute_fetchall("SELECT last_insert_rowid() as id"))[0]["id"]
    await db.close()
    token = secrets.token_hex(32)
    _sessions[token] = {"id": user_id, "username": username, "is_admin": False}
    return {"token": token, "user": _sessions[token]}


@app.get(
    "/api/auth/me",
    tags=["auth"],
    summary="Get current user",
    description="Returns the currently authenticated user's profile",
)
async def me(user: dict[str, Any] = Depends(require_user)):
    return {"user": user}


@app.post(
    "/api/auth/logout",
    tags=["auth"],
    summary="User logout",
    description="Invalidate the current session token",
)
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials and credentials.credentials in _sessions:
        del _sessions[credentials.credentials]
    return {"success": True}


# ── Accounts ────────────────────────────────────────────────


@app.get(
    "/api/accounts",
    tags=["accounts"],
    summary="List accounts",
    description="List all douyin accounts belonging to the user (or all for admins)",
)
async def list_accounts(user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if user["is_admin"]:
        rows = await db.execute_fetchall("SELECT * FROM accounts ORDER BY id DESC")
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM accounts WHERE user_id=? ORDER BY id DESC", (user["id"],)
        )
    await db.close()
    return [dict(r) for r in rows]


@app.post(
    "/api/accounts",
    tags=["accounts"],
    summary="Create account",
    description="Create a new douyin account with name and phone number",
)
async def create_account(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    name = _validate_input(data.get("name", ""), "账号名称", min_len=1, max_len=100)
    phone = str(data.get("phone", ""))[:50]
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO accounts(user_id, name, phone) VALUES(?,?,?)",
        (user["id"], name, phone),
    )
    await db.commit()
    await db.close()
    return {"id": cursor.lastrowid}


@app.put(
    "/api/accounts/{account_id}",
    tags=["accounts"],
    summary="Update account",
    description="Update an existing douyin account's name, phone, and send gap settings",
)
async def update_account(
    account_id: int, req: Request, user: dict[str, Any] = Depends(require_user)
):
    data = await req.json()
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    name = str(data.get("name", ""))[:100]
    phone = str(data.get("phone", ""))[:50]
    send_gap_min = int(data.get("send_gap_min", 10))
    send_gap_max = int(data.get("send_gap_max", 20))
    await db.execute(
        "UPDATE accounts SET name=?, phone=?, send_gap_min=?, send_gap_max=?, updated_at=? WHERE id=?",
        (
            name,
            phone,
            send_gap_min,
            send_gap_max,
            datetime.now().isoformat(),
            account_id,
        ),
    )
    await db.commit()
    await db.close()
    return {"success": True}


@app.delete(
    "/api/accounts/{account_id}",
    tags=["accounts"],
    summary="Delete account",
    description="Delete a douyin account and all associated tasks, friends, and history",
)
async def delete_account(account_id: int, user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    await db.execute("DELETE FROM tasks WHERE account_id=?", (account_id,))
    await db.execute("DELETE FROM friends WHERE account_id=?", (account_id,))
    await db.execute("DELETE FROM history WHERE account_id=?", (account_id,))
    await db.commit()
    await db.close()
    return {"success": True}


@app.post(
    "/api/accounts/{account_id}/cookies",
    tags=["accounts"],
    summary="Upload cookies",
    description="Upload browser cookies for a douyin account to authenticate automation",
)
async def upload_cookies(
    account_id: int, req: Request, user: dict[str, Any] = Depends(require_user)
):
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    data = await req.json()
    cookies_raw = data.get("cookies", [])
    if isinstance(cookies_raw, str):
        try:
            cookies_raw = json.loads(cookies_raw)
        except json.JSONDecodeError:
            raise app_error("ACCT_002", 400, "Cookie 格式无效")

    if not isinstance(cookies_raw, list):
        raise app_error("ACCT_002", 400, "Cookie 必须是数组")

    validated = []
    for idx, cookie in enumerate(cookies_raw):
        if not isinstance(cookie, dict):
            raise app_error("ACCT_002", 400, f"Cookie[{idx}] 必须是对象")
        if not cookie.get("name") or not cookie.get("value"):
            raise app_error("ACCT_002", 400, f"Cookie[{idx}] 缺少 name 或 value")
        validated.append(cookie)

    await db.execute(
        "UPDATE accounts SET cookies=?, updated_at=? WHERE id=?",
        (
            json.dumps(validated, ensure_ascii=False),
            datetime.now().isoformat(),
            account_id,
        ),
    )
    await db.commit()
    await db.close()
    return {"success": True, "cookie_count": len(validated)}


@app.post(
    "/api/accounts/{account_id}/storage-state",
    tags=["accounts"],
    summary="Upload storage state",
    description="Upload Playwright storage state JSON for a douyin account",
)
async def upload_storage_state(
    account_id: int, req: Request, user: dict[str, Any] = Depends(require_user)
):
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    data = await req.json()
    state_raw = data.get("storage_state", {})
    if isinstance(state_raw, str):
        try:
            state_raw = json.loads(state_raw)
        except json.JSONDecodeError:
            raise app_error("ACCT_002", 400, "StorageState 格式无效")

    if not isinstance(state_raw, dict):
        raise app_error("ACCT_002", 400, "StorageState 必须是 JSON 对象")

    await db.execute(
        "UPDATE accounts SET storage_state=?, updated_at=? WHERE id=?",
        (
            json.dumps(state_raw, ensure_ascii=False),
            datetime.now().isoformat(),
            account_id,
        ),
    )
    await db.commit()
    await db.close()
    return {"success": True}


@app.post(
    "/api/accounts/{account_id}/verify-login",
    tags=["accounts"],
    summary="Verify login status",
    description="Check if the account's cookies/storage state are still valid by attempting a login",
)
async def verify_account_login(
    account_id: int, user: dict[str, Any] = Depends(require_user)
):
    db = await get_db()
    if not user["is_admin"]:
        row = await db.execute_fetchall(
            "SELECT id FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"])
        )
        if not row:
            await db.close()
            raise app_error("AUTH_003", 403, "无权操作")
    account = dict(row[0])
    await db.close()

    cookies = json.loads(account.get("cookies", "[]"))
    storage_state_raw = account.get("storage_state", "")
    storage_state = json.loads(storage_state_raw) if storage_state_raw else None

    if not cookies and not storage_state:
        return {"success": False, "error": "未配置登录凭据"}

    browser = None
    try:
        from core.automation import DOUYIN_CHAT_URL, launch_browser

        browser, context, page = launch_browser(
            cookies=cookies, storage_state=storage_state
        )
        try:
            page.goto(DOUYIN_CHAT_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            logged, why = check_login(page)
            return {"success": logged, "error": None if logged else why}
        finally:
            context.close()
            browser.close()
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Friends ─────────────────────────────────────────────────


@app.get(
    "/api/friends",
    tags=["friends"],
    summary="List friends",
    description="List all friends, optionally filtered by account_id",
)
async def list_friends(
    account_id: int = 0, user: dict[str, Any] = Depends(require_user)
):
    db = await get_db()
    if account_id:
        if not user["is_admin"]:
            acc = await db.execute_fetchall(
                "SELECT id FROM accounts WHERE id=? AND user_id=?",
                (account_id, user["id"]),
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


@app.post(
    "/api/friends/sync",
    tags=["friends"],
    summary="Sync friends",
    description="Fetch and sync the friend list from douyin chat contacts for a given account",
)
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

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, fetch_chat_contacts, cookies, storage_state)

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


# ── Messages / Send ─────────────────────────────────────────


@app.post(
    "/api/messages/send",
    tags=["messages"],
    summary="Send message",
    description="Send a message to a friend via douyin chat automation",
)
async def send_message(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()

    account_id = data.get("account_id", 0)
    friend_name = _validate_input(
        data.get("friend_name", ""), "好友名称", min_len=1, max_len=100
    )
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
            "INSERT INTO history(account_id, user_id, friend_name, message_hash, status) VALUES(?,?,?,?,?)",
            (account_id, user["id"], friend_name, msg_hash, "success"),
        )
        await db.commit()

    await db.close()

    if "限流" in reason or "频繁" in reason:
        schedule_rate_limit_cooldown(settings.rate_limit_cooldown_minutes)

    return {
        "success": success,
        "code": "SEND_001" if not success else None,
        "message": reason,
    }


@app.post(
    "/api/messages/preview",
    tags=["messages"],
    summary="Preview message template",
    description="Render a message template with provided context variables for preview",
)
async def preview_template(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    template = str(data.get("template", ""))[:5000]
    context = {
        "account": str(data.get("account", "")),
        "friend": str(data.get("friend", "")),
        "yiyan": str(data.get("yiyan", "人生苦短，及时行乐")),
        "from": str(data.get("from", "一言")),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "weekday": [
            "星期一",
            "星期二",
            "星期三",
            "星期四",
            "星期五",
            "星期六",
            "星期日",
        ][datetime.now().weekday()],
        "spark_days": str(data.get("spark_days", "100")),
    }
    rendered = render_template(template, context)
    return {"rendered": rendered, "context": context}


# ── Tasks ───────────────────────────────────────────────────


@app.get(
    "/api/tasks",
    tags=["tasks"],
    summary="List tasks",
    description="List all scheduled tasks belonging to the user (or all for admins)",
)
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


@app.post(
    "/api/tasks",
    tags=["tasks"],
    summary="Create task",
    description="Create a new scheduled task with a cron expression for automated message sending",
)
async def create_task(req: Request, user: dict[str, Any] = Depends(require_user)):
    data = await req.json()
    account_id = int(data.get("account_id", 0))
    friend_name = _validate_input(
        data.get("friend_name", ""), "好友名称", min_len=1, max_len=100
    )
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


@app.put(
    "/api/tasks/{task_id}",
    tags=["tasks"],
    summary="Update task",
    description="Update an existing scheduled task's account, message, cron expression, and active status",
)
async def update_task(
    task_id: int, req: Request, user: dict[str, Any] = Depends(require_user)
):
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


@app.delete(
    "/api/tasks/{task_id}",
    tags=["tasks"],
    summary="Delete task",
    description="Delete a scheduled task permanently",
)
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


@app.post(
    "/api/tasks/{task_id}/run",
    tags=["tasks"],
    summary="Run task now",
    description="Trigger a scheduled task to execute immediately instead of waiting for its cron schedule",
)
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
        "UPDATE tasks SET last_run=? WHERE id=?",
        (datetime.now().isoformat(), task_id),
    )
    await db.execute(
        "INSERT INTO logs(account_id, task_id, user_id, friend_name, status, message, reason) VALUES(?,?,?,?,?,?,?)",
        (account_id, task_id, user["id"], task["friend_name"], "success" if success else "error", reason, reason),
    )
    await db.commit()

    if success:
        msg_hash = message_hash(
            task["friend_name"], task["message"], task.get("message_type", "text")
        )
        await db.execute(
            "INSERT INTO history(account_id, user_id, friend_name, message_hash, status) VALUES(?,?,?,?,?)",
            (account_id, user["id"], task["friend_name"], msg_hash, "success"),
        )
        await db.commit()

    await db.close()

    if "限流" in reason or "频繁" in reason:
        schedule_rate_limit_cooldown(settings.rate_limit_cooldown_minutes)
        schedule_auto_retry(task["friend_name"], task["message"], cookies, storage_state)

    if success:
        _send_dingtalk_notification(
            "抖音火花发送成功",
            f"### 发送成功\n\n好友: {task['friend_name']}\n消息: {task['message'][:50]}",
        )

    return {
        "success": success,
        "code": "SEND_001" if not success else None,
        "message": reason,
    }


@app.post(
    "/api/tasks/run-all",
    tags=["tasks"],
    summary="Run all tasks",
    description="Execute all active scheduled tasks immediately",
)
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
                    "SELECT id, cookies, storage_state FROM accounts WHERE id=?",
                    (account_id,),
                )
            await db2.close()

            if not acc:
                results.append(
                    {
                        "task_id": task_dict["id"],
                        "success": False,
                        "message": "账号不存在",
                    }
                )
                continue

            account = dict(acc[0])
            cookies = json.loads(account.get("cookies", "[]"))
            storage_state_raw = account.get("storage_state", "")
            storage_state = json.loads(storage_state_raw) if storage_state_raw else None

            if has_rate_limit_cooldown():
                results.append(
                    {
                        "task_id": task_dict["id"],
                        "success": False,
                        "message": "限流冷却中",
                    }
                )
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
                "UPDATE tasks SET last_run=? WHERE id=?",
                (datetime.now().isoformat(), task_dict["id"]),
            )
            await db3.execute(
                "INSERT INTO logs(account_id, task_id, user_id, friend_name, status, message, reason) VALUES(?,?,?,?,?,?,?)",
                (
                    account_id,
                    task_dict["id"],
                    user["id"],
                    task_dict["friend_name"],
                    "success" if success else "error",
                    reason,
                    reason,
                ),
            )
            await db3.commit()
            await db3.close()

            results.append(
                {"task_id": task_dict["id"], "success": success, "message": reason}
            )

            if "限流" in reason or "频繁" in reason:
                schedule_rate_limit_cooldown(settings.rate_limit_cooldown_minutes)
                break
        except Exception as e:
            results.append(
                {"task_id": task_dict.get("id", 0), "success": False, "message": str(e)}
            )

    return {"results": results}


# ── Logs ────────────────────────────────────────────────────


@app.get(
    "/api/logs",
    tags=["logs"],
    summary="List logs",
    description="List recent system logs, optionally limited by count",
)
async def list_logs(limit: int = 50, user: dict[str, Any] = Depends(require_user)):
    limit = min(max(limit, 1), 500)
    db = await get_db()
    if user["is_admin"]:
        rows = await db.execute_fetchall(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM logs WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user["id"], limit),
        )
    await db.close()
    return [dict(r) for r in rows]


# ── Settings ────────────────────────────────────────────────


@app.get(
    "/api/settings",
    tags=["settings"],
    summary="Get settings",
    description="Retrieve current system configuration settings",
)
async def get_settings(user: dict[str, Any] = Depends(require_user)):
    cfg = load_config()
    return {
        "schedule_time": cfg.get("schedule_time", "21:00"),
        "jitter_minutes": cfg.get("jitter_minutes", 30),
        "send_gap_min": cfg.get("send_gap_min", 6),
        "send_gap_max": cfg.get("send_gap_max", 12),
        "max_friends_per_run": cfg.get("max_friends_per_run", 20),
        "daily_limit": cfg.get("daily_limit", 50),
        "rate_limit_cooldown_minutes": cfg.get("rate_limit_cooldown_minutes", 45),
        "retry_delay_minutes": cfg.get("retry_delay_minutes", 45),
        "allow_registration": getattr(settings, "allow_registration", True),
    }


@app.post(
    "/api/settings",
    tags=["settings"],
    summary="Update settings",
    description="Update system configuration settings (admin only)",
)
async def update_settings(req: Request, user: dict[str, Any] = Depends(require_admin)):
    data = await req.json()
    cfg = load_config()
    for key in (
        "schedule_time",
        "jitter_minutes",
        "send_gap_min",
        "send_gap_max",
        "max_friends_per_run",
        "daily_limit",
        "rate_limit_cooldown_minutes",
        "retry_delay_minutes",
    ):
        if key in data:
            cfg[key] = data[key]
    save_config(cfg)

    if data.get("admin_pass"):
        db = await get_db()
        await db.execute(
            "UPDATE users SET password_hash=? WHERE username='admin'",
            (hash_password(data["admin_pass"]),),
        )
        await db.commit()
        await db.close()

    return {"success": True}


# ── Notify ──────────────────────────────────────────────────


@app.post("/api/notify/test", tags=["settings"])
async def test_notify(req: Request, user: dict = Depends(require_user)):
    data = await req.json()
    channel = data.get("channel", "all")
    title = data.get("title", "Test Notification")
    content = data.get("content", "This is a test from Douyin Spark Fusion")
    results = await send_notification(f"[{channel}] {title}", content)
    return {"success": True, "results": str(results)}


# ── Stats ───────────────────────────────────────────────────


@app.get(
    "/api/stats",
    tags=["settings"],
    summary="Dashboard stats",
    description="Get dashboard statistics including account, task, friend, and today's sent message counts",
)
async def dashboard_stats(user: dict[str, Any] = Depends(require_user)):
    db = await get_db()
    uid = user["id"]
    if user["is_admin"]:
        ac = await db.execute_fetchall("SELECT COUNT(*) as c FROM accounts")
        tk = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM tasks WHERE is_active=1"
        )
        fr = await db.execute_fetchall("SELECT COUNT(*) as c FROM friends")
        td = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM logs WHERE status='success' AND date(created_at)=date('now')"
        )
    else:
        ac = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM accounts WHERE user_id=?", (uid,)
        )
        tk = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND is_active=1", (uid,)
        )
        fr = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM friends WHERE user_id=?", (uid,)
        )
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


# ── Admin ───────────────────────────────────────────────────


@app.get(
    "/api/admin/users",
    tags=["admin"],
    summary="List all users",
    description="List all registered users in the system (admin only)",
)
async def list_users(user: dict[str, Any] = Depends(require_admin)):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, username, is_admin, group_id, created_at FROM users ORDER BY id"
    )
    await db.close()
    return [dict(r) for r in rows]


@app.delete(
    "/api/admin/users/{user_id}",
    tags=["admin"],
    summary="Delete user",
    description="Delete a user and all associated data (admin only, cannot delete self)",
)
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


# ── Static files ────────────────────────────────────────────


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def serve_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Fusion Spark</h1>")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.host, port=settings.port, reload=True)
