"""Channel Event Bridge — Redis pub/sub for channel ↔ pipeline integration.

Channels publish incoming items as events.  The cycle pipeline (or
``spec-editor analyze --channel``) subscribes and processes them.

Stream naming convention::

    channel:{channel_type}:in    — incoming items (channel → spec-editor)
    channel:{channel_type}:out   — outgoing events (spec-editor → channel)

Each message is a JSON-encoded :class:`ChannelEvent`.

Usage::

    # Producer (channel):
    bridge = ChannelBridge(DEFAULT_REDIS_URL)
    evt = ChannelEvent.from_item("telegram", chat_item)
    await bridge.publish(evt)

    # Consumer (pipeline):
    async for evt in bridge.subscribe("telegram"):
        await process_event(evt)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from src.agents.constants import DEFAULT_REDIS_URL
from src.channels.models import ChatItem, LogItem, TrackerItem

# ──────────────────────────────────────────────────────────────────
# ChannelEvent — serialisable event for Redis transport
# ──────────────────────────────────────────────────────────────────


@dataclass
class ChannelEvent:
    """A single item from a channel, ready for Redis transport.

    One ChatItem / TrackerItem / LogItem maps to one ChannelEvent.
    Serialised as JSON and pushed to a Redis stream.

    Stream keys are name-qualified::

        channel:telegram:in           # no name
        channel:telegram:dev-team:in  # with name
    """

    channel_type: str
    channel_name: str = ""   # optional instance qualifier
    item_type: str = ""      # "ChatItem" | "TrackerItem" | "LogItem"
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""
    created_at: float = field(default_factory=time.time)

    @staticmethod
    def from_item(
        channel_type: str,
        item: ChatItem | TrackerItem | LogItem,
        channel_name: str = "",
    ) -> "ChannelEvent":
        """Create a ChannelEvent from a typed channel item."""
        item_type = type(item).__name__
        payload = item.model_dump()
        name_part = f":{channel_name}" if channel_name else ""

        return ChannelEvent(
            channel_type=channel_type,
            channel_name=channel_name,
            item_type=item_type,
            payload=payload,
            event_id=f"{channel_type}{name_part}:{int(time.time() * 1000)}",
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ChannelEvent":
        """Deserialise from a dict (e.g. from Redis)."""
        return ChannelEvent(
            channel_type=data.get("channel_type", ""),
            channel_name=data.get("channel_name", ""),
            item_type=data.get("item_type", ""),
            payload=data.get("payload", {}),
            event_id=data.get("event_id", ""),
            created_at=data.get("created_at", time.time()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "channel_type": self.channel_type,
            "channel_name": self.channel_name,
            "item_type": self.item_type,
            "payload": self.payload,
            "event_id": self.event_id,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @property
    def channel_id(self) -> str:
        """Fully qualified channel identifier: ``type:name`` or ``type``."""
        if self.channel_name:
            return f"{self.channel_type}:{self.channel_name}"
        return self.channel_type

    def stream_key(self) -> str:
        """Redis stream key for incoming events, name-qualified."""
        if self.channel_name:
            return f"channel:{self.channel_type}:{self.channel_name}:in"
        return f"channel:{self.channel_type}:in"


# ──────────────────────────────────────────────────────────────────
# ChannelBridge — Redis pub/sub transport
# ──────────────────────────────────────────────────────────────────


class ChannelBridge:
    """Bridges channels and the pipeline via Redis streams.

    Reuses the existing Redis connection from the task queue when
    available — no duplicate connections or connection management.

    Producer side (channels)::

        bridge = ChannelBridge()
        evt = ChannelEvent.from_item("telegram", item)
        await bridge.publish(evt)

    Consumer side (pipeline / analyze)::

        async for evt in bridge.subscribe("telegram:dev-team"):
            # → analyze → create SRC-* → route response

    Shared connection::

        # Reuse the task queue's Redis client (same aioredis pattern):
        bridge = ChannelBridge(redis_client=task_queue._client)
    """

    def __init__(
        self,
        redis_url: str = "",
        redis_client: Any = None,  # aioredis.Redis from task queue
    ) -> None:
        self._redis_url = redis_url or DEFAULT_REDIS_URL
        self._redis = redis_client  # shared client from task queue
        self._own_client = False     # True if we created it ourselves

    # ── Stream key helpers ────────────────────────────────────────

    @staticmethod
    def stream_key_for(channel_type: str, channel_name: str = "") -> str:
        """Build the Redis stream key for a channel, name-qualified.

        Args:
            channel_type: Backend type — e.g. 'telegram', 'jira'
            channel_name: Optional instance name — e.g. 'dev-team', 'SPEC'
        """
        if channel_name:
            return f"channel:{channel_type}:{channel_name}:in"
        return f"channel:{channel_type}:in"

    @staticmethod
    def parse_channel_id(channel_id: str) -> tuple[str, str]:
        """Parse 'type:name' or 'type' into (type, name).

        >>> ChannelBridge.parse_channel_id("telegram:dev-team")
        ('telegram', 'dev-team')
        >>> ChannelBridge.parse_channel_id("jira")
        ('jira', '')
        """
        if ":" in channel_id:
            parts = channel_id.split(":", 1)
            return parts[0], parts[1]
        return channel_id, ""

    # ── Publish (producer) ─────────────────────────────────────────

    async def publish(self, event: ChannelEvent) -> bool:
        """Publish a channel event to the Redis stream.

        Returns True if the event was published, False on failure.
        Failures are logged but never raised — channels should
        degrade gracefully when Redis is unavailable.
        """
        try:
            redis = await self._get_redis()
            stream_key = event.stream_key()
            await redis.xadd(stream_key, {"data": event.to_json()})
            return True
        except Exception:
            return False

    # ── Subscribe (consumer) ───────────────────────────────────────

    async def subscribe(
        self,
        channel_id: str,
        consumer_id: str = "channel-pipeline",
        block_ms: int = 5000,
    ) -> AsyncIterator[ChannelEvent]:
        """Subscribe to incoming events for a channel.

        Args:
            channel_id: Channel identifier — 'telegram:dev-team' or 'jira'.
                Parsed into type + optional name for stream key construction.
            consumer_id: Consumer group identifier.
            block_ms: How long to block waiting for messages.

        Yields :class:`ChannelEvent` objects as they arrive.
        """
        channel_type, channel_name = self.parse_channel_id(channel_id)
        stream_key = self.stream_key_for(channel_type, channel_name)

        try:
            redis = await self._get_redis()
        except Exception:
            return

        last_id = "0"
        while True:
            try:
                result = await redis.xread(
                    {stream_key: last_id},
                    count=10,
                    block=block_ms,
                )
            except Exception:
                # Connection lost — yield and stop
                return

            if not result:
                continue

            for _stream_name, messages in result:
                for msg_id, msg_data in messages:
                    last_id = msg_id
                    data_str = msg_data.get(b"data", msg_data.get("data", "{}"))
                    if isinstance(data_str, bytes):
                        data_str = data_str.decode("utf-8")

                    try:
                        data = json.loads(data_str)
                        yield ChannelEvent.from_dict(data)
                    except (json.JSONDecodeError, TypeError):
                        continue

    # ── Internal ───────────────────────────────────────────────────

    async def _get_redis(self):
        """Lazy Redis connection — reuses shared client or creates own.

        If a redis_client was passed at init, uses that (shared with
        task queue).  Otherwise creates its own connection.
        """
        if self._redis is not None:
            return self._redis

        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=False,
            socket_connect_timeout=3,
            socket_timeout=5,
        )
        self._own_client = True
        return self._redis

    async def close(self) -> None:
        """Close the Redis connection — only if we own it."""
        if self._redis is not None and self._own_client:
            try:
                await self._redis.aclose()
            except AttributeError:
                await self._redis.close()
            self._redis = None
