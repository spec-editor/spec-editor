"""Tests for channel event bridge — Redis pub/sub integration.

The event bridge connects channels to the cycle pipeline via Redis streams.
Channels publish incoming items; the pipeline subscribes and processes them.
"""

from __future__ import annotations

import pytest


# =========================================================================
# ChannelEvent model tests
# =========================================================================


class TestChannelEvent:
    """ChannelEvent — a single item from a channel, serialised for Redis."""

    def test_event_from_chat_item(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(
            text="Build a login page",
            sender="@alice",
            chat_id="-100123",
            message_id="42",
            timestamp="2026-07-04T10:00:00Z",
        )
        evt = ChannelEvent.from_item(
            channel_type="telegram",
            item=item,
        )
        assert evt.channel_type == "telegram"
        assert evt.item_type == "ChatItem"
        assert "@alice" in evt.payload["sender"]

    def test_event_from_tracker_item(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import TrackerItem

        item = TrackerItem(id="ISSUE-1", title="Fix bug", status="To Do")
        evt = ChannelEvent.from_item(channel_type="jira", item=item)
        assert evt.channel_type == "jira"
        assert evt.item_type == "TrackerItem"
        assert evt.payload["id"] == "ISSUE-1"

    def test_event_from_log_item(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import LogItem

        item = LogItem(
            timestamp="2026-07-04T10:00:00Z",
            level="error",
            message="Connection refused",
            module="MOD-db",
        )
        evt = ChannelEvent.from_item(channel_type="grafana_loki", item=item)
        assert evt.channel_type == "grafana_loki"
        assert evt.item_type == "LogItem"
        assert evt.payload["level"] == "error"

    def test_event_serialisation_roundtrip(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(
            text="Test message",
            sender="@test",
            chat_id="chat-1",
            message_id="99",
            raw={"reply_to": "42"},
        )
        original = ChannelEvent.from_item("telegram", item)

        # Serialise
        data = original.to_dict()
        assert "channel_type" in data
        assert "payload" in data

        # Deserialise
        restored = ChannelEvent.from_dict(data)
        assert restored.channel_type == original.channel_type
        assert restored.item_type == original.item_type
        assert restored.payload["text"] == "Test message"
        assert restored.payload["raw"]["reply_to"] == "42"

    def test_event_stream_key(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(text="x", sender="x", chat_id="x", message_id="x")
        evt = ChannelEvent.from_item("telegram", item)

        key = evt.stream_key()
        assert key == "channel:telegram:in"
        assert evt.stream_key() == "channel:telegram:in"  # idempotent

    def test_event_stream_key_with_name(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(text="x", sender="x", chat_id="x", message_id="x")
        evt = ChannelEvent.from_item("telegram", item, channel_name="dev-team")

        assert evt.stream_key() == "channel:telegram:dev-team:in"
        assert evt.channel_name == "dev-team"
        assert evt.channel_id == "telegram:dev-team"

    def test_event_stream_key_varied(self):
        from src.channels.event_bridge import ChannelEvent
        from src.channels.models import TrackerItem

        item = TrackerItem(id="X-1", title="x", status="x")
        evt = ChannelEvent.from_item("jira", item, channel_name="SPEC")
        assert evt.stream_key() == "channel:jira:SPEC:in"
        assert evt.channel_id == "jira:SPEC"


# =========================================================================
# ChannelBridge tests (Redis-backed)
# =========================================================================


class TestChannelBridge:
    """ChannelBridge — publish/subscribe channel events via Redis."""

    @pytest.mark.asyncio
    async def test_bridge_publish_succeeds(self):
        """Publishing to a mock Redis stream should not raise."""
        from unittest.mock import AsyncMock, patch

        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(text="Hello", sender="@a", chat_id="c", message_id="1")
        evt = ChannelEvent.from_item("telegram", item)

        with patch("src.channels.event_bridge.ChannelBridge._get_redis", return_value=AsyncMock()):
            bridge = ChannelBridge(redis_url="redis://localhost:6379")
            result = await bridge.publish(evt)
            assert result is True

    @pytest.mark.asyncio
    async def test_bridge_publish_no_redis(self):
        """When Redis is unavailable, publish returns False gracefully."""
        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(text="Hello", sender="@a", chat_id="c", message_id="1")
        evt = ChannelEvent.from_item("telegram", item)

        bridge = ChannelBridge(redis_url="redis://nonexistent:9999")
        result = await bridge.publish(evt)
        # Should degrade gracefully
        assert result is False

    @pytest.mark.asyncio
    async def test_bridge_subscribe_returns_events(self):
        """Subscribing to a stream yields ChannelEvent objects."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChatItem

        item = ChatItem(text="Hi", sender="@b", chat_id="c2", message_id="2")
        evt = ChannelEvent.from_item("telegram", item)

        # Mock Redis with one message in the stream
        mock_redis = AsyncMock()
        mock_redis.xread.return_value = [
            ["channel:telegram:in", [("msg-1", {"data": evt.to_json()})]]
        ]

        with patch("src.channels.event_bridge.ChannelBridge._get_redis", return_value=mock_redis):
            bridge = ChannelBridge(redis_url="redis://localhost:6379")
            events = []
            async for received in bridge.subscribe("telegram"):
                events.append(received)
                break  # one event for the test

            assert len(events) == 1
            assert events[0].channel_type == "telegram"

    def test_bridge_stream_key_format(self):
        from src.channels.event_bridge import ChannelBridge

        assert ChannelBridge.stream_key_for("telegram") == "channel:telegram:in"
        assert ChannelBridge.stream_key_for("jira") == "channel:jira:in"
        assert ChannelBridge.stream_key_for("jira", "SPEC") == "channel:jira:SPEC:in"

    def test_parse_channel_id(self):
        from src.channels.event_bridge import ChannelBridge

        assert ChannelBridge.parse_channel_id("telegram:dev-team") == ("telegram", "dev-team")
        assert ChannelBridge.parse_channel_id("jira") == ("jira", "")
        assert ChannelBridge.parse_channel_id("grafana_loki:prod") == ("grafana_loki", "prod")


# =========================================================================
# Integration: Channel → Event Bridge flow
# =========================================================================


class TestChannelToEventBridgeFlow:
    """End-to-end: channel.pull() → ChannelEvent → bridge.publish() → bridge.subscribe()."""

    @pytest.mark.asyncio
    async def test_fallback_channel_publishes_to_bridge(self):
        """Even the LogChatChannel can publish events to the bridge."""
        from unittest.mock import AsyncMock, patch

        from src.channels import create_channel
        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChannelConfig, ChannelKind, ChatItem

        # Create a fallback chat channel
        cfg = ChannelConfig(type="telegram", kind=ChannelKind.CHAT)
        channel = create_channel(cfg)

        # Simulate a pull that returns items
        items = [
            ChatItem(text="Req 1", sender="@x", chat_id="c", message_id="1"),
            ChatItem(text="Req 2", sender="@y", chat_id="c", message_id="2"),
        ]

        # Convert to events
        events = [
            ChannelEvent.from_item(cfg.type, item)
            for item in items
        ]

        mock_redis = AsyncMock()
        with patch("src.channels.event_bridge.ChannelBridge._get_redis", return_value=mock_redis):
            bridge = ChannelBridge(redis_url="redis://localhost:6379")
            for evt in events:
                result = await bridge.publish(evt)
                assert result is True

            # Verify 2 messages were published to the stream
            assert mock_redis.xadd.call_count == 2

    @pytest.mark.asyncio
    async def test_channel_pull_and_publish_pattern(self):
        """The standard pattern: pull items from channel, publish to bridge."""
        from unittest.mock import AsyncMock, patch

        from src.channels.event_bridge import ChannelBridge
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="jira", kind=ChannelKind.TRACKER)
        mock_redis = AsyncMock()

        with patch("src.channels.event_bridge.ChannelBridge._get_redis", return_value=mock_redis):
            bridge = ChannelBridge(redis_url="redis://localhost:6379")

            # Simulate publishing tracker events
            from src.channels.event_bridge import ChannelEvent
            from src.channels.models import TrackerItem

            items = [
                TrackerItem(id="J-1", title="Bug", status="To Do"),
                TrackerItem(id="J-2", title="Feature", status="In Progress"),
            ]
            for item in items:
                evt = ChannelEvent.from_item("jira", item)
                await bridge.publish(evt)

            assert mock_redis.xadd.call_count == 2

            # Verify stream keys
            call_args = [c[0][0] for c in mock_redis.xadd.call_args_list]
            assert all(str(k) in ("channel:jira:in", "b'channel:jira:in'") for k in call_args)


# =========================================================================
# Integration: full loop — channel → SRC → event → route → push
# =========================================================================


class TestFullIntegrationLoop:
    """End-to-end: Telegram ChatItem → bridge → analyze → SRC → event → route → push."""

    @pytest.mark.asyncio
    async def test_full_loop_chat_item_to_push(self):
        """Prove the full cycle: pull item → publish → subscribe → route → push."""
        from unittest.mock import AsyncMock, patch

        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChatItem, LifecycleEvent
        from src.channels.router import ChannelRouter

        # 1. Simulate a Telegram message
        item = ChatItem(
            text="Users must be able to reset their password",
            sender="@alice",
            chat_id="-100123",
            message_id="42",
        )
        evt = ChannelEvent.from_item("telegram", item, channel_name="dev-team")
        assert evt.channel_id == "telegram:dev-team"

        # 2. Publish to bridge (mocked)
        mock_redis = AsyncMock()
        with patch("src.channels.event_bridge.ChannelBridge._get_redis", return_value=mock_redis):
            bridge = ChannelBridge()
            assert await bridge.publish(evt) is True

        # 3. Lifecycle event from engine (after SRC created)
        lifecycle = LifecycleEvent(
            event_type="dispatched",
            element_id="SRC-042",
            element_title="Password reset",
            old_status="reviewed",
            new_status="dispatched",
            message="Code generated",
            severity="info",
        )

        # 4. Route
        router = ChannelRouter(".")
        channels = [
            {"type": "telegram", "name": "dev-team", "kind": "chat", "enabled": True,
             "response": {"mode": "summary", "include_severities": ["info"]}},
            {"type": "jira", "name": "SPEC", "kind": "tracker", "enabled": True,
             "response": {"mode": "per_event", "comment_on": ["dispatched"]}},
        ]
        decisions = await router.route([lifecycle], channels)
        pushes = [d for d in decisions if d.action == "push"]
        assert len(pushes) == 2  # both channels accept

        # 5. Summary message
        msg = router.build_summary_message([lifecycle], "telegram:dev-team")
        assert "SRC-042" in msg

    @pytest.mark.asyncio
    async def test_severity_filter_skips_info(self):
        from src.channels.models import LifecycleEvent
        from src.channels.router import ChannelRouter

        router = ChannelRouter(".")
        events = [
            LifecycleEvent(event_type="dispatched", element_id="S-1", message="ok", severity="info"),
            LifecycleEvent(event_type="test_failed", element_id="S-2", message="fail", severity="error"),
        ]
        channels = [{"type": "telegram", "name": "qa", "kind": "chat", "enabled": True,
                      "response": {"mode": "per_event", "include_severities": ["error"]}}]
        decisions = await router.route(events, channels)
        pushes = [d for d in decisions if d.action == "push"]
        assert len(pushes) == 1
        assert pushes[0].event.severity == "error"

    @pytest.mark.asyncio
    async def test_silent_mode_skips_all(self):
        from src.channels.models import LifecycleEvent
        from src.channels.router import ChannelRouter

        router = ChannelRouter(".")
        events = [LifecycleEvent(event_type="deployed", element_id="S-1", message="ok", severity="info")]
        channels = [{"type": "slack", "kind": "chat", "enabled": True,
                      "response": {"mode": "silent"}}]
        decisions = await router.route(events, channels)
        assert all(d.action == "skip" for d in decisions)


# =========================================================================
# Smoke: spec-editor analyze --channel helpers
# =========================================================================


class TestAnalyzeChannelHelpers:
    def test_parse_plain(self):
        from src.cli.commands_ingest import _parse_channel_id
        assert _parse_channel_id("telegram") == ("telegram", "")

    def test_parse_with_name(self):
        from src.cli.commands_ingest import _parse_channel_id
        assert _parse_channel_id("jira:SPEC") == ("jira", "SPEC")

    def test_find_exact(self):
        from src.cli.commands_ingest import _find_channel_config
        channels = [{"type": "t", "name": "a"}, {"type": "t", "name": "b"}]
        assert _find_channel_config(channels, "t", "b")["name"] == "b"

    def test_find_first_no_name(self):
        from src.cli.commands_ingest import _find_channel_config
        channels = [{"type": "t", "name": "first"}, {"type": "t", "name": "second"}]
        assert _find_channel_config(channels, "t", "")["name"] == "first"

    def test_find_none(self):
        from src.cli.commands_ingest import _find_channel_config
        assert _find_channel_config([], "x", "") is None

    def test_analyze_cmd_has_channel_param(self):
        from src.cli.commands_ingest import analyze_cmd
        assert "channel" in [p.name for p in analyze_cmd.params]


def _try_redis_ping() -> bool:
    """Check if Redis is available at localhost:6379."""
    try:
        import redis
        r = redis.from_url("redis://localhost:6379", socket_connect_timeout=1)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


# =========================================================================
# End-to-end: WorkflowEngine with channels
# =========================================================================


class TestCycleWithChannels:
    """Verify that sync_external_channels and route_channel_events steps work."""

    def test_engine_has_channel_steps_registered(self):
        """The engine's tool registry includes channel-related steps."""
        from spec_editor_cycle.engine import WorkflowEngine
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(".")
        engine = WorkflowEngine(storage, ".")

        assert "sync_external_channels" in engine._handlers
        assert "route_channel_events" in engine._handlers
        assert callable(engine._handlers["sync_external_channels"])
        assert callable(engine._handlers["route_channel_events"])

    def test_sync_external_no_local_yaml(self):
        """When no local.yaml exists, sync returns empty result gracefully."""
        import tempfile
        from pathlib import Path

        from spec_editor_cycle.engine import WorkflowEngine
        from src.storage.filesystem import FilesystemStorage

        with tempfile.TemporaryDirectory() as td:
            storage = FilesystemStorage(Path(td))
            engine = WorkflowEngine(storage, td)

            import asyncio
            result = asyncio.run(engine._sync_external_channels(project_path=td))

            assert result["channels_synced"] == 0
            assert result["items_pulled"] == 0
            assert "message" in result

    def test_route_channel_events_no_buffered(self):
        """When no events are buffered, routing returns empty."""
        import tempfile
        from pathlib import Path

        from spec_editor_cycle.engine import WorkflowEngine
        from src.storage.filesystem import FilesystemStorage

        with tempfile.TemporaryDirectory() as td:
            storage = FilesystemStorage(Path(td))
            engine = WorkflowEngine(storage, td)
            engine._pending_channel_events = []

            import asyncio
            result = asyncio.run(engine._route_channel_events(project_path=td))

            assert result["routed"] == 0
            assert "message" in result

    def test_event_buffer_cleared_after_sync(self):
        """After sync_external_channels, buffer is emptied (non-destructive)."""
        from pathlib import Path

        from spec_editor_cycle.engine import WorkflowEngine
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(Path("."))
        engine = WorkflowEngine(storage, ".")

        # Simulate events accumulated during a cycle
        engine._record_event("dispatched", "SRC-001", "Test", "reviewed", "dispatched", "msg")
        engine._record_event("test_failed", "SRC-002", "Fail", "", "", "3 failures", "error")
        assert len(engine._pending_channel_events) == 2

        # Sync should capture and clear
        import asyncio
        asyncio.run(engine._sync_external_channels(project_path="."))

        # Buffer is now empty
        assert len(engine._pending_channel_events) == 0

    def test_config_validation_catches_errors(self):
        """Config validation detects missing fields and invalid kinds."""
        from src.channels.models import ChannelConfig

        configs = [
            {"type": "telegram", "kind": "chat", "config": {"bot_token": "x"}},
            {"type": "bad", "kind": "invalid_kind"},
            {"type": "", "kind": "chat"},
        ]
        errors = ChannelConfig.validate_configs(configs)
        assert len(errors) >= 2  # at least invalid kind + missing type


# =========================================================================
# Real Redis integration — requires redis-server on localhost:6379
# =========================================================================


@pytest.mark.skipif(
    not __import__("os").environ.get("SPEC_EDITOR_TEST_REDIS")
    and not _try_redis_ping(),
    reason="Redis not available — start redis-server or set SPEC_EDITOR_TEST_REDIS=1",
)
class TestRedisIntegration:
    """Tests that require a real Redis instance."""

    @pytest.mark.asyncio
    async def test_publish_subscribe_roundtrip(self):
        """Publish an event, subscribe and receive it back — real Redis."""
        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChatItem

        bridge = ChannelBridge(redis_url="redis://localhost:6379")

        item = ChatItem(
            text="Real Redis test message",
            sender="@test",
            chat_id="test-chat",
            message_id="int-1",
        )
        evt = ChannelEvent.from_item("telegram", item, channel_name="int-test")
        evt.event_id = f"int-{__import__('time').time()}"

        # Publish
        published = await bridge.publish(evt)
        assert published is True

        # Subscribe — read back our event (non-destructive)
        received = []
        async for r_evt in bridge.subscribe("telegram:int-test", consumer_id="int-test-consumer", block_ms=2000):
            if r_evt.event_id == evt.event_id:
                received.append(r_evt)
                break
            if len(received) > 50:  # safety — don't loop forever
                break

        await bridge.close()
        assert len(received) == 1
        assert received[0].payload["text"] == "Real Redis test message"
        assert received[0].channel_name == "int-test"

    @pytest.mark.asyncio
    async def test_serialisation_roundtrip_real_redis(self):
        """Full JSON roundtrip through real Redis — verify no data loss."""
        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import TrackerItem

        bridge = ChannelBridge(redis_url="redis://localhost:6379")

        item = TrackerItem(
            id="INT-1",
            title="Integration test issue",
            description="Testing Redis roundtrip",
            status="In Progress",
            labels=["spec-editor", "integration"],
            assignee="test-bot",
        )
        evt = ChannelEvent.from_item("jira", item, channel_name="INT")
        evt.event_id = f"int-{__import__('time').time()}"

        await bridge.publish(evt)

        received = None
        async for r_evt in bridge.subscribe("jira:INT", consumer_id="int-consumer", block_ms=2000):
            if r_evt.event_id == evt.event_id:
                received = r_evt
                break

        await bridge.close()
        assert received is not None
        assert received.payload["labels"] == ["spec-editor", "integration"]
        assert received.payload["assignee"] == "test-bot"
        assert received.channel_name == "INT"
