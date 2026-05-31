"""Mermaid.js renderer — renders spec graph as interactive HTML."""

import webbrowser
from pathlib import Path

import frontmatter


class MermaidRenderer:
    """Build Mermaid graph diagrams from spec-editor projects and render to HTML."""

    # Color scheme by aspect
    ASPECT_COLORS = {
        "modules": "#4A90D9",
        "user_scenarios": "#50B86C",
        "scenarios": "#50B86C",
        "data_entities": "#E8A838",
        "entities": "#E8A838",
        "non_functional": "#D94A4A",
        "nfr": "#D94A4A",
        "ui": "#9B59B6",
        "decisions": "#1ABC9C",
    }
    DEFAULT_COLOR = "#95A5A6"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_mermaid(self, project_path: Path) -> str:
        """Build a Mermaid graph diagram from the project's aspects/.

        Returns a complete Mermaid diagram string (graph TD + nodes + edges).
        """
        elements = self._load_elements(project_path)
        if not elements:
            return "graph TD\n  EMPTY[No elements found]\n"

        lines = ["graph TD"]

        # Collect edges to deduplicate
        edges: set[tuple[str, str, str]] = set()  # (from, to, label)
        node_ids: set[str] = set()

        for el in elements:
            eid = el["id"]
            node_ids.add(eid)
            aspect = el.get("aspect", "")
            title = el.get("title", eid)
            color = self.ASPECT_COLORS.get(aspect, self.DEFAULT_COLOR)
            safe_title = title.replace('"', "'")
            lines.append(f'  {eid}["{safe_title}"]')
            lines.append(f"  style {eid} fill:{color},stroke:#333,color:#fff")

            # Relationships
            for rel_type, targets in el.get("relationships", {}).items():
                for target in targets:
                    edges.add((eid, target, rel_type))

        # Add edges
        for from_id, to_id, label in sorted(edges):
            lines.append(f"  {from_id} -->|{label}| {to_id}")

        return "\n".join(lines)

    def render_html(self, project_path: Path, output_path: Path | None = None) -> Path:
        """Render spec as self-contained interactive HTML.

        Opens in default browser when output_path is None (writes to temp).
        """
        mermaid = self.build_mermaid(project_path)
        title = project_path.name

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spec Editor — {title}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  body {{ margin: 0; padding: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; }}
  h1 {{ color: #4A90D9; }}
  .mermaid {{ background: #16213e; border-radius: 8px; padding: 20px; }}
  .legend {{ margin-top: 20px; display: flex; gap: 16px; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-dot {{ width: 14px; height: 14px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Spec Editor — {title}</h1>
<div class="mermaid">
{mermaid}
</div>
<div class="legend">
{self._build_legend()}
</div>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'dark', securityLevel: 'loose' }});</script>
</body>
</html>"""

        if output_path is None:
            import tempfile
            output_path = Path(tempfile.mktemp(suffix=".html"))
            auto_open = True
        else:
            auto_open = False

        output_path.write_text(html, encoding="utf-8")

        if auto_open:
            webbrowser.open(f"file://{output_path}")

        return output_path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_elements(self, project_path: Path) -> list[dict]:
        """Load all elements from aspects/ directory."""
        aspects_dir = project_path / "aspects"
        if not aspects_dir.is_dir():
            return []

        elements = []
        for md_file in sorted(aspects_dir.rglob("*.md")):
            try:
                post = frontmatter.load(str(md_file))
                el = dict(post.metadata)
                # Normalize relationships from various frontmatter formats
                if "relationships" not in el:
                    rels = {}
                    for key in ("relates_to", "implements", "derived_from", "covered_by", "depends_on"):
                        val = el.get(key)
                        if val:
                            rels[key] = val if isinstance(val, list) else [val]
                    if rels:
                        el["relationships"] = rels
                elements.append(el)
            except Exception:
                continue

        return elements

    def _build_legend(self) -> str:
        """Build HTML legend showing aspect colors."""
        items = []
        for aspect, color in self.ASPECT_COLORS.items():
            items.append(
                f'<div class="legend-item">'
                f'<div class="legend-dot" style="background:{color}"></div>'
                f'<span>{aspect}</span>'
                f'</div>'
            )
        return "\n".join(items)
