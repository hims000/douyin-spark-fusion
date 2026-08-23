FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir playwright fastapi uvicorn aiosqlite apscheduler pydantic aiohttp python-multipart
RUN playwright install --with-deps chromium

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libnss3 libnspr4 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libasound2t64 curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /root/.cache/ms-playwright /home/appuser/.cache/ms-playwright
COPY . /app
WORKDIR /app
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /home/appuser
USER appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/home/appuser/.cache/ms-playwright
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD curl -f http://localhost:8000/api/health || exit 1
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]