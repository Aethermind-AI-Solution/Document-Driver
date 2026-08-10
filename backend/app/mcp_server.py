import secrets
from types import SimpleNamespace
from . import config


def build_service_principal() -> SimpleNamespace:
    """The fixed identity all MCP tool calls act as (for audit stamping)."""
    return SimpleNamespace(id=None, email="mcp-service", role=config.MCP_SERVICE_ROLE)


class TokenAuthASGI:
    """ASGI middleware: require a Bearer token on HTTP requests before delegating."""
    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        presented = auth[7:] if auth.startswith("Bearer ") else ""
        if not (presented and secrets.compare_digest(presented, self.token)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)
