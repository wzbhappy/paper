"""后端骨架冒烟测试：用 SQLite 内存库验证 health 与 projects CRUD 可用。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.main import app
from app.models import Base


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_openapi_exposes_projects():
    assert "/api/v1/projects" in app.openapi()["paths"]


@pytest.mark.asyncio
async def test_create_and_list_projects(client):
    created = await client.post(
        "/api/v1/projects",
        json={"title": "图神经网络综述", "discipline": "计算机科学"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "图神经网络综述"
    assert body["stage"] == "discovery"
    assert body["id"]

    listed = await client.get("/api/v1/projects")
    assert listed.status_code == 200
    titles = [p["title"] for p in listed.json()]
    assert "图神经网络综述" in titles
