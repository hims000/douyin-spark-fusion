# API Reference

Base URL: `http://localhost:8000`

Authentication: Bearer Token (via `Authorization: Bearer <token>` header). Tokens are obtained from `/api/auth/login` or `/api/auth/register`.

Error responses follow the format:
```json
{"code": "ERROR_CODE", "message": "Human-readable error message"}
```

## Health

### GET /metrics

No auth. Returns Prometheus-format metrics.

**Response** (text/plain):
```
# HELP process_virtual_memory_bytes Virtual memory usage
# TYPE process_virtual_memory_bytes gauge
process_virtual_memory_bytes 123456789.0
...
```

**curl example:**
```bash
curl http://localhost:8000/metrics
```

---

### GET /api/health

No auth. Returns system health status including uptime, version, DB/Playwright status, and memory usage.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-08-24T12:00:00+08:00",
  "version": "1.0.0",
  "db": "ok",
  "playwright": "ok",
  "memory_mb": 45.2
}
```

**curl example:**
```bash
curl http://localhost:8000/api/health
```

---

### GET /api/health/live

No auth. Simple liveness probe.

**Response:**
```json
{"status": "ok"}
```

**curl example:**
```bash
curl http://localhost:8000/api/health/live
```

---

### GET /api/health/ready

No auth. Readiness probe checking DB and Playwright availability.

**Response:**
```json
{
  "status": "ready",
  "db": "ok",
  "playwright": "ok"
}
```

**curl example:**
```bash
curl http://localhost:8000/api/health/ready
```

## Auth

### POST /api/auth/register

No auth. Create a new user account (requires `ALLOW_REGISTRATION=true`).

**Request body:**
```json
{
  "username": "myuser",
  "password": "mypassword"
}
```

**Validation:** username 2-50 chars, password 4-100 chars.

**Response:**
```json
{
  "token": "abc123...",
  "user": {"id": 2, "username": "myuser", "is_admin": false}
}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"myuser","password":"mypassword"}'
```

---

### POST /api/auth/login

No auth. Authenticate with username and password.

**Request body:**
```json
{
  "username": "admin",
  "password": "spark2024"
}
```

**Response:**
```json
{
  "token": "abc123...",
  "user": {"id": 1, "username": "admin", "is_admin": true}
}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"spark2024"}'
```

---

### GET /api/auth/me

Auth required. Returns the currently authenticated user.

**Response:**
```json
{
  "user": {"id": 1, "username": "admin", "is_admin": true}
}
```

**curl example:**
```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/auth/logout

Auth optional. Invalidates the current session token.

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer <token>"
```

## Accounts

### GET /api/accounts

Auth required. Lists all douyin accounts (own accounts for normal users, all accounts for admins).

**Response:**
```json
[
  {
    "id": 1,
    "user_id": 1,
    "name": "My Account",
    "phone": "13800138000",
    "cookies": "[...]",
    "storage_state": "{...}",
    "send_gap_min": 10,
    "send_gap_max": 20,
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00"
  }
]
```

**curl example:**
```bash
curl http://localhost:8000/api/accounts \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/accounts

Auth required. Create a new douyin account.

**Request body:**
```json
{
  "name": "My Account",
  "phone": "13800138000"
}
```

**Validation:** name 1-100 chars, phone max 50 chars.

**Response:**
```json
{"id": 1}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/accounts \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"My Account","phone":"13800138000"}'
```

---

### PUT /api/accounts/{account_id}

Auth required. Update account name, phone, and send gap settings.

**Request body:**
```json
{
  "name": "Updated Name",
  "phone": "13900139000",
  "send_gap_min": 10,
  "send_gap_max": 20
}
```

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X PUT http://localhost:8000/api/accounts/1 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Updated Name","phone":"13900139000","send_gap_min":10,"send_gap_max":20}'
```

---

### DELETE /api/accounts/{account_id}

Auth required. Deletes the account and all associated tasks, friends, and history.

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X DELETE http://localhost:8000/api/accounts/1 \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/accounts/{account_id}/cookies

Auth required. Upload browser cookies (JSON array) for a douyin account.

**Request body:**
```json
{
  "cookies": [
    {"name": "sessionid", "value": "abc123", "domain": ".douyin.com", "path": "/", "httpOnly": true, "secure": true}
  ]
}
```

**Response:**
```json
{"success": true, "cookie_count": 1}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/accounts/1/cookies \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"cookies":[{"name":"sessionid","value":"abc123","domain":".douyin.com"}]}'
```

---

### POST /api/accounts/{account_id}/storage-state

Auth required. Upload Playwright storage state JSON for a douyin account.

**Request body:**
```json
{
  "storage_state": {"cookies": [...], "origins": [...]}
}
```

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/accounts/1/storage-state \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"storage_state":{"cookies":[],"origins":[]}}'
```

---

### POST /api/accounts/{account_id}/verify-login

Auth required. Checks if the account's cookies/storage state are still valid by launching a browser and navigating to douyin chat.

**Response:**
```json
{"success": true, "error": null}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/accounts/1/verify-login \
  -H "Authorization: Bearer <token>"
```

## Friends

### GET /api/friends

Auth required. Lists all friends. Optionally filter by `account_id` query parameter.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| account_id | int | No | Filter friends by account ID |

**Response:**
```json
[
  {
    "id": 1,
    "account_id": 1,
    "user_id": 1,
    "name": "Friend Name",
    "spark_days": 30,
    "created_at": "2026-01-01T00:00:00"
  }
]
```

**curl example:**
```bash
curl "http://localhost:8000/api/friends?account_id=1" \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/friends/sync

Auth required. Fetch and sync friend list from douyin chat contacts for a given account. Requires Playwright to launch a browser and scrape the chat contact list.

**Request body:**
```json
{
  "account_id": 1
}
```

**Response:**
```json
{"success": true, "count": 29}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/friends/sync \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"account_id":1}'
```

## Messages

### POST /api/messages/send

Auth required. Send a message to a friend via douyin chat automation.

**Request body:**
```json
{
  "account_id": 1,
  "friend_name": "Friend Name",
  "message": "Hello {{friend}}! Happy {{weekday}}!",
  "message_type": "text",
  "dry_run": false,
  "image_path": "",
  "sticker_name": ""
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| account_id | int | Yes | Account ID to send from |
| friend_name | string | Yes | Friend name (1-100 chars) |
| message | string | Yes | Message content (max 5000 chars, supports template variables) |
| message_type | string | No | `text`, `image`, `sticker`, or `random` (default: `text`) |
| dry_run | bool | No | If true, only simulates without sending (default: false) |
| image_path | string | No | Path to image file (max 500 chars) |
| sticker_name | string | No | Sticker name (max 100 chars) |

**Response (success):**
```json
{"success": true, "code": null, "message": "消息发送成功"}
```

**Response (failure):**
```json
{"success": false, "code": "SEND_001", "message": "未找到好友"}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/messages/send \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"account_id":1,"friend_name":"Friend Name","message":"Hello!","message_type":"text"}'
```

---

### POST /api/messages/preview

Auth required. Renders a message template with provided context variables for preview.

**Request body:**
```json
{
  "template": "Hello {{friend}}, today is {{date}}",
  "account": "My Account",
  "friend": "Friend Name",
  "yiyan": "人生苦短，及时行乐",
  "from": "一言",
  "spark_days": "100"
}
```

**Template variables:**

| Variable | Description |
|----------|-------------|
| `{{friend}}` | Friend name |
| `{{account}}` | Account name |
| `{{date}}` | Current date (YYYY-MM-DD) |
| `{{time}}` | Current time (HH:MM) |
| `{{weekday}}` | Current weekday (Chinese) |
| `{{spark_days}}` | Spark streak days |
| `{{yiyan}}` | Yiyan quote content |
| `{{from}}` | Yiyan quote source |

**Response:**
```json
{
  "rendered": "Hello Friend Name, today is 2026-08-24",
  "context": {
    "account": "My Account",
    "friend": "Friend Name",
    "yiyan": "人生苦短，及时行乐",
    "from": "一言",
    "date": "2026-08-24",
    "time": "21:00",
    "weekday": "星期一",
    "spark_days": "100"
  }
}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/messages/preview \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"template":"Hello {{friend}}!","friend":"Test"}'
```

## Tasks

### GET /api/tasks

Auth required. Lists all scheduled tasks (own tasks for normal users, all tasks for admins).

**Response:**
```json
[
  {
    "id": 1,
    "account_id": 1,
    "user_id": 1,
    "friend_name": "Friend Name",
    "message": "Hello {{friend}}!",
    "message_type": "text",
    "cron_expr": "0 9 * * *",
    "is_active": 1,
    "last_run": "2026-01-01T09:00:00",
    "created_at": "2026-01-01T00:00:00"
  }
]
```

**curl example:**
```bash
curl http://localhost:8000/api/tasks \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/tasks

Auth required. Create a new scheduled task with a cron expression.

**Request body:**
```json
{
  "account_id": 1,
  "friend_name": "Friend Name",
  "message": "Hello {{friend}}! Happy {{weekday}}!",
  "message_type": "text",
  "cron_expr": "0 9 * * *"
}
```

**Validation:** cron expression must be a valid 5-part cron format.

**Response:**
```json
{"id": 1}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"account_id":1,"friend_name":"Friend Name","message":"Hello!","cron_expr":"0 9 * * *"}'
```

---

### PUT /api/tasks/{task_id}

Auth required. Update an existing scheduled task.

**Request body:**
```json
{
  "account_id": 1,
  "friend_name": "Friend Name",
  "message": "Updated message",
  "message_type": "text",
  "cron_expr": "0 10 * * *",
  "is_active": 1
}
```

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X PUT http://localhost:8000/api/tasks/1 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"account_id":1,"friend_name":"Friend","message":"Updated!","cron_expr":"0 10 * * *","is_active":1}'
```

---

### DELETE /api/tasks/{task_id}

Auth required. Delete a scheduled task permanently.

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X DELETE http://localhost:8000/api/tasks/1 \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/tasks/{task_id}/run

Auth required. Trigger a scheduled task to execute immediately. Uses the task's stored account, friend, message, and message type.

**Response (success):**
```json
{"success": true, "code": null, "message": "消息发送成功"}
```

**Response (failure):**
```json
{"success": false, "code": "SEND_001", "message": "未找到好友"}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/tasks/1/run \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/tasks/run-all

Auth required. Execute all active scheduled tasks immediately.

**Response:**
```json
{
  "results": [
    {"task_id": 1, "success": true, "message": "消息发送成功"},
    {"task_id": 2, "success": false, "message": "限流冷却中"}
  ]
}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/tasks/run-all \
  -H "Authorization: Bearer <token>"
```

## Logs

### GET /api/logs

Auth required. Lists recent system logs.

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| limit | int | No | 50 | Number of log entries (1-500) |

**Response:**
```json
[
  {
    "id": 1,
    "account_id": 1,
    "task_id": 1,
    "user_id": 1,
    "friend_name": "Friend Name",
    "status": "success",
    "message": "消息发送成功",
    "reason": "消息发送成功",
    "created_at": "2026-01-01T09:00:00"
  }
]
```

**curl example:**
```bash
curl "http://localhost:8000/api/logs?limit=20" \
  -H "Authorization: Bearer <token>"
```

## Stats

### GET /api/stats

Auth required. Returns dashboard statistics.

**Response:**
```json
{
  "accounts": 3,
  "tasks": 5,
  "friends": 29,
  "today_sent": 12
}
```

**curl example:**
```bash
curl http://localhost:8000/api/stats \
  -H "Authorization: Bearer <token>"
```

## Settings

### GET /api/settings

Auth required. Retrieves current system configuration.

**Response:**
```json
{
  "schedule_time": "21:00",
  "jitter_minutes": 30,
  "send_gap_min": 6,
  "send_gap_max": 12,
  "max_friends_per_run": 20,
  "daily_limit": 50,
  "rate_limit_cooldown_minutes": 45,
  "retry_delay_minutes": 45,
  "allow_registration": true
}
```

**curl example:**
```bash
curl http://localhost:8000/api/settings \
  -H "Authorization: Bearer <token>"
```

---

### POST /api/settings

Admin only. Updates system configuration settings.

**Request body:**
```json
{
  "schedule_time": "21:00",
  "jitter_minutes": 30,
  "send_gap_min": 6,
  "send_gap_max": 12,
  "max_friends_per_run": 20,
  "daily_limit": 50,
  "rate_limit_cooldown_minutes": 45,
  "retry_delay_minutes": 45,
  "admin_pass": "newpassword"
}
```

All fields are optional. If `admin_pass` is provided, the admin user's password is updated.

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/settings \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"schedule_time":"22:00","jitter_minutes":15}'
```

## Notify

### POST /api/notify/test

Auth required. Sends a test notification through configured channels.

**Request body:**
```json
{
  "channel": "all",
  "title": "Test Notification",
  "content": "This is a test from Douyin Spark Fusion"
}
```

**Response:**
```json
{"success": true, "results": "..."}
```

**curl example:**
```bash
curl -X POST http://localhost:8000/api/notify/test \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"channel":"all","title":"Test","content":"Hello from API"}'
```

## Admin

### GET /api/admin/users

Admin only. Lists all registered users (password hashes excluded).

**Response:**
```json
[
  {
    "id": 1,
    "username": "admin",
    "is_admin": 1,
    "group_id": null,
    "created_at": "2026-01-01T00:00:00"
  }
]
```

**curl example:**
```bash
curl http://localhost:8000/api/admin/users \
  -H "Authorization: Bearer <token>"
```

---

### DELETE /api/admin/users/{user_id}

Admin only. Deletes a user and all associated data (accounts, tasks, friends, logs, history). Cannot delete yourself.

**Response:**
```json
{"success": true}
```

**curl example:**
```bash
curl -X DELETE http://localhost:8000/api/admin/users/2 \
  -H "Authorization: Bearer <token>"
```

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| AUTH_001 | 401 | Invalid username or password |
| AUTH_002 | 401 | Not logged in |
| AUTH_003 | 403 | Insufficient permissions (not admin or not owner) |
| ACCT_001 | 404 | Account not found |
| ACCT_002 | 400 | Invalid cookie/storage-state format |
| TASK_001 | 404 | Task not found |
| SEND_001 | — | Message send failure (returned in response body) |
| RATE_001 | 429 | Rate limited or in cooldown period |

## Rate Limiting

The API enforces rate limiting at 120 requests per minute per client IP. Excess requests receive HTTP 429 with `RATE_001` error code.

Additionally, the douyin message send endpoints have a built-in cooldown mechanism. When rate-limiting keywords are detected in douyin's response, all send operations are paused for the configured `rate_limit_cooldown_minutes` (default 45 minutes).