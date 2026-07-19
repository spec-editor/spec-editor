"""TelegramChatChannel — real Telegram integration via Telethon.

Implements ChatChannel ABC for Telegram.  Wraps the existing
TelegramWatcher from src.ingestion.telegram_hook.

Configuration (local.yaml → channels: section):

    channels:
      - type: telegram
        kind: chat
        config:
          api_id: 12345
          api_hash: "abc..."
          phone: "+1234567890"
          chat_ids: ["-1001234567890"]
        analysis:
          intent_model: "deepseek/deepseek-chat"
          min_confidence: 0.7
        response:
          mode: summary
          include_severities: ["error", "warning"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.channels.chat_channel import ChatChannel
from src.channels.event_bridge import ChannelBridge, ChannelEvent
from src.channels.models import ChannelConfig, ChatItem, LifecycleEvent


class TelegramChatChannel(ChatChannel):
    """Real Telegram chat channel — listens for messages, can reply."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._bridge: ChannelBridge | None = None

    # ── ChatChannel ABC ────────────────────────────────────────────

    async def pull(self) -> list[ChatItem]:
        """Fetch recent messages from Telegram.

        Uses the existing TelegramWatcher to fetch history.
        If telethon is not installed, returns empty list gracefully.
        """
        try:
            from src.ingestion.telegram_hook import HookConfig, TelegramWatcher
        except ImportError:
            import sys
            print(
                "[telegram] telethon not installed — install with: pip install telethon",
                file=sys.stderr,
            )
            return []

        cfg = self._config.config
        api_id = cfg.get("api_id", 0)
        api_hash = cfg.get("api_hash", "")
        phone = cfg.get("phone", "")

        if not api_id or not api_hash:
            import sys
            print(
                "[telegram] Missing api_id/api_hash in channel config",
                file=sys.stderr,
            )
            return []

        hook_config = HookConfig(
            api_id=int(api_id),
            api_hash=str(api_hash),
            phone=str(phone),
            projects=[],
        )

        watcher = TelegramWatcher(hook_config)

        import asyncio
        from datetime import datetime, timezone, timedelta

        # Fetch messages from the last hour by default
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        messages = await watcher.fetch_history(since=since, limit=100)

        # Convert to ChatItem format
        items: list[ChatItem] = []
        for msg in messages:
            # msg is a dict from MessageProcessor
            items.append(ChatItem(
                text=msg.get("text", ""),
                sender=msg.get("sender", ""),
                chat_id=str(msg.get("chat_id", "")),
                message_id=str(msg.get("message_id", "")),
                timestamp=str(msg.get("timestamp", "")),
                raw=msg,
            ))

        # Publish to event bridge for async processing
        await self._publish_to_bridge(items)

        return items

    async def push(self, event: LifecycleEvent) -> bool:
        """Send a notification to the configured Telegram chat.

        Uses the response config to decide what to send.
        """
        mode = self._config.response.get("mode", "silent")
        if mode == "silent":
            return True

        severities = self._config.response.get("include_severities", ["error"])
        if event.severity not in severities:
            return True  # filtered out — not an error

        chat_ids = self._config.config.get("chat_ids", [])
        if not chat_ids:
            import sys
            print("[telegram] No chat_ids configured for push", file=sys.stderr)
            return False

        # Format the message
        prefix = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}.get(event.severity, "")
        text = f"{prefix} [{event.element_id}] {event.message}"

        try:
            from telethon import TelegramClient
            import asyncio

            api_id = int(self._config.config.get("api_id", 0))
            api_hash = str(self._config.config.get("api_hash", ""))
            phone = str(self._config.config.get("phone", ""))

            client = TelegramClient(
                str(Path.cwd() / "spec-editor-session"),
                api_id,
                api_hash,
            )
            await client.start(phone=phone)

            for chat_id in chat_ids:
                await client.send_message(int(chat_id), text)

            await client.disconnect()
            return True
        except Exception as exc:
            import sys
            print(f"[telegram] push failed: {exc}", file=sys.stderr)
            return False

    async def validate_connection(self) -> dict[str, Any]:
        """Verify Telegram API credentials and connectivity."""
        api_id = self._config.config.get("api_id", 0)
        api_hash = self._config.config.get("api_hash", "")

        if not api_id or not api_hash:
            return {"ok": False, "error": "Missing api_id or api_hash in config"}

        try:
            from telethon import TelegramClient

            client = TelegramClient(
                str(Path.cwd() / "spec-editor-session-validate"),
                int(api_id),
                str(api_hash),
            )
            await client.connect()
            is_authorized = await client.is_user_authorized()
            await client.disconnect()

            if is_authorized:
                return {"ok": True, "message": "Telegram connection OK"}
            else:
                return {"ok": False, "error": "Not authorized — run spec-editor hooks first"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Internal ───────────────────────────────────────────────────

    async def _publish_to_bridge(self, items: list[ChatItem]) -> None:
        """Publish pulled items to the Redis event bridge."""
        if self._bridge is None:
            self._bridge = ChannelBridge()

        for item in items:
            evt = ChannelEvent.from_item(self._type, item)
            await self._bridge.publish(evt)
