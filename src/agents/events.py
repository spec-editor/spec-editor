"""Event Bus — Redis pub/sub for inter-agent communication.

Extracted from task_queue.py to keep core independent of persistent
agent infrastructure. Used by storage layer to notify plugins of changes.

Channels (auto-prefixed with project_slug):
    elements:changed   — any element created/updated/deleted (storage layer)
    spec:created       — new requirement created by analyst-manager
    spec:refine        — vague requirement needs detailing
    spec:updated       — requirement refined by analyst-manager
    cycle:bug          — new SRC-BUG-* found by project-manager
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


class EventBus:
    """Redis pub/sub for inter-manager communication."""

    def __init__(self, redis_url: str, prefix: str = "") -> None:
        import redis

        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix + ":" if prefix else ""
        self._pubsub = self._redis.pubsub()

    def publish(self, channel: str, data: dict) -> None:
        """Publish an event to a channel."""
        import json

        self._redis.publish(f"{self._prefix}events:{channel}", json.dumps(data))

    def subscribe(self, *channels: str):
        """Subscribe to event channels. Yields (channel, data) tuples."""
        import json

        full_channels = [f"{self._prefix}events:{c}" for c in channels]
        self._pubsub.subscribe(*full_channels)
        for message in self._pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                except json.JSONDecodeError:
                    data = {"raw": message["data"]}
                # Strip prefix from channel name
                channel = message["channel"].replace(f"{self._prefix}events:", "")
                yield channel, data

    def close(self) -> None:
        self._pubsub.close()
        self._redis.close()


def get_event_bus(project_path: str | Path) -> EventBus:
    """Create an EventBus from project local.yaml config.

    Delegates to the pluggable ``src.events`` factory for backend selection
    (Redis, memory, NATS).  Falls back to the legacy RedisEventBus for
    backward compatibility if the new factory is unavailable.
    """
    try:
        from src.events import create_event_bus as _create_bus

        bus = _create_bus(project_path)
        # Wrap in legacy EventBus-compatible interface
        if hasattr(bus, "publish") and hasattr(bus, "subscribe"):
            # The new AbstractEventBus has the same interface,
            # but legacy code calls EventBus() directly. We wrap it.
            import types

            wrapper = types.SimpleNamespace()
            wrapper._bus = bus
            wrapper.publish = bus.publish
            wrapper.subscribe = bus.subscribe
            wrapper.close = bus.close
            return wrapper  # duck-typed — has publish/subscribe/close
    except Exception:
        pass

    # Legacy fallback
    proj = Path(project_path)
    local_yaml = proj / "local.yaml"
    url = "redis://localhost:6379"
    slug = proj.name.replace(" ", "-").replace(".", "-")
    if local_yaml.exists():
        import yaml

        data = yaml.safe_load(local_yaml.read_text()) or {}
        url = data.get("queue_url", url)
        slug = data.get("project_slug", slug)
    if "?" in url:
        url = url.split("?")[0]
    return EventBus(url, slug)


def get_queue_url(project_path: str | Path) -> str:
    """Read queue URL from local.yaml or env. Adds project_slug prefix for Redis."""
    url = os.environ.get("SPEC_EDITOR_QUEUE_URL", "")
    proj = Path(project_path)

    if not url:
        local_yaml = proj / "local.yaml"
        if local_yaml.exists():
            import yaml

            data = yaml.safe_load(local_yaml.read_text()) or {}
            url = data.get("queue_url", "")

    if not url:
        import sys

        print(
            "[yellow]WARNING:[/yellow] No queue_url in local.yaml. "
            "Using file-based queue at tasks/. "
            "Run `spec-editor init` to recreate with Redis, or add "
            "'queue_url: redis://localhost:6379' to local.yaml.",
            file=sys.stderr,
        )
        return f"file://{proj / 'tasks'}"

    # Resolve relative file:// paths
    if url.startswith("file://") and not url.startswith("file:///"):
        rel = url[7:]  # strip file://
        url = f"file://{proj / rel}"

    # For Redis, add project prefix
    if "redis" in url:
        slug = _get_project_slug(proj)
        if slug and "?" in url:
            url += f"&prefix={slug}"
        elif slug:
            url += f"?prefix={slug}"
    return url


def _get_project_slug(project_path: Path) -> str:
    """Read project_slug from local.yaml or derive from directory name."""
    local_yaml = project_path / "local.yaml"
    if local_yaml.exists():
        import yaml

        data = yaml.safe_load(local_yaml.read_text()) or {}
        slug = data.get("project_slug", "")
        if slug:
            return slug
    return project_path.name.replace(" ", "-").replace(".", "-")


def ensure_redis_available(project_path: str | Path, timeout: float = 10.0) -> None:
    """Ensure Redis is reachable for the current project queue.

    If Redis is configured and unavailable, try to start it via Docker
    Compose if a compose file exists. Otherwise raise a RuntimeError.
    """
    url = get_queue_url(project_path)
    if not url.startswith("redis://") and not url.startswith("rediss://"):
        return

    from src.agents.task_queue import RedisTaskQueue

    queue = RedisTaskQueue(url)
    if queue.ping():
        return

    compose_path = _find_redis_docker_compose(Path(project_path))
    if compose_path is None:
        raise RuntimeError(
            f"Redis unavailable at {url}. Start Redis or check queue_url in local.yaml."
        )

    if shutil.which("docker") is None and shutil.which("docker-compose") is None:
        raise RuntimeError(
            f"Redis unavailable at {url}. Docker is not installed, so spec-editor cannot auto-start Redis. "
            f"Install Docker or start Redis manually, then run: docker compose -f {compose_path.name} up -d redis"
        )

    if not _start_redis_via_docker(compose_path):
        raise RuntimeError(
            f"Redis unavailable at {url}. Failed to start Redis via Docker Compose ({compose_path.name}). "
            "Start Redis manually and re-run."
        )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if queue.ping():
            return
        time.sleep(1)

    raise RuntimeError(
        f"Redis unavailable at {url} after starting Docker Compose. "
        f"Check Redis logs or run 'docker compose -f {compose_path.name} logs -f redis'."
    )


def _find_redis_docker_compose(project_path: Path) -> Path | None:
    for filename in ("docker-compose.dev.yml", "docker-compose.yml"):
        path = project_path / filename
        if path.exists():
            return path
    return None


def _start_redis_via_docker(compose_path: Path) -> bool:
    for cmd in (
        ["docker", "compose", "-f", str(compose_path), "up", "-d", "redis"],
        ["docker-compose", "-f", str(compose_path), "up", "-d", "redis"],
    ):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(
                cmd,
                cwd=str(compose_path.parent),
                capture_output=True,
                check=True,
                text=True,
                timeout=30,
            )
            return True
        except subprocess.CalledProcessError:
            continue
        except FileNotFoundError:
            continue
    return False
