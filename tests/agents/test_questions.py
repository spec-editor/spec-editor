"""Tests for async agent questions (QuestionList)."""

import json
import tempfile
from pathlib import Path

import pytest

from src.agents.questions import QuestionList


class TestQuestionList:
    """QuestionList: list of questions in questions.jsonl."""

    def test_ask_creates_question(self):
        """ask() creates a question and writes to file."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            q = ql.ask(
                agent="Agent 1", question="Which color?", options=["red", "blue"]
            )
            assert q.id == "Q-0001"
            assert q.status == "open"
            assert q.agent == "Agent 1"
            assert q.options == ["red", "blue"]

            # Verify it was written to the file
            jsonl_path = Path(tmp) / "questions.jsonl"
            assert jsonl_path.exists()
            with open(jsonl_path) as f:
                data = json.loads(f.readline())
            assert data["id"] == "Q-0001"

    def test_list_open_returns_only_open(self):
        """list_open() returns only open questions."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            ql.ask("Agent 1", "Вопрос 1")
            ql.ask("Agent 2", "Вопрос 2")
            ql.answer("Q-0001", "Ответ 1")

            open_qs = ql.list_open()
            assert len(open_qs) == 1
            assert open_qs[0].id == "Q-0002"

    def test_answer_updates_status(self):
        """answer() changes status to answered and records the answer."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            ql.ask("Agent 1", "Вопрос")
            q = ql.answer("Q-0001", "Мой ответ")

            assert q is not None
            assert q.status == "answered"
            assert q.answer == "Мой ответ"

            # Verify in the file
            all_qs = ql._all()
            assert all_qs[0].status == "answered"

    def test_answer_not_found(self):
        """answer() for nonexistent ID returns None."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            q = ql.answer("Q-9999", "ответ")
            assert q is None

    def test_answer_already_answered(self):
        """answer() for already-answered question returns None."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            ql.ask("Agent 1", "Вопрос")
            ql.answer("Q-0001", "Первый ответ")
            q = ql.answer("Q-0001", "Второй ответ")
            assert q is None

    def test_multiple_questions_increment_ids(self):
        """Question IDs increment."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            q1 = ql.ask("A", "Q1")
            q2 = ql.ask("B", "Q2")
            q3 = ql.ask("C", "Q3")
            assert q1.id == "Q-0001"
            assert q2.id == "Q-0002"
            assert q3.id == "Q-0003"

    def test_empty_list_when_no_file(self):
        """list_open() on empty directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            ql = QuestionList(Path(tmp))
            assert ql.list_open() == []
