"""Context builder — extracts spec elements for code files and task queries."""

import re
from collections import defaultdict
from pathlib import Path

from src.storage.adapter import StorageAdapter
from src.storage.models import Element

_IMPLEMENTS_RE = re.compile(r'@implements\("([^"]+)"\)', re.IGNORECASE)

TOKEN_CHARS_PER_TOKEN = 4


class ContextBuilder:
    """Build specification context for AI coding assistants.

    Modes: file (@implements), element (by ID), task (text search).
    Pro: smart_context (hierarchical sub-graph), context_with_budget (token-limited).
    """

    def __init__(self, storage: StorageAdapter, project_path: Path | None = None) -> None:
        self._storage = storage
        self._project_path = project_path

    # -- Public API (OSS) --

    def for_file(self, file_path: Path) -> str:
        req_ids = self._parse_implements(file_path)
        if not req_ids:
            return f"No @implements annotations found in {file_path}"
        elements = self._load_elements(req_ids)
        if not elements:
            return f"No spec elements found for: {', '.join(req_ids)}"
        return self._build_context(elements, title=f"File: {file_path.name}")

    def for_task(self, query: str, top_n: int = 10) -> str:
        results = self._storage.search(query)
        if not results:
            seen: set[str] = set()
            all_results = []
            for word in query.split():
                for s in self._storage.search(word):
                    if s.id not in seen:
                        seen.add(s.id)
                        all_results.append(s)
            results = all_results
        if not results:
            return f"No spec elements found for: {query}"
        top_ids = [s.id for s in results[:top_n]]
        elements = self._load_elements(top_ids)
        return self._build_context(elements, title=f"Task: {query}")

    def for_element(self, element_id: str) -> str:
        element = self._load_single(element_id)
        if element is None:
            return f"Element not found: {element_id}"
        return self._build_context([element], title=f"Element: {element_id}")

    # -- Public API (Pro) --

    def smart_context(self, element_ids: list[str], depth: int = 2) -> str:
        primary = [self._load_single(eid) for eid in element_ids]
        primary = [p for p in primary if p is not None]
        if not primary:
            return "No spec elements found"

        visited: set[str] = set()
        layers: dict[int, list[str]] = {0: [e.id for e in primary]}
        for e in primary:
            visited.add(e.id)

        frontier = set(layers[0])
        for d in range(1, depth + 1):
            next_frontier: set[str] = set()
            for eid in frontier:
                el = self._load_single(eid)
                if el is None:
                    continue
                neighbours: set[str] = set()
                if el.parent:
                    neighbours.add(el.parent)
                neighbours.update(el.children)
                for entries in el.relationships.values():
                    for entry in entries:
                        neighbours.add(entry.target)
                for nid in neighbours:
                    if nid not in visited:
                        visited.add(nid)
                        next_frontier.add(nid)
            if next_frontier:
                layers[d] = sorted(next_frontier)
                frontier = next_frontier
            else:
                break

        level_names = {0: "Primary", 1: "Directly Related", 2: "Indirectly Related"}
        lines = ["## Spec Editor Context (Smart Assembly)", ""]
        lines.append(f"**{len(element_ids)} roots, depth={depth}, {len(visited)} elements in sub-graph**")
        lines.append("")

        for d in range(depth + 1):
            if d not in layers:
                continue
            lines.append(f"### {level_names.get(d, f'Depth {d}')}")
            lines.append("")
            for eid in layers[d]:
                el = self._load_single(eid)
                if el is None:
                    continue
                lines.append(f"- **{el.id}**: {el.title} ({el.aspect}/{el.element_type})")
                if el.content and d < 2:
                    desc = el.content[:200].replace(chr(10), " ")
                    lines.append(f"  {desc}")
            lines.append("")
        return chr(10).join(lines).strip()

    def context_with_budget(
        self, element_ids: list[str], token_budget: int = 4000, depth: int = 2
    ) -> str:
        def estimate(text: str) -> int:
            return len(text) // TOKEN_CHARS_PER_TOKEN

        full = self.smart_context(element_ids, depth=depth)
        if estimate(full) <= token_budget:
            return full

        reduced = self.smart_context(element_ids, depth=1)
        if estimate(reduced) <= token_budget:
            return reduced

        primary = [self._load_single(eid) for eid in element_ids]
        primary = [p for p in primary if p is not None]
        minimal = self._build_context(primary, title="Requirements (budget-constrained)")
        if estimate(minimal) <= token_budget:
            return minimal

        while estimate(minimal) > token_budget and len(minimal) > 100:
            minimal = minimal[:len(minimal) * 3 // 4] + "..."
        return minimal

    # -- Internal --

    def _parse_implements(self, file_path: Path) -> list[str]:
        if not file_path.exists():
            return []
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        return list(set(_IMPLEMENTS_RE.findall(text)))

    def _load_elements(self, req_ids: list[str]) -> list[Element]:
        elements = []
        for rid in req_ids:
            el = self._load_single(rid)
            if el:
                elements.append(el)
        return elements

    def _load_single(self, element_id: str) -> Element | None:
        try:
            return self._storage.read_element(element_id)
        except (KeyError, Exception):
            return None

    def _build_context(self, elements: list[Element], title: str = "") -> str:
        lines = ["## Spec Editor Context", ""]
        if title:
            lines.append(f"**{title}**")
            lines.append("")

        seen_ids: set[str] = set()
        for el in elements:
            seen_ids.add(el.id)

        neighbour_ids: set[str] = set()
        for el in elements:
            if el.parent:
                neighbour_ids.add(el.parent)
            neighbour_ids.update(el.children)
            for entries in el.relationships.values():
                for entry in entries:
                    neighbour_ids.add(entry.target)

        neighbours_to_load = neighbour_ids - seen_ids
        neighbours = [self._load_single(nid) for nid in neighbours_to_load]
        neighbours = [n for n in neighbours if n is not None]

        lines.append("### Implemented Requirements")
        lines.append("")
        for el in elements:
            lines.append(f"**{el.id}**: {el.title} ({el.aspect}/{el.element_type})")
            if el.content:
                lines.append(f"  {el.content[:300]}")
            lines.append("")

        if neighbours:
            lines.append("### Related Elements")
            lines.append("")
            by_aspect: dict[str, list[Element]] = defaultdict(list)
            for n in neighbours:
                by_aspect[n.aspect].append(n)
            for aspect, els in sorted(by_aspect.items()):
                lines.append(f"**{aspect}**")
                for e in els:
                    lines.append(f"- {e.id}: {e.title}")
                lines.append("")

        return chr(10).join(lines).strip()
