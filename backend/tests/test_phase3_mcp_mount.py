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
    with TestClient(app) as client:
        # a request to the mounted MCP path without the token is rejected
        r = client.post("/mcp/", headers={"Content-Type": "application/json"}, json={})
        assert r.status_code == 401

        # an authenticated request reaches the MCP app at the spec'd /mcp path
        # (not /mcp/mcp) - proves the path, auth, and lifespan all work.
        r = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert r.status_code not in (404, 401)
