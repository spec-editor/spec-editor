"""TrackerChannel — structured card/issue tracking channels.

Tracker channels sync cards/issues bidirectionally with spec-editor
elements: Jira, Plankanban, Trello, Linear, GitHub Issues.

Analysis strategy: status → ElementStatus mapping, label → tag mapping.
Response strategy: silent status sync, optional comments on significant events.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.channels import ExternalChannel
from src.channels.models import ChannelConfig, LifecycleEvent, TrackerItem


class TrackerChannel(ExternalChannel):
    """Abstract tracker channel — structured cards ↔ spec-editor elements."""

    kind = "tracker"

    @abstractmethod
    async def pull(self) -> list[TrackerItem]:
        """Fetch cards/issues from the tracker.

        Only items with the configured sync label (e.g. 'spec-editor')
        should be returned, unless the config specifies otherwise.
        """
        ...

    @abstractmethod
    async def push(self, event: LifecycleEvent) -> bool:
        """Update the tracker card/issue to match the element status.

        Status mapping is bidirectional and configured per-project
        in ``local.yaml`` → ``channels[].mapping.status``.
        """
        ...

    @abstractmethod
    async def validate_connection(self) -> dict[str, Any]:
        """Verify API URL, auth token, and project/board access."""
        ...


class NoopTrackerChannel(TrackerChannel):
    """Development fallback — logs tracker sync to stderr.

    Used when no real tracker backend (Jira, Plankanban) is configured.
    """

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        import sys
        print(f"[tracker:{config.type}] NoopTrackerChannel active — events logged to stderr", file=sys.stderr)

    async def pull(self) -> list[TrackerItem]:
        return []

    async def push(self, event: LifecycleEvent) -> bool:
        import sys
        print(
            f"[tracker:{self._type}] push {event.event_type}: "
            f"{event.element_id} {event.old_status}→{event.new_status}",
            file=sys.stderr,
        )
        return True

    async def validate_connection(self) -> dict[str, Any]:
        return {"ok": True, "message": "NoopTrackerChannel — no real backend"}
