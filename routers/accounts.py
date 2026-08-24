from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from core.automation import _cookies_to_storage_state
from core.models import get_db

from .auth import app_error, require_user

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_qr_sessions: dict[str, dict[str, Any]] = {}
_qr_sessions_lock = threading.Lock()


def _validate_input(value: str, label: str, min_len: int = 1, max_len: int = 500) -> str:
    from fastapi import HTTPException

    if not isinstance(value, str):
        raise HTTPException(400, f"{label} 必须是字符串")
    if len(value.strip()) < min_len:
        raise HTTPException(400, f"{label} 至少需要 {min_len} 个字符")
    if len(value) > max_len:
        raise HTTPException(400, f"{label} 不能超过 {max_len} 个字符")
    return value.strip()


@router.get("", summary="List accounts")
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


@router.post("", summary="Create account")
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


@router.put("/{account_id}", summary="Update account")
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
        (name, phone, send_gap_min, send_gap_max, datetime.now().isoformat(), account_id),
    )
    await db.commit()
    await db.close()
    return {"success": True}


@router.delete("/{account_id}", summary="Delete account")
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


@router.post("/{account_id}/cookies", summary="Upload cookies")
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
        if not cookie.get("domain"):
            raise app_error("ACCT_002", 400, f"Cookie[{idx}] 缺少 domain")
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name == "" and value == "":
            continue
        validated.append(cookie)
    await db.execute(
        "UPDATE accounts SET cookies=?, storage_state=?, updated_at=? WHERE id=?",
        (
            json.dumps(validated, ensure_ascii=False),
            json.dumps(_cookies_to_storage_state(validated), ensure_ascii=False),
            datetime.now().isoformat(),
            account_id,
        ),
    )
    await db.commit()
    await db.close()
    return {"success": True, "cookie_count": len(validated)}


@router.post("/{account_id}/storage-state", summary="Upload storage state")
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
        (json.dumps(state_raw, ensure_ascii=False), datetime.now().isoformat(), account_id),
    )
    await db.commit()
    await db.close()
    return {"success": True}


@router.post("/{account_id}/verify-login", summary="Verify login status")
async def verify_account_login(
    account_id: int, user: dict[str, Any] = Depends(require_user)
):
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
        return {"success": False, "error": "未配置登录凭据"}

    try:
        from playwright.sync_api import sync_playwright

        from core.automation import (
            DOUYIN_CHAT_URL,
            _normalize_cookies,
            check_login,
        )

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        if storage_state and isinstance(storage_state, (dict, str)):
            context = browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1366, "height": 768},
            )
        elif cookies:
            context = browser.new_context(
                storage_state={
                    "cookies": _normalize_cookies(cookies),
                    "origins": [{"origin": "https://www.douyin.com", "localStorage": []}],
                },
                viewport={"width": 1366, "height": 768},
            )
        else:
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
            )
        page = context.new_page()
        try:
            page.goto(DOUYIN_CHAT_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            logged, why = check_login(page)
            if logged:
                nickname = page.evaluate("""
                    () => {
                        const items = document.querySelectorAll('.conversationConversationItemtitle');
                        if (items.length > 0) {
                            const name = (items[0].textContent || '').trim();
                            if (name && name.length > 0 && name.length < 30) return name;
                        }
                        return null;
                    }
                """)
                if nickname:
                    db = await get_db()
                    await db.execute("UPDATE accounts SET name=? WHERE id=?", (nickname, account_id))
                    await db.commit()
                    await db.close()
                    return {"success": True, "nickname": nickname}
            return {"success": logged, "error": None if logged else why}
        finally:
            context.close()
            browser.close()
            pw.stop()
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/qr-login", summary="Get QR code for login")
async def get_qr_code():
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        locale="zh-CN",
    )
    await ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};"
    )
    page = await ctx.new_page()
    qr_result = {}

    async def capture(response):
        if "get_qrcode" in response.url or "get_qr" in response.url:
            try:
                body = await response.text()
                data = json.loads(body)
                d = data.get("data", {})
                qr = d.get("qrcode") or d.get("qrcode_url") or d.get("qr_url") or d.get("url")
                token = d.get("token") or d.get("qrcode_token") or d.get("qr_token")
                if qr:
                    qr_result["qr"] = qr
                    qr_result["token"] = token
            except Exception:
                pass

    page.on("response", capture)

    try:
        await page.goto("https://creator.douyin.com/", wait_until="load", timeout=60000)
        await page.wait_for_timeout(5000)

        await page.evaluate(
            "()=>{for(const b of document.querySelectorAll('button'))"
            "{if(b.textContent?.trim()==='登录'){b.click();return}}}"
        )
        await page.wait_for_timeout(5000)

        for _ in range(30):
            if qr_result.get("qr"):
                break
            await page.wait_for_timeout(1000)

        if qr_result.get("qr"):
            qr = qr_result["qr"]
            token = qr_result.get("token", "")
            if qr.startswith("data:image"):
                b64 = qr.split(",", 1)[1]
            elif qr.startswith("iVBOR"):
                b64 = qr
            else:
                await browser.close()
                await pw.stop()
                return {"qr_b64": "", "token": ""}

            with _qr_sessions_lock:
                _qr_sessions[token] = {
                    "pw": pw, "browser": browser, "ctx": ctx, "page": page,
                    "created_at": time.time(),
                }
            return {"qr_b64": b64, "token": token}
        else:
            await browser.close()
            await pw.stop()
    except Exception:
        await browser.close()
        await pw.stop()

    return {"qr_b64": "", "token": ""}


@router.get("/qr-login/{token}/wait", summary="Wait for QR code scan")
async def wait_qr_scan(token: str, timeout: int = 300):
    with _qr_sessions_lock:
        session = _qr_sessions.pop(token, None)
    if not session:
        return {"success": False, "cookies": []}

    pw = session["pw"]
    browser = session["browser"]
    ctx = session["ctx"]
    page = session["page"]
    qr_status = {"status": "new"}

    async def check_response(response):
        if "check_qrconnect" in response.url:
            try:
                body = await response.text()
                data = json.loads(body)
                d = data.get("data", {})
                status = d.get("status", "")
                if status:
                    qr_status["status"] = status
            except Exception:
                pass

    page.on("response", check_response)

    try:
        start = time.time()
        while time.time() - start < timeout:
            await page.wait_for_timeout(2000)

            if qr_status["status"] == "confirmed":
                return {"success": True, "cookies": await ctx.cookies()}

            cookies = await ctx.cookies()
            for c in cookies:
                if c["name"] in ("sessionid", "sessionid_ss") and c["value"] and len(c["value"]) > 10:
                    return {"success": True, "cookies": cookies}
    finally:
        page.remove_listener("response", check_response)
        await browser.close()
        await pw.stop()

    return {"success": False, "cookies": []}
