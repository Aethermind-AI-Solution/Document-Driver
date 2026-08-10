import asyncio
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from app.mcp_server import TokenAuthASGI


def _inner():
    return Starlette(routes=[Route("/", lambda r: PlainTextResponse("ok"))])


def test_missing_token_is_401():
    client = TestClient(TokenAuthASGI(_inner(), token="secret"))
    assert client.get("/").status_code == 401


def test_wrong_token_is_401():
    client = TestClient(TokenAuthASGI(_inner(), token="secret"))
    assert client.get("/", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_correct_token_passes_through():
    client = TestClient(TokenAuthASGI(_inner(), token="secret"))
    r = client.get("/", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.text == "ok"


def test_non_http_scope_passes_through_without_auth():
    called = {"v": False}
    async def inner(scope, receive, send):
        called["v"] = True
    sent = []
    async def send(m):
        sent.append(m)
    async def recv():
        return {"type": "lifespan.startup"}
    asyncio.run(TokenAuthASGI(inner, token="secret")({"type": "lifespan"}, recv, send))
    assert called["v"] is True and sent == []
