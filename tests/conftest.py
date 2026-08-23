import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import app
from core.models import init_db


@pytest.fixture(autouse=True)
def setup_db():
    import asyncio as _asyncio

    _asyncio.run(init_db())
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def auth_client(client):
    await client.post(
        "/api/auth/register", json={"username": "testuser", "password": "test1234"}
    )
    resp = await client.post(
        "/api/auth/login", json={"username": "testuser", "password": "test1234"}
    )
    token = resp.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
