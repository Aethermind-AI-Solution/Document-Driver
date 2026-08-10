from types import SimpleNamespace
from . import config


def build_service_principal() -> SimpleNamespace:
    """The fixed identity all MCP tool calls act as (for audit stamping)."""
    return SimpleNamespace(id=None, email="mcp-service", role=config.MCP_SERVICE_ROLE)
