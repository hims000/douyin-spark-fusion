import pytest

@pytest.mark.asyncio
async def test_create_task(auth_client):
    acc = await auth_client.post("/api/accounts", json={"name": "任务账号", "phone": ""})
    acc_id = acc.json()["id"]
    resp = await auth_client.post("/api/tasks", json={
        "account_id": acc_id, "friend_name": "测试好友",
        "cron_expr": "0 9 * * *", "message": "续火花"
    })
    assert resp.status_code == 200
    assert "id" in resp.json()

@pytest.mark.asyncio
async def test_list_tasks(auth_client):
    acc = await auth_client.post("/api/accounts", json={"name": "列表账号", "phone": ""})
    acc_id = acc.json()["id"]
    await auth_client.post("/api/tasks", json={"account_id": acc_id, "friend_name": "好友A", "cron_expr": "0 8 * * *", "message": ""})
    await auth_client.post("/api/tasks", json={"account_id": acc_id, "friend_name": "好友B", "cron_expr": "0 9 * * *", "message": ""})
    resp = await auth_client.get("/api/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2

@pytest.mark.asyncio
async def test_delete_task(auth_client):
    acc = await auth_client.post("/api/accounts", json={"name": "删除账号", "phone": ""})
    acc_id = acc.json()["id"]
    task = await auth_client.post("/api/tasks", json={"account_id": acc_id, "friend_name": "好友", "cron_expr": "0 10 * * *", "message": ""})
    task_id = task.json()["id"]
    resp = await auth_client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_task_unauthorized(client):
    resp = await client.post("/api/tasks", json={"account_id": 1, "friend_name": "x", "cron_expr": "0 9 * * *", "message": ""})
    assert resp.status_code == 401