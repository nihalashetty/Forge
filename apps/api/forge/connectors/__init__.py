"""Connectors - pre-built integrations (Slack, Gmail, Outlook, …) as installable manifests.

A connector is a RECIPE, not a runtime. Installing one expands a manifest into the entities
Forge already executes:

    manifest -> AuthProvider  (credentials + refresh)
             -> ToolSet       (the folder / MCP toolset / agent grant unit)
             -> Tool * N      (rest_api tools, or mcp tools behind an McpClient)

Nothing downstream knows a connector exists: agents, the workflow `tool_call` node, the MCP
server, traces, and cost accounting all see ordinary tools. That is deliberate - it means the
connector layer can never become a second execution path that drifts from the first, and an
installed connector survives the connector subsystem being removed entirely.

Independence: the catalog is a directory of JSON files shipped inside this package. Loading it
performs no network I/O and requires no third-party service, SDK, or API key. Optional
authoring-time sources (the public MCP registry) are strictly opt-in and never required to
install or run a connector.
"""

from forge.connectors.catalog import CATALOG, get_manifest, list_manifests
from forge.connectors.manifest import ConnectorManifest, ManifestError, parse_manifest

__all__ = [
    "CATALOG",
    "ConnectorManifest",
    "ManifestError",
    "get_manifest",
    "list_manifests",
    "parse_manifest",
]
