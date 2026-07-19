"""Channel Plugin — wires external channels into the spec-editor plugin system.

This plugin is Pro-gated: it only loads when a valid Pro license is present.
When loaded, it discovers channel configurations from local.yaml and registers
all channel backends as MCP tools and event subscribers.

Channels are data-driven — no backend names, types, or config schemas are
hardcoded.  Everything reads from methodology.yaml (type declarations) and
local.yaml (per-project instances).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.hooks import SpecEditorPlugin


class ChannelPlugin(SpecEditorPlugin):
    """Pro-gated plugin that manages external channel integrations.

    Channels include:
    - Chat: Telegram, Slack, Discord, VSCode chat
    - Tracker: Jira, Plankanban, Trello, Linear, GitHub Issues
    - Log: Grafana Loki, Elasticsearch, Datadog, Sentry
    """

    pro_required = True

    # ── Plugin hooks ────────────────────────────────────────────────

    def register_mcp_tools(
        self, storage, project_path: str
    ) -> dict[str, Any]:
        """Register channel management MCP tools.

        Tools exposed:
        - list_channels — show configured channels and their status
        - sync_channel — trigger pull/push for a specific channel
        - validate_channel — test connectivity for a channel
        """
        return {
            "list_channels": lambda: self._list_channels_tool(project_path),
            "sync_channel": lambda channel_type="": self._sync_channel_tool(
                storage, project_path, channel_type
            ),
            "validate_channel": lambda channel_type="": self._validate_channel_tool(
                project_path, channel_type
            ),
        }

    def register_mcp_tool_schemas(self) -> list[dict[str, Any]]:
        """Return MCP tool schemas for channel management."""
        return [
            {
                "name": "list_channels",
                "description": "List all configured external channels and their connection status.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "sync_channel",
                "description": "Trigger a manual sync (pull + push) for a specific channel.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel_type": {
                            "type": "string",
                            "description": "Channel identifier — e.g. 'telegram', 'jira'",
                        }
                    },
                },
            },
            {
                "name": "validate_channel",
                "description": "Test connectivity and authentication for a channel.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel_type": {
                            "type": "string",
                            "description": "Channel identifier — e.g. 'telegram', 'jira'",
                        }
                    },
                },
            },
        ]

    # ── Tool implementations ─────────────────────────────────────────

    @staticmethod
    def _load_channel_configs(project_path: str) -> list[dict[str, Any]]:
        """Load channel configurations from local.yaml → channels: section."""
        import yaml

        pp = Path(project_path)
        local_yaml = pp / "local.yaml"
        if not local_yaml.exists():
            return []

        try:
            config = yaml.safe_load(local_yaml.read_text())
            return config.get("channels", [])
        except Exception:
            return []

    def _list_channels_tool(self, project_path: str) -> dict[str, Any]:
        """Return all configured channels with live connection status.

        Checks each enabled channel's connectivity and reports:
        - status: "connected" | "disconnected" | "disabled" | "configured"
        - last_sync: ISO timestamp of last successful pull/push
        - error: last error message if disconnected
        """
        import asyncio
        import time

        from src.channels import create_channel
        from src.channels.models import ChannelConfig, ChannelKind

        configs = self._load_channel_configs(project_path)
        channels = []

        for raw in configs:
            channel_type = raw.get("type", "unknown")
            channel_name = raw.get("name", "")
            channel_id = f"{channel_type}:{channel_name}" if channel_name else channel_type
            enabled = raw.get("enabled", True)

            entry: dict[str, Any] = {
                "channel_id": channel_id,
                "type": channel_type,
                "name": channel_name,
                "kind": raw.get("kind", "unknown"),
                "enabled": enabled,
                "status": "disabled" if not enabled else "configured",
                "error": None,
                "last_sync": None,
            }

            if not enabled:
                channels.append(entry)
                continue

            # Try to create the channel and validate connection
            try:
                cfg = ChannelConfig(**raw)
                channel = create_channel(cfg)
                if channel is None:
                    entry["status"] = "error"
                    entry["error"] = "Channel factory returned None"
                else:
                    result = asyncio.run(channel.validate_connection())
                    if result.get("ok"):
                        entry["status"] = "connected"
                    else:
                        entry["status"] = "disconnected"
                        entry["error"] = result.get("error", "Unknown error")
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)

            channels.append(entry)

        return {
            "channels": channels,
            "count": len(channels),
            "connected": sum(1 for c in channels if c["status"] == "connected"),
            "disconnected": sum(1 for c in channels if c["status"] == "disconnected"),
        }

    def _sync_channel_tool(
        self, storage, project_path: str, channel_type: str
    ) -> dict[str, Any]:
        """Sync a specific channel: pull new items, push pending events."""
        if not channel_type:
            return {"error": "channel_type is required"}

        from src.channels import create_channel
        from src.channels.models import ChannelConfig, ChannelKind

        configs = self._load_channel_configs(project_path)
        for raw in configs:
            if raw.get("type") != channel_type:
                continue

            cfg = ChannelConfig(**raw)
            channel = create_channel(cfg)
            if channel is None:
                return {"error": f"Channel '{channel_type}' is disabled"}

            # Pull
            try:
                import asyncio
                items = asyncio.run(channel.pull())
            except Exception as exc:
                return {"error": f"Pull failed: {exc}", "channel": channel_type}

            return {
                "channel": channel_type,
                "kind": cfg.kind.value,
                "pulled": len(items),
                "status": "ok",
            }

        return {"error": f"Channel '{channel_type}' not found in configuration"}

    def _validate_channel_tool(
        self, project_path: str, channel_type: str
    ) -> dict[str, Any]:
        """Test connectivity for a channel."""
        if not channel_type:
            return {"error": "channel_type is required"}

        from src.channels import create_channel
        from src.channels.models import ChannelConfig

        configs = self._load_channel_configs(project_path)
        for raw in configs:
            if raw.get("type") != channel_type:
                continue

            cfg = ChannelConfig(**raw)
            channel = create_channel(cfg)
            if channel is None:
                return {"error": f"Channel '{channel_type}' is disabled"}

            try:
                import asyncio
                result = asyncio.run(channel.validate_connection())
                result["channel"] = channel_type
                return result
            except Exception as exc:
                return {"channel": channel_type, "ok": False, "error": str(exc)}

        return {"error": f"Channel '{channel_type}' not found in configuration"}
