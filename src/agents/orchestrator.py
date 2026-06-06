"""Orchestrator agent (Agent 3)."""

from enum import Enum

from src.agents.base import BaseAgent
from src.agents.prompts import get_orchestrator_prompt
from src.agents.tools import build_read_only_handlers, get_tool_definitions
from src.config.methodology import Methodology, format_methodology
from src.providers.base import LLMProvider, LLMResponse, Message, MessageRole
from src.storage.adapter import StorageAdapter
from src.tracing import implements


@implements("MOD-001-C3")
class OrchestratorDecision(str, Enum):
    CONTINUE = "continue"
    WARNING = "warning"
    CONFLICT = "conflict"
    TIMEOUT = "timeout"
    COMPLETE = "complete"


class OrchestratorAgent(BaseAgent):
    """Orchestrator agent. Read-only access."""

    def __init__(
        self,
        provider: LLMProvider,
        storage: StorageAdapter,
        methodology: Methodology,
        source_dir: str | None = None,
    ) -> None:
        tools = get_tool_definitions(writable=False)
        tool_handlers = build_read_only_handlers(storage, methodology, source_dir)
        system_prompt = get_orchestrator_prompt().format(
            methodology_description=format_methodology(methodology),
        )
        super().__init__(
            name="orchestrator",
            provider=provider,
            system_prompt=system_prompt,
            tools=tools,
            tool_handlers=tool_handlers,
        )

    async def evaluate_round(
        self,
        round_num: int,
        max_rounds: int,
        dialogue_history: list[Message],
        agent1_declared: bool = False,
        agent2_declared: bool = False,
    ) -> tuple[OrchestratorDecision, str]:
        # Limit reached — warning, but not a stop
        if round_num >= max_rounds:
            return OrchestratorDecision.WARNING, (
                f"Round limit reached ({max_rounds}). "
                f"If quality is sufficient — finish. Otherwise continue."
            )
        if agent1_declared and agent2_declared:
            return (
                OrchestratorDecision.COMPLETE,
                "Both agents confirmed requirements are complete.",
            )

        evaluation_prompt = _build_evaluation_prompt(
            round_num=round_num,
            dialogue_history=dialogue_history,
            agent1_declared=agent1_declared,
            agent2_declared=agent2_declared,
        )
        response = await self.run(evaluation_prompt)
        return _parse_decision(response)


def _build_evaluation_prompt(
    round_num: int,
    dialogue_history: list[Message],
    agent1_declared: bool,
    agent2_declared: bool,
) -> str:
    lines = [f"Evaluate round {round_num} of dialogue between Agent 1 and Agent 2.", ""]
    if agent1_declared:
        lines.append("Agent 1 ALREADY declared completion (report_complete).")
    if agent2_declared:
        lines.append("Agent 2 ALREADY declared completion (report_complete).")
    lines.append("")
    lines.append("Dialogue history (last messages):")
    lines.append("")
    # Show last messages within a ~3000 char budget
    budget = 3000
    recent = dialogue_history[-10:] if len(dialogue_history) > 10 else dialogue_history
    for msg in reversed(recent):
        agent_name = msg.name or msg.role.value
        content = msg.content or "(tool calls)"
        entry = f"[{agent_name}] {content}"
        if budget - len(entry) < 0:
            break
        lines.insert(6, entry)  # insert after header, in reverse order
        budget -= len(entry)
    lines.append("")
    lines.append(
        "Answer with ONE word: continue, warning, conflict, or complete. "
        "Then briefly explain the reason."
    )
    lines.append("")
    lines.append(
        "IMPORTANT: conflict — ONLY when agents EXPLICITLY disagree "
        '(one says "delete", the other "keep"). '
        "Quality problems are warnings."
    )
    return "\n".join(lines)


def _parse_decision(response: LLMResponse) -> tuple[OrchestratorDecision, str]:
    import re

    text = (response.content or "").strip().lower()
    for decision in OrchestratorDecision:
        if text.startswith(decision.value):
            return decision, response.content or ""
    last_line = text.split("\n")[-1].strip()
    for decision in OrchestratorDecision:
        if last_line == decision.value:
            return decision, response.content or ""
    for decision in OrchestratorDecision:
        if re.search(r"\b" + re.escape(decision.value) + r"\b", text):
            return decision, response.content or ""
    return OrchestratorDecision.CONTINUE, response.content or ""
