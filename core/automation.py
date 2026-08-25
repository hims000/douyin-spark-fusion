from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    sync_playwright,
)

from .config import DATA_DIR, settings

logger = logging.getLogger("fusion-spark")

_browser_pool: dict[str, Any] = {}
_browser_cache: dict[str, tuple] = {}
_browser_cache_lock = threading.Lock()
_thread_local = threading.local()
_session_cache: dict[int, tuple] = {}
_session_cache_lock = threading.Lock()

def _get_thread_pool() -> dict[str, Any]:
    if not hasattr(_thread_local, "browser_pool"):
        _thread_local.browser_pool = {}
    return _thread_local.browser_pool


def cache_browser_session(account_id: int, pw, browser, context):
    with _session_cache_lock:
        _session_cache[account_id] = (pw, browser, context)


def get_cached_browser_session(account_id: int):
    with _session_cache_lock:
        return _session_cache.get(account_id)


def clear_cached_browser_session(account_id: int):
    with _session_cache_lock:
        old = _session_cache.pop(account_id, None)
        if old:
            try:
                old[2].close()
            except Exception:
                pass


def _get_cached_browser(cookies_hash: str):
    with _browser_cache_lock:
        return _browser_cache.get(cookies_hash)

def _set_cached_browser(cookies_hash: str, playwright, browser, context):
    with _browser_cache_lock:
        if len(_browser_cache) >= 10:
            oldest = next(iter(_browser_cache))
            pw, br, ctx = _browser_cache.pop(oldest)
            try:
                ctx.close()
            except Exception:
                pass
        _browser_cache[cookies_hash] = (playwright, browser, context)


def _compute_cookies_hash(cookies: list[dict[str, Any]]) -> str:
    try:
        normalized = _normalize_cookies(cookies)
        return hashlib.md5(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    except ValueError:
        return ""


TEMPLATE_REGEX = re.compile(r"\{\{(\w+)\}\}")

DOUYIN_CHAT_URL = "https://creator.douyin.com/creator-micro/data/following/chat"
STATE_PATH = DATA_DIR / "state.json"

LOGIN_MARKERS = (
    "text=私信",
    'input[placeholder*="搜索"]',
    '[role="textbox"][placeholder*="搜索"]',
)
LOGIN_REQUIRED_MARKERS = (
    "text=扫码登录",
    "text=验证码登录",
    "text=登录后",
)
RISK_MARKERS = (
    "text=安全验证",
    "text=完成验证",
    "text=验证身份",
)
SEARCH_INPUTS = (
    'input[placeholder*="搜索"]',
    'input[placeholder="搜索"]',
    '[role="textbox"][placeholder*="搜索"]',
    'input[aria-label*="搜索"]',
    '[role="textbox"][aria-label*="搜索"]',
)
MESSAGE_INPUTS = (
    '[data-contents="true"]',
    '.DraftEditor-editor [contenteditable="true"]',
    '.DraftEditor-root [contenteditable="true"]',
    '[contenteditable="true"][data-placeholder*="发送消息"]',
    '[contenteditable="true"][aria-label*="消息"]',
    '[contenteditable="true"]',
    'textarea[placeholder*="消息"]',
)
IMAGE_INPUTS = ('input[type="file"][accept*="image"]', 'input[type="file"]')
STICKER_BUTTONS = (
    "svg.messageMsgInputiconAction",
    'button[aria-label*="表情"]',
    '[role="button"][aria-label*="表情"]',
    '[title*="表情"]',
)
STICKER_PANELS = (
    ".componentsemojiemojiPanel",
    '[class*="emojiPanel"]',
    '[role="dialog"]',
    '[class*="sticker"]',
)
SEND_BUTTONS = (
    '[class*="messageMsgInputpublishBtn"]',
    ".e2e-send-msg-bt",
    'button[aria-label*="发送"]',
    '[role="button"][aria-label*="发送"]',
)
CHAT_PANEL_MARKERS = (
    '[class*="RightPanelHeader"]',
    '[class*="chatHeader"]',
    '[class*="ChatHeader"]',
    '[class*="messageContent"]',
    '[class*="chatContent"]',
    '[class*="MessagePanel"]',
)

RATE_LIMIT_KEYWORDS = [
    "操作频繁",
    "操作太频繁",
    "发送过于频繁",
    "请稍后再试",
    "稍后再试",
    "安全验证",
    "滑动验证",
    "验证码",
    "验证中心",
    "人机验证",
    "网络异常",
    "请勿频繁",
]

LOGIN_TEXTS = ["扫码登录", "验证码登录", "登录后查看", "登录后即可"]

MESSAGE_TEMPLATE_PLACEHOLDERS = {
    "account",
    "friend",
    "yiyan",
    "from",
    "date",
    "time",
    "weekday",
    "spark_days",
}

LATEST_OUTGOING_MESSAGE = (
    '.messageMessageListlist [data-index="0"] '
    ".messageMessageBoxmessageBox:has(.messageMessageBoxcontentBox.messageMessageBoxisFromMe)"
)

SEND_FAILURE_MARKERS = (
    "text=发送失败",
    '[aria-label*="重试"]',
    '[title*="重试"]',
    '[class*="sendFailed"]',
    '[class*="SendFailed"]',
)


class AuthenticationError(RuntimeError):
    pass


class RiskControlError(RuntimeError):
    pass


class RateLimitError(RuntimeError):
    pass


class PageOperationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _already_sent_today(friend_name: str) -> bool:
    try:
        import sqlite3

        conn = sqlite3.connect(os.path.join(DATA_DIR, "fusion.db"))
        row = conn.execute(
            "SELECT id FROM history WHERE friend_name=? AND date(created_at)=date('now') AND status='success' LIMIT 1",
            (friend_name,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _mark_sent_today(friend_name: str):
    pass


def generate_ai_message(friend_name: str, spark_days: int = 0, api_key: str | None = None, model: str = "gpt-4o-mini") -> str:
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return f"和{friend_name}的每一天都很珍贵✨"

    import requests

    spark_text = f"已经是第{spark_days}天" if spark_days > 0 else ""
    prompt = f"给好友「{friend_name}」写一条续火花消息，不超过20字，温暖有趣。{spark_text}"

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 50},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return f"和{friend_name}的每一天都很珍贵✨"


def _normalize_cookies(cookies: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for index, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            raise ValueError(f"Cookie[{index}] 必须是对象")
        name = cookie.get("name")
        value = cookie.get("value")
        domain = cookie.get("domain")
        if name == "":
            continue
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError(f"Cookie[{index}] 缺少有效的 name 或 value")
        if not isinstance(domain, str) or not domain:
            raise ValueError(f"Cookie[{index}] 缺少有效的 domain")

        expires = cookie.get("expires", cookie.get("expirationDate", -1))
        if cookie.get("session") is True:
            expires = -1
        if isinstance(expires, bool) or not isinstance(expires, (int, float)):
            expires = -1

        same_site_raw = str(cookie.get("sameSite", "")).lower()
        same_site_map = {
            "strict": "Strict",
            "lax": "Lax",
            "none": "None",
            "no_restriction": "None",
        }
        same_site = same_site_map.get(same_site_raw, "Lax")

        normalized.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cookie.get("path")
                if isinstance(cookie.get("path"), str)
                else "/",
                "expires": expires,
                "httpOnly": bool(cookie.get("httpOnly", False)),
                "secure": bool(cookie.get("secure", False)),
                "sameSite": same_site,
            }
        )
    if not normalized:
        raise ValueError("没有有效 Cookie")
    return normalized


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@functools.lru_cache(maxsize=128)
def _render_cached(
    template: str,
    account: str,
    friend: str,
    yiyan: str,
    from_: str,
    date: str,
    time: str,
    weekday: str,
    spark_days: str,
) -> str:
    context = {
        "account": account,
        "friend": friend,
        "yiyan": yiyan,
        "from": from_,
        "date": date,
        "time": time,
        "weekday": weekday,
        "spark_days": spark_days,
    }
    return TEMPLATE_REGEX.sub(lambda m: context.get(m.group(1), ""), template)


def render_template(template: str, context: dict[str, str]) -> str:
    return _render_cached(
        template,
        context.get("account", ""),
        context.get("friend", ""),
        context.get("yiyan", ""),
        context.get("from", ""),
        context.get("date", ""),
        context.get("time", ""),
        context.get("weekday", ""),
        context.get("spark_days", ""),
    )


def check_login(page: Page) -> tuple[bool, str]:
    url = page.url
    if "login" in url.lower() or "passport" in url.lower():
        return False, f"页面已跳转到登录页（{url}）"

    try:
        qr = page.locator("#animate_qrcode_container")
        if qr.count() and qr.first.is_visible():
            return False, "页面出现扫码登录二维码，登录态已过期"
    except Exception:
        pass

    for text in LOGIN_TEXTS:
        try:
            loc = page.get_by_text(text, exact=False)
            for i in range(min(loc.count(), 3)):
                if loc.nth(i).is_visible():
                    return False, f"页面出现登录提示「{text}」"
        except Exception:
            continue

    cookies = page.context.cookies()
    if not any(c["name"].startswith("sessionid") for c in cookies) and not any(
        c["name"] == "passport_csrf_token" for c in cookies
    ):
        return False, "未检测到登录 Cookie"
    return True, "ok"


def detect_rate_limit(page: Page) -> str | None:
    for kw in RATE_LIMIT_KEYWORDS:
        try:
            loc = page.get_by_text(kw, exact=False)
            for i in range(loc.count()):
                if loc.nth(i).bounding_box():
                    return kw
        except Exception:
            continue
    return None


def verify_in_conversation(page: Page, name: str) -> bool:
    for exact in (True, False):
        try:
            loc = page.get_by_text(name, exact=exact)
            for i in range(loc.count()):
                try:
                    box = loc.nth(i).bounding_box()
                except Exception:
                    continue
                if box and box.get("x", 0) > 300 and box.get("y", 0) < 100:
                    return True
        except Exception:
            continue
    return False


def _find_contact(page: Page, name: str):
    exact = page.get_by_text(name, exact=True)
    if exact.count():
        return exact.first
    return (
        page.locator(".conversationConversationItemtitle").filter(has_text=name).first
    )


def _search_result(page: Page, name: str):
    search_items = page.locator('[class*="SearchPanelitem"]').filter(has_text=name)
    for index in range(search_items.count()):
        item = search_items.nth(index)
        button = item.locator('[class*="SearchPanelitemchat_btn"]').first
        if button.count():
            return button

    row_selectors = (
        '[data-e2e="conversation-item"]',
        '[class*="conversationConversationItem"]',
        '[class*="conversation-item"]',
        '[class*="ConversationItem"]',
    )
    for selector in row_selectors:
        rows = page.locator(selector).filter(has_text=name)
        for index in range(rows.count()):
            row = rows.nth(index)
            try:
                class_name = row.get_attribute("class") or ""
                if (
                    "wrapper" in class_name
                    or row.get_attribute("data-e2e") == "conversation-item"
                ):
                    return row
            except Exception:
                continue

    candidates = [
        page.get_by_text(name, exact=True),
        page.get_by_text(name, exact=False),
    ]
    for candidate_group in candidates:
        count = candidate_group.count()
        visible = []
        for index in range(count):
            candidate = candidate_group.nth(index)
            try:
                if candidate.is_visible():
                    visible.append(candidate)
            except Exception:
                continue
        if len(visible) == 1:
            return visible[0]
        if len(visible) > 1:
            return visible[0]

    hidden_titles = page.locator('[class*="conversationConversationItemtitle"]').filter(
        has_text=name
    )
    for index in range(hidden_titles.count()):
        row = hidden_titles.nth(index).locator(
            "xpath=ancestor::*[contains(@class, 'conversationConversationItem')][1]"
        )
        if row.count() and row.is_visible():
            return row

    for selector in (
        f'[title="{_css_escape(name)}"]',
        f'[aria-label="{_css_escape(name)}"]',
    ):
        candidate = page.locator(selector).first
        if candidate.count() and candidate.is_visible():
            return candidate
    return None


def _any_visible(
    page: Page, selectors: tuple[str, ...], timeout_ms: int = 2000
) -> bool:
    per_selector = max(250, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        try:
            page.locator(selector).first.wait_for(state="visible", timeout=per_selector)
            return True
        except Exception:
            continue
    return False


def _first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15000):
    per_selector = max(500, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except Exception:
            continue
    raise PageOperationError(f"找不到页面元素，已尝试: {', '.join(selectors)}")


def _trigger_send(page: Page) -> None:
    for selector in SEND_BUTTONS:
        candidate = page.locator(selector).first
        try:
            if candidate.count() and candidate.is_visible():
                candidate.click()
                return
        except Exception:
            continue
    page.keyboard.press("Enter")


def _mark_latest_outgoing_message(page: Page) -> tuple[str, str]:
    anchor = secrets.token_hex(8)
    latest = page.locator(LATEST_OUTGOING_MESSAGE).first
    if not latest.count():
        return anchor, ""
    content = latest.locator('[data-e2e="msg-item-content"]').first
    before_content = content.inner_html() if content.count() else latest.inner_html()
    latest.evaluate(
        "(element, value) => element.setAttribute('data-douyin-sender-anchor', value)",
        anchor,
    )
    return anchor, before_content


def _confirm_outgoing_message(
    page: Page,
    before: tuple[str, str],
    label: str = "",
    expected_text: str = "",
    resource_key: str = "",
) -> None:
    anchor, before_content = before
    try:
        page.wait_for_function(
            """([selector, anchor, previousContent, expectedResource, expectedText]) => {
                const message = document.querySelector(selector);
                if (!message) return false;
                const content = message.querySelector('[data-e2e="msg-item-content"]') || message;
                const isNewMessage =
                    message.getAttribute('data-douyin-sender-anchor') !== anchor ||
                    content.innerHTML !== previousContent;
                if (!isNewMessage) return false;
                if (expectedText) {
                    const normalize = value => (value || '').replace(/[\\s\\u200B\\u200C\\u200D\\uFEFF]+/g, ' ').trim();
                    return normalize(content.innerText).includes(normalize(expectedText));
                }
                if (!expectedResource) return true;
                const images = [...content.querySelectorAll('img')];
                return images.some(image => (image.src || '').includes(expectedResource)) || images.length > 0;
            }""",
            arg=[
                LATEST_OUTGOING_MESSAGE,
                anchor,
                before_content,
                resource_key,
                expected_text,
            ],
            timeout=15000,
        )
        page.wait_for_timeout(3000)
        latest = page.locator(LATEST_OUTGOING_MESSAGE).first
        for selector in SEND_FAILURE_MARKERS:
            marker = latest.locator(selector).first
            if marker.count() and marker.is_visible():
                raise PageOperationError(f"{label}发送失败，页面提示可以重试")
    except PageOperationError:
        raise
    except Exception as exc:
        raise PageOperationError(f"{label}已发送，但没有检测到新的已发送消息") from exc
    finally:
        anchors = page.locator("[data-douyin-sender-anchor]")
        try:
            anchors.evaluate_all(
                "elements => elements.forEach(element => element.removeAttribute('data-douyin-sender-anchor'))"
            )
        except Exception:
            pass


def send_text(page: Page, content: str) -> None:
    editor = None
    for sel in (
        'xpath=//div[contains(@class, "chat-input-")]//div[@contenteditable="true"]',
        'xpath=(//div[@contenteditable="true"])[last()]',
        'xpath=//div[@contenteditable="true" and @role="textbox"]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count():
                editor = loc
                break
        except Exception:
            continue
    if editor is None:
        editor = page.locator('[contenteditable="true"]').first
    editor.click()
    page.wait_for_timeout(400)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    page.keyboard.type(content, delay=100)
    page.wait_for_timeout(800)
    cur = (editor.inner_text() or "") if editor.count() else ""
    if content not in cur:
        raise PageOperationError(f"文字未进入输入框，当前内容: {cur[:30]}")
    page.keyboard.press("Enter")
    deadline = time.time() + 8
    while time.time() < deadline:
        time.sleep(1)
        try:
            cur = (editor.inner_text() or "") if editor.count() else ""
            if content not in cur:
                return
        except Exception:
            pass
    raise PageOperationError("消息已发送但输入框未清空，可能发送失败")


def send_image(page: Page, image_path: str) -> None:
    message_items = page.locator('[data-e2e="msg-item-content"]')
    before = message_items.count()
    file_input = None
    for selector in IMAGE_INPUTS:
        candidate = page.locator(selector).first
        if candidate.count():
            file_input = candidate
            break
    if file_input is None:
        raise PageOperationError("找不到图片上传控件")
    file_input.set_input_files(image_path)
    page.wait_for_timeout(1500)
    _trigger_send(page)
    try:
        page.wait_for_function(
            """([selector, count]) => document.querySelectorAll(selector).length > count""",
            arg=['[data-e2e="msg-item-content"]', before],
            timeout=15000,
        )
    except Exception as exc:
        raise PageOperationError("图片消息已触发发送，但无法确认是否发送成功") from exc


def send_sticker(
    page: Page, sticker_name: str, fallback_index: int | None = None
) -> None:
    before = _mark_latest_outgoing_message(page)
    try:
        button = _first_visible(page, STICKER_BUTTONS)
        button.click(force=True)
        panel = _first_visible(page, STICKER_PANELS)

        item = panel.locator(".emojiEmojiItememojiItem").filter(has_text=sticker_name)
        for index in range(item.count()):
            candidate = item.nth(index)
            description = candidate.locator(".emojiEmojiItememojiItemDesc")
            if (
                description.count()
                and description.first.inner_text().strip() == sticker_name
            ):
                candidate.click(force=True)
                _confirm_outgoing_message(page, before, label=f"表情「{sticker_name}」")
                return

        candidates = (
            panel.get_by_role("img", name=sticker_name, exact=True),
            panel.get_by_role("button", name=sticker_name, exact=True),
            panel.locator(f'[aria-label="{_css_escape(sticker_name)}"]'),
            panel.locator(f'[title="{_css_escape(sticker_name)}"]'),
            panel.locator(f'[alt="{_css_escape(sticker_name)}"]'),
        )
        for candidate in candidates:
            if candidate.count() and candidate.first.is_visible():
                candidate.first.click(force=True)
                _confirm_outgoing_message(page, before, label=f"表情「{sticker_name}」")
                return

        if fallback_index is not None:
            items = panel.locator('[role="button"], img, [aria-label], [title]')
            if items.count() > fallback_index:
                items.nth(fallback_index).click(force=True)
                _confirm_outgoing_message(page, before, label=f"表情「{sticker_name}」")
                return

        raise PageOperationError(f"在表情面板中找不到表情: {sticker_name}")
    finally:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass


def open_and_send_message(
    page: Page,
    friend_name: str,
    message: str,
    message_type: str = "text",
    image_path: str = "",
    sticker_name: str = "",
    dry_run: bool = False,
) -> tuple[bool, str]:
    try:
        search = None
        for sel in (
            'xpath=//input[@placeholder]',
            '[role="textbox"]',
            'xpath=//div[@contenteditable="true"]',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    search = loc
                    break
            except Exception:
                continue
        if search is None:
            search = page.get_by_placeholder("搜索", exact=False).first
        search.click()
        search.fill("")
        page.wait_for_timeout(500)
        search.fill(friend_name)
        page.wait_for_timeout(2000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        btn = page.get_by_text("发消息", exact=False).first
        if btn.count():
            btn.click(force=True)
            page.wait_for_timeout(4000)
        else:
            candidate = page.get_by_text(friend_name, exact=True).first
            if candidate.count() == 0:
                candidate = page.get_by_text(friend_name, exact=False).first
            if candidate.count() == 0:
                page.keyboard.press("Escape")
                return False, f"搜索不到好友: {friend_name}"
            candidate.click(force=True)
            page.wait_for_timeout(3000)
            btn = page.get_by_text("发消息", exact=False).first
            if btn.count():
                btn.click(force=True)
                page.wait_for_timeout(3000)
    except Exception as e:
        return False, f"找不到搜索框: {e}"

    if detect_rate_limit(page):
        return False, "检测到限流提示"

    if dry_run:
        return True, "dry-run"

    try:
        if detect_rate_limit(page):
            return False, "发送前检测到限流提示"

        if message_type == "text":
            send_text(page, message)
        elif message_type == "image":
            if not image_path:
                return False, "图片消息缺少文件路径"
            send_image(page, image_path)
        elif message_type == "sticker":
            if not sticker_name:
                return False, "表情消息缺少表情名称"
            send_sticker(page, sticker_name)
        else:
            return False, f"不支持的消息类型: {message_type}"

        return True, "ok"
    except PageOperationError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"发送异常: {exc}"


def message_hash(friend_name: str, message: str, message_type: str) -> str:
    payload = json.dumps(
        {"friend": friend_name, "message": message, "type": message_type},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def get_browser() -> Browser:
    pool = _get_thread_pool()
    if "browser" in pool and pool["browser"].is_connected():
        return pool["browser"]

    try:
        release_browser()
    except Exception:
        pass

    args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-default-apps",
        "--mute-audio",
        "--no-first-run",
    ]
    launch_args = {"headless": settings.headless, "args": args}
    if settings.browser_path:
        launch_args["executable_path"] = settings.browser_path

    p = sync_playwright().start()
    browser = p.chromium.launch(**launch_args)
    pool["playwright"] = p
    pool["browser"] = browser
    return browser


def release_browser() -> None:
    pool = _get_thread_pool()
    try:
        if "browser" in pool:
            try:
                pool["browser"].close()
            except Exception:
                pass
            del pool["browser"]
    except Exception:
        pass
    try:
        if "playwright" in pool:
            try:
                pool["playwright"].stop()
            except Exception:
                pass
            del pool["playwright"]
    except Exception:
        pass


def launch_browser(
    cookies: list[dict[str, Any]] | None = None,
    storage_state: dict[str, Any] | None = None,
) -> tuple[Browser, BrowserContext, Page]:
    browser = get_browser()

    context_args: dict[str, Any] = {
        "viewport": {"width": 1440, "height": 1000},
        "locale": "zh-CN",
    }
    if storage_state:
        if isinstance(storage_state, dict):
            context_args["storage_state"] = storage_state
        elif isinstance(storage_state, str):
            try:
                context_args["storage_state"] = json.loads(storage_state)
            except json.JSONDecodeError:
                context_args["storage_state"] = storage_state

    context = browser.new_context(**context_args)

    if not storage_state and cookies:
        context.add_cookies(_normalize_cookies(cookies))

    page = context.new_page()
    return browser, context, page


def _cookies_to_storage_state(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cookies": _normalize_cookies(cookies),
        "origins": [
            {
                "origin": "https://www.douyin.com",
                "localStorage": [],
            }
        ],
    }


def fetch_chat_contacts(
    cookies: list[dict[str, Any]] | None = None,
    storage_state: dict[str, Any] | None = None,
    account_id: int = 0,
) -> dict[str, Any]:
    result: dict[str, Any] = {"at": _now(), "names": [], "error": None}
    context = None

    if not cookies and not storage_state:
        result["error"] = "未配置登录凭据"
        return result

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=settings.headless,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
            ],
        )
        if storage_state and isinstance(storage_state, (dict, str)):
            context = browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1366, "height": 768},
            )
        else:
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
            )
            if cookies:
                context.add_cookies(_normalize_cookies(cookies))
        page = context.new_page()

        goto_ok = False
        for attempt in range(3):
            try:
                page.goto(DOUYIN_CHAT_URL, timeout=30000, wait_until="domcontentloaded")
                goto_ok = True
                break
            except Exception as e:
                logger.info("获取联系人时第 %s 次打开页面失败: %s", attempt + 1, e)
                time.sleep(5)
        if not goto_ok:
            result["error"] = "无法打开抖音私信页面"
            return result

        page.wait_for_timeout(10000)
        logged, why = check_login(page)
        if not logged:
            result["error"] = why
            return result

        if _any_visible(page, RISK_MARKERS, timeout_ms=2000):
            result["error"] = "检测到安全验证页面，请手动完成验证"
            return result

        extract_js = """
            () => {
                const out = [];
                const seen = new Set();
                document.querySelectorAll('[class*="item-header-name-"]').forEach(t => {
                    const name = (t.textContent || '').trim();
                    if (!name || seen.has(name)) return;
                    seen.add(name);
                    out.push({ name: name, streak: '' });
                });
                return out;
            }
        """

        collected: list[dict] = []
        for attempt in range(3):
            try:
                page.wait_for_selector(
                    '[class*="item-header-name-"]', timeout=15000
                )
            except Exception:
                logger.info("第 %s 次等待联系人列表超时，页面URL: %s", attempt + 1, page.url)
                try:
                    logger.info("页面标题: %s", page.title())
                except Exception:
                    pass

            stable = 0
            for _ in range(20):
                data = page.evaluate(extract_js) or []
                new_items = [x for x in data if x not in collected]
                if new_items:
                    collected.extend(new_items)
                    stable = 0
                else:
                    stable += 1
                    if stable >= 2:
                        break
                try:
                    page.mouse.move(200, 350)
                    page.mouse.wheel(0, 800)
                except Exception:
                    pass
                page.wait_for_timeout(1200)

            if collected:
                break
            try:
                page.reload(wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(12000)
            except Exception:
                pass

        result["names"] = collected
        logger.info("已读取聊天列表联系人 %s 个", len(result["names"]))
    except Exception as e:
        logger.error("获取联系人异常: %s", e)
        result["error"] = f"获取联系人异常: {e}"
    finally:
        if account_id and context and browser and pw:
            cache_browser_session(account_id, pw, browser, context)
        else:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
    return result


def extract_logged_in_nickname(
    cookies: list[dict[str, Any]] | None = None,
    storage_state: dict[str, Any] | None = None,
) -> str | None:
    context = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=settings.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        if storage_state and isinstance(storage_state, (dict, str)):
            context = browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1366, "height": 768},
            )
        elif cookies:
            context = browser.new_context(
                storage_state=_cookies_to_storage_state(cookies),
                viewport={"width": 1366, "height": 768},
            )
        else:
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
            )
        page = context.new_page()
        page.goto(DOUYIN_CHAT_URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        logged, _ = check_login(page)
        if not logged:
            return None
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
        return nickname if nickname else None
    except Exception:
        return None
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass


def run_send_task(
    friend_name: str,
    message: str,
    message_type: str = "text",
    image_path: str = "",
    sticker_name: str = "",
    dry_run: bool = False,
    cookies: list[dict[str, Any]] | None = None,
    storage_state: dict[str, Any] | None = None,
    account_id: int = 0,
) -> tuple[bool, str]:
    if _already_sent_today(friend_name):
        return False, "今日已发送"

    context = None
    page = None
    browser = None
    pw = None
    own_session = False

    try:
        cached = get_cached_browser_session(account_id) if account_id else None
        if cached:
            pw, browser, context = cached
            page = context.new_page()
        elif not cookies and not storage_state:
            return False, "未配置登录凭据"
        else:
            own_session = True
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=settings.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            if storage_state and isinstance(storage_state, (dict, str)):
                context = browser.new_context(
                    storage_state=storage_state,
                    viewport={"width": 1366, "height": 768},
                )
            else:
                context = browser.new_context(
                    viewport={"width": 1366, "height": 768},
                )
                if cookies:
                    context.add_cookies(_normalize_cookies(cookies))
            page = context.new_page()

        goto_ok = False
        for attempt in range(3):
            try:
                page.goto(DOUYIN_CHAT_URL, timeout=60000, wait_until="domcontentloaded")
                goto_ok = True
                break
            except Exception as e:
                logger.info("第 %s 次打开页面失败: %s", attempt + 1, e)
                time.sleep(5)
        if not goto_ok:
            return False, "无法打开抖音私信页面"

        time.sleep(8)
        logged, why = check_login(page)
        if not logged:
            return False, why

        if _any_visible(page, RISK_MARKERS, timeout_ms=2000):
            return False, "检测到安全验证页面"

        success, reason = open_and_send_message(
            page,
            friend_name,
            message,
            message_type,
            image_path=image_path,
            sticker_name=sticker_name,
            dry_run=dry_run,
        )

        if success:
            _mark_sent_today(friend_name)

        return success, reason
    except Exception as e:
        logger.error("发送任务异常: %s", e)
        return False, f"运行异常: {e}"
    finally:
        if page and not cached:
            try:
                page.close()
            except Exception:
                pass
        if own_session:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass
