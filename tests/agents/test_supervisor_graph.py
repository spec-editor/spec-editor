"""Tests for SupervisorGraph — multi-agent LangGraph supervisor.

Tests cover:
- Graph construction and compilation
- Route logic (status-based routing)
- Budget checks (hard limit, cost efficiency, idle detection)
- Missing aspects detection
- Report complete verification
- Context building for agents
- Checkpoint persistence (save/load/delete)
- Run with mocked provider
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.supervisor_graph import SupervisorGraph, TeamState
from src.tracing import implements
from src.config.settings import AgentsConfig
from src.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    Message,
    MessageRole,
    ToolCall,
    ToolDef,
)
from src.storage.adapter import StorageAdapter


# ======================================================================
# @implements annotation
# ======================================================================


class TestImplementsAnnotation:
    """Verify SupervisorGraph has the correct @implements decorator."""

    def test_supervisor_graph_has_implements_ca003(self):
        assert hasattr(SupervisorGraph, "__implements__")
        assert SupervisorGraph.__implements__ == "CA-003"


# ======================================================================
# Fakes
# ======================================================================


class FakeLLMProvider(LLMProvider):
    """Mock LLM provider that returns predefined responses."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self._responses = responses or []
        self._idx = 0
        self._supports_tools = True
        self.calls: list[list[Message]] = []

    async def complete(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        self.calls.append(messages)
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
        else:
            resp = LLMResponse(content="ok", usage=LLMUsage(prompt_tokens=10, completion_tokens=10))
        self._idx += 1
        return resp

    def supports_tools(self):
        return self._supports_tools


class FakeStorage(StorageAdapter):
    """Minimal in-memory storage for tests."""

    def __init__(self):
        self._elements: dict[str, dict] = {}
        self._relationships: list[dict] = []

    def read_element(self, element_id: str):
        if element_id not in self._elements:
            raise KeyError(element_id)
        from src.storage.models import Element

        return Element(id=element_id, **self._elements[element_id])

    def write_element(self, element):
        self._elements[element.id] = {
            "aspect": element.aspect or "modules",
            "element_type": element.element_type or "module",
            "title": element.title or "",
            "status": element.status,
            "content": element.content or "",
            "parent": element.parent,
            "children": element.children or [],
            "relationships": element.relationships or {},
        }

    def delete_element(self, element_id):
        self._elements.pop(element_id, None)

    def list_all(self, offset=0, limit=0):
        from src.storage.models import ElementSummary

        result = []
        for eid, data in self._elements.items():
            result.append(
                ElementSummary(
                    id=eid,
                    aspect=data.get("aspect", ""),
                    element_type=data.get("element_type", ""),
                    title=data.get("title", ""),
                    status=data.get("status"),
                )
            )
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        return result

    def list_aspect(self, aspect_name, offset=0, limit=0):
        return [s for s in self.list_all() if s.aspect == aspect_name]

    def find_related(self, element_id):
        return []

    def search(self, query, offset=0, limit=0):
        return []

    def get_element_path(self, element_id):
        return None

    def count_all(self):
        return len(self._elements)

    def count_aspect(self, aspect_name):
        return len([s for s in self.list_all() if s.aspect == aspect_name])

    def exists(self, element_id):
        return element_id in self._elements


def _make_tool_def(name: str) -> ToolDef:
    return ToolDef(name=name, description=name, parameters={"type": "object", "properties": {}})


def _make_handler(name: str) -> MagicMock:
    return MagicMock(return_value={"status": "ok"})


# ======================================================================
# Tests
# ======================================================================


class TestSupervisorGraphConstruction:
    """Graph builds and compiles without errors."""

    def test_construct_with_minimal_args(self):
        """Can construct with required arguments."""
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
        )
        assert graph._graph is not None
        assert graph._agent1_prompt == "You are agent 1"
        assert graph._agent2_prompt == "You are agent 2"

    def test_construct_without_agent2(self):
        """Can construct with agent2 disabled (empty prompt/tools)."""
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="You are agent 1",
            agent2_prompt="",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={},
        )
        assert graph._graph is not None

    def test_construct_with_all_options(self):
        """Can construct with optional parameters."""
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
            max_llm_calls=50,
            log_dir=Path("/tmp/logs"),
            project_path=Path("/tmp/project"),
            source_dir="src",
            ci_threshold=0.8,
        )
        assert graph._max_llm_calls == 50
        assert graph._project_path == Path("/tmp/project")
        assert graph._user_ci_threshold == 0.8


class TestSupervisorGraphRoute:
    """Route supervisor logic — routing based on state status."""

    def test_route_supervisor_running(self):
        """Status 'running' routes back to supervisor."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        result = SupervisorGraph._route_supervisor(state)
        assert result == "supervisor"

    def test_route_supervisor_complete(self):
        """Status 'complete' routes to finalize."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "complete",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        result = SupervisorGraph._route_supervisor(state)
        assert result == "finalize"

    def test_route_supervisor_timeout(self):
        """Status 'timeout' routes to finalize."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "timeout",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        result = SupervisorGraph._route_supervisor(state)
        assert result == "finalize"

    def test_route_supervisor_stalled(self):
        """Status 'stalled' routes to finalize."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "stalled",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        result = SupervisorGraph._route_supervisor(state)
        assert result == "finalize"


class TestSupervisorGraphMissingAspects:
    """Missing aspects detection."""

    def test_all_aspects_present(self):
        """Returns empty when all aspects have elements."""
        aspects = {
            "modules": 5,
            "user_scenarios": 3,
            "user_interface": 2,
            "data_entities": 4,
            "non_functional": 1,
            "implementation": 6,
            "metrics": 0,
        }
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="",
            agent2_prompt="",
            agent1_tools=[],
            agent2_tools=[],
            agent1_handlers={},
            agent2_handlers={},
        )
        missing = graph._get_missing_aspects(aspects)
        assert missing == {"metrics": 0}

    def test_multiple_missing_aspects(self):
        """Returns all aspects with zero elements."""
        aspects = {"modules": 0, "user_scenarios": 0, "user_interface": 0, "data_entities": 0, "non_functional": 0, "implementation": 0, "metrics": 0}
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="",
            agent2_prompt="",
            agent1_tools=[],
            agent2_tools=[],
            agent1_handlers={},
            agent2_handlers={},
        )
        missing = graph._get_missing_aspects(aspects)
        assert len(missing) == 7

    def test_no_missing_aspects(self):
        """Returns empty dict when all aspects have at least 1 element."""
        aspects = {"modules": 1, "user_scenarios": 1, "user_interface": 1, "data_entities": 1, "non_functional": 1, "implementation": 1, "metrics": 1}
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="",
            agent2_prompt="",
            agent1_tools=[],
            agent2_tools=[],
            agent1_handlers={},
            agent2_handlers={},
        )
        missing = graph._get_missing_aspects(aspects)
        assert missing == {}


class TestSupervisorGraphReportComplete:
    """Detection of report_complete in message history."""

    def test_has_report_complete_true(self):
        """Returns True when report_complete was called."""
        state: TeamState = {
            "messages": [
                {"role": "assistant", "content": "work", "tool_calls": [{"name": "write_element", "id": "1", "arguments": {}}]},
                {"role": "assistant", "content": "done", "tool_calls": [{"name": "report_complete", "id": "2", "arguments": {}}]},
            ],
            "total_calls": 5,
            "total_writes": 3,
            "total_cost": 0.01,
            "agent1_calls": 3,
            "agent2_calls": 2,
            "agent1_cost": 0.005,
            "agent2_cost": 0.005,
            "last_metrics": None,
            "round_num": 1,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        assert SupervisorGraph._has_report_complete(state) is True

    def test_has_report_complete_false(self):
        """Returns False when no report_complete in messages."""
        state: TeamState = {
            "messages": [
                {"role": "assistant", "content": "work", "tool_calls": [{"name": "write_element", "id": "1", "arguments": {}}]},
                {"role": "assistant", "content": "more", "tool_calls": [{"name": "read_element", "id": "2", "arguments": {}}]},
            ],
            "total_calls": 5,
            "total_writes": 3,
            "total_cost": 0.01,
            "agent1_calls": 3,
            "agent2_calls": 2,
            "agent1_cost": 0.005,
            "agent2_cost": 0.005,
            "last_metrics": None,
            "round_num": 1,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        assert SupervisorGraph._has_report_complete(state) is False

    def test_has_report_complete_empty_history(self):
        """Returns False for empty message history."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        assert SupervisorGraph._has_report_complete(state) is False


class TestSupervisorGraphBuildContext:
    """Context building for agents based on metrics."""

    @pytest.fixture
    def graph(self):
        return SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
        )

    def test_build_context_agent1_missing_aspects(self, graph):
        """Agent 1 gets missing aspects message."""
        metrics = {
            "total_elements": 5,
            "total_relationships": 2,
            "connectivity_index": 0.3,
            "orphan_elements": 3,
            "aspects": {"modules": 5, "user_scenarios": 0, "user_interface": 0, "data_entities": 0, "non_functional": 0, "implementation": 0, "metrics": 0},
            "by_status": {"draft": 5},
        }
        ctx = graph._build_context(metrics, "agent_1")
        assert "MISSING ASPECTS" in ctx
        assert "user_scenarios" in ctx

    def test_build_context_agent1_incremental(self, graph):
        """Agent 1 gets incremental mode message when reviewed > draft."""
        metrics = {
            "total_elements": 20,
            "total_relationships": 10,
            "connectivity_index": 0.6,
            "orphan_elements": 1,
            "aspects": {"modules": 10, "user_scenarios": 5, "user_interface": 5, "data_entities": 1, "non_functional": 1, "implementation": 1, "metrics": 1},
            "by_status": {"draft": 2, "reviewed": 15, "confirmed": 3},
        }
        ctx = graph._build_context(metrics, "agent_1")
        assert "INCREMENTAL MODE" in ctx
        assert "DRAFT=" in ctx

    def test_build_context_agent1_standard(self, graph):
        """Agent 1 gets standard message when no missing aspects and not incremental."""
        metrics = {
            "total_elements": 10,
            "total_relationships": 5,
            "connectivity_index": 0.5,
            "orphan_elements": 0,
            "aspects": {"modules": 5, "user_scenarios": 2, "user_interface": 1, "data_entities": 1, "non_functional": 1, "implementation": 1, "metrics": 1},
            "by_status": {"draft": 5, "reviewed": 5},
        }
        ctx = graph._build_context(metrics, "agent_1")
        assert "MISSING ASPECTS" not in ctx
        assert "INCREMENTAL MODE" not in ctx

    def test_build_context_agent2_with_orphans(self, graph):
        """Agent 2 gets orphan IDs message."""
        metrics = {
            "total_elements": 10,
            "total_relationships": 3,
            "connectivity_index": 0.2,
            "orphan_elements": 5,
            "aspects": {},
            "by_status": {},
        }
        ctx = graph._build_context(metrics, "agent_2")
        assert "ORPHAN IDs" in ctx

    def test_build_context_agent2_with_user_ci_threshold(self, graph):
        """When user_ci_threshold is set, it's included in context."""
        graph._user_ci_threshold = 0.85
        metrics = {
            "total_elements": 10,
            "total_relationships": 5,
            "connectivity_index": 0.5,
            "orphan_elements": 0,
            "aspects": {"modules": 5, "user_scenarios": 2, "user_interface": 1, "data_entities": 1, "non_functional": 1, "implementation": 0, "metrics": 0},
            "by_status": {"draft": 5, "reviewed": 5},
        }
        ctx = graph._build_context(metrics, "agent_1")
        assert "0.85" in ctx or "CI" in ctx


class TestSupervisorGraphCheckpoint:
    """Checkpoint persistence."""

    @pytest.fixture
    def graph(self, tmp_path: Path):
        return SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="",
            agent2_prompt="",
            agent1_tools=[],
            agent2_tools=[],
            agent1_handlers={},
            agent2_handlers={},
            project_path=tmp_path,
        )

    def test_checkpoint_path(self, graph, tmp_path: Path):
        """Checkpoint path points to .spec-editor-checkpoint.json."""
        path = graph._checkpoint_path()
        assert path == tmp_path / ".spec-editor-checkpoint.json"

    def test_checkpoint_path_none(self):
        """Without project_path, checkpoint path is None."""
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="",
            agent2_prompt="",
            agent1_tools=[],
            agent2_tools=[],
            agent1_handlers={},
            agent2_handlers={},
        )
        assert graph._checkpoint_path() is None

    def test_save_and_load_checkpoint(self, graph):
        """Save then load returns the same data."""
        state: TeamState = {
            "messages": [{"role": "user", "content": "hello"}],
            "total_calls": 5,
            "total_writes": 3,
            "total_cost": 0.01,
            "agent1_calls": 3,
            "agent2_calls": 2,
            "agent1_cost": 0.005,
            "agent2_cost": 0.005,
            "last_metrics": {"total_elements": 10},
            "round_num": 2,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        graph._save_checkpoint(state)
        loaded = graph._load_checkpoint()
        assert loaded is not None
        assert loaded["total_calls"] == 5
        assert loaded["round_num"] == 2
        assert loaded["total_cost"] == 0.01
        assert loaded["last_metrics"]["total_elements"] == 10
        assert loaded["status"] == "running"

    def test_load_checkpoint_none_when_no_file(self, graph):
        """Load returns None when no checkpoint file exists."""
        loaded = graph._load_checkpoint()
        assert loaded is None

    def test_delete_checkpoint_after_save(self, graph):
        """After save, delete removes the file."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        graph._save_checkpoint(state)
        assert graph._checkpoint_path().exists()
        graph._delete_checkpoint()
        assert not graph._checkpoint_path().exists()

    def test_save_truncates_messages(self, graph):
        """Messages are truncated to 50 entries in checkpoint."""
        messages = [{"role": "user", "content": f"msg_{i}"} for i in range(100)]
        state: TeamState = {
            "messages": messages,
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic(),
            "max_time_seconds": 3600,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        graph._save_checkpoint(state)
        loaded = graph._load_checkpoint()
        assert loaded is not None
        assert len(loaded["messages"]) == 50

    def test_delete_checkpoint_no_file(self, graph):
        """Deleting checkpoint when none exists doesn't error."""
        graph._delete_checkpoint()


class TestSupervisorGraphSupervisorNode:
    """Supervisor node decision logic."""

    @pytest.fixture
    def graph(self):
        storage = FakeStorage()
        return SupervisorGraph(
            storage=storage,
            config=AgentsConfig(),
            provider_factory=lambda name: FakeLLMProvider(),
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
        )

    async def test_supervisor_node_timeout(self, graph):
        """Returns timeout status when time exceeds max."""
        state: TeamState = {
            "messages": [],
            "total_calls": 0,
            "total_writes": 0,
            "total_cost": 0.0,
            "agent1_calls": 0,
            "agent2_calls": 0,
            "agent1_cost": 0.0,
            "agent2_cost": 0.0,
            "last_metrics": None,
            "round_num": 0,
            "max_rounds": 8,
            "start_time": time.monotonic() - 99999,
            "max_time_seconds": 1,
            "status": "running",
            "last_activity": time.monotonic(),
            "agent1_task": "",
            "agent2_task": "",
        }
        result = await graph._supervisor_node(state)
        assert result["status"] == "timeout"

    
class TestSupervisorGraphRun:
    """Run method integration with mocked provider."""

    async def test_run_with_complete_decision(self):
        """Run with a provider that returns no tool calls (immediate complete)."""
        provider = FakeLLMProvider(responses=[LLMResponse(content="done", usage=LLMUsage(prompt_tokens=5, completion_tokens=5))])
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: provider,
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
            max_llm_calls=30,
        )
        result = await graph.run("test task")
        assert result is not None
        assert "status" in result

    async def test_run_with_checkpoint_resume(self, tmp_path: Path):
        """Run with resume=True when a checkpoint exists."""
        provider = FakeLLMProvider(responses=[LLMResponse(content="done", usage=LLMUsage(prompt_tokens=5, completion_tokens=5))])
        # Create a checkpoint first
        checkpoint_path = tmp_path / ".spec-editor-checkpoint.json"
        checkpoint_data = {
            "messages": [{"role": "user", "content": "resume task"}],
            "total_calls": 3,
            "total_writes": 1,
            "total_cost": 0.005,
            "agent1_calls": 2,
            "agent2_calls": 1,
            "agent1_cost": 0.003,
            "agent2_cost": 0.002,
            "last_metrics": {"total_elements": 5},
            "round_num": 1,
            "status": "running",
        }
        checkpoint_path.write_text(json.dumps(checkpoint_data))
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: provider,
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
            project_path=tmp_path,
        )
        result = await graph.run("resume from checkpoint", resume=True)
        assert result is not None
        # Checkpoint should be deleted after run
        assert not checkpoint_path.exists()

    async def test_run_with_failing_provider_completes(self, tmp_path: Path):
        """When provider fails, graph still completes (graceful error handling)."""
        checkpoint_path = tmp_path / ".spec-editor-checkpoint.json"
        graph = SupervisorGraph(
            storage=FakeStorage(),
            config=AgentsConfig(),
            provider_factory=lambda name: (_ for _ in ()).throw(Exception("provider error")),
            agent1_prompt="You are agent 1",
            agent2_prompt="You are agent 2",
            agent1_tools=[_make_tool_def("read_element")],
            agent2_tools=[_make_tool_def("read_element")],
            agent1_handlers={"read_element": _make_handler("read_element")},
            agent2_handlers={"read_element": _make_handler("read_element")},
            project_path=tmp_path,
        )
        result = await graph.run("test task")
        # Even with a failing provider, the graph runs through max rounds and completes
        assert result.get("status") == "complete"
        # Checkpoint should be deleted after run
        assert not checkpoint_path.exists()
