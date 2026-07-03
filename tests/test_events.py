"""Tests for Redis availability and queue URL helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.events import ensure_redis_available


class FakeRedisTaskQueue:
    def __init__(self, url: str) -> None:
        self.url = url

    def ping(self) -> bool:
        return False


def test_ensure_redis_available_skips_non_redis_queue(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "local.yaml").write_text("queue_url: file://tasks\n")

    ensure_redis_available(project)


def test_ensure_redis_available_raises_without_redis_and_no_compose(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "local.yaml").write_text("queue_url: redis://localhost:6379\n")

    monkeypatch.setattr("src.agents.task_queue.RedisTaskQueue", FakeRedisTaskQueue)

    with pytest.raises(RuntimeError, match=r"Redis unavailable at redis://localhost:6379"):
        ensure_redis_available(project)


def test_ensure_redis_available_raises_when_docker_missing_and_compose_exists(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "local.yaml").write_text("queue_url: redis://localhost:6379\n")
    (project / "docker-compose.dev.yml").write_text(
        "version: '3.8'\nservices:\n  redis:\n    image: redis:7-alpine\n"
    )

    monkeypatch.setattr("src.agents.task_queue.RedisTaskQueue", FakeRedisTaskQueue)
    monkeypatch.setattr("src.agents.events.shutil.which", lambda _: None)

    with pytest.raises(RuntimeError, match=r"Docker is not installed"):
        ensure_redis_available(project)
