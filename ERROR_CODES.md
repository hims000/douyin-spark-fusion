# Error Codes Reference

| Code | HTTP Status | Meaning | Trigger | Solution |
|------|-------------|---------|---------|----------|
| AUTH_001 | 401 | Invalid credentials | Wrong username or password | Check credentials |
| AUTH_002 | 401 | Token expired | Session timeout | Re-login |
| AUTH_003 | 403 | Permission denied | Non-admin accessing admin endpoint | Contact admin |
| ACCT_001 | 404 | Account not found | Invalid account ID | Check account ID |
| ACCT_002 | 400 | Cookie invalid | Malformed or empty cookie JSON | Re-export cookies from browser |
| TASK_001 | 404 | Task not found | Invalid task ID | Check task ID |
| SEND_001 | 500 | Send failed | Playwright error or network issue | Check logs, retry |
| RATE_001 | 429 | Rate limited | Douyin rate limit detected | Wait 45 minutes before retry |