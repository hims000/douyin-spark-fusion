import hashlib
import hmac
import logging
import os
import time

import aiohttp

logger = logging.getLogger("notifier")

_notify_cache: dict[str, str] = {}
_cache_loaded = False


async def _load_notify_config() -> dict[str, str]:
    global _notify_cache, _cache_loaded
    if _cache_loaded:
        return _notify_cache

    config = {}
    try:
        from .models import get_db

        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT key, value FROM settings WHERE key LIKE 'notify_%'"
        )
        await db.close()
        for r in rows:
            key = r["key"].replace("notify_", "", 1)
            v = r["value"]
            if v:
                config[key] = v
    except Exception:
        pass

    for k in (
        "DINGTALK_WEBHOOK", "DINGTALK_SECRET",
        "FEISHU_WEBHOOK", "FEISHU_SECRET",
        "WECOM_WEBHOOK",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "BARK_DEVICE_KEY",
        "RESEND_API_KEY", "RESEND_FROM", "MAIL_TO",
    ):
        if k not in config or not config[k]:
            env_val = os.getenv(k, "")
            if env_val:
                config[k] = env_val

    _notify_cache = config
    _cache_loaded = True
    return config


def _invalidate_cache():
    global _cache_loaded
    _cache_loaded = False


async def send_dingtalk(webhook, secret, title, content):
    timestamp = str(round(time.time() * 1000))
    sign = hmac.new(
        secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256
    ).hexdigest()
    url = f"{webhook}&timestamp={timestamp}&sign={sign}"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": content}}
    async with aiohttp.ClientSession() as s, s.post(url, json=payload) as r:
        return await r.json()


async def send_feishu(webhook, secret, title, content):
    timestamp = str(int(time.time()))
    sign = hmac.new(f"{timestamp}\n{secret}".encode(), "", hashlib.sha256).hexdigest()
    payload = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    async with aiohttp.ClientSession() as s, s.post(webhook, json=payload) as r:
        return await r.json()


async def send_wecom(webhook, content):
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    async with aiohttp.ClientSession() as s, s.post(webhook, json=payload) as r:
        return await r.json()


async def send_telegram(token, chat_id, content):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": content, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as s, s.post(url, json=payload) as r:
        return await r.json()


async def send_bark(key, title, content, bark_url="https://api.day.app"):
    url = f"{bark_url}/{key}/"
    payload = {"title": title, "body": content, "group": "douyin-spark"}
    async with aiohttp.ClientSession() as s, s.post(url, json=payload) as r:
        return await r.json()


async def send_resend(api_key, sender, to, subject, html):
    payload = {"from": sender, "to": [to], "subject": subject, "html": html}
    async with aiohttp.ClientSession() as s, s.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    ) as r:
        data = await r.json()
        if r.status != 200:
            raise ValueError(data.get("message", "Resend API error"))
        return data


async def send_notification(title, content, screenshots=None):
    """统一通知入口。优先从 DB settings 表读取配置，回退到环境变量。"""
    cfg = await _load_notify_config()

    tasks = []
    if cfg.get("DINGTALK_WEBHOOK") and cfg.get("DINGTALK_SECRET"):
        tasks.append(
            send_dingtalk(
                cfg["DINGTALK_WEBHOOK"], cfg["DINGTALK_SECRET"], title, content
            )
        )
    if cfg.get("FEISHU_WEBHOOK") and cfg.get("FEISHU_SECRET"):
        tasks.append(
            send_feishu(
                cfg["FEISHU_WEBHOOK"], cfg["FEISHU_SECRET"], title, content
            )
        )
    if cfg.get("WECOM_WEBHOOK"):
        tasks.append(send_wecom(cfg["WECOM_WEBHOOK"], content))
    if cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"):
        tasks.append(
            send_telegram(
                cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"], content
            )
        )
    if cfg.get("BARK_DEVICE_KEY"):
        tasks.append(
            send_bark(
                cfg["BARK_DEVICE_KEY"],
                title,
                content,
                os.getenv("BARK_URL", "https://api.day.app"),
            )
        )
    if cfg.get("RESEND_API_KEY") and cfg.get("MAIL_TO"):
        tasks.append(
            send_resend(
                cfg["RESEND_API_KEY"],
                cfg.get("RESEND_FROM", "admin@hims.ccwu.cc"),
                cfg["MAIL_TO"],
                title,
                content,
            )
        )

    results = []
    for t in tasks:
        try:
            results.append(await t)
        except Exception as e:
            logger.warning("Notification failed: %s: %s", type(t).__name__, e)
    return results
