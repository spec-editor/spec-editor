"""Dialogue orchestrator — round evaluation, completion detection."""

import json

from src.agents.orchestrator import OrchestratorDecision
from src.providers.base import Message, MessageRole
from src.tracing import implements


@implements("MOD-001-C3")
class DialogueOrchestrator:
    """Round evaluation, completion detection, declared check."""

    def __init__(self, provider, storage, methodology) -> None:
        self._provider = provider
        self._storage = storage
        self._methodology = methodology

    def evaluate(
        self, round_num: int, max_rounds: int, history: list[Message]
    ) -> tuple[OrchestratorDecision, str]:
        """Evaluate a round."""
        if round_num >= max_rounds:
            a1_declared = self._has_declared(history, "Agent 1")
            a2_declared = self._has_declared(history, "Agent 2")
            if a1_declared and a2_declared:
                return OrchestratorDecision.COMPLETE, (
                    f"Round limit reached ({max_rounds}) — both agents declared, finishing"
                )
            return OrchestratorDecision.WARNING, (
                f"Round limit reached ({max_rounds}). "
                f"Agents: Agent1={'declared' if a1_declared else 'NOT declared'}, "
                f"Agent2={'declared' if a2_declared else 'NOT declared'}. "
                f"Continue until both declare completion."
            )

        a1_declared = self._has_declared(history, "Agent 1")
        a2_declared = self._has_declared(history, "Agent 2")

        if a1_declared and a2_declared:
            return OrchestratorDecision.COMPLETE, "Both agents confirmed completion"

        # LLM evaluation (in tests — mock)
        return OrchestratorDecision.CONTINUE, "continue"

    @staticmethod
    def _has_declared(history: list[Message], name: str) -> bool:
        """report_complete in tool_calls OR key phrases in text."""
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            if msg.name != name:
                continue

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.name == "report_complete":
                        if (
                            i + 1 < len(history)
                            and history[i + 1].role == MessageRole.TOOL
                        ):
                            try:
                                result = json.loads(history[i + 1].content or "{}")
                                if result.get("declaration") == "rejected":
                                    return False
                            except Exception:
                                pass
                        return True
                return False

            text = (msg.content or "").lower()
            if any(
                p in text
                for p in (
                    "propose completion",
                    "complete work",
                    "ready to complete",
                )
            ):
                return True

        return False
