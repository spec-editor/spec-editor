"""Formatters: Markdown, Jinja2 HTML."""

from src.export.pipeline import ExportData, Formatter


class MarkdownFormatter(Formatter):
    """Formats ExportData as Markdown."""

    def format(self, data: ExportData, config: dict | None = None) -> str:
        lines = [
            f"# {data.doc_title}",
            "",
            "---",
            "",
        ]
        for section in data.sections:
            lines.append(f"## {section.title}")
            lines.append("")
            if section.description:
                lines.append(f"_{section.description}_")
                lines.append("")

            current_group = None
            for el in section.elements:
                group = getattr(el, "group_key", "") or el.parent or ""
                if group and group != current_group:
                    current_group = group
                    lines.append(f"### {group}")
                    lines.append("")
                lines.append(f"**{el.id}** — {el.title}")
                if el.content:
                    lines.append("")
                    lines.append(el.content.strip())
                # Relationships
                rel_lines = _format_element_rels(el)
                if rel_lines:
                    lines.append("")
                    lines.extend(rel_lines)
                lines.append("")

        return "\n".join(lines)


def _format_element_rels(el) -> list[str]:
    """Format element relationships as Markdown bullet points."""
    result: list[str] = []
    for rel_type, entries in el.relationships.items():
        if rel_type in ("derived_from",):
            continue
        targets = [e["target"] if isinstance(e, dict) else e.target for e in entries]
        if targets:
            result.append(f"- **{rel_type}**: {', '.join(targets)}")
    if el.children:
        result.append(f"- **children**: {', '.join(el.children)}")
    return result


class Jinja2Formatter(Formatter):
    """Formats ExportData as HTML via Jinja2."""

    def format(self, data: ExportData, config: dict | None = None) -> str:
        try:
            from jinja2 import BaseLoader, Environment
        except ImportError:
            return MarkdownFormatter().format(data, config)

        # Register markdown filter BEFORE template compilation
        import markdown as md_lib

        env = Environment(loader=BaseLoader())
        env.filters["markdown"] = lambda text: md_lib.markdown(
            text or "",
            extensions=["extra", "codehilite", "tables"],
        )

        template_path = (config or {}).get("template", "")
        if template_path:
            with open(template_path, encoding="utf-8") as f:
                tmpl = env.from_string(f.read())
        else:
            tmpl = env.from_string(_DEFAULT_HTML_TEMPLATE)

        return tmpl.render(
            title=data.doc_title,
            sections=[
                {
                    "number": s.number,
                    "title": s.title,
                    "description": s.description,
                    "diagram": s.diagram,
                    "elements": [
                        {
                            "id": e.id,
                            "title": e.title,
                            "content": e.content,
                            "aspect": e.aspect,
                            "type": e.element_type,
                            "status": e.status,
                            "children": e.children,
                            "relationships": e.relationships,
                            "back_refs": e.back_refs,
                            "group_key": e.group_key,
                            "inline_steps": e.inline_steps,
                        }
                        for e in s.elements
                    ],
                }
                for s in data.sections
            ],
        )


_DEFAULT_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; }
    h1 { border-bottom: 2px solid #333; }
    h2 { border-bottom: 1px solid #999; margin-top: 30px; }
    h3 { color: #555; }
    .elem { border-left: 3px solid #4a90d9; padding: 8px 12px; margin: 8px 0; background: #f8f9fa; }
    .elem-id { color: #888; font-size: 0.85em; }
    .elem-content { margin-top: 6px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>{{ title }}</h1>
  {% for section in sections %}
  <h2>{{ section.number }}. {{ section.title }}</h2>
  {% if section.description %}<p><em>{{ section.description }}</em></p>{% endif %}
  {% for elem in section.elements %}
  <div class="elem">
    <span class="elem-id">{{ elem.id }}</span>
    <strong>{{ elem.title }}</strong>
    <span style="color:#888">[{{ elem.type }}]</span>
    {% if elem.content %}<div class="elem-content">{{ elem.content }}</div>{% endif %}
  </div>
  {% endfor %}
  {% endfor %}
</body>
</html>"""
