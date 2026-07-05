"""AgentPool tests — pool of parallel agents."""

import asyncio

import pytest

from src.agents.role import AgentRole


class FakeAgent:
    """Mock agent for pool tests."""

    def __init__(self, name: str):
        self.name = name
        self.responses: list = []
        self._history: list = []

    async def run(self, message, history=None):
        from src.providers.base import LLMResponse

        return LLMResponse(content=f"response from {self.name}")


class FakeLoop:
    """Mock AgentLoop — returns a predefined response."""

    def __init__(self, agent, name: str, is_helper: bool = False):
        self.agent = agent
        self.name = name
        self.is_helper = is_helper


class TestAgentPool:
    """AgentPool: managing agent lifecycle."""

    @pytest.mark.asyncio
    async def test_pool_creates_base_agents(self):
        """Pool creates two main agents."""
        from src.agents.dialogue import AgentPool

        bus = _make_bus()
        a1 = FakeAgent("Agent 1")
        a2 = FakeAgent("Agent 2")

        pool = AgentPool(
            main_agents=[a1, a2], bus=bus, max_agents=8, make_loop=_fake_make_loop
        )

        assert pool.active_count == 2

    @pytest.mark.asyncio
    async def test_pool_spawns_helper(self):
        """spawn_helper creates a new agent in the pool."""
        from src.agents.dialogue import AgentPool

        bus = _make_bus()
        pool = AgentPool(
            main_agents=[FakeAgent("A1")],
            bus=bus,
            max_agents=8,
            make_loop=_fake_make_loop,
        )

        name = await pool.spawn("modules", "create modules")
        assert name.startswith("Helper-modules-")
        assert pool.active_count == 2  # 1 main + 1 helper

    @pytest.mark.asyncio
    async def test_pool_respects_max_agents(self):
        """Pool does not exceed the agent limit."""
        from src.agents.dialogue import AgentPool

        bus = _make_bus()
        pool = AgentPool(
            main_agents=[FakeAgent("A1")],
            bus=bus,
            max_agents=2,
            make_loop=_fake_make_loop,
        )

        await pool.spawn("m1", "task1")  # OK — 2 agents now
        with pytest.raises(RuntimeError, match="limit"):
            await pool.spawn("m2", "task2")  # rejected — limit 2

    @pytest.mark.asyncio
    async def test_pool_cleans_finished_tasks(self):
        """Finished tasks are removed from the pool."""
        from src.agents.dialogue import AgentPool

        bus = _make_bus()
        pool = AgentPool(
            main_agents=[FakeAgent("A1")],
            bus=bus,
            max_agents=8,
            make_loop=_fake_make_loop,
        )

        await pool.spawn("test", "task")
        assert pool.active_count == 2  # 1 main + 1 helper
        # Cancel helper (last task)
        pool._tasks[-1].cancel()
        try:
            await pool._tasks[-1]
        except (asyncio.CancelledError, Exception):
            pass
        # After cleanup — only main agent
        pool._cleanup()
        assert pool.active_count == 1


def _make_bus():
    """Create MessageBus for tests."""
    from src.agents.dialogue import MessageBus

    return MessageBus()


def _fake_make_loop(
    agent, name, bus, stop_event, initial_message=None, is_helper=False
):
    """Fake agent loop. Main agents live forever, helpers exit immediately."""

    async def _main_loop():
        while True:
            await asyncio.sleep(3600)  # main agents never exit

    async def _helper_loop():
        pass  # helpers exit immediately

    fn = _helper_loop if is_helper else _main_loop
    return asyncio.create_task(fn())
