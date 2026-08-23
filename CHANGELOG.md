# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.0.1 (2026-08-24)

### Added
- AI message generation via OpenAI API
- Anti-duplicate sending (same friend, same day)
- Message history table with send records
- System status page (CPU, memory, disk, uptime)
- WebSocket real-time push for send progress
- GitHub Pages documentation site
- CI/CD: ruff + pytest + Docker build + Codecov

### Fixed
- Browser Pool: reuse context for faster hot restart
- SQLite: composite indexes for logs and message_history
- Async Playwright: ThreadPoolExecutor wrapper for sync calls

## v1.0.0 (2026-08-24)

### Features
- 5-layer friend search with spatial bounding box verification
- 4 message types: text, image, sticker, random
- 12-keyword rate limit detection
- 7 notification channels: DingTalk, Feishu, WeCom, Telegram, Bark, Email, Browser
- Multi-user system with SQLite + admin panel
- Docker + docker-compose + nginx + certbot
- Prometheus metrics + Grafana dashboard
- 20 automated E2E tests
- Dark theme with localStorage persistence
- Mobile responsive design
- One-click deploy.sh script
- GitHub Actions CI/CD

### Security
- No eval() — uses json.loads() exclusively
- PBKDF2-HMAC password hashing with salt
- No hardcoded credentials
- Rate limiting on API endpoints
- CORS with specific origins

### Verified
- 29 friends synced successfully
- 5+ messages sent to real douyin accounts
- 20/20 tests passing
- ruff 0 errors