import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

@pytest.mark.asyncio
async def test_rate_limit_keywords():
    from core.automation import RATE_LIMIT_KEYWORDS
    assert len(RATE_LIMIT_KEYWORDS) == 12
    assert "操作频繁" in RATE_LIMIT_KEYWORDS
    assert "安全验证" in RATE_LIMIT_KEYWORDS

@pytest.mark.asyncio
async def test_rate_limit_detection():
    from core.automation import RATE_LIMIT_KEYWORDS
    assert all(isinstance(kw, str) for kw in RATE_LIMIT_KEYWORDS)