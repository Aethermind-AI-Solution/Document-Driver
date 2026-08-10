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
