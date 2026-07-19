"""ChatChannel — unstructured natural-language message channels.

Chat channels receive human messages (Telegram, Slack, Discord, VSCode chat)
and optionally respond with summaries or confirmations.

Analysis strategy: NL parsing, intent detection, entity extraction.
Response strategy: conversational, batched summaries.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.channels import ExternalChannel
from src.channels.models import ChannelConfig, ChatItem, LifecycleEvent


class ChatChannel(ExternalChannel):
    """Abstract chat channel — unstructured NL in, optional reply out."""

    kind = "chat"

    @abstractmethod
    async def pull(self) -> list[ChatItem]:
        """Fetch new messages from the chat source.

        Implementations should be idempotent — track message_id so
        re-processing the same message does not create duplicates.
        """
        ...

    @abstractmethod
    async def push(self, event: LifecycleEvent) -> bool:
        """Send a message to the chat.

        The routing agent decides what to send based on the channel's
        ``response.mode`` config: ``summary`` (batched), ``per_event``
        (every event), or ``silent`` (never send).
        """
        ...

    @abstractmethod
    async def validate_connection(self) -> dict[str, Any]:
        """Verify bot token, chat access, and connectivity."""
        ...


class LogChatChannel(ChatChannel):
    """Development fallback — logs chat pull/push to stderr.

    Used when no real chat backend (Telegram, Slack) is configured.
    """

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        import sys
        print(f"[chat:{config.type}] LogChatChannel active — messages logged to stderr", file=sys.stderr)

    async def pull(self) -> list[ChatItem]:
        return []

    async def push(self, event: LifecycleEvent) -> bool:
        import sys
        print(
            f"[chat:{self._type}] push {event.event_type}: {event.message[:120]}",
            file=sys.stderr,
        )
        return True

    async def validate_connection(self) -> dict[str, Any]:
        return {"ok": True, "message": "LogChatChannel — no real backend"}
