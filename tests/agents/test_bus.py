"""MessageBus tests — async message bus between agents."""

import asyncio

import pytest

from src.agents.dialogue import MessageBus
from src.providers.base import Message, MessageRole, ToolCall


class TestMessageBus:
    """MessageBus: post, wait_for_others, get_history."""

    @pytest.mark.asyncio
    async def test_post_and_get_history(self):
        """Messages are saved and accessible via get_history."""
        bus = MessageBus()
        msg = Message(role=MessageRole.ASSISTANT, content="hello", name="Agent 1")
        async with bus._condition:
            bus.post(msg)
        history = await bus.get_history()
        assert len(history) == 1
        assert history[0].content == "hello"

    @pytest.mark.asyncio
    async def test_wait_for_others_gets_message_from_different_agent(self):
        """wait_for_others returns a message from a different agent."""
        bus = MessageBus()

        # Post a message from Agent 2
        async with bus._condition:
            bus.post(Message(role=MessageRole.ASSISTANT, content="hi", name="Agent 2"))
            bus._condition.notify_all()

        # Agent 1 waits
        idx, content = await bus.wait_for_others("Agent 1", last_seen=0)
        assert content == "hi"
        assert idx == 1

    @pytest.mark.asyncio
    async def test_wait_for_others_ignores_own_messages(self):
        """wait_for_others ignores its own messages."""
        bus = MessageBus()

        # Post own message
        async with bus._condition:
            bus.post(
                Message(role=MessageRole.ASSISTANT, content="mine", name="Agent 1")
            )
            bus._condition.notify_all()

        # Agent 1 waits — should not see its own
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                bus.wait_for_others("Agent 1", last_seen=0), timeout=0.1
            )

    @pytest.mark.asyncio
    async def test_wait_for_others_respects_last_seen(self):
        """wait_for_others does not return already-read messages."""
        bus = MessageBus()

        async with bus._condition:
            bus.post(
                Message(role=MessageRole.ASSISTANT, content="msg1", name="Agent 2")
            )
            bus.post(
                Message(role=MessageRole.ASSISTANT, content="msg2", name="Agent 2")
            )
            bus._condition.notify_all()

        # Read first
        idx, content = await bus.wait_for_others("Agent 1", last_seen=0)
        assert content == "msg1"
        assert idx == 1

        # Read second (last_seen=1)
        idx2, content2 = await bus.wait_for_others("Agent 1", last_seen=1)
        assert content2 == "msg2"
        assert idx2 == 2

    @pytest.mark.asyncio
    async def test_wait_for_others_accepts_tool_calls_without_content(self):
        """wait_for_others sees a message with tool_calls even without text."""
        bus = MessageBus()

        async with bus._condition:
            bus.post(
                Message(
                    role=MessageRole.ASSISTANT,
                    content="",  # empty text
                    tool_calls=[ToolCall(id="1", name="read_element", arguments={})],
                    name="Agent 2",
                )
            )
            bus._condition.notify_all()

        # Agent 1 waits — should see the message with tool_calls
        idx, content = await bus.wait_for_others("Agent 1", last_seen=0)
        assert idx == 1
        assert content == "(executed tools)"

    @pytest.mark.asyncio
    async def test_concurrent_post_and_wait(self):
        """Concurrent post and wait."""
        bus = MessageBus()

        async def poster():
            await asyncio.sleep(0.05)
            async with bus._condition:
                bus.post(
                    Message(role=MessageRole.ASSISTANT, content="async", name="Agent 2")
                )
                bus._condition.notify_all()

        async def waiter():
            idx, content = await bus.wait_for_others("Agent 1", last_seen=0)
            return idx, content

        _, (idx, content) = await asyncio.gather(poster(), waiter())
        assert content == "async"
