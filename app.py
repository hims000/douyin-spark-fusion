from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core.config import VERSION, settings
from core.metrics import get_memory_usage, get_metrics
from core.models import get_db, init_db
from routers import accounts, admin, auth, friends, logs, messages, tasks
from routers import settings as settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fusion-spark")

APP_START_TIME = time.time()

_rate_limit_store: dict[str, list[float]] = {}


def _check_rate_limit(key: str, max_requests: int = 120, window: int = 60) -> bool:
    now = time.time()
    if key not in _rate_limit_store:
        _rate_limit_store[key] = []
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window]
    if len(_rate_limit_store[key]) >= max_requests:
        return True
    _rate_limit_store[key].append(now)
    return False


def _cleanup_rate_limit_store():
    now = time.time()
    stale = [k for k, v in _rate_limit_store.items() if not v or all(now - t > 120 for t in v)]
    for k in stale:
        del _rate_limit_store[k]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _cleanup_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _cleanup_scheduler.add_job(_cleanup_rate_limit_store, IntervalTrigger(minutes=5), id="rate_limit_cleanup")
    _cleanup_scheduler.start()
    yield
    _cleanup_scheduler.shutdown(wait=False)


app = FastAPI(
    title="Douyin Spark Fusion API",
    description="Self-hosted douyin spark automation service. Automatically send messages to maintain friendship streaks on Douyin.",
    version=VERSION,
    openapi_tags=[
        {"name": "health", "description": "Health check endpoints"},
        {"name": "auth", "description": "User authentication (register/login/token)"},
        {"name": "accounts", "description": "Douyin account management (CRUD + cookie upload)"},
        {"name": "friends", "description": "Friend list sync and management"},
        {"name": "messages", "description": "Message sending and template preview"},
        {"name": "tasks", "description": "Scheduled task management (CRUD cron jobs)"},
        {"name": "logs", "description": "System logs and history"},
        {"name": "settings", "description": "System settings and configuration"},
        {"name": "admin", "description": "Admin-only endpoints (user management)"},
    ],
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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
async def logging_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method, request.url.path, response.status_code, duration
    )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(client_ip, max_requests=120, window=60):
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_001", "message": "请求过于频繁，请稍后再试"},
        )
    return await call_next(request)


app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(friends.router)
app.include_router(messages.router)
app.include_router(tasks.router)
app.include_router(logs.router)
app.include_router(settings_router.router)
app.include_router(admin.router)


@app.get("/metrics")
async def metrics():
    return Response(content=get_metrics(), media_type="text/plain")


@app.get("/api/health", tags=["health"], summary="Health check")
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
        "version": VERSION,
        "db": db_status,
        "playwright": playwright_status,
        "memory_mb": round(get_memory_usage(), 1),
        "uptime_seconds": int(time.time() - APP_START_TIME),
    }


@app.get("/api/health/ready", tags=["health"], summary="Readiness check")
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


@app.get("/api/health/live", tags=["health"], summary="Liveness check")
async def health_live():
    return {"status": "ok"}


@app.get("/api/status", tags=["health"], summary="System status")
async def system_status():
    import psutil

    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime = time.time() - APP_START_TIME
    days = int(uptime // 86400)
    hours = int((uptime % 86400) // 3600)
    minutes = int((uptime % 3600) // 60)
    uptime_display = f"{days}天{hours}小时{minutes}分" if days > 0 else f"{hours}小时{minutes}分"

    db = await get_db()
    today_sent = await db.execute_fetchall(
        "SELECT COUNT(*) as c FROM logs WHERE status='success' AND date(created_at)=date('now')"
    )
    today_total = await db.execute_fetchall(
        "SELECT COUNT(*) as c FROM logs WHERE date(created_at)=date('now')"
    )
    rate_limited = await db.execute_fetchall(
        "SELECT COUNT(*) as c FROM logs WHERE reason LIKE '%限流%' AND date(created_at)=date('now')"
    )

    cookie_valid = False
    try:
        row = await db.execute_fetchall(
            "SELECT id, cookies, storage_state FROM accounts WHERE (cookies IS NOT NULL AND cookies != '[]' AND cookies != '') OR (storage_state IS NOT NULL AND storage_state != '') LIMIT 1"
        )
        if row:
            acct = dict(row[0])
            cookies = json.loads(acct.get("cookies", "[]"))
            ss = acct.get("storage_state", "")
            storage_state = json.loads(ss) if ss else None
            if cookies or storage_state:
                from core.automation import DOUYIN_CHAT_URL, check_login, launch_browser

                try:
                    browser, context, page = launch_browser(cookies=cookies, storage_state=storage_state)
                    try:
                        page.goto(DOUYIN_CHAT_URL, timeout=15000, wait_until="domcontentloaded")
                        page.wait_for_timeout(5000)
                        logged, _ = check_login(page)
                        cookie_valid = logged
                    finally:
                        context.close()
                        browser.close()
                except Exception:
                    cookie_valid = False
            else:
                cookie_valid = True
    except Exception:
        cookie_valid = False

    await db.close()

    return {
        "cpu_percent": cpu,
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / 1024**3, 2),
        "memory_total_gb": round(mem.total / 1024**3, 2),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "uptime_hours": round(uptime / 3600, 1),
        "uptime_display": uptime_display,
        "today_sent": today_sent[0]["c"] if today_sent else 0,
        "today_total": today_total[0]["c"] if today_total else 0,
        "rate_limited": rate_limited[0]["c"] if rate_limited else 0,
        "cookie_valid": cookie_valid,
    }


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
