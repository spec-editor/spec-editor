"""Channel framework — pluggable external integrations.

Channels are the unified abstraction for all external data sources
and sinks: chat (Telegram, Slack), trackers (Jira, Plankanban, Trello),
and log sources (Grafana Loki, Datadog).

All channel behaviour is data-driven — configured via methodology.yaml
(channel type declarations) and local.yaml (per-project instances).

Usage::

    from src.channels import create_channel
    from src.channels.models import ChannelConfig, ChannelKind

    cfg = ChannelConfig(type="telegram", kind=ChannelKind.CHAT)
    channel = create_channel(cfg)
    items = await channel.pull()
    await channel.push(event)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.channels.models import ChannelConfig, ChannelKind, LifecycleEvent


class ExternalChannel(ABC):
    """Abstract external channel — bidirectional data flow.

    Each subclass handles one channel KIND (chat, tracker, log).
    Specific backends (Telegram, Jira, Grafana Loki) are created
    via the :func:`create_channel` factory from config.
    """

    # Set by subclasses — chat, tracker, or log
    kind: str = ""

    def __init__(self, config: ChannelConfig) -> None:
        self._config = config
        self._type = config.type

    @property
    def channel_type(self) -> str:
        """Channel identifier from config — e.g. 'telegram', 'jira'."""
        return self._type

    @abstractmethod
    async def pull(self) -> list[Any]:
        """Fetch new items from the external source.

        Returns a list of typed items:
        - ChatChannel → list[ChatItem]
        - TrackerChannel → list[TrackerItem]
        - LogChannel → list[LogItem]
        """
        ...

    @abstractmethod
    async def push(self, event: LifecycleEvent) -> bool:
        """Push a lifecycle event to the external channel.

        Returns True if the event was successfully delivered.
        Implementations should be idempotent and never raise.
        """
        ...

    @abstractmethod
    async def validate_connection(self) -> dict[str, Any]:
        """Check connectivity and authentication.

        Returns:
            {"ok": True} or {"ok": False, "error": "..."}
        """
        ...


# ── Factory ─────────────────────────────────────────────────────────


def create_channel(config: ChannelConfig) -> ExternalChannel | None:
    """Create a channel instance from configuration.

    Channel type → backend mapping is data-driven: we look up the
    channel kind and instantiate the appropriate backend class.
    Specific backends (telegram, jira, grafana_loki) are tried first;
    if no backend is found, a LogChannel fallback is used for development.

    Returns None if the channel is disabled.
    """
    if not config.enabled:
        return None

    _ensure_backends()  # Lazy registration of built-in backends

    backend_class = _BACKEND_REGISTRY.get(config.type)
    if backend_class is None:
        # Fallback: use a log-based noop channel for unrecognised types
        backend_class = _FALLBACKS.get(config.kind, _LogFallbackChannel)

    return backend_class(config)


# ── Backend registry (populated by plugins) ─────────────────────────

_BACKEND_REGISTRY: dict[str, type[ExternalChannel]] = {}
"""Maps channel type string → channel backend class.

Plugins register themselves::

    from src.channels import register_backend
    register_backend("telegram", TelegramChannel)
"""

# Backend registration is lazy — call _ensure_backends() before first use.
_backends_loaded = False


def _ensure_backends() -> None:
    """Register built-in channel backends (lazy, one-shot).

    Called on first create_channel() or register_backend() to avoid
    circular imports during module initialisation.
    """
    global _backends_loaded
    if _backends_loaded:
        return
    _backends_loaded = True

    try:
        from src.channels.backends.telegram_chat import TelegramChatChannel
        register_backend("telegram", TelegramChatChannel)
    except ImportError:
        pass

    try:
        from src.channels.backends.jira_tracker import JiraTrackerChannel
        register_backend("jira", JiraTrackerChannel)
    except ImportError:
        pass

    try:
        from src.channels.backends.grafana_loki_log import GrafanaLokiLogChannel
        register_backend("grafana_loki", GrafanaLokiLogChannel)
    except ImportError:
        pass

    try:
        from src.channels.backends.slack_discord_chat import SlackChatChannel, DiscordChatChannel
        register_backend("slack", SlackChatChannel)
        register_backend("discord", DiscordChatChannel)
    except ImportError:
        pass

    try:
        from src.channels.backends.trello_plankanban_tracker import TrelloTrackerChannel, PlankanbanTrackerChannel
        register_backend("trello", TrelloTrackerChannel)
        register_backend("plankanban", PlankanbanTrackerChannel)
    except ImportError:
        pass

    try:
        from src.channels.backends.github_issues_tracker import GitHubIssuesTrackerChannel
        register_backend("github_issues", GitHubIssuesTrackerChannel)
    except ImportError:
        pass

_FALLBACKS: dict[ChannelKind, type[ExternalChannel]] = {}
"""Fallback backends per kind — used when no real backend is registered."""


def register_backend(channel_type: str, backend_class: type[ExternalChannel]) -> None:
    """Register a channel backend implementation.

    Called by plugins during discovery.  Example::

        register_backend("jira", JiraChannel)
    """
    _BACKEND_REGISTRY[channel_type] = backend_class


# ── Fallback (noop) implementations for development ──────────────────

from src.channels.chat_channel import ChatChannel, LogChatChannel
from src.channels.log_channel import LogChannel, NoopLogChannel
from src.channels.tracker_channel import TrackerChannel, NoopTrackerChannel

_FALLBACKS[ChannelKind.CHAT] = LogChatChannel
_FALLBACKS[ChannelKind.TRACKER] = NoopTrackerChannel
_FALLBACKS[ChannelKind.LOG] = NoopLogChannel


class _LogFallbackChannel(ExternalChannel):
    """Catch-all fallback — logs pull/push to stderr."""

    async def pull(self) -> list[Any]:
        import sys
        print(f"[channel:{self._type}] pull() — no backend registered", file=sys.stderr)
        return []

    async def push(self, event: LifecycleEvent) -> bool:
        import sys
        print(f"[channel:{self._type}] push({event.event_type}) — no backend registered", file=sys.stderr)
        return True

    async def validate_connection(self) -> dict[str, Any]:
        return {"ok": False, "error": f"No backend registered for '{self._type}'"}
