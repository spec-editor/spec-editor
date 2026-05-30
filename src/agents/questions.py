"""Async question list from agents to the human.

Agents ask questions via ask_question, without blocking work.
The human answers via answer_question. The orchestrator tracks.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class Question(BaseModel):
    """Question from an agent."""

    id: str = ""
    agent: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)  # answer choices
    status: str = "open"  # open, answered, dismissed
    answer: str = ""
    timestamp: str = ""


class QuestionList:
    """List of questions in the questions.jsonl file."""

    def __init__(self, project_path: Path) -> None:
        self._path = project_path / "questions.jsonl"
        self._counter = 0

    def ask(
        self, agent: str, question: str, options: list[str] | None = None
    ) -> Question:
        """Ask a question. Returns the created Question."""
        self._counter += 1
        q = Question(
            id=f"Q-{self._counter:04d}",
            agent=agent,
            question=question,
            options=options or [],
            status="open",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(q.model_dump_json() + "\n")
        return q

    def list_open(self) -> list[Question]:
        """List of open questions."""
        return self._filter(status="open")

    def answer(self, question_id: str, answer: str) -> Question | None:
        """Answer a question. Returns the updated Question or None."""
        questions = list(self._all())
        found = None
        for q in questions:
            if q.id == question_id and q.status == "open":
                q.status = "answered"
                q.answer = answer
                found = q
                break
        if found:
            self._rewrite(questions)
        return found

    def _filter(self, status: str) -> list[Question]:
        return [q for q in self._all() if q.status == status]

    def _all(self) -> list[Question]:
        if not self._path.exists():
            return []
        result = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                try:
                    result.append(Question(**json.loads(line.strip())))
                except Exception:
                    pass
        return result

    def _rewrite(self, questions: list[Question]) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            for q in questions:
                f.write(q.model_dump_json() + "\n")
