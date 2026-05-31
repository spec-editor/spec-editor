"""Gatherers: data collectors from storage."""

from pathlib import Path

import yaml

from src.export.pipeline import ExportData, ExportElement, ExportSection, Gatherer
from src.storage.adapter import StorageAdapter


class SRSGatherer(Gatherer):
    """Gathers SRS according to a template from srs_template.yaml."""

    def gather(
        self, storage: StorageAdapter, template_path: Path, project_path: Path
    ) -> ExportData:
        if not template_path.exists():
            return ExportData(
                doc_title="SRS", metadata={"warning": "template not found"}
            )

        with open(template_path, encoding="utf-8") as f:
            template = yaml.safe_load(f) or {}

        list_elems: dict[str, list[ExportElement]] = {}
        seen_ids: set[str] = set()
        seen_content: set[str] = set()
        duplicates = 0

        for summary in storage.list_all():
            try:
                el = storage.read_element(summary.id)
            except Exception:
                continue
            if el.id in seen_ids:
                duplicates += 1
                continue
            content_hash = el.content.strip() if el.content else None
            if content_hash and content_hash in seen_content:
                duplicates += 1
                continue
            seen_ids.add(el.id)
            if content_hash:
                seen_content.add(content_hash)

            aspect = el.aspect
            if aspect not in list_elems:
                list_elems[aspect] = []
            list_elems[aspect].append(
                ExportElement(
                    id=el.id,
                    title=el.title,
                    content=el.content,
                    aspect=el.aspect,
                    element_type=el.element_type,
                    status=el.status.value,
                    parent=el.parent,
                    children=list(el.children),
                    relationships=_serialize_rels(el.relationships),
                    back_refs={},
                )
            )

        # Back references and inline_steps
        id_to_title: dict[str, str] = {}
        back_refs, ui_to_steps = _build_refs(list_elems, id_to_title)

        for aspect, elems in list_elems.items():
            for el in elems:
                el.back_refs = back_refs.get(el.id, {})
                if el.element_type in ("screen", "widget", "control", "section"):
                    el.inline_steps = ui_to_steps.get(el.id, [])
        _propagate_inline_steps(list_elems, ui_to_steps)

        # Sections
        sections: list[ExportSection] = []
        for sec in template.get("sections", []):
            elements: list[ExportElement] = []
            for aspect_name in sec.get("aspects", []):
                elems = list_elems.get(aspect_name, [])
                elements.extend(_group_and_sort(elems, sec.get("group_by", "")))
            sections.append(
                ExportSection(
                    title=sec["title"],
                    number=sec.get("number", ""),
                    description=sec.get("description", ""),
                    elements=elements,
                )
            )

        # Diagrams (disabled)
        # diagrams = _build_diagrams(list_elems)
        # for sec in sections:
        #     for dkey, dcode in diagrams.items():
        #         if dkey in sec.title.lower():
        #             sec.diagram = dcode

        return ExportData(
            doc_title=template.get("title", "SRS"),
            sections=sections,
            metadata={"duplicates": duplicates},
        )


def _serialize_rels(relationships: dict) -> dict:
    result = {}
    for rel_type, entries in relationships.items():
        result[rel_type] = [{"role": e.role, "target": e.target} for e in entries]
    return result


def _build_refs(list_elems: dict, id_to_title: dict) -> tuple[dict, dict]:
    """Build back references and UI→steps map."""
    back_refs: dict = {}
    ui_to_steps: dict = {}

    for aspect, elems in list_elems.items():
        for el in elems:
            id_to_title[el.id] = el.title
            for rel_type, entries in el.relationships.items():
                for entry in entries:
                    target = (
                        entry["target"] if isinstance(entry, dict) else entry.target
                    )
                    back_refs.setdefault(target, {}).setdefault(rel_type, []).append(
                        {"id": el.id, "title": id_to_title.get(el.id, el.id)}
                    )
            if el.element_type == "step":
                for rel_type, entries in el.relationships.items():
                    if rel_type == "interacts_with":
                        for entry in entries:
                            target = (
                                entry["target"]
                                if isinstance(entry, dict)
                                else entry.target
                            )
                            ui_to_steps.setdefault(target, []).append(
                                {
                                    "id": el.id,
                                    "title": el.title,
                                    "content": el.content[:300] if el.content else "",
                                }
                            )
    return back_refs, ui_to_steps


def _propagate_inline_steps(list_elems: dict, ui_to_steps: dict) -> None:
    """Propagate inline_steps upward: control → widget → screen → section."""
    for aspect, elems in list_elems.items():
        parent_map: dict[str, list] = {}
        for el in elems:
            if el.parent:
                parent_map.setdefault(el.parent, []).append(el)

        def _collect(el_id, visited=None):
            if visited is None:
                visited = set()
            if el_id in visited:
                return []
            visited.add(el_id)
            steps = list(ui_to_steps.get(el_id, []))
            for child in parent_map.get(el_id, []):
                steps.extend(_collect(child.id, visited))
            return steps

        for el in elems:
            if el.element_type in ("screen", "section", "widget"):
                all_steps = _collect(el.id)
                if all_steps:
                    seen = set()
                    unique = []
                    for s in all_steps:
                        if s["id"] not in seen:
                            seen.add(s["id"])
                            unique.append(s)
                    el.inline_steps = unique


def _group_and_sort(
    elements: list[ExportElement], group_by: str
) -> list[ExportElement]:
    if not group_by:
        return elements
    all_ids = {e.id for e in elements}
    parent_map: dict[str, list[ExportElement]] = {}
    roots: list[ExportElement] = []
    child_of: dict[str, list[str]] = {}
    for el in elements:
        if el.parent and el.parent in all_ids:
            child_of.setdefault(el.parent, []).append(el.id)
        for cid in el.children:
            child_of.setdefault(el.id, []).append(cid)
    for el in elements:
        if el.parent and el.parent in all_ids:
            parent_map.setdefault(el.parent, []).append(el)
        else:
            roots.append(el)
    result: list[ExportElement] = []

    def _walk(node_id: str, group_key: str):
        for child in parent_map.get(node_id, []):
            child.group_key = group_key
            result.append(child)
            _walk(child.id, group_key)

    for root in roots:
        root.group_key = root.id
        result.append(root)
        _walk(root.id, root.id)
    return result


def _build_diagrams(list_elems: dict) -> dict[str, str]:
    diagrams = {}
    # Component diagram
    modules = list_elems.get("modules", [])
    if modules:
        lines = ["graph TD"]
        ids = set()
        for m in modules:
            sid = m.id.replace("-", "_")
            if sid not in ids:
                lines.append(f'    {sid}["{m.title}"]')
                ids.add(sid)
        for m in modules:
            sid = m.id.replace("-", "_")
            for rel_type, entries in m.relationships.items():
                if rel_type == "depends_on":
                    for entry in entries:
                        target = (
                            entry["target"] if isinstance(entry, dict) else entry.target
                        )
                        lines.append(f"    {sid} --> {target.replace('-', '_')}")
        diagrams["Gathering complete"] = "\n".join(lines)

    # Entity diagram
    entities = [
        e for e in list_elems.get("data_entities", []) if e.element_type == "entity"
    ]
    if len(entities) > 1:
        lines = ["erDiagram"]
        for e in entities:
            sid = e.id.replace("-", "_")
            lines.append(f"    {sid} {{")
            lines.append(f'        string title "{e.title}"')
            lines.append("    }")
        for e in entities:
            sid = e.id.replace("-", "_")
            for rel_type, entries in e.relationships.items():
                if rel_type == "references":
                    for entry in entries:
                        target = (
                            entry["target"] if isinstance(entry, dict) else entry.target
                        )
                        lines.append(
                            f"    {sid} ||--o{{ {target.replace('-', '_')} : references"
                        )
        diagrams["Gathering complete"] = "\n".join(lines)

    # Navigation diagram
    screens = [
        e for e in list_elems.get("user_interface", []) if e.element_type == "screen"
    ]
    if screens:
        lines = ["graph LR"]
        for s in screens:
            sid = s.id.replace("-", "_")
            lines.append(f'    {sid}["{s.title}"]')
        for s in screens:
            sid = s.id.replace("-", "_")
            for rel_type, entries in s.relationships.items():
                if rel_type == "navigates_to":
                    for entry in entries:
                        target = (
                            entry["target"] if isinstance(entry, dict) else entry.target
                        )
                        lines.append(f"    {sid} --> {target.replace('-', '_')}")
        diagrams["Mermaid diagram"] = "\n".join(lines)

    return diagrams
