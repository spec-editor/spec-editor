"""Dialogue result model."""

from pydantic import BaseModel, Field

from src.mcp.metrics import MetricsReport
from src.providers.base import Message


class DialogueResult(BaseModel):
    status: str = Field(default="unknown")
    rounds_completed: int = 0
    final_metrics: MetricsReport | None = None
    dialogue_history: list[Message] = Field(default_factory=list)
