import asyncio
import io
import json
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import github
from app.main import app
from app.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    with TestClient(app) as c:
        yield c


def wait(client, scan_id):
    import time
    for _ in range(100):
        r = client.get("/api/scans/" + scan_id).json()
        if r["status"] in {"complete", "partial", "failed"}:
            return r
        time.sleep(.02)
    raise AssertionError("task did not finish")


def test_full_api_flow_and_ai_disabled(client, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr(github, "resolve", AsyncMock(return_value=("main", "a" * 40)))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("root/main.py", 'password="synthetic-value"\n')
    monkeypatch.setattr(github, "download", AsyncMock(return_value=stream.getvalue()))
    response = client.post("/api/scans", json={"repository_url": "https://github.com/example/repo"})
    assert response.status_code == 202
    scan_id = response.json()["id"]
    record = wait(client, scan_id)
    assert record["status"] == "complete"
    assert record["report"]["commit_sha"] == "a" * 40
    assert "synthetic-value" not in json.dumps(record)
    download = client.get(f"/api/scans/{scan_id}/report")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert "synthetic-value" not in download.text
    advice = client.post(f"/api/scans/{scan_id}/advice")
    assert advice.status_code == 200 and not advice.json()["available"]


def test_invalid_urls_and_not_found(client):
    assert client.post("/api/scans", json={"repository_url": "https://evil.com/a/b"}).status_code == 422
    assert client.get("/api/scans/unknown").status_code == 404
    assert client.get("/").status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


def test_network_failure_is_actionable(client, monkeypatch):
    monkeypatch.setattr(github, "resolve", AsyncMock(side_effect=httpx.ConnectError("must not expose details")))
    scan_id = client.post("/api/scans", json={"repository_url": "https://github.com/example/repo"}).json()["id"]
    record = wait(client, scan_id)
    assert record["status"] == "failed" and "网络" in record["error"]
    assert "must not expose" not in str(record)
    assert client.get(f"/api/scans/{scan_id}/report").status_code == 409


def test_store_marks_interrupted_tasks_failed(tmp_path):
    path = str(tmp_path / "store.sqlite3")
    store = Store(path)
    store.put({"id": "x", "status": "running"})
    restarted = Store(path)
    assert restarted.get("x")["status"] == "failed"


def test_github_rate_limit_and_no_redirect_following():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429))) as c:
            with pytest.raises(github.ScanError, match="限流"):
                await github.api_json(c, "/repos/example/repo")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"Location": "https://evil.com"})), follow_redirects=False) as c:
            with pytest.raises(github.ScanError):
                await github.download(c, "example/repo", "a" * 40)
    asyncio.run(run())


def test_github_empty_repository():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(409))) as c:
            with pytest.raises(github.ScanError, match="为空"):
                await github.api_json(c, "/repos/example/repo/commits/main")
    asyncio.run(run())
