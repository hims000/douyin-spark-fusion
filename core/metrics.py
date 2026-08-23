from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY
import time
import psutil
import os

SEND_TOTAL = Counter('douyin_send_total', 'Total send attempts', ['status'])
SEND_DURATION = Histogram('douyin_send_duration_seconds', 'Send duration in seconds', buckets=[5, 10, 15, 20, 30, 45, 60, 120])
ACTIVE_SESSIONS = Gauge('douyin_active_sessions', 'Active Playwright browser sessions')
COOKIE_VALID = Gauge('douyin_cookie_valid', 'Whether the douyin cookie is valid (1=valid, 0=invalid)')
API_REQUEST_DURATION = Histogram('http_request_duration_seconds', 'API request duration', ['method', 'endpoint'])
DB_QUERY_DURATION = Histogram('db_query_duration_seconds', 'Database query duration', ['operation'])

def get_metrics():
    return generate_latest(REGISTRY)

def increment_send(status: str):
    SEND_TOTAL.labels(status=status).inc()

def observe_send_duration(seconds: float):
    SEND_DURATION.observe(seconds)

def set_active_sessions(count: int):
    ACTIVE_SESSIONS.set(count)

def set_cookie_valid(valid: bool):
    COOKIE_VALID.set(1 if valid else 0)

def get_memory_usage():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024