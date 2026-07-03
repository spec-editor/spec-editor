"""Agent tools for async questions (questions.jsonl)."""

from pathlib import Path
from typing import Callable

from src.providers.base import ToolDef, make_tool_params as _params

# ======================================================================
# Tool functions
# ======================================================================


async def ask_question_tool(
    project_path: str,
    agent: str,
    question: str,
    options: list[str] | None = None,
) -> dict:
    """Ask an async question to the human/orchestrator. Does NOT block work.

    The question is written to questions.jsonl. You can continue working.
    The answer will come later — the orchestrator will deliver it in the next round.

    Args:
        project_path: path to project root
        agent: your name (Agent 1 / Agent 2)
        question: question text
        options: answer choices (optional)
    """
    from src.agents.questions import QuestionList

    ql = QuestionList(Path(project_path))
    q = ql.ask(agent=agent, question=question, options=options)
    return {
        "status": "ok",
        "question_id": q.id,
        "message": f"Question {q.id} saved. Continue working, answer will arrive later.",
    }


async def list_questions_tool(project_path: str, status: str = "open") -> dict:
    """Get the list of questions from questions.jsonl.

    Default — only open. status=all — all.
    """
    from src.agents.questions import QuestionList

    ql = QuestionList(Path(project_path))
    if status == "all":
        questions = ql._all()
    else:
        questions = ql.list_open()
    return {
        "count": len(questions),
        "questions": [
            {
                "id": q.id,
                "agent": q.agent,
                "question": q.question,
                "options": q.options,
                "status": q.status,
                "answer": q.answer,
                "timestamp": q.timestamp,
            }
            for q in questions
        ],
    }


async def answer_question_tool(
    project_path: str,
    question_id: str,
    answer: str,
) -> dict:
    """Answer an agent's question.

    Updates the question status to "answered" in questions.jsonl.
    """
    from src.agents.questions import QuestionList

    ql = QuestionList(Path(project_path))
    q = ql.answer(question_id, answer)
    if q:
        return {
            "status": "ok",
            "question_id": q.id,
            "message": f"Question {q.id} answered",
        }
    return {
        "status": "error",
        "message": f"Question {question_id} has been answered and removed from queue",
    }


# ======================================================================
# ToolDef — JSON Schema for function calling
# ======================================================================




QUESTIONS_RO_TOOLS: list[ToolDef] = [
    ToolDef(
        name="list_questions",
        description="[Agent] Show open agent questions from questions.jsonl. status=all — all questions.",
        parameters=_params(
            {
                "project_path": {
                    "type": "string",
                    "description": "Path to project root",
                },
                "status": {
                    "type": "string",
                    "description": "open (pending answer) from all agents",
                },
            },
            ["project_path"],
        ),
    ),
]

QUESTIONS_RW_TOOLS: list[ToolDef] = [
    ToolDef(
        name="ask_question",
        description="List open questions for the user. Use status=all to see all questions, or leave empty for open questions only.",
        parameters=_params(
            {
                "project_path": {
                    "type": "string",
                    "description": "Path to project root",
                },
                "agent": {
                    "type": "string",
                    "description": "Get answers from a specific agent (Agent 1 / Agent 2)",
                },
                "question": {"type": "string", "description": "Question text"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Answer options for the user to choose from",
                },
            },
            ["project_path", "agent", "question"],
        ),
    ),
    ToolDef(
        name="answer_question",
        description="Dismiss a question from questions.jsonl. Sets the question status to answered.",
        parameters=_params(
            {
                "project_path": {
                    "type": "string",
                    "description": "Path to project root",
                },
                "question_id": {
                    "type": "string",
                    "description": "Question ID (e.g., Q-0001)",
                },
                "answer": {"type": "string", "description": "Answer text"},
            },
            ["project_path", "question_id", "answer"],
        ),
    ),
]


# ======================================================================
# Handler registration
# ======================================================================


def add_question_tools_handlers(
    handlers: dict[str, Callable],
    source_dir: str | None = None,
) -> None:
    """Add question tool handlers to the handlers dict."""
    sd = source_dir or ""
    proj_path = str(Path(sd).parent) if sd else ""

    handlers.update(
        {
            "list_questions": lambda project_path="", status="open": (
                list_questions_tool(project_path or proj_path, status)
            ),
            "ask_question": lambda project_path="", agent="", question="", options=None: (
                ask_question_tool(
                    project_path or proj_path,
                    agent,
                    question,
                    options,
                )
            ),
            "answer_question": lambda project_path="", question_id="", answer="": (
                answer_question_tool(project_path or proj_path, question_id, answer)
            ),
        }
    )
