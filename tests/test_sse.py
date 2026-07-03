"""Tests for SSE (Server-Sent Events) system.

Covers: SseEvent, SseConnection, SseHub, HTTP /events endpoint,
        SSE event firing from MCP tool handlers.

References:
    SSE spec: https://html.spec.whatwg.org/multipage/server-sent-events.html
"""

import json
import threading
import time
from pathlib import Path

import pytest

from src.mcp.server import MCPHandler
from src.mcp.sse import (
    SseConnection,
    SseEvent,
    SseHub,
    stream_sse_events,
)

# =============================================================================
# SseEvent
# =============================================================================


class TestSseEvent:
    def test_to_sse_bytes_format(self):
        event = SseEvent("element_updated", {"id": "MOD-001", "title": "Test"})
        result = event.to_sse_bytes().decode("utf-8")

        assert "event: element_updated" in result
        assert "data:" in result
        assert "MOD-001" in result

    def test_to_sse_bytes_includes_timestamp(self):
        event = SseEvent("connected", {})
        assert event.timestamp > 0

    def test_to_sse_bytes_is_valid_utf8(self):
        event = SseEvent("test", {"key": "value"})
        data = event.to_sse_bytes()
        data.decode("utf-8")  # Should not raise


# =============================================================================
# SseConnection
# =============================================================================


class TestSseConnection:
    def test_send_and_read(self):
        conn = SseConnection()
        event = SseEvent("test", {"data": "hello"})
        conn.send(event)

        result = conn.read(timeout=1.0)
        assert b"event: test" in result
        assert b"hello" in result

    def test_close_stops_read(self):
        conn = SseConnection()
        conn.close()
        result = conn.read(timeout=0.1)
        assert result == b""

    def test_read_returns_keepalive_on_timeout(self):
        conn = SseConnection()
        result = conn.read(timeout=0.1)
        assert b"keepalive" in result

    def test_multiple_sends_queued(self):
        conn = SseConnection()
        conn.send(SseEvent("first", {"n": 1}))
        conn.send(SseEvent("second", {"n": 2}))

        r1 = conn.read(timeout=1.0)
        r2 = conn.read(timeout=1.0)
        assert b"first" in r1
        assert b"second" in r2


# =============================================================================
# SseHub
# =============================================================================


class TestSseHub:
    def test_add_and_remove_connection(self):
        hub = SseHub()
        conn = SseConnection()
        hub.add_connection(conn)
        assert hub.connection_count == 1

        hub.remove_connection(conn)
        assert hub.connection_count == 0

    def test_broadcast_sends_to_all(self):
        hub = SseHub()
        conn1 = SseConnection()
        conn2 = SseConnection()

        hub.add_connection(conn1)
        hub.add_connection(conn2)

        hub.notify("element_updated", {"id": "MOD-001"})

        # Both connections should receive the event
        r1 = conn1.read(timeout=0.5)
        r2 = conn2.read(timeout=0.5)

        assert b"element_updated" in r1
        assert b"element_updated" in r2
        assert b"MOD-001" in r1

    def test_broadcast_removes_dead_connections(self):
        hub = SseHub()
        conn = SseConnection()
        conn.close()  # Dead from the start

        hub.add_connection(conn)
        hub.notify("test", {})

        assert hub.connection_count == 0

    def test_connection_count_only_counts_alive(self):
        hub = SseHub()
        alive = SseConnection()
        dead = SseConnection()
        dead.close()

        hub.add_connection(alive)
        hub.add_connection(dead)

        assert hub.connection_count == 1

    def test_notify_convenience_method(self):
        hub = SseHub()
        conn = SseConnection()
        hub.add_connection(conn)

        hub.notify("diagram_generated", {"aspect": "modules"})
        result = conn.read(timeout=0.5)
        assert b"diagram_generated" in result


# =============================================================================
# SSE event firing from MCP server
# =============================================================================


class TestSseFromMcpServer:
    """Verify that MCP tool handlers fire SSE events."""

    def _make_storage(self, tmp_path: Path):
        from src.storage.filesystem import FilesystemStorage

        (tmp_path / "methodology.yaml").write_text(
            "name: test\nversion: '1.0'\naspects:\n  - name: modules\n    title: Modules\n    element_types:\n      - name: module\n        title: Module\n",
            encoding="utf-8",
        )
        (tmp_path / "source").mkdir(exist_ok=True)
        return FilesystemStorage(tmp_path)
class TestSseStream:
    def test_stream_writes_events(self):
        hub = SseHub()
        stop = threading.Event()
        received: list[bytes] = []

        def write_fn(data: bytes) -> None:
            received.append(data)
            if len(received) >= 3:
                stop.set()

        # Start streaming in a thread
        thread = threading.Thread(
            target=stream_sse_events, args=(hub, write_fn, stop), daemon=True
        )
        thread.start()

        # Send two events
        time.sleep(0.05)
        hub.notify("test1", {"n": 1})
        time.sleep(0.05)
        hub.notify("test2", {"n": 2})

        thread.join(timeout=3.0)

        assert len(received) >= 2
        # First should be connected event
        assert b"connected" in received[0]
        # Should have received our test events
        all_data = b"".join(received)
        assert b"test1" in all_data
        assert b"test2" in all_data
