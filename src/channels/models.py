"""Channel data models — types, configs, raw items.

All channel behaviour is driven by configuration from methodology.yaml
and local.yaml.  No hardcoded channel names, statuses, or analysis rules.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Channel type taxonomy ──────────────────────────────────────────


class ChannelKind(str, Enum):
    """Top-level channel taxonomy — determines analysis & response strategy."""

    CHAT = "chat"          # Unstructured natural language — Telegram, Slack, etc.
    TRACKER = "tracker"    # Structured cards/issues — Jira, Plankanban, Trello
    LOG = "log"            # Streaming machine events — Grafana Loki, Datadog, etc.


# ── Raw items (what channels produce/consume) ───────────────────────


class ChatItem(BaseModel):
    """A single message from a chat channel."""

    text: str = ""
    sender: str = ""
    chat_id: str = ""
    thread_id: str | None = None
    message_id: str = ""
    timestamp: str = ""
    attachments: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class TrackerItem(BaseModel):
    """A card/issue from a tracker channel."""

    id: str
    title: str = ""
    description: str = ""
    status: str = ""
    labels: list[str] = Field(default_factory=list)
    assignee: str | None = None
    due_date: str | None = None
    url: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class LogItem(BaseModel):
    """A log line / event from a log channel."""

    timestamp: str = ""
    level: str = "info"
    message: str = ""
    module: str = ""
    trace_id: str | None = None
    count: int = 1
    raw: dict[str, Any] = Field(default_factory=dict)


# ── Channel configuration (driven by local.yaml) ───────────────────


class ChannelConfig(BaseModel):
    """Configuration for a single external channel.

    Read from ``local.yaml`` → ``channels:`` list.  Every channel type
    (chat, tracker, log) uses the same base config; type-specific
    options go into ``config`` and ``analysis`` dicts.

    The optional ``name`` field distinguishes multiple instances of the
    same channel type::

        channels:
          - type: telegram
            name: dev-team        # ← optional qualifier
            kind: chat
            config:
              chat_ids: ["-100123"]
          - type: telegram
            name: qa-alerts
            kind: chat
            config:
              chat_ids: ["-100456"]

    Stream keys become ``channel:telegram:dev-team:in`` instead of
    ``channel:telegram:in`` when ``name`` is set.
    """

    type: str = Field(description="Channel identifier — e.g. telegram, jira, grafana_loki")
    name: str = Field(
        default="",
        description="Optional instance name to distinguish multiple channels of the same type (e.g. 'dev-team', 'qa-alerts')",
    )
    kind: ChannelKind = Field(description="Taxonomy: chat | tracker | log")
    enabled: bool = True

    # Connection
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific connection params (url, token, chat_id, etc.)",
    )

    # Analysis rules
    analysis: dict[str, Any] = Field(
        default_factory=dict,
        description="How to process incoming data (thresholds, dedup, intent model, etc.)",
    )

    # Response rules
    response: dict[str, Any] = Field(
        default_factory=dict,
        description="How to push outgoing data (mode, severity filter, comment policy, etc.)",
    )

    # Status mapping (tracker channels only)
    mapping: dict[str, Any] = Field(
        default_factory=dict,
        description="External status ↔ element status bidirectional mapping",
    )

    @property
    def channel_id(self) -> str:
        """Fully qualified channel identifier: ``type:name`` or just ``type``."""
        if self.name:
            return f"{self.type}:{self.name}"
        return self.type

    @staticmethod
    def validate_configs(channels_list: list[dict]) -> list[str]:
        """Validate a list of channel config dicts from local.yaml.

        Returns a list of error messages.  Empty list = all valid.
        Checks: required fields, valid kind, known type (best-effort).
        """
        errors: list[str] = []
        valid_kinds = {"chat", "tracker", "log"}

        for i, raw in enumerate(channels_list):
            prefix = f"channels[{i}]"
            ctype = raw.get("type", "")

            if not ctype:
                errors.append(f"{prefix}: missing required field 'type'")
                continue

            kind = raw.get("kind", "")
            if not kind:
                errors.append(f"{prefix} ({ctype}): missing required field 'kind'")
            elif kind not in valid_kinds:
                errors.append(f"{prefix} ({ctype}): invalid kind '{kind}' — must be one of {valid_kinds}")

            cfg = raw.get("config", {})
            if not isinstance(cfg, dict):
                errors.append(f"{prefix} ({ctype}): 'config' must be a dict")

            # Warn about common missing fields based on kind
            if kind == "chat" and not cfg.get("bot_token") and not cfg.get("api_id"):
                errors.append(f"{prefix} ({ctype}): chat channels typically need bot_token or api_id in config")
            if kind == "tracker" and not cfg.get("url"):
                errors.append(f"{prefix} ({ctype}): tracker channels typically need url in config")

        return errors


# ── Lifecycle event (pushed to channels) ────────────────────────────


class LifecycleEvent(BaseModel):
    """An event that may be pushed to external channels."""

    event_type: str = Field(description="created | status_changed | deployed | test_failed | ...")
    element_id: str = ""
    element_title: str = ""
    old_status: str | None = None
    new_status: str | None = None
    message: str = ""
    severity: str = "info"
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = ""
