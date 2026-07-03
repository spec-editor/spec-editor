"""SSE (Server-Sent Events) endpoint for real-time push notifications.

Adds /events endpoint to the MCP HTTP server. Clients (frontend,
VSCode Webview, JCEF browser) subscribe via EventSource.

Events:
    element_updated  — an element was created, updated, or deleted
    diagram_generated — a diagram was generated
    project_switched  — the project was switched

References:
    SSE spec: https://html.spec.whatwg.org/multipage/server-sent-events.html
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

# =============================================================================
# Event types
# =============================================================================


class SseEvent:
    """A server-sent event."""

    def __init__(self, event_type: str, data: dict[str, Any]) -> None:
        self.event_type = event_type
        self.data = data
        self.timestamp = time.time()

    def to_sse_bytes(self) -> bytes:
        """Format as SSE message."""
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        lines = [
            f"event: {self.event_type}",
            f"data: {payload}",
            "",
            "",  # extra blank line terminates the message
        ]
        return "\n".join(lines).encode("utf-8")


# =============================================================================
# SSE connection (per-client)
# =============================================================================


class SseConnection:
    """A single SSE client connection, backed by a thread-safe queue."""

    def __init__(self) -> None:
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._closed = False

    def send(self, event: SseEvent) -> None:
        """Enqueue an event for this client. Non-blocking."""
        if not self._closed:
            self._queue.put(event.to_sse_bytes())

    def close(self) -> None:
        """Close this connection."""
        self._closed = True
        # Push a sentinel to unblock the generator
        self._queue.put(b"")

    def is_closed(self) -> bool:
        return self._closed

    def read(self, timeout: float = 30.0) -> bytes:
        """Block until an event is available, or timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            # Send a keep-alive comment to prevent proxy/client timeout
            return b": keepalive\n\n"


# =============================================================================
# SSE hub (manages all connections)
# =============================================================================


class SseHub:
    """Manages SSE connections and broadcasts events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: list[SseConnection] = []

    def add_connection(self, conn: SseConnection) -> None:
        with self._lock:
            self._connections.append(conn)

    def remove_connection(self, conn: SseConnection) -> None:
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)

    def broadcast(self, event: SseEvent) -> None:
        """Send event to all connected clients."""
        with self._lock:
            dead: list[SseConnection] = []
            for conn in self._connections:
                if conn.is_closed():
                    dead.append(conn)
                else:
                    conn.send(event)
            for conn in dead:
                self._connections.remove(conn)

    def notify(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event by type name."""
        self.broadcast(SseEvent(event_type, data))

    @property
    def connection_count(self) -> int:
        with self._lock:
            # Clean up dead connections
            alive = [c for c in self._connections if not c.is_closed()]
            self._connections[:] = alive
            return len(alive)


# =============================================================================
# SSE HTTP request handler
# =============================================================================


def handle_sse_request(sse_hub: SseHub) -> tuple[int, dict[str, str], bytes | None]:
    """Handle a GET /events request.

    Returns:
        (status_code, headers, body_bytes_or_None)

    The caller should use the headers and stream the body.
    Since stdlib http.server doesn't support streaming well,
    we return headers and the caller writes the SSE stream.
    """
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    # body=None signals "streaming response" to the caller
    return 200, headers, None


def stream_sse_events(sse_hub: SseHub, write_fn, stop_event: threading.Event) -> None:
    """Stream SSE events to an HTTP response.

    This is called in a thread. It blocks until the connection is closed
    or stop_event is set.

    Args:
        sse_hub: The SSE hub to subscribe to.
        write_fn: A callable that writes bytes to the HTTP response.
        stop_event: Set when the connection should be closed.
    """
    conn = SseConnection()
    sse_hub.add_connection(conn)

    # Send initial connection event
    init_event = SseEvent("connected", {"message": "SSE connection established"})
    write_fn(init_event.to_sse_bytes())

    try:
        while not stop_event.is_set() and not conn.is_closed():
            data = conn.read(timeout=30.0)
            try:
                write_fn(data)
            except (BrokenPipeError, OSError):
                break
    finally:
        conn.close()
        sse_hub.remove_connection(conn)
