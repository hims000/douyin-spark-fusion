import pytest


@pytest.mark.asyncio
async def test_render_template_all_placeholders(auth_client):
    resp = await auth_client.post(
        "/api/messages/preview",
        json={
            "template": "{{account}}向{{friend}}发送：{{yiyan}}——{{from}} {{date}} {{weekday}}",
            "account": "账号1",
            "friend": "好友A",
            "yiyan": "今天天气真好",
            "from": "一言",
            "spark_days": "100",
        },
    )
    assert resp.status_code == 200
    rendered = resp.json()["rendered"]
    assert "账号1" in rendered
    assert "好友A" in rendered
    assert "今天天气真好" in rendered


@pytest.mark.asyncio
async def test_render_template_newline(auth_client):
    resp = await auth_client.post(
        "/api/messages/preview",
        json={
            "template": "第一行\\n第二行",
            "account": "a",
            "friend": "b",
            "yiyan": "",
            "from": "",
            "spark_days": "0",
        },
    )
    assert resp.status_code == 200
    assert "\\n" in resp.json()["rendered"]


@pytest.mark.asyncio
async def test_render_template_unknown_placeholder(auth_client):
    resp = await auth_client.post(
        "/api/messages/preview",
        json={
            "template": "{{unknown_placeholder}}",
            "account": "a",
            "friend": "b",
            "yiyan": "",
            "from": "",
            "spark_days": "0",
        },
    )
    assert resp.status_code == 200
    rendered = resp.json()["rendered"]
    assert "{{unknown_placeholder}}" not in rendered
