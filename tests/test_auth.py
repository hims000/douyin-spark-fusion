import uuid

import pytest


@pytest.mark.asyncio
async def test_register(client):
    uname = f"test_{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/auth/register", json={"username": uname, "password": "pass1234"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["user"]["username"] == uname


@pytest.mark.asyncio
async def test_login(client):
    uname = f"login_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/auth/register", json={"username": uname, "password": "pass1234"}
    )
    resp = await client.post(
        "/api/auth/login", json={"username": uname, "password": "pass1234"}
    )
    assert resp.status_code == 200
    assert "token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    uname = f"wrong_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/auth/register", json={"username": uname, "password": "pass1234"}
    )
    resp = await client.post(
        "/api/auth/login", json={"username": uname, "password": "wrong"}
    )
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
