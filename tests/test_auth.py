import pytest

@pytest.mark.asyncio
async def test_register(client):
    resp = await client.post("/api/auth/register", json={"username": "newuser", "password": "pass1234"})
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["user"]["username"] == "newuser"

@pytest.mark.asyncio
async def test_login(client):
    await client.post("/api/auth/register", json={"username": "loginuser", "password": "pass1234"})
    resp = await client.post("/api/auth/login", json={"username": "loginuser", "password": "pass1234"})
    assert resp.status_code == 200
    assert "token" in resp.json()

@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/auth/register", json={"username": "wrongpw", "password": "pass1234"})
    resp = await client.post("/api/auth/login", json={"username": "wrongpw", "password": "wrong"})
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_me(auth_client):
    resp = await auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "testuser"

@pytest.mark.asyncio
async def test_unauthorized(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401