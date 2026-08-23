import os, json, hmac, hashlib, time, smtplib, logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import aiohttp

logger = logging.getLogger("notifier")

async def send_dingtalk(webhook, secret, title, content):
    timestamp = str(round(time.time() * 1000))
    sign = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).hexdigest()
    url = f"{webhook}&timestamp={timestamp}&sign={sign}"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": content}}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            return await r.json()

async def send_feishu(webhook, secret, title, content):
    timestamp = str(int(time.time()))
    sign = hmac.new(f"{timestamp}\n{secret}".encode(), "", hashlib.sha256).hexdigest()
    payload = {"timestamp": timestamp, "sign": sign, "msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": title}}, "elements": [{"tag": "markdown", "content": content}]}}
    async with aiohttp.ClientSession() as s:
        async with s.post(webhook, json=payload) as r:
            return await r.json()

async def send_wecom(webhook, content):
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    async with aiohttp.ClientSession() as s:
        async with s.post(webhook, json=payload) as r:
            return await r.json()

async def send_telegram(token, chat_id, content):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": content, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            return await r.json()

async def send_bark(key, title, content, bark_url="https://api.day.app"):
    url = f"{bark_url}/{key}/"
    payload = {"title": title, "body": content, "group": "douyin-spark"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            return await r.json()

async def send_email(host, port, user, password, to, subject, body, attachments=None):
    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html", "utf-8"))
    if attachments:
        for path in attachments:
            with open(path, "rb") as f:
                img = MIMEImage(f.read())
                img.add_header("Content-Disposition", "attachment", filename=os.path.basename(path))
                msg.attach(img)
    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.sendmail(user, [to], msg.as_string())

async def send_notification(title, content, screenshots=None):
    """统一通知入口，根据环境变量自动启用已配置的通知渠道。失败隔离。"""
    tasks = []
    if os.getenv("DINGTALK_WEBHOOK") and os.getenv("DINGTALK_SECRET"):
        tasks.append(send_dingtalk(os.getenv("DINGTALK_WEBHOOK"), os.getenv("DINGTALK_SECRET"), title, content))
    if os.getenv("FEISHU_WEBHOOK") and os.getenv("FEISHU_SECRET"):
        tasks.append(send_feishu(os.getenv("FEISHU_WEBHOOK"), os.getenv("FEISHU_SECRET"), title, content))
    if os.getenv("WECOM_WEBHOOK"):
        tasks.append(send_wecom(os.getenv("WECOM_WEBHOOK"), content))
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        tasks.append(send_telegram(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), content))
    if os.getenv("BARK_DEVICE_KEY"):
        tasks.append(send_bark(os.getenv("BARK_DEVICE_KEY"), title, content, os.getenv("BARK_URL", "https://api.day.app")))
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS") and os.getenv("MAIL_TO"):
        tasks.append(send_email(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "465")), os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"), os.getenv("MAIL_TO"), title, content, screenshots))
    results = []
    for t in tasks:
        try:
            results.append(await t)
        except Exception as e:
            logger.warning(f"Notification failed: {type(t).__name__}: {e}")
    return results