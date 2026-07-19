"""SlackChatChannel — Slack integration via Bolt SDK / Web API.

Configuration (local.yaml):

    channels:
      - type: slack
        name: dev-alerts
        kind: chat
        config:
          bot_token: "${SLACK_BOT_TOKEN}"
          channel: "#dev-alerts"
        response:
          mode: per_event
          include_severities: ["error"]
"""

from __future__ import annotations
from typing import Any

from src.channels.chat_channel import ChatChannel
from src.channels.models import ChannelConfig, ChatItem, LifecycleEvent
from src.channels.http_helpers import get_aiohttp, http_get, http_post


class SlackChatChannel(ChatChannel):
    """Slack chat channel — messages via Web API."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._token = config.config.get("bot_token", "")
        self._channel = config.config.get("channel", "#general")

    async def pull(self) -> list[ChatItem]:
        if not self._token:
            return []
        headers = {"Authorization": f"Bearer {self._token}"}
        url = "https://slack.com/api/conversations.history"
        status, data = await http_get(url, headers=headers, params={"channel": self._channel, "limit": 20})
        if status != 200 or not data:
            return []
        items = []
        for msg in data.get("messages", []):
            if msg.get("subtype"):
                continue
            items.append(ChatItem(text=msg.get("text", ""), sender=msg.get("user", ""),
                                   chat_id=self._channel, message_id=msg.get("ts", ""),
                                   timestamp=msg.get("ts", ""), raw=msg))
        return items

    async def push(self, event: LifecycleEvent) -> bool:
        if not self._token:
            return False
        prefix = {"info": ":information_source:", "warning": ":warning:", "error": ":red_circle:"}
        text = f"{prefix.get(event.severity, '')} *{event.element_id}*: {event.message}"
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        status, _ = await http_post("https://slack.com/api/chat.postMessage", headers=headers,
                                     json_data={"channel": self._channel, "text": text})
        return status == 200

    async def validate_connection(self) -> dict[str, Any]:
        if not self._token:
            return {"ok": False, "error": "Missing bot_token"}
        headers = {"Authorization": f"Bearer {self._token}"}
        status, data = await http_post("https://slack.com/api/auth.test", headers=headers)
        if status == 200 and data and data.get("ok"):
            return {"ok": True, "message": f"Slack OK — team: {data.get('team', '?')}"}
        return {"ok": False, "error": str(data.get("error", f"HTTP {status}")) if data else f"HTTP {status}"}


class DiscordChatChannel(ChatChannel):
    """Discord chat channel — via Bot API."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._token = config.config.get("bot_token", "")
        self._channel_id = str(config.config.get("channel_id", ""))

    async def pull(self) -> list[ChatItem]:
        if not self._token or not self._channel_id:
            return []
        headers = {"Authorization": f"Bot {self._token}"}
        url = f"https://discord.com/api/v10/channels/{self._channel_id}/messages?limit=20"
        status, msgs = await http_get(url, headers=headers)
        if status != 200 or not msgs:
            return []
        items = []
        for msg in msgs:
            items.append(ChatItem(text=msg.get("content", ""),
                                   sender=msg.get("author", {}).get("username", ""),
                                   chat_id=self._channel_id, message_id=msg.get("id", ""),
                                   timestamp=msg.get("timestamp", ""), raw=msg))
        return items

    async def push(self, event: LifecycleEvent) -> bool:
        if not self._token or not self._channel_id:
            return False
        prefix = {"info": ":information_source:", "warning": ":warning:", "error": ":red_circle:"}
        text = f"{prefix.get(event.severity, '')} **{event.element_id}**: {event.message}"
        headers = {"Authorization": f"Bot {self._token}", "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/channels/{self._channel_id}/messages"
        status, _ = await http_post(url, headers=headers, json_data={"content": text})
        return status == 200

    async def validate_connection(self) -> dict[str, Any]:
        if not self._token:
            return {"ok": False, "error": "Missing bot_token"}
        headers = {"Authorization": f"Bot {self._token}"}
        status, data = await http_get("https://discord.com/api/v10/users/@me", headers=headers)
        if status == 200 and data:
            return {"ok": True, "message": f"Discord OK — bot: {data.get('username', '?')}"}
        return {"ok": False, "error": f"HTTP {status}"}
