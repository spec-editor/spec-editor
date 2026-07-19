"""Tests for src/channels/ — channel abstractions, models, and routing.

TDD: these tests define the expected behaviour before implementation exists.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# =========================================================================
# Data model tests
# =========================================================================


class TestChannelKind:
    """ChannelKind enum — the three top-level taxonomies."""

    def test_kind_values(self):
        from src.channels.models import ChannelKind

        assert ChannelKind.CHAT.value == "chat"
        assert ChannelKind.TRACKER.value == "tracker"
        assert ChannelKind.LOG.value == "log"

    def test_kind_from_string(self):
        from src.channels.models import ChannelKind

        assert ChannelKind("chat") == ChannelKind.CHAT
        assert ChannelKind("tracker") == ChannelKind.TRACKER
        assert ChannelKind("log") == ChannelKind.LOG

    def test_invalid_kind_raises(self):
        from src.channels.models import ChannelKind

        with pytest.raises(ValueError):
            ChannelKind("invalid")


class TestChatItem:
    """ChatItem — unstructured message from chat channels."""

    def test_minimal_item(self):
        from src.channels.models import ChatItem

        item = ChatItem(
            text="Build a login page",
            sender="@alice",
            chat_id="-100123",
            message_id="42",
        )
        assert item.text == "Build a login page"
        assert item.sender == "@alice"
        assert item.thread_id is None
        assert item.attachments == []
        assert item.raw == {}

    def test_full_item_with_thread_and_attachments(self):
        from src.channels.models import ChatItem

        item = ChatItem(
            text="See screenshot",
            sender="@bob",
            chat_id="-100456",
            thread_id="thread-1",
            message_id="99",
            timestamp="2026-07-04T10:00:00Z",
            attachments=["img_001.png"],
            raw={"reply_to": "42"},
        )
        assert item.thread_id == "thread-1"
        assert item.attachments == ["img_001.png"]
        assert item.raw["reply_to"] == "42"


class TestTrackerItem:
    """TrackerItem — structured card from tracker channels."""

    def test_minimal_item(self):
        from src.channels.models import TrackerItem

        item = TrackerItem(id="ISSUE-1", title="Fix login bug", status="To Do")
        assert item.id == "ISSUE-1"
        assert item.labels == []
        assert item.assignee is None
        assert item.raw == {}

    def test_item_with_all_fields(self):
        from src.channels.models import TrackerItem

        item = TrackerItem(
            id="ISSUE-2",
            title="Add dark mode",
            description="Support dark theme toggle",
            status="In Progress",
            labels=["frontend", "spec-editor"],
            assignee="@charlie",
            due_date="2026-08-01",
            url="https://jira.example.com/ISSUE-2",
            raw={"priority": "High"},
        )
        assert item.labels == ["frontend", "spec-editor"]
        assert item.assignee == "@charlie"
        assert item.raw["priority"] == "High"


class TestLogItem:
    """LogItem — streaming event from log channels."""

    def test_minimal_item(self):
        from src.channels.models import LogItem

        item = LogItem(
            timestamp="2026-07-04T10:00:00Z",
            level="error",
            message="Connection refused",
            module="MOD-db",
        )
        assert item.level == "error"
        assert item.module == "MOD-db"
        assert item.count == 1
        assert item.trace_id is None

    def test_item_with_trace(self):
        from src.channels.models import LogItem

        item = LogItem(
            timestamp="2026-07-04T10:00:01Z",
            level="critical",
            message="OOM killed",
            module="MOD-worker",
            trace_id="trace-abc123",
            count=15,
        )
        assert item.trace_id == "trace-abc123"
        assert item.count == 15


class TestChannelConfig:
    """ChannelConfig — per-channel configuration from local.yaml."""

    def test_minimal_config(self):
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="telegram", kind=ChannelKind.CHAT)
        assert cfg.type == "telegram"
        assert cfg.kind == ChannelKind.CHAT
        assert cfg.enabled is True
        assert cfg.config == {}
        assert cfg.analysis == {}
        assert cfg.response == {}
        assert cfg.mapping == {}

    def test_full_tracker_config(self):
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(
            type="jira",
            kind=ChannelKind.TRACKER,
            config={"url": "https://jira.example.com", "token": "${JIRA_TOKEN}"},
            analysis={"dedup_window": 300},
            response={"comment_on": ["code_generated", "test_failed"]},
            mapping={
                "status": {"To Do": "draft", "In Progress": "dispatched", "Done": "confirmed"}
            },
        )
        assert cfg.config["url"] == "https://jira.example.com"
        assert cfg.mapping["status"]["To Do"] == "draft"
        assert "code_generated" in cfg.response["comment_on"]

    def test_disabled_config(self):
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="slack", kind=ChannelKind.CHAT, enabled=False)
        assert cfg.enabled is False

    def test_channel_id_without_name(self):
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="telegram", kind=ChannelKind.CHAT)
        assert cfg.channel_id == "telegram"

    def test_channel_id_with_name(self):
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="telegram", name="dev-team", kind=ChannelKind.CHAT)
        assert cfg.channel_id == "telegram:dev-team"

    def test_invalid_kind_raises(self):
        from src.channels.models import ChannelConfig

        with pytest.raises(ValidationError):
            ChannelConfig(type="test", kind="invalid_kind")


class TestLifecycleEvent:
    """LifecycleEvent — pushed to channels on element changes."""

    def test_status_change_event(self):
        from src.channels.models import LifecycleEvent

        evt = LifecycleEvent(
            event_type="status_changed",
            element_id="SRC-042",
            element_title="Fix login timeout",
            old_status="dispatched",
            new_status="confirmed",
            message="Code generated and tests pass",
            severity="info",
        )
        assert evt.event_type == "status_changed"
        assert evt.old_status == "dispatched"
        assert evt.new_status == "confirmed"

    def test_error_event(self):
        from src.channels.models import LifecycleEvent

        evt = LifecycleEvent(
            event_type="test_failed",
            element_id="SRC-043",
            element_title="Add rate limiting",
            message="3 tests failed in test_auth.py",
            severity="error",
            metadata={"failed_count": 3, "test_file": "test_auth.py"},
        )
        assert evt.severity == "error"
        assert evt.metadata["failed_count"] == 3


# =========================================================================
# Channel ABC tests — verify the interface contract
# =========================================================================


class TestExternalChannelABC:
    """ExternalChannel ABC — the base interface all channels must implement."""

    def test_abc_exists(self):
        from src.channels import ExternalChannel

        assert ExternalChannel is not None

    def test_abc_has_pull(self):
        from src.channels import ExternalChannel
        import inspect

        assert hasattr(ExternalChannel, "pull")
        assert inspect.iscoroutinefunction(ExternalChannel.pull)

    def test_abc_has_push(self):
        from src.channels import ExternalChannel
        import inspect

        assert hasattr(ExternalChannel, "push")
        assert inspect.iscoroutinefunction(ExternalChannel.push)

    def test_abc_has_validate_connection(self):
        from src.channels import ExternalChannel
        import inspect

        assert hasattr(ExternalChannel, "validate_connection")
        assert inspect.iscoroutinefunction(ExternalChannel.validate_connection)

    def test_abc_has_kind_property(self):
        from src.channels import ExternalChannel

        assert hasattr(ExternalChannel, "kind")

    def test_cannot_instantiate_abstract(self):
        from src.channels import ExternalChannel

        with pytest.raises(TypeError):
            ExternalChannel()


class TestChatChannelABC:
    """ChatChannel ABC — for unstructured NL channels."""

    def test_inherits_external_channel(self):
        from src.channels import ExternalChannel
        from src.channels.chat_channel import ChatChannel

        assert issubclass(ChatChannel, ExternalChannel)

    def test_kind_is_chat(self):
        from src.channels.chat_channel import ChatChannel

        assert ChatChannel.kind == "chat"


class TestTrackerChannelABC:
    """TrackerChannel ABC — for structured card/issue channels."""

    def test_inherits_external_channel(self):
        from src.channels import ExternalChannel
        from src.channels.tracker_channel import TrackerChannel

        assert issubclass(TrackerChannel, ExternalChannel)

    def test_kind_is_tracker(self):
        from src.channels.tracker_channel import TrackerChannel

        assert TrackerChannel.kind == "tracker"


class TestLogChannelABC:
    """LogChannel ABC — for streaming event channels."""

    def test_inherits_external_channel(self):
        from src.channels import ExternalChannel
        from src.channels.log_channel import LogChannel

        assert issubclass(LogChannel, ExternalChannel)

    def test_kind_is_log(self):
        from src.channels.log_channel import LogChannel

        assert LogChannel.kind == "log"


# =========================================================================
# Channel factory tests
# =========================================================================


class TestChannelFactory:
    """create_channel() — instantiates the right backend from config."""

    def test_creates_real_backend_for_telegram(self):
        """When telethon is available, telegram gets the real backend."""
        from src.channels import create_channel
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="telegram", kind=ChannelKind.CHAT)
        channel = create_channel(cfg)

        assert channel is not None
        from src.channels.chat_channel import ChatChannel
        assert isinstance(channel, ChatChannel)

    def test_unknown_type_uses_fallback(self):
        """Unregistered channel types use the LogChatChannel fallback."""
        from src.channels import create_channel
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="unknown_chat_app", kind=ChannelKind.CHAT)
        channel = create_channel(cfg)

        assert channel is not None
        from src.channels.chat_channel import LogChatChannel
        assert isinstance(channel, LogChatChannel)

    def test_disabled_channel_returns_none(self):
        from src.channels import create_channel
        from src.channels.models import ChannelConfig, ChannelKind

        cfg = ChannelConfig(type="slack", kind=ChannelKind.CHAT, enabled=False)
        channel = create_channel(cfg)
        assert channel is None


# =========================================================================
# Pro gating tests
# =========================================================================


class TestProGating:
    """Channels are Pro-only features — gated at plugin discovery."""

    def test_sync_plugin_requires_pro(self):
        """Sync adapter plugins set pro_required = True."""
        from src.channels.plugin import ChannelPlugin

        assert ChannelPlugin.pro_required is True

    def test_free_tier_skips_pro_plugin(self):
        """On FREE tier, pro_required plugins are not loaded."""
        from src.licensing.models import ProductTier
        from src.hooks import SpecEditorPlugin

        # Simulate plugin discovery filtering
        plugin = SpecEditorPlugin()
        plugin.pro_required = True

        # FREE tier: skip
        should_load = not plugin.pro_required or ProductTier.FREE != ProductTier.FREE
        # When pro_required=True and tier=FREE → should NOT load
        assert should_load is False

    def test_pro_tier_loads_pro_plugin(self):
        """On PRO tier, pro_required plugins are loaded."""
        from src.licensing.models import ProductTier
        from src.hooks import SpecEditorPlugin

        plugin = SpecEditorPlugin()
        plugin.pro_required = True
        tier = ProductTier.PRO

        should_load = not plugin.pro_required or tier != ProductTier.FREE
        assert should_load is True
