"""Analyze images (diagrams, screenshots, UI mockups) using local vision LLM.

Uses Ollama with granite3.2-vision (IBM, optimized for documents/diagrams)
running on Apple Silicon. No cloud API calls.

Usage:
    provider = OllamaVisionProvider(model="granite3.2-vision:2b")
    analyzer = ImageAnalyzer(storage, provider)
    result = await analyzer.analyze(image_path)
    # result = {"description": "...", "diagram_type": "flowchart", ...}
"""

import base64
import json
from pathlib import Path

from src.config import get_logger
from src.providers.base import LLMProvider, Message, MessageRole
from src.providers.ollama_provider import OllamaProvider
from src.storage.adapter import StorageAdapter

logger = get_logger(__name__)

_DIAGRAM_PROMPT = """Analyze this image and describe what you see.

If this is a DIAGRAM or CHART:
1. Identify the diagram type (flowchart, ER diagram, architecture, sequence, etc.)
2. Describe the main components/nodes and how they are connected
3. Extract any text labels visible in the diagram
4. Summarize what the diagram is trying to convey

If this is a UI SCREENSHOT or MOCKUP:
1. Describe the layout and key UI elements
2. Identify the purpose of this screen
3. List any visible controls, buttons, text fields

If this is a TEXT DOCUMENT:
1. Extract and transcribe the visible text
2. Summarize the content

Be specific and detailed. Extract EVERY visible label and relationship."""


class ImageAnalyzer:
    """Analyzes images using a local vision LLM via Ollama."""

    def __init__(
        self,
        storage: StorageAdapter,
        provider: LLMProvider | None = None,
    ) -> None:
        self._storage = storage
        self._provider = provider

    async def analyze(self, file_path: str) -> dict:
        """Analyze an image file and return structured description.

        Args:
            file_path: Path to image file (PNG, JPG, etc.)

        Returns:
            dict with keys: description, diagram_type, elements, error
        """
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}", "description": ""}

        if self._provider is None:
            try:
                self._provider = OllamaProvider(model="granite3.2-vision:2b")
            except Exception as exc:
                return {
                    "error": f"Ollama not available: {exc}",
                    "description": "",
                }

        # Read and encode image
        try:
            image_data = base64.b64encode(path.read_bytes()).decode("utf-8")
        except Exception as exc:
            return {"error": f"Cannot read image: {exc}", "description": ""}

        # Detect image type
        suffix = path.suffix.lower()
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(suffix, "image/png")

        messages = [
            Message(
                role=MessageRole.SYSTEM, content="You analyze images and diagrams."
            ),
            Message(
                role=MessageRole.USER,
                content=_DIAGRAM_PROMPT,
            ),
        ]

        # Add image as base64 in Ollama format
        # Ollama vision API: put image in content as {"type": "image_url", "image_url": {...}}
        # But our OllamaProvider uses the chat API directly, so we need to
        # extend the message with the image data

        try:
            response = await self._provider.complete_with_image(
                messages=messages,
                image_data=image_data,
                media_type=media_type,
            )
        except Exception as exc:
            logger.error("vision_analyze_error", error=str(exc))
            return {
                "error": f"Vision analysis failed: {exc}",
                "description": "",
            }

        return {
            "description": response.content,
            "error": None,
        }
