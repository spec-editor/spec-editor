"""MCP module: export specification to SRS (IEEE 830)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from src.config import get_logger
from src.storage.adapter import StorageAdapter
from src.storage.models import Element

logger = get_logger(__name__)


class SRSExportResult(BaseModel):
    """SRS export result."""

    content: str = ""  # finished document in Markdown
    sections: int = 0
    elements: int = 0
    duplicates_found: int = 0
    warnings: list[str] = Field(default_factory=list)


def export_srs(
    storage: StorageAdapter,
    template_path: Path,
    source_dir: Path | None = None,
) -> SRSExportResult:
    """Generate SRS document from a template.

    The template is a YAML file with section descriptions and aspect mappings.
    """
    if not template_path.exists():
        return SRSExportResult(warnings=[f"«TRANSLATED» «TRANSLATED» «TRANSLATED»: {template_path}"])

    with open(template_path, encoding="utf-8") as f:
        template = yaml.safe_load(f) or {}

    all_elements: dict[str, list[Element]] = {}
    duplicates = 0

    # Collect all elements from storage
    for summary in storage.list_all():
        try:
            element = storage.read_element(summary.id)
            aspect = element.aspect
            if aspect not in all_elements:
                all_elements[aspect] = []
            all_elements[aspect].append(element)
        except Exception:
            logger.warning("skip_element_export", element_id=summary.id)

    # Deduplication
    all_elements, duplicates = _deduplicate(all_elements)

    # Document generation
    lines = [
        f"# {template.get('title', 'SRS')}",
        f"«TRANSLATED»: {template.get('version', '1.0')}",
        "",
        "---",
        "",
    ]

    sections_count = 0
    elements_count = 0

    for section in template.get("sections", []):
        lines.append(f"## {section['title']}")
        lines.append("")
        if section.get("description"):
            lines.append(f"_{section['description']}_")
            lines.append("")

        # Sections from aspects
        for aspect_name in section.get("aspects", []):
            elements = all_elements.get(aspect_name, [])
            if not elements:
                lines.append(f"_«TRANSLATED» '{aspect_name}' «TRANSLATED» «TRANSLATED» «TRANSLATED»_")
                lines.append("")
                continue

            group_by = section.get("group_by", "")
            grouped = _group_elements(elements, group_by)

            for group_key, group_elements in grouped.items():
                if group_key:
                    lines.append(f"### {group_key}")
                    lines.append("")
                for el in group_elements:
                    lines.append(f"**{el.id}** — {el.title}")
                    if el.content:
                        lines.append("")
                        lines.append(el.content.strip())
                    lines.append("")
                    elements_count += 1

        sections_count += 1

    if duplicates:
        lines.append("---")
        lines.append(f"_«TRANSLATED» «TRANSLATED»: {duplicates}_")
        lines.append("")

    return SRSExportResult(
        content="\n".join(lines),
        sections=sections_count,
        elements=elements_count,
        duplicates_found=duplicates,
    )


def _deduplicate(
    all_elements: dict[str, list[Element]],
) -> tuple[dict[str, list[Element]], int]:
    """Find and remove duplicates. Returns (cleaned dict, number of duplicates)."""
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    duplicates = 0
    cleaned: dict[str, list[Element]] = {}

    for aspect, elements in all_elements.items():
        cleaned[aspect] = []
        for el in elements:
            if el.id in seen_ids:
                duplicates += 1
                continue
            content_hash = el.content.strip() if el.content else ""
            if content_hash and content_hash in seen_content:
                duplicates += 1
                continue
            seen_ids.add(el.id)
            if content_hash:
                seen_content.add(content_hash)
            cleaned[aspect].append(el)

    return cleaned, duplicates


def _group_elements(elements: list[Element], group_by: str) -> dict[str, list[Element]]:
    """Group elements for display in SRS."""
    if group_by == "element_type":
        result: dict[str, list[Element]] = {}
        for el in elements:
            key = el.element_type
            if key not in result:
                result[key] = []
            result[key].append(el)
        return result

    if group_by == "parent":
        result: dict[str, list[Element]] = {
            el.id: [el] for el in elements if not el.parent
        }
        for el in elements:
            if el.parent:
                key = el.parent
                if key not in result:
                    result[key] = []
                result[key].append(el)
        return result

    return {"": elements}
