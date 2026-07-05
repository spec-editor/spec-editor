"""ContextCompactor tests — smart context compaction."""

from src.agents.compaction import ContextCompactor
from src.providers.base import Message, MessageRole


class TestContextCompactor:
    """ContextCompactor: plan, result, token budget."""

    def test_initial_state(self):
        """New compactor is empty."""
        c = ContextCompactor(max_llm_calls=5, token_budget=100)
        assert c.calls == 0
        assert c.total_tokens == 0
        assert not c.should_compact()

    def test_should_compact_on_llm_calls(self):
        """Triggers when LLM call limit is reached."""
        c = ContextCompactor(max_llm_calls=3, token_budget=99999)
        c.record_llm_call(10, 5)
        c.record_llm_call(10, 5)
        assert not c.should_compact()
        c.record_llm_call(10, 5)
        assert c.should_compact()

    def test_should_compact_on_token_budget(self):
        """Triggers when token budget is exceeded."""
        c = ContextCompactor(max_llm_calls=99, token_budget=100)
        c.record_llm_call(60, 30)  # 90 tokens
        assert not c.should_compact()
        c.record_llm_call(10, 5)  # 105 tokens
        assert c.should_compact()

    def test_record_plan_captures_first_message(self):
        """record_plan saves only the first message."""
        c = ContextCompactor()
        c.record_plan("Will create modules and UI")
        c.record_plan("This is already second — ignored")
        assert "Will create modules and UI" in c._first_plan

    def test_record_tool_call_counts_aspects(self):
        """record_tool_call counts aspects for write_element."""
        c = ContextCompactor()
        c.record_tool_call("write_element", {"aspect": "modules"})
        c.record_tool_call("write_element", {"aspect": "modules"})
        c.record_tool_call("write_element", {"aspect": "user_interface"})
        c.record_tool_call("add_relationship", {})
        assert c._aspect_counts["modules"] == 2
        assert c._aspect_counts["user_interface"] == 1
        assert c._tool_counts["add_relationship"] == 1

    def test_compact_preserves_system_prompt(self):
        """After compaction, the system prompt is preserved."""
        c = ContextCompactor(max_llm_calls=1, token_budget=99999)
        messages = [
            Message(role=MessageRole.SYSTEM, content="SYSTEM"),
            Message(
                role=MessageRole.ASSISTANT,
                content="plan: create modules",
                tool_calls=[],
                name="Agent 1",
            ),
        ]
        c.record_llm_call(10, 5)
        c.record_plan("plan: create modules")
        result = c.compact(messages, reason="test")
        assert result[0].role == MessageRole.SYSTEM
        assert result[0].content == "SYSTEM"

    def test_compact_includes_plan_in_summary(self):
        """Summary contains the plan."""
        c = ContextCompactor(max_llm_calls=1, token_budget=99999)
        messages = [
            Message(role=MessageRole.SYSTEM, content="SYS"),
            Message(
                role=MessageRole.ASSISTANT,
                content="final answer",
                tool_calls=[],
                name="Agent 1",
            ),
        ]
        c.record_llm_call(10, 5)
        c.record_plan("build modules")
        result = c.compact(messages, reason="test")
        summary = result[1].content  # second message is the summary
        assert "build modules" in summary
        assert "[Context compacted:" in summary

    def test_reset_clears_all_counters(self):
        """reset() zeros all counters."""
        c = ContextCompactor()
        c.record_llm_call(10, 5)
        c.record_plan("plan")
        c.record_tool_call("write_element", {"aspect": "m"})
        c.reset()
        assert c.calls == 0
        assert c.total_tokens == 0
        assert not c._first_plan
        assert not c._aspect_counts
