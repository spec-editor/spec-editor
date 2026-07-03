"""Event Bus — pluggable inter-component messaging.

Abstracts message publishing/subscription so the storage layer,
workflow engine, and plugins can communicate without hardcoding Redis.

Configured via ``local.yaml`` → ``events:`` section.

Usage::

    from src.events import create_event_bus

    bus = create_event_bus(project_path)
    bus.publish("elements:changed", {"element_id": "MOD-001"})

    for channel, data in bus.subscribe("elements:changed"):
        handle(channel, data)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator


class AbstractEventBus(ABC):
    """Pluggable pub/sub event bus.

    Channels are plain strings (e.g. ``elements:changed``, ``spec:created``).
    Implementations handle prefixing/scoping internally.
    """

    @abstractmethod
    def publish(self, channel: str, data: dict[str, Any]) -> None:
        """Publish an event to a channel.

        Args:
            channel: Channel name (e.g. ``elements:changed``)
            data: JSON-serializable event payload
        """
        ...

    @abstractmethod
    def subscribe(self, *channels: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Subscribe to one or more channels.

        Yields ``(channel, data)`` tuples. Blocks until messages arrive.
        For non-blocking use, wrap in a background thread or asyncio task.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Clean up connections."""
        ...


# ── Backend implementations ────────────────────────────────────────


class RedisEventBus(AbstractEventBus):
    """Redis pub/sub backend.

    Channels are prefixed with ``{prefix}:events:`` for namespacing.
    """

    def __init__(self, redis_url: str, prefix: str = "") -> None:
        import redis

        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix + ":" if prefix else ""
        self._pubsub = self._redis.pubsub()

    def publish(self, channel: str, data: dict[str, Any]) -> None:
        import json

        self._redis.publish(
            f"{self._prefix}events:{channel}",
            json.dumps(data, default=str),
        )

    def subscribe(self, *channels: str) -> Iterator[tuple[str, dict[str, Any]]]:
        import json

        full_channels = [f"{self._prefix}events:{c}" for c in channels]
        self._pubsub.subscribe(*full_channels)
        for message in self._pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                except json.JSONDecodeError:
                    data = {"raw": message["data"]}
                channel = message["channel"].replace(f"{self._prefix}events:", "")
                yield channel, data

    def close(self) -> None:
        self._pubsub.close()
        self._redis.close()


class MemoryEventBus(AbstractEventBus):
    """In-memory event bus for testing and single-process use.

    Thread-safe via a lock. Subscribe returns a generator
    that yields all historical + future messages on the channel.
    """

    def __init__(self) -> None:
        import threading
        from collections import defaultdict

        self._lock = threading.Lock()
        self._channels: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._listeners: dict[str, list[Any]] = defaultdict(list)

    def publish(self, channel: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._channels[channel].append(data)
            # Notify listeners
            for event in self._listeners.get(channel, []):
                event.set()

    def subscribe(self, *channels: str) -> Iterator[tuple[str, dict[str, Any]]]:
        import threading
        from collections import defaultdict

        # Yield all historical messages first
        with self._lock:
            for channel in channels:
                for data in self._channels.get(channel, []):
                    yield channel, data

        # Then wait for new messages
        events: dict[str, threading.Event] = {}
        with self._lock:
            for channel in channels:
                event = threading.Event()
                events[channel] = event
                self._listeners[channel].append(event)

        idx: dict[str, int] = defaultdict(int)
        try:
            while True:
                # Wait for any event
                for event in events.values():
                    event.wait(timeout=1.0)
                # Check for new messages
                with self._lock:
                    for channel in channels:
                        while idx[channel] < len(self._channels[channel]):
                            data = self._channels[channel][idx[channel]]
                            idx[channel] += 1
                            yield channel, data
                    # Clear events that were triggered
                    for event in events.values():
                        event.clear()
        finally:
            with self._lock:
                for channel, event in events.items():
                    self._listeners[channel].remove(event)

    def close(self) -> None:
        with self._lock:
            self._listeners.clear()


class NatsEventBus(AbstractEventBus):
    """NATS pub/sub backend (stub).

    Requires ``nats-py`` package. Subjects are ``events.{channel}``.
    """

    def __init__(
        self,
        nats_url: str = "nats://localhost:4222",
        prefix: str = "",
    ) -> None:
        self._url = nats_url
        self._prefix = prefix
        self._nc: Any = None  # NATS connection

    def _ensure_connected(self) -> None:
        if self._nc is not None:
            return
        try:
            import nats
        except ImportError:
            raise ImportError(
                "nats-py is required for NATS event bus. "
                "Install with: pip install nats-py"
            )
        import asyncio

        async def _connect():
            self._nc = await nats.connect(self._url)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                future = asyncio.run_coroutine_threadsafe(_connect(), loop)
                future.result(timeout=5)
            else:
                loop.run_until_complete(_connect())
        except Exception:
            # Fallback: try running in new event loop
            asyncio.run(_connect())

    def publish(self, channel: str, data: dict[str, Any]) -> None:
        import asyncio
        import json

        self._ensure_connected()
        subject = f"events.{self._prefix}.{channel}" if self._prefix else f"events.{channel}"

        async def _pub():
            await self._nc.publish(subject, json.dumps(data, default=str).encode())

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                future = asyncio.run_coroutine_threadsafe(_pub(), loop)
                future.result(timeout=5)
            else:
                loop.run_until_complete(_pub())
        except Exception:
            asyncio.run(_pub())

    def subscribe(self, *channels: str) -> Iterator[tuple[str, dict[str, Any]]]:
        import asyncio
        import json
        import queue
        import threading

        self._ensure_connected()
        msg_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()

        async def _sub():
            async def handler(msg):
                try:
                    data = json.loads(msg.data.decode())
                except json.JSONDecodeError:
                    data = {"raw": msg.data.decode()}
                channel = msg.subject.replace("events.", "").replace(f"{self._prefix}.", "")
                msg_queue.put((channel, data))

            for channel in channels:
                subject = (
                    f"events.{self._prefix}.{channel}"
                    if self._prefix
                    else f"events.{channel}"
                )
                await self._nc.subscribe(subject, cb=handler)

            # Keep subscription alive
            while True:
                await asyncio.sleep(1)

        thread = threading.Thread(target=asyncio.run, args=(_sub(),), daemon=True)
        thread.start()

        while True:
            try:
                yield msg_queue.get(timeout=1.0)
            except queue.Empty:
                continue

    def close(self) -> None:
        if self._nc:
            import asyncio

            async def _close():
                await self._nc.close()

            try:
                asyncio.run(_close())
            except Exception:
                pass
            self._nc = None


# ── Factory ─────────────────────────────────────────────────────────


def create_event_bus(project_path: str | Path) -> AbstractEventBus:
    """Create an EventBus from project configuration.

    Reads ``local.yaml`` → ``events:`` section:

    .. code-block:: yaml

        events:
          backend: redis           # redis | memory | nats
          redis:
            url: redis://localhost:6379
          nats:
            url: nats://localhost:4222

    Falls back to ``redis://localhost:6379`` if no config found.
    """
    import os

    proj = Path(project_path)
    backend_name = "redis"
    backend_config: dict[str, Any] = {}
    slug = proj.name.replace(" ", "-").replace(".", "-")

    local_yaml = proj / "local.yaml"
    if local_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(local_yaml.read_text()) or {}
            events_cfg = data.get("events", {})
            backend_name = events_cfg.get("backend", "redis")
            backend_config = events_cfg.get(backend_name, {})
            slug = data.get("project_slug", slug)
        except Exception:
            pass

    backend_name = os.environ.get("SPEC_EDITOR__EVENTS_BACKEND", backend_name)

    if backend_name == "memory":
        return MemoryEventBus()
    elif backend_name == "nats":
        url = backend_config.get("url", "nats://localhost:4222")
        return NatsEventBus(nats_url=url, prefix=slug)
    else:
        url = backend_config.get("url", "redis://localhost:6379")
        if "?" in url:
            url = url.split("?")[0]
        return RedisEventBus(url, slug)
