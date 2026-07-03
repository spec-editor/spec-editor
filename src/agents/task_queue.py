"""Task Queue — abstract transport for agent task dispatching.

Supports Redis Streams (primary) and file-based queue (fallback).
Designed to be swappable: Redis → Upstash → NATS via same interface.

Usage::

    queue = TaskQueue.connect("redis://localhost:6379")
    # or
    queue = TaskQueue.connect("file://tasks/")

    # Producer (workflow engine):
    await queue.push("coding", {"bug_id": "SRC-042", "task": "Fix..."})

    # Consumer (agent worker):
    async for task in queue.subscribe("coding"):
        result = await handle(task)
        await queue.ack(task)

    # Check pending:
    pending = await queue.pending("coding")  # → [Task, ...]
"""

from __future__ import annotations

import abc
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

# Re-export event bus utilities (moved to events.py, kept here for
# backward compatibility with code that imports from task_queue).
from src.agents.events import EventBus, get_event_bus, get_queue_url  # noqa: F401

# ──────────────────────────────────────────────────────────────────
# Data
# ──────────────────────────────────────────────────────────────────


@dataclass
class Task:
    """A task dispatched to an agent worker."""

    task_id: str
    role: str  # "coding" | "tester" | "devops"
    payload: dict[str, Any]  # arbitrary JSON-serializable data
    created_at: float = field(default_factory=time.time)
    attempts: int = 0


@dataclass
class TaskResult:
    """Result of executing a task."""

    task_id: str
    role: str
    status: str  # "ok" | "failed" | "escalated"
    payload: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────
# Abstract interface
# ──────────────────────────────────────────────────────────────────


class AbstractTaskQueue(abc.ABC):
    """Abstract task queue. Implementations: Redis, file, NATS, Upstash."""

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def push(self, role: str, payload: dict) -> str:
        """Push a task to a role queue. Returns task_id."""
        ...

    @abc.abstractmethod
    async def subscribe(self, role: str, consumer_id: str = "") -> AsyncIterator[Task]:
        """Subscribe to tasks for a role. Yields tasks as they arrive."""
        ...

    @abc.abstractmethod
    async def ack(self, task: Task, result: TaskResult) -> None:
        """Acknowledge a completed task with result."""
        ...

    @abc.abstractmethod
    async def pending(self, role: str) -> list[Task]:
        """List pending (unacknowledged) tasks for a role."""
        ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    # ── Factory ──

    @staticmethod
    def connect(url: str) -> AbstractTaskQueue:
        """Create a queue from a connection URL.

        ``redis://host:port`` — Redis Streams (with file fallback)
        ``file:///path/to/dir`` — File-based queue
        ``upstash://token@host:port`` — Upstash Redis (future)
        ``nats://host:port`` — NATS JetStream (future)
        """
        if url.startswith("redis://") or url.startswith("rediss://"):
            q = RedisTaskQueue(url)
            # Test Redis connectivity; fallback to file if unavailable
            if not q.ping():
                raise ConnectionError(
                    f"Redis unavailable at {url}. Start Redis or check queue_url in local.yaml."
                )
            return q
        if url.startswith("file://"):
            return FileTaskQueue(url)
        raise ValueError(f"Unsupported queue URL: {url}")


# ──────────────────────────────────────────────────────────────────
# Redis Streams implementation
# ──────────────────────────────────────────────────────────────────


class RedisTaskQueue(AbstractTaskQueue):
    """Redis Streams task queue.

    Stream keys:  ``tasks:{role}``  (pending),  ``done:{role}`` (results)

    Requires:  ``pip install redis[hiredis]``
    """

    def __init__(self, url: str = "redis://localhost:6379") -> None:
        self._url = url
        self._client: Any = None
        self._consumer_groups: set[str] = set()
        # Extract project prefix from URL params: ?prefix=slug
        self._prefix = ""
        if "prefix=" in url:
            from urllib.parse import parse_qs, urlparse

            params = parse_qs(urlparse(url).query)
            prefixes = params.get("prefix", [])
            if prefixes:
                self._prefix = prefixes[0] + ":"
                self._url = url.split("?")[0]  # clean URL for Redis connection

    def ping(self) -> bool:
        """Test Redis connectivity. Returns True if Redis is reachable."""
        try:
            import redis

            r = redis.from_url(self._url, socket_connect_timeout=2)
            r.ping()
            r.close()
            return True
        except Exception:
            return False

    async def connect(self) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(
            self._url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            socket_keepalive=True,
            health_check_interval=15,
        )

    async def push(self, role: str, payload: dict) -> str:
        task_id = _make_task_id(role)
        data = {
            "task_id": task_id,
            "payload": json.dumps(payload),
            "created_at": str(time.time()),
        }
        await self._client.xadd(f"{self._prefix}tasks:{role}", data, maxlen=10000)
        return task_id

    async def subscribe(self, role: str, consumer_id: str = "") -> AsyncIterator[Task]:
        consumer_id = consumer_id or f"agent-{role}-{os.getpid()}"
        stream = f"{self._prefix}tasks:{role}"
        group = f"{self._prefix}group-{role}"

        # Create consumer group (idempotent).
        if group not in self._consumer_groups:
            try:
                await self._client.xgroup_create(stream, group, "0", mkstream=True)
            except Exception:
                pass  # already exists
            self._consumer_groups.add(group)

        while True:
            try:
                msgs = await self._client.xreadgroup(
                    group, consumer_id, {stream: ">"}, count=1, block=5000
                )
            except Exception:
                await asyncio.sleep(1)
                continue

            for _stream, entries in msgs:
                for msg_id, data in entries:
                    try:
                        payload = json.loads(data.get("payload", "{}"))
                    except json.JSONDecodeError:
                        payload = {}
                    task = Task(
                        task_id=data.get("task_id", msg_id),
                        role=role,
                        payload=payload,
                        created_at=float(data.get("created_at", 0)),
                    )
                    yield task
                    await self._client.xack(stream, group, msg_id)

    async def ack(self, task: Task, result: TaskResult) -> None:
        data = {
            "task_id": task.task_id,
            "status": result.status,
            "result": json.dumps(result.payload),
        }
        # Retry transient Redis errors (timeout, connection, etc.)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self._client.xadd(
                    f"{self._prefix}done:{task.role}", data, maxlen=10000
                )
                return
            except Exception as exc:
                msg = str(exc).lower()
                is_transient = any(
                    kw in msg
                    for kw in ("timeout", "connection", "eof", "reset", "ssl:")
                )
                if not is_transient or attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def pending(self, role: str) -> list[Task]:
        """Return list of pending (delivered but not acked) tasks.

        Uses XPENDING_RANGE to get actual message dicts with message_id.
        XPENDING summary returns ``{"pending": <int>}`` — the count, not
        a list — so we must use the range variant to iterate messages.
        """
        stream = f"{self._prefix}tasks:{role}"
        group = f"{self._prefix}group-{role}"
        tasks: list[Task] = []
        try:
            # XPENDING_RANGE returns list of dicts:
            # [{"message_id": ..., "consumer": ..., "idle_time": ..., "delivery_count": ...}, ...]
            entries = await self._client.xpending_range(
                stream, group, min="-", max="+", count=1000
            )
        except Exception:
            return tasks
        for entry in entries:
            msg_id = entry.get("message_id", "")
            if msg_id:
                tasks.append(Task(task_id=msg_id, role=role, payload={}))
        return tasks

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()


# ──────────────────────────────────────────────────────────────────
# File-based queue (fallback, no external deps)
# ──────────────────────────────────────────────────────────────────


class FileTaskQueue(AbstractTaskQueue):
    """File-based task queue. Zero dependencies.

    Directory structure::

        tasks/{role}/pending/{task_id}.json   ← PM writes here
        tasks/{role}/done/{task_id}.json      ← agent writes result

    Agents poll via ``watchdog`` or simple sleep loop.
    """

    def __init__(self, url: str = "file://tasks") -> None:
        self._base = Path(url.replace("file://", ""))
        self._watchers: dict[str, asyncio.Task] = {}

    async def connect(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)

    async def push(self, role: str, payload: dict) -> str:
        task_id = _make_task_id(role)
        pending_dir = self._base / role / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        task_file = pending_dir / f"{task_id}.json"
        task_file.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "role": role,
                    "payload": payload,
                    "created_at": time.time(),
                }
            )
        )
        return task_id

    async def subscribe(self, role: str, consumer_id: str = "") -> AsyncIterator[Task]:
        pending_dir = self._base / role / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()

        while True:
            for fpath in sorted(pending_dir.glob("*.json")):
                if fpath.name in seen:
                    continue
                seen.add(fpath.name)
                try:
                    data = json.loads(fpath.read_text())
                    task = Task(
                        task_id=data["task_id"],
                        role=role,
                        payload=data.get("payload", {}),
                        created_at=data.get("created_at", 0),
                    )
                    # Move to in_progress so we don't re-process
                    prog_dir = self._base / role / "in_progress"
                    prog_dir.mkdir(parents=True, exist_ok=True)
                    fpath.rename(prog_dir / fpath.name)
                    yield task
                except Exception:
                    pass
            await asyncio.sleep(0.5)

    async def ack(self, task: Task, result: TaskResult) -> None:
        # Move from in_progress to done
        prog_dir = self._base / task.role / "in_progress"
        done_dir = self._base / task.role / "done"
        done_dir.mkdir(parents=True, exist_ok=True)
        # Move the original task file
        task_file = prog_dir / f"{task.task_id}.json"
        if task_file.exists():
            task_file.rename(done_dir / f"{task.task_id}.json")
        # Write result
        result_file = done_dir / f"{task.task_id}.result.json"
        result_file.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "status": result.status,
                    "payload": result.payload,
                }
            )
        )

    async def pending(self, role: str) -> list[Task]:
        pending_dir = self._base / role / "pending"
        if not pending_dir.is_dir():
            return []
        tasks = []
        for fpath in sorted(pending_dir.glob("*.json")):
            try:
                data = json.loads(fpath.read_text())
                tasks.append(
                    Task(
                        task_id=data["task_id"],
                        role=role,
                        payload=data.get("payload", {}),
                    )
                )
            except Exception:
                pass
        return tasks

    async def close(self) -> None:
        pass  # nothing to close


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_task_id(role: str) -> str:
    import uuid

    return f"{role}-{uuid.uuid4().hex[:12]}"
