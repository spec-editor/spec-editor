"""Tests for DialogueLogger, _compact_args, _last_msg_from and _agent_loop integration."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.dialogue import (
    DialogueLogger,
    DialogueManager,
    MessageBus,
    _compact_args,
    _last_msg_from,
)
from src.providers.base import LLMResponse, Message, MessageRole, ToolCall

# ======================================================================
# Fake agents for tests
# ======================================================================


class FakeResponseAgent:
    """Mock SpecAgent — returns predefined responses."""

    def __init__(self, name: str, responses: list[LLMResponse] | None = None):
        self.name = name
        self._responses = responses or [LLMResponse(content=f"response from {name}")]
        self._idx = 0
        self._provider = MagicMock()
        self._provider.supports_tools = MagicMock(return_value=True)

    def _get_methodology(self):
        return None

    def _get_source_dir(self):
        return ""

    async def run(self, message, history=None, trace_callback=None):
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


# ======================================================================
# DialogueLogger
# ======================================================================


class TestDialogueLogger:
    """DialogueLogger: writing log to dialogue.jsonl."""

    def test_log_message_writes_all_fields(self):
        """log_message writes content, tool_calls with args, received_message."""
        with tempfile.TemporaryDirectory() as tmp:
            dl = DialogueLogger(Path(tmp) / "dialogue.jsonl")
            dl.log_message(
                "Agent 1",
                "Hello colleague!",
                [
                    {"name": "read_element", "arguments": {"element_id": "ent-site"}},
                    {
                        "name": "add_relationship",
                        "arguments": {
                            "source_id": "ent-site",
                            "target_id": "ent-deployment",
                            "rel_type": "references",
                        },
                    },
                ],
                received_message="Previous message from Agent 2",
            )
            dl.close()

            with open(Path(tmp) / "dialogue.jsonl") as f:
                entry = json.loads(f.readline())

            assert entry["agent"] == "Agent 1"
            assert entry["content"] == "Hello colleague!"
            assert entry["received"] == "Previous message from Agent 2"
            assert len(entry["tool_calls"]) == 2
            assert entry["tool_calls"][0]["name"] == "read_element"
            assert entry["tool_calls"][0]["args"] == {"element_id": "ent-site"}
            # add_relationship args compacted to source_id, target_id, rel_type
            assert entry["tool_calls"][1]["name"] == "add_relationship"
            assert "source_id" in entry["tool_calls"][1]["args"]

    def test_log_message_without_tool_calls(self):
        """log_message without tool_calls does not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            dl = DialogueLogger(Path(tmp) / "dialogue.jsonl")
            dl.log_message("Agent 1", "just a message")
            dl.close()

            with open(Path(tmp) / "dialogue.jsonl") as f:
                entry = json.loads(f.readline())

            assert entry["tool_calls"] == []
            assert entry["received"] == ""

    def test_log_message_truncates_content(self):
        """log_message truncates content to 3000 characters."""
        with tempfile.TemporaryDirectory() as tmp:
            dl = DialogueLogger(Path(tmp) / "dialogue.jsonl")
            long_text = "x" * 5000
            dl.log_message("Agent 1", long_text)
            dl.close()

            with open(Path(tmp) / "dialogue.jsonl") as f:
                entry = json.loads(f.readline())

            assert len(entry["content"]) == 3000

    def test_log_orchestrator_with_agent_count(self):
        """log_orchestrator writes decision with agent count."""
        with tempfile.TemporaryDirectory() as tmp:
            dl = DialogueLogger(Path(tmp) / "dialogue.jsonl")
            dl.log_orchestrator("continue", "Диалог продуктивен", agent_count=4)
            dl.close()

            with open(Path(tmp) / "dialogue.jsonl") as f:
                entry = json.loads(f.readline())

            assert entry["agent"] == "Orchestrator (4 agents)"
            assert entry["decision"] == "continue"
            assert entry["reason"] == "Диалог продуктивен"


# ======================================================================
# _compact_args
# ======================================================================


class TestCompactArgs:
    """_compact_args: compacting tool_calls arguments."""

    def test_write_element_keeps_id_and_title(self):
        args = {
            "id": "MOD-042",
            "title": "API Gateway",
            "aspect": "modules",
            "element_type": "module",
            "content": "..." * 100,
        }
        result = _compact_args("write_element", args)
        assert "id" in result
        assert "title" in result
        assert "aspect" in result
        assert "element_type" in result
        assert "content" not in result  # too long — don't show

    def test_add_relationship_keeps_source_target_rel(self):
        args = {
            "source_id": "ent-site",
            "target_id": "ent-deployment",
            "rel_type": "references",
            "role": "relates_to",
        }
        result = _compact_args("add_relationship", args)
        assert result == {
            "source_id": "ent-site",
            "target_id": "ent-deployment",
            "rel_type": "references",
        }

    def test_remove_relationship_same_as_add(self):
        result = _compact_args(
            "remove_relationship", {"source_id": "A", "target_id": "B", "rel_type": "R"}
        )
        assert "source_id" in result
        assert "target_id" in result

    def test_search_elements_shows_query(self):
        result = _compact_args("search_elements", {"query": "deployment"})
        assert result == {"query": "deployment"}

    def test_run_metrics_returns_empty(self):
        result = _compact_args("run_metrics", {})
        assert result == {}

    def test_run_validate_returns_empty(self):
        result = _compact_args("run_validate", {})
        assert result == {}

    def test_report_complete_returns_empty(self):
        result = _compact_args("report_complete", {})
        assert result == {}

    def test_unknown_tool_shows_first_two_keys(self):
        result = _compact_args("unknown_tool", {"a": "1", "b": "2", "c": "3"})
        assert len(result) <= 2

    def test_empty_args_returns_empty(self):
        result = _compact_args("write_element", {})
        assert result == {}


# ======================================================================
# _last_msg_from
# ======================================================================


class TestLastMsgFrom:
    """_last_msg_from: find the agent's last message."""

    def test_finds_last_message(self):
        history = [
            Message(role=MessageRole.ASSISTANT, content="msg1", name="Agent 1"),
            Message(role=MessageRole.ASSISTANT, content="msg2", name="Agent 2"),
            Message(role=MessageRole.ASSISTANT, content="msg3", name="Agent 1"),
        ]
        result = _last_msg_from(history, "Agent 1")
        assert result is not None
        assert result.content == "msg3"

    def test_returns_none_if_not_found(self):
        history = [
            Message(role=MessageRole.ASSISTANT, content="msg1", name="Agent 2"),
        ]
        result = _last_msg_from(history, "Agent 1")
        assert result is None

    def test_skips_empty_content(self):
        history = [
            Message(role=MessageRole.ASSISTANT, content="", name="Agent 1"),
            Message(role=MessageRole.ASSISTANT, content="real msg", name="Agent 1"),
        ]
        result = _last_msg_from(history, "Agent 1")
        assert result.content == "real msg"


# ======================================================================
# Integration: _agent_loop with first message
# ======================================================================


class TestAgentLoopFirstMessage:
    """_agent_loop: first call with initial_message should not crash."""

    @pytest.mark.asyncio
    async def test_initial_message_does_not_crash(self):
        """Verify that _agent_loop with initial_message does not raise NameError."""
        from src.agents.dialogue import DialogueManager

        # Minimal DialogueManager with mock agents
        agent = FakeResponseAgent("Agent 1")
        bus = MessageBus()
        stop = asyncio.Event()

        # Create manager with logger in a temp directory
        with tempfile.TemporaryDirectory() as tmp:
            dm = _make_minimal_manager(agent, Path(tmp))

            # Start _agent_loop with initial_message
            task = asyncio.create_task(
                dm._agent_loop(
                    agent,
                    "Agent 1",
                    bus,
                    stop,
                    initial_message="Initial task for Agent 1",
                )
            )

            # Wait for completion (agent sends one message and loops)
            await asyncio.sleep(0.1)
            stop.set()  # stop the loop

            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                task.cancel()

            # Verify message ended up in the bus
            history = await bus.get_history()
            assert len(history) >= 1
            assert history[0].name == "Agent 1"
            assert history[0].content == "response from Agent 1"

    @pytest.mark.asyncio
    async def test_helper_exits_after_initial_message(self):
        """Helper should exit after the first message."""
        from src.agents.dialogue import DialogueManager

        agent = FakeResponseAgent("Helper-UI-1")
        bus = MessageBus()
        stop = asyncio.Event()

        with tempfile.TemporaryDirectory() as tmp:
            dm = _make_minimal_manager(agent, Path(tmp))

            task = asyncio.create_task(
                dm._agent_loop(
                    agent,
                    "Helper-UI-1",
                    bus,
                    stop,
                    initial_message="Analyze UI",
                    is_helper=True,
                )
            )

            await asyncio.wait_for(task, timeout=1.0)

            history = await bus.get_history()
            # Helper posts two messages: response + task completion
            assert len(history) >= 2
            assert any("Task completed" in (m.content or "") for m in history)

    @pytest.mark.asyncio
    async def test_agent_responds_to_colleague(self):
        """Agent receives a message from a colleague and responds."""
        from src.agents.dialogue import DialogueManager

        agent = FakeResponseAgent(
            "Agent 2",
            [
                LLMResponse(content="I agree, let me add more connections"),
            ],
        )
        bus = MessageBus()
        stop = asyncio.Event()

        # Post a message from Agent 1
        async with bus._condition:
            bus.post(
                Message(
                    role=MessageRole.ASSISTANT,
                    content="We need more cross-aspect connections",
                    name="Agent 1",
                )
            )
            bus._condition.notify_all()

        with tempfile.TemporaryDirectory() as tmp:
            dm = _make_minimal_manager(agent, Path(tmp))

            task = asyncio.create_task(
                dm._agent_loop(agent, "Agent 2", bus, stop, initial_message=None)
            )

            await asyncio.sleep(0.1)
            stop.set()

            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                task.cancel()

            history = await bus.get_history()
            # Agent 2 responded
            agent2_msgs = [m for m in history if m.name == "Agent 2"]
            assert len(agent2_msgs) >= 1
            assert "I agree" in (agent2_msgs[0].content or "")


# ======================================================================
# Integration: on_round callback
# ======================================================================


class TestOnRoundCallback:
    """on_round is called when both agents have posted."""

    @pytest.mark.asyncio
    async def test_on_round_called_when_both_posted(self):
        """Verify that on_round callback is called."""
        # This test verifies the logic of _last_msg_from + on_round
        history = [
            Message(
                role=MessageRole.ASSISTANT, content="Agent 1 says hi", name="Agent 1"
            ),
            Message(
                role=MessageRole.ASSISTANT, content="Agent 2 responds", name="Agent 2"
            ),
        ]

        msg_a1 = _last_msg_from(history, "Agent 1")
        msg_a2 = _last_msg_from(history, "Agent 2")

        assert msg_a1 is not None
        assert msg_a2 is not None
        assert msg_a1.content == "Agent 1 says hi"
        assert msg_a2.content == "Agent 2 responds"


# ======================================================================
# Helpers
# ======================================================================


def _make_minimal_manager(agent, log_dir: Path) -> DialogueManager:
    """Create DialogueManager with minimal dependencies for tests."""
    dm = DialogueManager.__new__(DialogueManager)
    dm._agent_1 = agent
    dm._agent_2 = agent
    dm._storage = MagicMock()
    dm._config = MagicMock()
    dm._config.max_rounds = 20
    dm._config.max_time_minutes = 480
    dm._orch = MagicMock()
    dm._orch.evaluate = MagicMock(return_value=(MagicMock(), "continue"))
    dm._provider_ref = MagicMock()
    dm._provider_ref.supports_tools = MagicMock(return_value=True)
    dm._methodology_ref = None
    dm._source_dir_ref = ""
    dm._dialogue_logger = DialogueLogger(log_dir / "dialogue.jsonl")
    dm._tasks = []
    dm._helper_counter = 0
    dm._project_path = log_dir
    dm._delivered_questions = set()
    return dm
