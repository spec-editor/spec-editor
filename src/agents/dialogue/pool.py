"""Agent pool — parallel agent spawn, limit, lifecycle."""

import asyncio


class AgentPool:
    """Parallel agent pool — spawn, limit, lifecycle."""

    def __init__(
        self, main_agents: list, bus, max_agents: int = 8, make_loop=None
    ) -> None:
        self._bus = bus
        self._max_agents = max_agents
        self._tasks: list = []
        self._counter = 0
        self._make_loop = make_loop

        stop_ev = asyncio.Event()
        for agent in main_agents:
            if make_loop:
                task = make_loop(
                    agent,
                    agent.name,
                    bus,
                    stop_ev,
                    initial_message=None,
                    is_helper=False,
                )
                if task:
                    self._tasks.append(task)

    async def spawn(self, role: str, task_desc: str) -> str:
        """Spawn a helper. Returns name."""
        self._cleanup()
        if len(self._tasks) >= self._max_agents:
            raise RuntimeError(f"agent limit reached ({self._max_agents})")
        self._counter += 1
        name = f"Helper-{role}-{self._counter}"
        if self._make_loop:
            task = self._make_loop(
                None,
                name,
                self._bus,
                asyncio.current_task() is None and asyncio.Event() or None,
                initial_message=f": {role}. : {task_desc}",
                is_helper=True,
            )
            if task:
                self._tasks.append(task)
        return name

    @property
    def active_count(self) -> int:
        self._cleanup()
        return len(self._tasks)

    def _cleanup(self) -> None:
        self._tasks = [t for t in self._tasks if not t.done()]
