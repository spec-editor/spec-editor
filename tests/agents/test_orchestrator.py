"""DialogueOrchestrator tests — round evaluation, completion, stagnation."""

import asyncio

import pytest

from src.agents.orchestrator import OrchestratorDecision
from src.providers.base import LLMResponse, Message, MessageRole, ToolCall


class FakeOrchProvider:
    """Mock orchestrator provider — returns predefined decisions."""

    def __init__(self, decisions: list[str] | None = None):
        self._decisions = decisions or ["continue"]
        self._idx = 0

    async def complete(self, messages, tools=None, **kwargs):
        decision = self._decisions[min(self._idx, len(self._decisions) - 1)]
        self._idx += 1
        return LLMResponse(content=decision)

    def supports_tools(self) -> bool:
        return True


def _msg(name: str, content: str, tool_calls: list | None = None) -> Message:
    return Message(
        role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls, name=name
    )


def _history_with_declared(agent: str) -> list[Message]:
    """History where agent just called report_complete."""
    return [
        _msg(
            agent,
            "done",
            tool_calls=[ToolCall(id="1", name="report_complete", arguments={})],
        ),
        Message(
            role=MessageRole.TOOL,
            content='{"status":"ok","declaration":"complete"}',
            tool_call_id="1",
        ),
    ]


def _history_with_rejected(agent: str) -> list[Message]:
    """History where report_complete was rejected."""
    return [
        _msg(
            agent,
            "done",
            tool_calls=[ToolCall(id="1", name="report_complete", arguments={})],
        ),
        Message(
            role=MessageRole.TOOL,
            content='{"status":"error","declaration":"rejected"}',
            tool_call_id="1",
        ),
    ]


class TestDialogueOrchestrator:
    """DialogueOrchestrator: decisions on continuation/completion."""

    def test_continue_when_no_declarations(self):
        """If nobody has declared — continue."""
        from src.agents.dialogue import DialogueOrchestrator

        history = [_msg("Agent 1", "lets work"), _msg("Agent 2", "agreed")]
        orch = DialogueOrchestrator(
            provider=FakeOrchProvider(["continue"]),
            storage=None,
            methodology=None,
        )
        decision, _ = orch.evaluate(1, 20, history)
        assert decision == OrchestratorDecision.CONTINUE

    def test_complete_when_both_declared_and_accepted(self):
        """Both declared and accepted → complete."""
        from src.agents.dialogue import DialogueOrchestrator

        history = _history_with_declared("Agent 1") + _history_with_declared("Agent 2")
        orch = DialogueOrchestrator(
            provider=FakeOrchProvider(),
            storage=None,
            methodology=None,
        )
        decision, _ = orch.evaluate(1, 20, history)
        assert decision == OrchestratorDecision.COMPLETE

    def test_not_complete_when_rejected(self):
        """If report_complete rejected → not counted."""
        from src.agents.dialogue import DialogueOrchestrator

        history = _history_with_rejected("Agent 1") + _history_with_rejected("Agent 2")
        orch = DialogueOrchestrator(
            provider=FakeOrchProvider(["continue"]),
            storage=None,
            methodology=None,
        )
        decision, _ = orch.evaluate(1, 20, history)
        assert decision == OrchestratorDecision.CONTINUE

    def test_llm_complete_overridden_without_declared(self):
        """LLM said complete, but agents not declared → continue."""
        from src.agents.dialogue import DialogueOrchestrator

        history = [_msg("Agent 1", "looks done"), _msg("Agent 2", "yes finished")]
        orch = DialogueOrchestrator(
            provider=FakeOrchProvider(["complete"]),  # LLM wants complete
            storage=None,
            methodology=None,
        )
        decision, _ = orch.evaluate(1, 20, history)
        assert decision == OrchestratorDecision.CONTINUE  # overridden!

    def test_warning_on_round_limit(self):
        """When round limit is reached — warning."""
        from src.agents.dialogue import DialogueOrchestrator

        history = [_msg("A1", "work"), _msg("A2", "work")]
        orch = DialogueOrchestrator(
            provider=FakeOrchProvider(),
            storage=None,
            methodology=None,
        )
        decision, _ = orch.evaluate(20, 20, history)
        assert decision == OrchestratorDecision.WARNING
