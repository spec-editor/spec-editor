"""LogChannel — streaming machine-generated event channels.

Log channels consume structured log streams (Grafana Loki, Elasticsearch,
Datadog, Sentry, CloudWatch) and produce SRC-BUG-* elements when thresholds
are breached.

Analysis strategy: pattern matching, threshold gating, dedup by content hash.
Response strategy: create/update SRC-BUG-*, optionally create external alerts.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.channels import ExternalChannel
from src.channels.models import ChannelConfig, LifecycleEvent, LogItem


class LogChannel(ExternalChannel):
    """Abstract log channel — streaming events → SRC-BUG-* elements."""

    kind = "log"

    @abstractmethod
    async def pull(self) -> list[LogItem]:
        """Query the log source for new events.

        Implementations apply threshold gating from ``config.analysis``:
        - ``dedup_window`` — seconds to collapse identical errors
        - ``thresholds.<level>.count`` — min occurrences to create a bug
        - ``thresholds.<level>.window_sec`` — time window for counting
        """
        ...

    async def push(self, event: LifecycleEvent) -> bool:
        """Log channels rarely push back. Default: no-op.

        Override to create external incidents (e.g. PagerDuty).
        """
        return True

    @abstractmethod
    async def validate_connection(self) -> dict[str, Any]:
        """Verify log source URL, auth, and query access."""
        ...


class NoopLogChannel(LogChannel):
    """Development fallback — logs pull to stderr.

    Used when no real log backend (Grafana Loki, Datadog) is configured.
    """

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        import sys
        print(f"[log:{config.type}] NoopLogChannel active — events logged to stderr", file=sys.stderr)

    async def pull(self) -> list[LogItem]:
        return []

    async def validate_connection(self) -> dict[str, Any]:
        return {"ok": True, "message": "NoopLogChannel — no real backend"}
