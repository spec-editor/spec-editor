"""Blind Voting — multi-agent independent analysis without mutual influence.

Pattern from spec2ship: agents analyse the same task independently,
without seeing each other's responses. The orchestrator collects,
compares, and synthesises. This eliminates anchoring bias and sycophancy.

Usage:
    voter = BlindVoter(orchestrator, storage, methodology)
    round1 = await voter.run_blind_round([agent1, agent2], task="Design API")
    if round1.consensus_score < 0.5:
        round2 = await voter.run_blind_round([agent1, agent2],
            task=f"Converge on: {round1.comparison}")

Strategies:
    - CONSENSUS: continue rounds until agents agree (default)
    - MAJORITY: pick the most common answer
    - WEIGHTED: weighted by agent confidence
    - DEBATE: after blind round, open debate, then blind again
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.config import get_logger
from src.storage.adapter import StorageAdapter

logger = get_logger(__name__)


class VotingStrategy(str, Enum):
    """Blind voting resolution strategy."""

    CONSENSUS = "consensus"  # rounds continue until agents agree
    MAJORITY = "majority"  # pick the most common answer
    WEIGHTED = "weighted"  # weighted by agent confidence/role
    DEBATE = "debate"  # blind → open debate → blind again


@dataclass
class BlindRound:
    """A single blind voting round.

    Agents independently respond to the same task.
    Consensus score measures word-level agreement.
    """

    task: str
    round_id: str = field(default_factory=lambda: f"blind-{uuid.uuid4().hex[:8]}")
    strategy: VotingStrategy = VotingStrategy.CONSENSUS
    responses: dict[str, str] = field(default_factory=dict)
    verdict: str = ""
    consensus_score: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def record_response(self, agent_name: str, response: str) -> None:
        """Record an agent's blind response."""
        self.responses[agent_name] = response

    @property
    def comparison(self) -> str:
        """Human-readable comparison of agent responses."""
        if len(self.responses) < 2:
            return "Only one response."
        lines = ["## Agent Responses (blind)"]
        for name, text in self.responses.items():
            preview = text[:200].replace("\n", " ")
            lines.append(f"- **{name}**: {preview}")
        lines.append(f"\nConsensus: {self.consensus_score:.0%}")
        return "\n".join(lines)


class BlindVotingResult:
    """Aggregated result of multiple blind voting rounds."""

    def __init__(self) -> None:
        self.total_rounds: int = 0
        self.all_responses: dict[
            str, list[str]
        ] = {}  # agent_name → [responses per round]
        self.final_consensus: float = 0.0
        self.summary: str = ""
        self.rounds: list[BlindRound] = []


class BlindVoter:
    """Orchestrates blind voting rounds across multiple agents.

    Agents do NOT see each other's responses during a round.
    The orchestrator collects, analyses word overlap for consensus,
    and can request additional rounds if consensus is not reached.
    """

    # ------------------------------------------------------------------
    # Heuristic keywords for auto strategy selection
    # ------------------------------------------------------------------

    _DEBATE_KEYWORDS: tuple[str, ...] = (
        "architecture",
        "design decision",
        "trade-off",
        "tradeoff",
        "monolith",
        "microservice",
        "vs",
        "versus",
        "choose between",
        "pick one",
        "which is better",
    )
    _RISK_KEYWORDS: tuple[str, ...] = (
        "risk",
        "threat",
        "vulnerability",
        "security",
        "compliance",
        "audit",
        "attack",
        "breach",
    )
    _MAJORITY_KEYWORDS: tuple[str, ...] = (
        "estimate",
        "predict",
        "forecast",
        "how many",
        "how long",
        "how much",
        "effort",
        "story points",
        "probability",
        "likelihood",
    )

    def __init__(
        self,
        orchestrator: Any,
        storage: StorageAdapter,
        methodology: Any,
        strategy: VotingStrategy | None = None,
        adaptive: bool = False,
        consensus_threshold: float = 0.5,
        max_rounds: int = 3,
    ) -> None:
        self._orchestrator = orchestrator
        self._storage = storage
        self._methodology = methodology
        self._adaptive = adaptive
        self._consensus_threshold = consensus_threshold
        self._max_rounds = max_rounds
        self._rounds: list[BlindRound] = []

        # Resolve strategy: explicit > adaptive auto-select > default CONSENSUS
        if strategy is not None:
            self.strategy = strategy
        elif adaptive:
            self.strategy = VotingStrategy.CONSENSUS  # will be set per-round
        else:
            self.strategy = VotingStrategy.CONSENSUS

    # ------------------------------------------------------------------
    # Auto strategy selection
    # ------------------------------------------------------------------

    @classmethod
    def auto_select_strategy(cls, task: str) -> VotingStrategy:
        """Select the best voting strategy based on task content.

        Analyses keywords in the task description:
        - Architecture/design trade-offs → DEBATE (no single right answer)
        - Risk/security assessments → WEIGHTED (expert opinion matters)
        - Estimations/forecasts → MAJORITY (wisdom of the crowd)
        - Everything else → CONSENSUS (agents must agree)

        Returns:
            Recommended VotingStrategy
        """
        task_lower = task.lower()

        # DEBATE: architectural decisions, trade-offs, "vs" comparisons
        if any(kw in task_lower for kw in cls._DEBATE_KEYWORDS):
            return VotingStrategy.DEBATE

        # WEIGHTED: risk, security, compliance — expertise matters
        if any(kw in task_lower for kw in cls._RISK_KEYWORDS):
            return VotingStrategy.WEIGHTED

        # MAJORITY: estimations, predictions — wisdom of crowd
        if any(kw in task_lower for kw in cls._MAJORITY_KEYWORDS):
            return VotingStrategy.MAJORITY

        # Default: CONSENSUS — agents must converge
        return VotingStrategy.CONSENSUS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_blind_round(
        self,
        agents: list[Any],
        task: str,
        strategy: VotingStrategy | None = None,
    ) -> BlindRound:
        """Run a single blind voting round.

        All agents receive the SAME task and respond independently.
        Responses are collected and consensus is computed.

        Args:
            agents: List of agents (each must have an async `run(task)` method)
            task: The question/task for all agents
            strategy: Voting strategy for this round (defaults to instance strategy)

        Returns:
            BlindRound with all responses and consensus score
        """
        # Resolve strategy: explicit > adaptive auto-select > instance default
        if strategy is not None:
            resolved = strategy
        elif self._adaptive:
            resolved = self.auto_select_strategy(task)
        else:
            resolved = self.strategy

        br = BlindRound(
            task=task,
            strategy=resolved,
        )

        # Run all agents in parallel — they don't see each other
        async def _ask(agent: Any) -> tuple[str, str]:
            name = getattr(agent, "name", str(id(agent)))
            try:
                response = await agent.run(task)
                text = (
                    response.content if hasattr(response, "content") else str(response)
                )
                return name, (text or "").strip()
            except Exception as exc:
                logger.error("blind_agent_error", agent=name, error=str(exc))
                return name, f"[ERROR: {exc}]"

        results = await asyncio.gather(*[_ask(a) for a in agents])

        for name, text in results:
            br.record_response(name, text)

        # Compute consensus score
        br.consensus_score = self._compute_consensus(list(br.responses.values()))
        self._rounds.append(br)

        logger.info(
            "blind_round_complete",
            round_id=br.round_id,
            agents=len(br.responses),
            consensus=round(br.consensus_score, 2),
            task_preview=task[:80],
        )

        return br

    async def run_until_consensus(
        self,
        agents: list[Any],
        task: str,
    ) -> BlindVotingResult:
        """Run blind voting rounds until consensus is reached or max rounds.

        Between rounds, agents are shown the previous round's comparison
        so they can converge without seeing individual messages.
        """
        current_task = task

        for i in range(self._max_rounds):
            br = await self.run_blind_round(agents, current_task)

            if br.consensus_score >= self._consensus_threshold:
                break

            # Show comparison without revealing individual identities
            current_task = (
                f"{task}\n\n"
                f"PREVIOUS ROUND (blind): no consensus ({br.consensus_score:.0%}).\n"
                f"{br.comparison}\n\n"
                f"Try to converge. Focus on shared concepts."
            )

        return self.build_final_result()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_history(self) -> list[BlindRound]:
        """Get all completed rounds (latest first)."""
        return list(reversed(self._rounds))

    def build_final_result(self, summary: str = "") -> BlindVotingResult:
        """Build an aggregated result from all rounds."""
        result = BlindVotingResult()
        result.total_rounds = len(self._rounds)
        result.rounds = list(self._rounds)

        # Collect all responses per agent
        for br in self._rounds:
            for name, text in br.responses.items():
                if name not in result.all_responses:
                    result.all_responses[name] = []
                result.all_responses[name].append(text)

        # Average consensus across rounds
        if self._rounds:
            result.final_consensus = sum(r.consensus_score for r in self._rounds) / len(
                self._rounds
            )

        result.summary = summary
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_consensus(self, responses: list[str]) -> float:
        """Compute word-overlap consensus between all responses.

        Returns 0.0 (no agreement) to 1.0 (identical).
        """
        if len(responses) <= 1:
            return 1.0

        # Tokenize each response into normalized word sets
        import re

        word_sets: list[set[str]] = []
        for text in responses:
            # Split on whitespace, hyphens, and common punctuation
            raw_words = re.split(r"[\s\-_,;:.!?\"'()\[\]{}]+", text.lower())
            words = {w for w in raw_words if len(w) > 2}
            word_sets.append(words)

        # Average pairwise Jaccard similarity
        total_sim = 0.0
        pairs = 0
        for i in range(len(word_sets)):
            for j in range(i + 1, len(word_sets)):
                a, b = word_sets[i], word_sets[j]
                if not a and not b:
                    sim = 1.0
                elif not a or not b:
                    sim = 0.0
                else:
                    sim = len(a & b) / len(a | b)
                total_sim += sim
                pairs += 1

        return total_sim / max(pairs, 1)
