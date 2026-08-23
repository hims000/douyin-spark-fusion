import pytest


@pytest.mark.asyncio
async def test_create_account(auth_client):
    resp = await auth_client.post(
        "/api/accounts", json={"name": "测试账号", "phone": "13800138000"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data


@pytest.mark.asyncio
async def test_list_accounts(auth_client):
    await auth_client.post("/api/accounts", json={"name": "账号1", "phone": ""})
    await auth_client.post("/api/accounts", json={"name": "账号2", "phone": ""})
    resp = await auth_client.get("/api/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_update_account(auth_client):
    resp = await auth_client.post("/api/accounts", json={"name": "旧名称", "phone": ""})
    acc_id = resp.json()["id"]
    resp = await auth_client.put(
        f"/api/accounts/{acc_id}", json={"name": "新名称", "phone": "13900001111"}
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_delete_account(auth_client):
    resp = await auth_client.post("/api/accounts", json={"name": "待删除", "phone": ""})
    acc_id = resp.json()["id"]
    resp = await auth_client.delete(f"/api/accounts/{acc_id}")
    assert resp.status_code == 200
    resp = await auth_client.get("/api/accounts")
    assert all(a["id"] != acc_id for a in resp.json())


@pytest.mark.asyncio
async def test_account_unauthorized(client):
    resp = await client.post("/api/accounts", json={"name": "unauth", "phone": ""})
    assert resp.status_code == 401
