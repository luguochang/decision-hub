from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.workbench_adapters.mcp import build_core_mcp_server


def create_server() -> MCPServer:
    database = Database()
    database.initialize()
    return build_core_mcp_server(
        WorkbenchAssetService(database),
        owner_id=os.getenv("DECISION_HUB_OWNER_ID", "owner"),
    )


def run() -> None:
    server = create_server()
    transport = os.getenv("DECISION_HUB_MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise SystemExit("DECISION_HUB_MCP_TRANSPORT must be stdio, sse, or streamable-http")
    if transport == "streamable-http":
        server.run(
            "streamable-http",
            host=os.getenv("DECISION_HUB_MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("DECISION_HUB_MCP_PORT", "8001")),
            stateless_http=True,
        )
    elif transport == "sse":
        server.run("sse")
    else:
        server.run("stdio")


if __name__ == "__main__":
    run()
