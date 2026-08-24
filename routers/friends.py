from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from core.automation import DOUYIN_CHAT_URL, _normalize_cookies
from core.models import get_db

from .auth import app_error, require_user

router = APIRouter(prefix="/api/friends", tags=["friends"])

SEARCH_INPUTS = (
    'input[placeholder*="搜索"]',
    'input[placeholder="搜索"]',
    '[role="textbox"][placeholder*="搜索"]',
    'input[aria-label*="搜索"]',
    '[role="textbox"][aria-label*="搜索"]',
)


async def _fetch_contacts_async(cookies, storage_state):
    from playwright.async_api import async_playwright

    result: dict[str, Any] = {"names": [], "error": None}
    context = None
    browser = None
    pw = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context_args = {"viewport": {"width": 1440, "height": 1000}, "locale": "zh-CN"}
        if storage_state and isinstance(storage_state, (dict, str)):
            context_args["storage_state"] = storage_state
        context = await browser.new_context(**context_args)
        if not storage_state and cookies:
            await context.add_cookies(_normalize_cookies(cookies))
        page = await context.new_page()

        await page.goto(DOUYIN_CHAT_URL, wait_until="domcontentloaded", timeout=45000)

        import asyncio

        for attempt in range(3):
            matched = None
            for selector in SEARCH_INPUTS:
                try:
                    await page.locator(selector).first.wait_for(state="visible", timeout=15000)
                    matched = selector
                    break
                except Exception:
                    continue
            if matched is not None:
                await page.wait_for_timeout(3000)
                break
            if attempt < 2:
                await page.reload(wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)

        collected = []
        for _ in range(3):
            data = await page.evaluate("""
                () => {
                    const out = [];
                    const seen = new Set();
                    document.querySelectorAll('.conversationConversationItemtitle').forEach(t => {
                        const name = (t.textContent || '').trim();
                        if (!name || seen.has(name)) return;
                        seen.add(name);
                        const wrap = t.parentElement;
                        const s = wrap ? wrap.querySelector('.commonStreaknormalText') : null;
                        out.push({ name: name, streak: s ? (s.textContent || '').trim() : '' });
                    });
                    return out;
                }
            """)
            new_items = [x for x in data if x not in collected]
            if new_items:
                collected.extend(new_items)
            else:
                break
            try:
                await page.mouse.move(200, 350)
                await page.mouse.wheel(0, 800)
            except Exception:
                pass
            await asyncio.sleep(1.2)

        result["names"] = collected
        return result
    except Exception as e:
        result["error"] = str(e)
        return result
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


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

    result = await _fetch_contacts_async(cookies, storage_state)

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
