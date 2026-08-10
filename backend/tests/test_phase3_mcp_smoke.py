def test_fastmcp_importable_and_builds():
    from mcp.server.fastmcp import FastMCP
    s = FastMCP("probe", stateless_http=True)
    app = s.streamable_http_app()
    assert app is not None


def test_service_principal_shape(monkeypatch):
    from app import config, mcp_server
    monkeypatch.setattr(config, "MCP_SERVICE_ROLE", "reviewer")
    p = mcp_server.build_service_principal()
    assert p.id is None and p.email == "mcp-service" and p.role == "reviewer"
