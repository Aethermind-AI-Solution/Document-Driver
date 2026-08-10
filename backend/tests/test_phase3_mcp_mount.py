from fastapi import FastAPI
from starlette.testclient import TestClient
from app import config, main


def test_mount_mcp_disabled_when_no_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_API_TOKEN", "")
    app = FastAPI()
    assert main.mount_mcp(app) is False
    assert not any(getattr(r, "path", "").startswith("/mcp") for r in app.routes)


def test_mount_mcp_enabled_and_requires_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_API_TOKEN", "secret")
    app = FastAPI()
    assert main.mount_mcp(app) is True
    # a request to the mounted MCP path without the token is rejected
    client = TestClient(app)
    r = client.post("/mcp/", headers={"Content-Type": "application/json"}, json={})
    assert r.status_code == 401
