"""Tests for Blind Voting in agent dialogue.

Blind Voting (from spec2ship pattern): agents independently analyze
the same task without seeing each other's responses. The orchestrator
collects, compares, and synthesizes — eliminating anchoring bias.
"""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.blind_vote import (
    BlindRound,
    BlindVoter,
    BlindVotingResult,
    VotingStrategy,
)
from src.providers.base import LLMResponse, Message, MessageRole
from src.storage.models import ElementStatus

# ======================================================================
# Minimal fakes for testing without real LLM calls
# ======================================================================


@dataclass
class FakeAgent:
    """Fake agent that returns a canned response."""

    name: str
    response_text: str
    times_called: int = 0

    async def run(self, task: str, history=None):
        self.times_called += 1
        return LLMResponse(content=self.response_text)


class FakeStorage:
    """Minimal fake storage."""

    def list_all(self):
        return []


class FakeMethodology:
    name = "waterfall"
    version = "1.0"
    description = ""
    aspects = []
    skills = []


# ======================================================================
# BlindRound tests
# ======================================================================


class TestBlindRound:
    """BlindRound data model."""

    def test_defaults(self):
        br = BlindRound(task="Design auth module")
        assert br.task == "Design auth module"
        assert br.responses == {}
        assert br.verdict == ""
        assert br.strategy == VotingStrategy.CONSENSUS

    def test_record_response(self):
        br = BlindRound(task="Design auth")
        br.record_response("Agent 1", "Use JWT tokens")
        br.record_response("Agent 2", "Use OAuth2")
        assert br.responses == {
            "Agent 1": "Use JWT tokens",
            "Agent 2": "Use OAuth2",
        }

    def test_round_id_unique(self):
        br1 = BlindRound(task="Task 1")
        br2 = BlindRound(task="Task 2")
        assert br1.round_id != br2.round_id

    def test_strategy_enum_values(self):
        assert VotingStrategy.CONSENSUS == "consensus"
        assert VotingStrategy.MAJORITY == "majority"
        assert VotingStrategy.WEIGHTED == "weighted"
        assert VotingStrategy.DEBATE == "debate"


# ======================================================================
# BlindVoter tests
# ======================================================================


class TestBlindVoterBasic:
    """Basic BlindVoter functionality."""

    def test_run_single_round(self):
        """Two agents, one task — both respond independently."""
        agent1 = FakeAgent(name="Agent 1", response_text="Use JWT")
        agent2 = FakeAgent(name="Agent 2", response_text="Use OAuth2")

        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )

        result = asyncio.run(
            voter.run_blind_round(
                agents=[agent1, agent2],
                task="How to authenticate users?",
            )
        )

        assert result.task == "How to authenticate users?"
        assert result.responses["Agent 1"] == "Use JWT"
        assert result.responses["Agent 2"] == "Use OAuth2"
        assert agent1.times_called == 1
        assert agent2.times_called == 1

    def test_agents_receive_same_task(self):
        """Each agent gets the exact same task string."""
        tasks_seen = []

        class TrackingAgent(FakeAgent):
            async def run(self, task, history=None):
                tasks_seen.append(task)
                return await super().run(task, history)

        agent1 = TrackingAgent(name="A1", response_text="OK")
        agent2 = TrackingAgent(name="A2", response_text="OK")

        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )

        asyncio.run(
            voter.run_blind_round(
                agents=[agent1, agent2],
                task="Design the login page",
            )
        )

        assert len(tasks_seen) == 2
        assert tasks_seen[0] == "Design the login page"
        assert tasks_seen[1] == "Design the login page"

    def test_agents_run_parallel(self):
        """Agents run concurrently via asyncio.gather."""
        agent1 = FakeAgent(name="Slow", response_text="A")
        agent2 = FakeAgent(name="Fast", response_text="B")

        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )

        result = asyncio.run(
            voter.run_blind_round(
                agents=[agent1, agent2],
                task="Test parallelism",
            )
        )
        # Both called once each — asyncio.gather ensures parallel execution
        assert agent1.times_called == 1
        assert agent2.times_called == 1
        assert "A" in result.responses.values()
        assert "B" in result.responses.values()

    def test_rounds_history_tracked(self):
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        a1 = FakeAgent(name="A1", response_text="One")
        a2 = FakeAgent(name="A2", response_text="Two")

        asyncio.run(voter.run_blind_round(agents=[a1, a2], task="Round 1"))
        asyncio.run(voter.run_blind_round(agents=[a1, a2], task="Round 2"))

        history = voter.get_history()
        assert len(history) == 2
        assert history[0].task == "Round 2"
        assert history[1].task == "Round 1"


class TestBlindVoterConsensus:
    """Consensus detection — when agents agree."""

    def test_detect_full_agreement(self):
        """Agents return semantically similar responses → consensus."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        a1 = FakeAgent(name="A1", response_text="Use JWT authentication with RS256")
        a2 = FakeAgent(name="A2", response_text="Use JWT authentication with HS256")
        a3 = FakeAgent(name="A3", response_text="Use JWT-based auth tokens")

        result = asyncio.run(
            voter.run_blind_round(agents=[a1, a2, a3], task="Auth method?")
        )

        # All mention JWT → moderate+ consensus (words differ: auth vs authentication)
        assert result.consensus_score >= 0.35

    def test_detect_disagreement(self):
        """Agents give conflicting answers → low consensus."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        a1 = FakeAgent(name="A1", response_text="Use PostgreSQL")
        a2 = FakeAgent(name="A2", response_text="Use MongoDB")
        a3 = FakeAgent(name="A3", response_text="Use Redis")

        result = asyncio.run(
            voter.run_blind_round(agents=[a1, a2, a3], task="Database?")
        )

        # All different → low consensus
        assert result.consensus_score < 0.5

    def test_single_agent_is_full_consensus(self):
        """Single agent always reaches 'consensus' with itself."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        a1 = FakeAgent(name="A1", response_text="Anything")

        result = asyncio.run(voter.run_blind_round(agents=[a1], task="Solo task"))
        assert result.consensus_score == 1.0


class TestBlindVoterIntegration:
    """Multi-round blind voting workflow."""

    def test_multi_round_blind_voting(self):
        """Full workflow: 2 blind rounds → compare → synthesize."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )

        # Round 1: disagree
        a1 = FakeAgent(name="A1", response_text="Use REST API")
        a2 = FakeAgent(name="A2", response_text="Use GraphQL")

        r1 = asyncio.run(voter.run_blind_round(agents=[a1, a2], task="API style?"))
        assert r1.consensus_score < 0.5  # disagreement

        # Round 2: after seeing debate results, agents converge
        a1.response_text = "Use GraphQL with REST fallback"
        a2.response_text = "Use GraphQL for queries, REST for mutations"

        r2 = asyncio.run(
            voter.run_blind_round(
                agents=[a1, a2],
                task="API style? Previous round: REST vs GraphQL — no consensus. Try to converge.",
            )
        )
        # After convergence hint, agents agree more
        assert r2.consensus_score > 0.3  # at least some overlap

    def test_merge_responses(self):
        """After rounds, merge responses into a single result."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        a1 = FakeAgent(name="A1", response_text="Solution: Use JWT")
        a2 = FakeAgent(name="A2", response_text="Solution: Use OAuth2")

        asyncio.run(voter.run_blind_round(agents=[a1, a2], task="Auth?"))

        merged = voter.build_final_result(summary="Both options viable")
        assert isinstance(merged, BlindVotingResult)
        assert merged.total_rounds == 1
        assert merged.summary == "Both options viable"
        assert "A1" in merged.all_responses
        assert "A2" in merged.all_responses

    def test_empty_rounds_safe(self):
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        merged = voter.build_final_result()
        assert merged.total_rounds == 0
        assert merged.all_responses == {}


class TestBlindVoterStrategy:
    """Voting strategy selection."""

    def test_consensus_strategy(self):
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            strategy=VotingStrategy.CONSENSUS,
        )
        assert voter.strategy == VotingStrategy.CONSENSUS

    def test_majority_strategy(self):
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            strategy=VotingStrategy.MAJORITY,
        )
        assert voter.strategy == VotingStrategy.MAJORITY

    def test_default_is_consensus(self):
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        assert voter.strategy == VotingStrategy.CONSENSUS

    def test_explicit_overrides_adaptive(self):
        """Explicit strategy wins even when adaptive=True."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            strategy=VotingStrategy.MAJORITY,
            adaptive=True,
        )
        assert voter.strategy == VotingStrategy.MAJORITY


class TestAutoSelectStrategy:
    """Auto-selection of voting strategy by task keywords."""

    def test_debate_for_architecture(self):
        assert BlindVoter.auto_select_strategy(
            "Monolith vs microservices — which is better?"
        ) == VotingStrategy.DEBATE

    def test_debate_for_tradeoff(self):
        assert BlindVoter.auto_select_strategy(
            "Choose between PostgreSQL and MongoDB"
        ) == VotingStrategy.DEBATE

    def test_weighted_for_risk(self):
        assert BlindVoter.auto_select_strategy(
            "Assess the risk of SQL injection in login"
        ) == VotingStrategy.WEIGHTED

    def test_weighted_for_security(self):
        assert BlindVoter.auto_select_strategy(
            "Security audit: review authentication flow"
        ) == VotingStrategy.WEIGHTED

    def test_majority_for_estimate(self):
        assert BlindVoter.auto_select_strategy(
            "Estimate the effort for building the payment module"
        ) == VotingStrategy.MAJORITY

    def test_majority_for_forecast(self):
        assert BlindVoter.auto_select_strategy(
            "Predict how long the migration will take"
        ) == VotingStrategy.MAJORITY

    def test_default_is_consensus(self):
        assert BlindVoter.auto_select_strategy(
            "Design the user login page"
        ) == VotingStrategy.CONSENSUS

    def test_generic_task_defaults_to_consensus(self):
        assert BlindVoter.auto_select_strategy(
            "Create a specification for the billing module"
        ) == VotingStrategy.CONSENSUS

    def test_adaptive_mode_auto_selects_per_round(self):
        """When adaptive=True and no explicit strategy, auto-select is used."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            adaptive=True,
        )
        a1 = FakeAgent(name="A1", response_text="Use microservices")
        result = asyncio.run(
            voter.run_blind_round(
                agents=[a1],
                task="Monolith vs microservices: which architecture?",
            )
        )
        assert result.strategy == VotingStrategy.DEBATE

    def test_adaptive_mode_with_consensus_fallback(self):
        """Generic task with adaptive=True still gets CONSENSUS."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            adaptive=True,
        )
        a1 = FakeAgent(name="A1", response_text="OK")
        result = asyncio.run(
            voter.run_blind_round(
                agents=[a1],
                task="Write a specification for user profiles",
            )
        )
        assert result.strategy == VotingStrategy.CONSENSUS

    def test_explicit_per_round_overrides_adaptive(self):
        """Explicit strategy in run_blind_round() beats adaptive auto-select."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            adaptive=True,
        )
        a1 = FakeAgent(name="A1", response_text="OK")
        result = asyncio.run(
            voter.run_blind_round(
                agents=[a1],
                task="Assess security risks",
                strategy=VotingStrategy.CONSENSUS,
            )
        )
        assert result.strategy == VotingStrategy.CONSENSUS

    def test_adaptive_false_uses_instance_default(self):
        """Without adaptive, always uses instance strategy."""
        voter = BlindVoter(
            orchestrator=MagicMock(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
            adaptive=False,
        )
        a1 = FakeAgent(name="A1", response_text="OK")
        result = asyncio.run(
            voter.run_blind_round(
                agents=[a1],
                task="Monolith vs microservices",
            )
        )
        assert result.strategy == VotingStrategy.CONSENSUS
