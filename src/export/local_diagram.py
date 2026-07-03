"""Local Mermaid diagram improvement via Ollama + Qwen2.5-Coder.

Takes a template-generated Mermaid diagram and asks the local LLM to
improve its layout, grouping, and readability. The LLM preserves all
element IDs and relationships exactly as provided.

Usage:
    provider = OllamaProvider(model="qwen2.5-coder:7b")
    generator = LocalDiagramGenerator(storage, provider)
    result = await generator.generate(project_path, aspect="modules")
    # result = {"mermaid": "graph TD\\n  MOD-001[...]", "error": None}
"""

from pathlib import Path

from src.config import get_logger
from src.providers.base import LLMProvider, Message, MessageRole
from src.providers.ollama_provider import OllamaProvider
from src.storage.adapter import StorageAdapter

logger = get_logger(__name__)

_IMPROVE_PROMPT = """You are a Mermaid diagram layout expert.

Below is a Mermaid diagram generated from a template. The elements and
relationships are CORRECT — do NOT change any node IDs, labels, or edges.

Your task: IMPROVE the visual layout and readability:
1. Group related nodes using `subgraph` blocks with descriptive titles
2. Reorder node definitions so connected nodes are closer together
3. Add `%% comments` explaining each subgraph
4. Keep ALL existing node definitions and edges EXACTLY as-is
5. Do NOT add `linkStyle` or `style` directives — the template already has them

CRITICAL RULES:
- Every node ID with `["Label"]` MUST appear as a definition BEFORE any edge uses it
- DO NOT remove any `NODE-ID["Label"]` lines from the original diagram
- DO NOT remove any `style NODE-ID ...` lines
- The original diagram is CORRECT — only reorder and add subgraphs

Rules:
- Node IDs like `MOD-001` contain hyphens. Wrap them in quotes in subgraphs
- Preserve the diagram type (`graph TD`, `flowchart LR`, `erDiagram`)
- Output ONLY the Mermaid code in a ```mermaid fence, no explanation

Original diagram:
{diagram_code}
"""


class LocalDiagramGenerator:
    """Improves template-generated diagrams using a local LLM."""

    def __init__(
        self,
        storage: StorageAdapter,
        provider: LLMProvider | None = None,
    ) -> None:
        self._storage = storage
        self._provider = provider

    async def generate(
        self,
        project_path: Path,
        aspect: str = "",
        node_path: str = "",
    ) -> dict:
        """Generate and improve a Mermaid diagram.

        1. Use MermaidRenderer for a correct template-based diagram
        2. Send it to local LLM for layout improvement
        """
        if self._provider is None:
            try:
                self._provider = OllamaProvider()
            except Exception as exc:
                return {
                    "mermaid": "",
                    "error": f"Ollama not available: {exc}. "
                    f"Run 'ollama pull qwen2.5-coder:7b' first.",
                }

        # Step 1: Generate template-based diagram
        try:
            from src.view.renderer import MermaidRenderer

            renderer = MermaidRenderer()
            base_diagram = renderer.build_mermaid(
                project_path,
                element_id=node_path or None,
                diagram_type="graph",
                aspect_name=aspect or None,
            )
        except Exception as exc:
            logger.error("template_diagram_error", error=str(exc))
            return {"mermaid": "", "error": f"Template error: {exc}"}

        if not base_diagram or not base_diagram.strip():
            return {"mermaid": "", "error": "No elements to diagram"}

        # Limit diagram size for LLM context
        lines = base_diagram.strip().split("\n")
        if len(lines) > 80:
            # Keep first line (type declaration) + up to 80 edges/nodes
            lines = lines[:1] + lines[-80:]
        base_diagram = "\n".join(lines)

        # Step 2: Ask LLM to improve layout
        prompt = _IMPROVE_PROMPT.format(diagram_code=base_diagram)

        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content="You are a Mermaid diagram layout expert.",
            ),
            Message(role=MessageRole.USER, content=prompt),
        ]

        try:
            response = await self._provider.complete(messages=messages)
        except Exception as exc:
            logger.error("local_diagram_error", error=str(exc))
            # Fall back to template diagram on LLM error
            return {"mermaid": base_diagram, "error": None}

        improved = self._extract_mermaid(response.content)
        if not improved or len(improved) < 20:
            # LLM produced garbage — fall back to template
            return {"mermaid": base_diagram, "error": None}

        return {"mermaid": improved, "error": None}

    @staticmethod
    def _extract_mermaid(text: str) -> str:
        """Extract Mermaid code from LLM response."""
        text = text.strip()

        if "```mermaid" in text:
            start = text.find("```mermaid") + len("```mermaid")
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) > 2:
                return "\n".join(lines[1:-1]).strip()

        return text
