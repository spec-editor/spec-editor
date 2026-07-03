"""Dialogue message bus — shared async message queue between agents."""

import asyncio

from src.providers.base import Message


class MessageBus:
    """Shared message bus between agents.

    Agents write messages, read new messages from their colleague.
    """

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._condition = asyncio.Condition()
        self._msg_count = 0  # how many messages the agent has already read

    def post(self, message: Message) -> None:
        self._messages.append(message)

    async def get_history(self) -> list[Message]:
        async with self._condition:
            return list(self._messages)

    async def wait_for_others(self, my_name: str, last_seen: int) -> tuple[int, str]:
        """Wait for a new message from another agent.

        Returns: (new index, message text)
        """
        async with self._condition:
            while True:
                # Look for new messages NOT from me
                for i in range(last_seen, len(self._messages)):
                    msg = self._messages[i]
                    if msg.name != my_name and (msg.content or msg.tool_calls):
                        return (i + 1, msg.content or "(executed tools)")
                # No new ones — wait
                await self._condition.wait()

    def notify_all(self) -> None:
        """Notify all waiting agents (call within lock)."""
