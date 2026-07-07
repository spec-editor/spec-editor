"""Mermaid.js renderer — renders spec graph as interactive HTML."""

import webbrowser
from pathlib import Path

import frontmatter


class MermaidRenderer:
    """Build Mermaid diagrams from spec-editor projects and render to HTML."""

    # Color scheme by aspect
    ASPECT_COLORS = {
        "modules": "#4A90D9",
        "user_scenarios": "#50B86C",
        "scenarios": "#50B86C",
        "data_entities": "#E8A838",
        "entities": "#E8A838",
        "non_functional": "#D94A4A",
        "nfr": "#D94A4A",
        "user_interface": "#9B59B6",
        "ui": "#9B59B6",
        "implementation": "#E67E22",
        "metrics": "#1ABC9C",
        "decisions": "#1ABC9C",
        "sources": "#95A5A6",
    }
    DEFAULT_COLOR = "#95A5A6"

    # Edge colors by relationship type
    REL_COLORS = {
        "consists_of": "#4A90D9",  # blue — structural
        "depends_on": "#E8A838",  # orange — dependency
        "derived_from": "#95A5A6",  # grey — traceability
        "refines": "#50B86C",  # green — refinement
        "interacts_with": "#9B59B6",  # purple — interaction
        "applies_to": "#D94A4A",  # red — constraints
        "implements": "#E67E22",  # dark orange
        "covered_by": "#1ABC9C",  # teal
        "next_step": "#50B86C",  # green
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_mermaid(
        self,
        project_path: Path,
        element_id: str | None = None,
        aspect_name: str | None = None,
        diagram_type: str = "graph",
        relation_scope: str | None = None,
    ) -> str:
        """Build a Mermaid diagram from the project's aspects/.

        Args:
            element_id: if set, show only this element and its direct connections
            aspect_name: if set, show all elements in this aspect and their relationships
            diagram_type: "graph" (default), "flowchart", "class", "er", "sequence",
                         "state", "gantt", "pie", "mindmap", "timeline", "sankey"
            relation_scope: if set, filter edges: "internal" (same aspect only),
                           "external" (different aspects only), None (all)

        Returns a complete Mermaid diagram string.
        """
        elements = self._load_elements(project_path)
        if not elements:
            return f"{diagram_type} TD\n  EMPTY[No elements found]\n"

        # Build element lookup
        el_by_id = {el["id"]: el for el in elements}

        # Filter elements
        # Apply element_id filter: focused element + 1-hop outgoing
        # relationships + its own hierarchy chain (parent + children).
        if element_id:
            related_ids = {element_id}

            # 1. Direct outgoing relationships (what this element points to)
            if element_id in el_by_id:
                for _rel_type, targets in (
                    el_by_id[element_id].get("relationships", {}).items()
                ):
                    for t in targets:
                        tid = t["target"] if isinstance(t, dict) else t
                        related_ids.add(tid)

            # 2. Parent chain (walk up)
            current = element_id
            while current:
                el = el_by_id.get(current)
                pid = el.get("parent") if el else None
                if pid and pid in el_by_id:
                    related_ids.add(pid)
                    current = pid
                else:
                    break

            # 3. Children chain for the focused element (recursive)
            def _add_children(eid: str, depth: int = 10) -> None:
                if depth <= 0:
                    return
                el = el_by_id.get(eid)
                if not el:
                    return
                for cid in el.get("children", []):
                    if cid not in related_ids and cid in el_by_id:
                        related_ids.add(cid)
                        _add_children(cid, depth - 1)

            _add_children(element_id)

            # 4. relation_scope element filter: remove elements from
            #    wrong aspects before passing to diagram builder.
            if relation_scope:
                focus_el = el_by_id.get(element_id, {})
                focus_aspect = focus_el.get("aspect", "")
                if relation_scope == "internal":
                    elements = [
                        el for el in elements
                        if el.get("aspect") == focus_aspect
                    ]
                elif relation_scope == "external":
                    elements = [
                        el for el in elements
                        if el.get("aspect") != focus_aspect or el["id"] == element_id
                    ]

            elements = [el for el in elements if el["id"] in related_ids]
        elif aspect_name:
            elements = [el for el in elements if el.get("aspect") == aspect_name]

        if not elements:
            return f"{diagram_type} TD\n  EMPTY[No matching elements for '{element_id or aspect_name}']\n"

        # Dispatch to type-specific builder
        dt = diagram_type.lower() if diagram_type else "graph"
        if dt in ("graph", "flowchart"):
            return self._build_graph(elements, el_by_id, element_id, aspect_name, relation_scope)
        elif dt == "class":
            return self._build_class_diagram(elements, project_path)
        elif dt == "er":
            return self._build_er_diagram(elements)
        elif dt == "state":
            return self._build_state_diagram(elements, el_by_id)
        elif dt == "sequence":
            return self._build_sequence_diagram(elements, el_by_id)
        elif dt == "gantt":
            return self._build_gantt(elements)
        elif dt == "pie":
            return self._build_pie(elements)
        elif dt == "mindmap":
            return self._build_mindmap(elements, el_by_id, project_path)
        elif dt == "timeline":
            return self._build_timeline(elements)
        elif dt == "sankey":
            return self._build_sankey(elements, el_by_id)
        elif dt == "cycle":
            return self._build_cycle(elements, el_by_id, element_id)
        else:
            return self._build_graph(elements, el_by_id, element_id, aspect_name)

    def render_html(
        self,
        project_path: Path,
        output_path: Path | None = None,
        element_id: str | None = None,
        aspect_name: str | None = None,
    ) -> Path:
        """Render spec as self-contained interactive HTML.

        Opens in default browser when output_path is None (writes to temp).
        """
        mermaid = self.build_mermaid(
            project_path, element_id=element_id, aspect_name=aspect_name
        )
        title = f"Spec Editor — {project_path.name}"
        if element_id:
            title += f" — {element_id}"
        elif aspect_name:
            title += f" — {aspect_name}"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
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
<h1>{title}</h1>
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
    # Hierarchy grouping (shared by ER, Class diagrams)
    # ------------------------------------------------------------------

    class HierarchyGroups:
        """Result of _group_by_hierarchy()."""
        def __init__(self):
            self.entities: list[dict] = []       # elements with children[] — structural
            self.standalones: list[dict] = []    # no parent, no children — leaf roots
            self.leaf_ids: set[str] = set()      # IDs of all leaf (non-structural) elements
            self.children_of: dict[str, list[dict]] = {}  # entity_id → direct leaf children

    def _group_by_hierarchy(self, elements: list[dict]) -> HierarchyGroups:
        """Classify elements by structural role based solely on children[].

        Structural (entity): element has children[] (non-empty).
        Leaf (field/step):    element has no children[] (empty or absent).

        Leaves are grouped under their nearest structural ancestor
        (not necessarily the direct parent — this handles arbitrary nesting depth).

        Returns HierarchyGroups with entities, standalones, and children_of mapping.
        """
        result = self.HierarchyGroups()
        el_by_id = {el["id"]: el for el in elements}

        def _find_nearest_structural(eid: str) -> str | None:
            """Walk up parent chain to find nearest structural ancestor."""
            el = el_by_id.get(eid)
            if not el:
                return None
            pid = el.get("parent")
            if not pid:
                return None
            parent = el_by_id.get(pid)
            if not parent:
                return None
            if parent.get("children"):
                return pid  # structural
            return _find_nearest_structural(pid)  # keep walking up

        for el in elements:
            eid = el["id"]
            if el.get("children"):
                # Has children → structural entity
                result.entities.append(el)
            else:
                # No children → leaf
                result.leaf_ids.add(eid)

        # Group leaves under their nearest structural ancestor
        for eid in result.leaf_ids:
            nearest = _find_nearest_structural(eid)
            if nearest:
                result.children_of.setdefault(nearest, []).append(el_by_id[eid])
            else:
                # Leaf with no structural ancestor → standalone entity
                result.standalones.append(el_by_id[eid])

        # Deduplicate children (a leaf may appear multiple times if it has
        # multiple paths in the hierarchy).
        for eid in result.children_of:
            seen = set()
            unique = []
            for child in result.children_of[eid]:
                cid = child["id"]
                if cid not in seen:
                    seen.add(cid)
                    unique.append(child)
            result.children_of[eid] = unique

        return result

    # ------------------------------------------------------------------
    # Diagram type builders
    # ------------------------------------------------------------------

    def _build_graph(
        self,
        elements: list[dict],
        el_by_id: dict[str, dict],
        element_id: str | None,
        aspect_name: str | None,
        relation_scope: str | None = None,
    ) -> str:
        """Build a graph TD / flowchart LR diagram."""
        lines = ["graph LR"]

        edges: set[tuple[str, str, str]] = set()
        node_ids: set[str] = set()

        for el in elements:
            eid = el["id"]
            node_ids.add(eid)
            aspect = el.get("aspect", "")
            title = el.get("title", eid)
            color = self.ASPECT_COLORS.get(aspect, self.DEFAULT_COLOR)
            safe_title = title.replace('"', "'")
            safe_eid = eid.replace("-", "_")
            lines.append(f'  {safe_eid}["{safe_title}"]')
            lines.append(f"  style {safe_eid} fill:{color},stroke:#333,color:#fff")

            src_aspect = el.get("aspect", "")
            for rel_type, targets in el.get("relationships", {}).items():
                for t in targets:
                    target_id = t["target"] if isinstance(t, dict) else t

                    # relation_scope filter: compare source and target aspects
                    if relation_scope:
                        tgt_el = el_by_id.get(target_id, {})
                        tgt_aspect = tgt_el.get("aspect", "")
                        same_aspect = src_aspect == tgt_aspect
                        if relation_scope == "internal" and not same_aspect:
                            continue
                        if relation_scope == "external" and same_aspect:
                            continue
                        # When relation_scope is active, allow cross-aspect
                        # targets — they will be rendered as edge destinations.
                    elif element_id or aspect_name:
                        # Without relation_scope, keep current behaviour:
                        # skip targets not in the filtered element set.
                        if target_id not in node_ids and target_id not in {
                            e["id"] for e in elements
                        }:
                            continue

                    edges.add((eid, target_id, rel_type))

        # ── Implicit hierarchy edges from parent / children fields ──
        # Processed AFTER the main loop so node_ids is fully populated,
        # handling elements declared in any order within the aspect.
        # Only adds child→parent edges (not bidirectional) to avoid clutter.
        for el in elements:
            pid = el.get("parent")
            if pid and pid in node_ids:
                edges.add((el["id"], pid, "child_of"))

        # When relation_scope is active, add any cross-aspect target nodes
        # that are referenced by edges but not yet in the diagram.
        if relation_scope:
            for (_from, to_id, _label) in edges:
                if to_id not in node_ids and to_id in el_by_id:
                    node_ids.add(to_id)
                    tgt = el_by_id[to_id]
                    tgt_aspect = tgt.get("aspect", "")
                    tgt_title = tgt.get("title", to_id)
                    color = self.ASPECT_COLORS.get(tgt_aspect, self.DEFAULT_COLOR)
                    safe_title = tgt_title.replace('"', "'")
                    safe_eid = to_id.replace("-", "_")
                    lines.append(f'  {safe_eid}["{safe_title}"]')
                    lines.append(
                        f"  style {safe_eid} fill:{color},stroke:#333,color:#fff"
                    )

        edge_lines = []
        for i, (from_id, to_id, label) in enumerate(sorted(edges)):
            safe_from = from_id.replace("-", "_")
            safe_to = to_id.replace("-", "_")
            edge_lines.append(f"  {safe_from} -->|{label}| {safe_to}")

        return "\n".join(lines + edge_lines)

    def _build_class_diagram(self, elements: list[dict], project_path: Path) -> str:
        """Build a classDiagram using hierarchy grouping.

        Elements with children[] are classes. Elements without children are
        attributes/methods and are grouped inside their nearest ancestor class.
        Standalone elements (no parent, no children) become empty classes.
        """
        groups = self._group_by_hierarchy(elements)
        lines = ["classDiagram"]

        # Render classes with their attributes
        for entity in groups.entities:
            eid = entity["id"]
            safe_eid = eid.replace("-", "_")
            title = entity.get("title", eid).replace('"', "'")
            lines.append(f"  class {safe_eid} {{")
            lines.append(f"    +String id = {eid}")
            lines.append(f"    +String title = {title}")

            # Children that are leaves → class members
            for child in groups.children_of.get(eid, []):
                cid = child["id"]
                ctitle = child.get("title", cid).replace('"', "'")
                dtype = child.get("data_type", "String")
                lines.append(f"    +{dtype} {ctitle}")

            lines.append(f"  }}")

            # Color by aspect
            aspect = entity.get("aspect", "")
            color = self.ASPECT_COLORS.get(aspect, self.DEFAULT_COLOR)
            lines.append(f"  style {safe_eid} fill:{color},stroke:#333,color:#fff")

        # Standalone elements → empty classes
        for el in groups.standalones:
            eid = el["id"]
            safe_eid = eid.replace("-", "_")
            title = el.get("title", eid).replace('"', "'")
            lines.append(f"  class {safe_eid} {{")
            lines.append(f"    +String id = {eid}")
            lines.append(f"    +String title = {title}")
            lines.append(f"  }}")
            aspect = el.get("aspect", "")
            color = self.ASPECT_COLORS.get(aspect, self.DEFAULT_COLOR)
            lines.append(f"  style {safe_eid} fill:{color},stroke:#333,color:#fff")

        # Relationships — only between structural elements (entities + standalones)
        structural_ids = {e["id"] for e in groups.entities} | {s["id"] for s in groups.standalones}
        el_by_id = {el["id"]: el for el in elements}
        for eid in structural_ids:
            el = el_by_id.get(eid)
            if not el:
                continue
            src_safe = eid.replace("-", "_")
            for rel_type, targets in el.get("relationships", {}).items():
                for t in targets:
                    tid = t["target"] if isinstance(t, dict) else t
                    if tid not in structural_ids:
                        continue
                    role = t.get("role", "") if isinstance(t, dict) else ""
                    label = f"{rel_type}"
                    if role:
                        label += f" ({role})"
                    tgt_safe = tid.replace("-", "_")
                    lines.append(f"  {src_safe} --> {tgt_safe} : {label}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # ER diagram helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_er_identifier(s: str) -> str:
        """Sanitize a string for use as a Mermaid ER identifier.

        Mermaid ER entity names and field type/name must be valid identifiers:
        alphanumeric + underscore only. All other characters are replaced with
        underscore, and consecutive underscores are collapsed.
        """
        import re
        result = re.sub(r"[^a-zA-Z0-9_]", "_", s)
        result = re.sub(r"_+", "_", result)
        result = result.strip("_")
        return result or "_"  # never return empty

    def _build_er_diagram(self, elements: list[dict]) -> str:
        """Build an entity-relationship diagram using hierarchy grouping.

        Elements with children[] are entities. Elements without children are
        fields/attributes and are grouped inside their nearest ancestor entity.
        Standalone elements (no parent, no children) become entities without attributes.

        Only relationships between entity-level elements are shown.
        """
        groups = self._group_by_hierarchy(elements)
        lines = ["erDiagram"]

        el_by_id = {el["id"]: el for el in elements}

        # Render entities with their field children
        for entity in groups.entities:
            eid = entity["id"]
            title = entity.get("title", eid)
            safe_title = self._sanitize_er_identifier(title)

            lines.append(f"  {safe_title} {{")

            children = groups.children_of.get(eid, [])
            if children:
                for child in children:
                    ctitle = child.get("title", child["id"])
                    dtype = child.get("data_type", "string")
                    safe_ctitle = self._sanitize_er_identifier(ctitle)
                    safe_dtype = self._sanitize_er_identifier(dtype)
                    lines.append(f"    {safe_dtype} {safe_ctitle}")
            else:
                # Entity with no leaf children: show it exists
                eltype = entity.get("element_type", "")
                lines.append(f'    string id "{eid}"')
                if eltype:
                    lines.append(f'    string element_type "{eltype}"')

            lines.append(f"  }}")

        # Standalone → entities without attributes
        for el in groups.standalones:
            eid = el["id"]
            title = el.get("title", eid)
            safe_title = self._sanitize_er_identifier(title)
            lines.append(f"  {safe_title} {{")
            lines.append(f'    string id "{eid}"')
            lines.append(f"  }}")

        # Relationships — only between structural elements
        structural_ids = {e["id"] for e in groups.entities} | {s["id"] for s in groups.standalones}

        for eid in structural_ids:
            el = el_by_id.get(eid)
            if not el:
                continue
            src_title = self._sanitize_er_identifier(el.get("title", eid))
            for rel_type, targets in el.get("relationships", {}).items():
                for t in targets:
                    tid = t["target"] if isinstance(t, dict) else t
                    if tid not in structural_ids:
                        continue
                    tgt_el = el_by_id.get(tid)
                    if not tgt_el:
                        continue
                    tgt_title = self._sanitize_er_identifier(tgt_el.get("title", tid))
                    role = t.get("role", "") if isinstance(t, dict) else ""
                    label = f"{rel_type}"
                    if role:
                        label += f" ({role})"
                    lines.append(f'  {src_title} ||--o{{ {tgt_title} : "{label}"')

        return "\n".join(lines)

    def _build_state_diagram(
        self, elements: list[dict], el_by_id: dict[str, dict]
    ) -> str:
        """Build a state diagram showing element statuses and transitions."""
        lines = ["stateDiagram-v2"]

        # Collect unique statuses
        statuses = sorted({el.get("status", "draft") for el in elements})
        for s in statuses:
            lines.append(f'  state "{s}" as {s}')

        # Show elements in each status
        for st in statuses:
            els_in_status = [el for el in elements if el.get("status", "draft") == st]
            if els_in_status:
                ids = ", ".join(e["id"] for e in els_in_status)
                lines.append(f"  note right of {st}")
                lines.append(f"    {ids}")
                lines.append(f"  end note")

        # Typical status transitions
        lines.append(f"  [*] --> draft")
        if "reviewed" in statuses:
            lines.append(f"  draft --> reviewed")
        if "confirmed" in statuses:
            lines.append(f"  reviewed --> confirmed")
        if "deprecated" in statuses:
            lines.append(f"  confirmed --> deprecated")
            lines.append(f"  deprecated --> [*]")
        lines.append(f"  draft --> [*]")

        return "\n".join(lines)

    def _build_sequence_diagram(
        self, elements: list[dict], el_by_id: dict[str, dict]
    ) -> str:
        """Build a sequence diagram showing element interaction flow."""
        lines = ["sequenceDiagram"]

        # Use participants from the elements set
        participants_seen: set[str] = set()
        for el in elements:
            eid = el["id"]
            if eid not in participants_seen:
                participants_seen.add(eid)
                title = el.get("title", eid)
                lines.append(f"  participant {eid} as {title}")

        # Messages from relationships
        for el in elements:
            for rel_type, targets in el.get("relationships", {}).items():
                for t in targets:
                    tid = t["target"] if isinstance(t, dict) else t
                    role = t.get("role", "") if isinstance(t, dict) else ""
                    note = f"{rel_type}"
                    if role:
                        note += f" ({role})"
                    lines.append(f"  {el['id']} ->> {tid}: {note}")

        return "\n".join(lines)

    def _build_gantt(self, elements: list[dict]) -> str:
        """Build a Gantt chart showing elements grouped by aspect."""
        import datetime

        today = datetime.date.today().isoformat()
        lines = ["gantt", f"  title Specification Elements", f"  dateFormat YYYY-MM-DD"]

        # Group by aspect
        by_aspect: dict[str, list[dict]] = {}
        for el in elements:
            aspect = el.get("aspect", "other")
            by_aspect.setdefault(aspect, []).append(el)

        # Gantt: flat list with :after chain.
        # Mermaid 11 limitations:
        #  - Sections break :after references (can't cross section boundaries).
        #  - Hyphens AND underscores in :after IDs are parsed as operators.
        # Workaround: strip all separators from IDs (MOD003), no sections.
        prev_id = None
        for aspect, els in sorted(by_aspect.items()):
            for el in els:
                eid = el["id"]
                safe_eid = eid.replace("-", "")
                title = el.get("title", eid)[:40]
                safe_title = title.replace(",", " ").replace(":", " ")
                label = safe_title
                if prev_id:
                    safe_prev = prev_id.replace("-", "")
                    lines.append(f"  {safe_eid}: {label} :after {safe_prev}, 1d")
                else:
                    lines.append(f"  {safe_eid}: {label} :{today}, 1d")
                prev_id = eid

        return "\n".join(lines)

    def _build_pie(self, elements: list[dict]) -> str:
        """Build a pie chart showing elements per aspect."""
        lines = ["pie title Elements per Aspect"]

        by_aspect: dict[str, int] = {}
        for el in elements:
            aspect = el.get("aspect", "other")
            by_aspect[aspect] = by_aspect.get(aspect, 0) + 1

        for aspect, count in sorted(by_aspect.items()):
            lines.append(f'  "{aspect}" : {count}')

        return "\n".join(lines)

    def _build_mindmap(
        self,
        elements: list[dict],
        el_by_id: dict[str, dict],
        project_path: Path,
    ) -> str:
        """Build a mindmap with project root, aspects, and elements."""
        lines = ["mindmap"]
        lines.append(f"  root(({project_path.name}))")

        # Group by aspect
        by_aspect: dict[str, list[dict]] = {}
        for el in elements:
            aspect = el.get("aspect", "other")
            by_aspect.setdefault(aspect, []).append(el)

        for aspect, els in sorted(by_aspect.items()):
            lines.append(f"    {aspect}")
            for el in els:
                eid = el["id"]
                title = el.get("title", eid)[:50]
                safe_title = title.replace('"', "'")
                lines.append(f"      {eid}")
                lines.append(f"        {safe_title}")

        return "\n".join(lines)

    def _build_timeline(self, elements: list[dict]) -> str:
        """Build a timeline grouped by aspect."""
        lines = ["timeline", "  title Specification Elements"]

        # Group by aspect
        by_aspect: dict[str, list[dict]] = {}
        for el in elements:
            aspect = el.get("aspect", "other")
            by_aspect.setdefault(aspect, []).append(el)

        for aspect, els in sorted(by_aspect.items()):
            ids = ", ".join(e["id"] for e in els)
            titles = "; ".join(e.get("title", e["id"])[:30] for e in els)
            lines.append(f"  section {aspect}")
            lines.append(f"    {ids} : {titles}")

        return "\n".join(lines)

    def _build_sankey(self, elements: list[dict], el_by_id: dict[str, dict]) -> str:
        """Build a Sankey diagram showing relationships between aspects.

        Mermaid Sankey requires a strictly acyclic (DAG) graph. We:
        1. Count all flows between aspects
        2. Merge bidirectional pairs into net direction
        3. Topologically sort nodes; remove back-edges that create cycles
        """
        lines = ["sankey-beta"]

        # 1. Count flows between aspects
        aspect_flows: dict[tuple[str, str], int] = {}
        for el in elements:
            src_aspect = el.get("aspect", "other")
            for _rel_type, targets in el.get("relationships", {}).items():
                for t in targets:
                    tid = t["target"] if isinstance(t, dict) else t
                    tgt = el_by_id.get(tid, {})
                    tgt_aspect = tgt.get("aspect", "other") if tgt else "other"
                    if src_aspect == tgt_aspect:
                        continue
                    key = (src_aspect, tgt_aspect)
                    aspect_flows[key] = aspect_flows.get(key, 0) + 1

        # 2. Merge bidirectional flows
        merged: dict[tuple[str, str], int] = {}
        processed: set[tuple[str, str]] = set()
        for (src, tgt), count in sorted(aspect_flows.items()):
            if (src, tgt) in processed:
                continue
            reverse = (tgt, src)
            reverse_count = aspect_flows.get(reverse, 0)
            if reverse_count > 0:
                processed.add(reverse)
                net = count - reverse_count
                if net > 0:
                    merged[(src, tgt)] = net
                elif net < 0:
                    merged[(tgt, src)] = -net
                # net == 0: omit
            else:
                merged[(src, tgt)] = count

        # 3. Greedy DAG: add edges in descending weight order,
        #    skipping those that would create a cycle (detected via DFS).
        # Collect nodes
        nodes: set[str] = set()
        for (s, t) in merged:
            nodes.add(s)
            nodes.add(t)

        # Sort edges by weight descending
        sorted_edges = sorted(merged.items(), key=lambda x: -x[1])

        # Build adjacency for cycle detection
        adj_dag: dict[str, set[str]] = {n: set() for n in nodes}
        acyclic: dict[tuple[str, str], int] = {}

        def _would_cycle(src: str, tgt: str) -> bool:
            """Check if adding src→tgt creates a cycle (DFS from tgt back to src)."""
            visited: set[str] = set()
            stack = [tgt]
            while stack:
                v = stack.pop()
                if v == src:
                    return True
                if v in visited:
                    continue
                visited.add(v)
                stack.extend(adj_dag.get(v, set()))
            return False

        for (src, tgt), count in sorted_edges:
            if src == tgt:
                continue
            if not _would_cycle(src, tgt):
                acyclic[(src, tgt)] = count
                adj_dag[src].add(tgt)

        for (src, tgt), count in sorted(acyclic.items()):
            lines.append(f"  {src},{tgt},{count}")

        return "\n".join(lines)

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
                # Skip elements without required 'id' field (e.g. README.md, template files)
                if "id" not in el:
                    continue
                # Normalize relationships from various frontmatter formats
                if "relationships" not in el:
                    rels = {}
                    for key in (
                        "relates_to",
                        "implements",
                        "derived_from",
                        "covered_by",
                        "depends_on",
                    ):
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
                f"<span>{aspect}</span>"
                f"</div>"
            )
        return "\n".join(items)

    def _build_cycle(
        self,
        elements: list[dict],
        el_by_id: dict[str, dict],
        focus_id: str | None,
    ) -> str:
        """Build a Mermaid graph showing the cycle traceability chain.

        Shows: bug → derived_from → affected requirement → @implements → code
        → StructuredLogEmitter → logs → LogAnalyzer → bug (cycle closed).
        """
        lines = ["graph LR"]
        added_nodes: set[str] = set()
        added_edges: set[tuple[str, str, str]] = set()  # (src, label, tgt)

        def _node_id(eid: str) -> str:
            return eid.replace("-", "_").replace(".", "_")

        def _label(el: dict) -> str:
            tid = el.get("title", el["id"])[:60]
            return tid.replace('"', "'")

        def _add_node(eid: str, el: dict, color: str = "") -> str:
            nid = _node_id(eid)
            if nid not in added_nodes:
                lbl = _label(el)
                if color:
                    lines.append(f'    {nid}["{lbl}"]:::{color}')
                else:
                    lines.append(f'    {nid}["{lbl}"]')
                added_nodes.add(nid)
            return nid

        def _add_edge(src: str, label: str, tgt: str) -> None:
            key = (src, label, tgt)
            if key not in added_edges:
                lines.append(f'    {_node_id(src)} -->|"{label}"| {_node_id(tgt)}')
                added_edges.add(key)

        # Determine focus: specific bug, specific module, or overview.
        bug_elements = [e for e in elements if e["id"].startswith("SRC-BUG-")]

        if focus_id:
            # Show chain for focused element.
            if focus_id not in el_by_id:
                lines.append(f'    NONE["Element {focus_id} not found"]')
                return "\n".join(lines)

            focus_el = el_by_id[focus_id]

            if focus_id.startswith("SRC-BUG-"):
                # Bug → affected requirements → code → logs → back to bug.
                _add_node(focus_id, focus_el, "bug")

                for rtype, targets in focus_el.get("relationships", {}).items():
                    for t in targets:
                        tid = t["target"] if isinstance(t, dict) else t
                        if tid in el_by_id:
                            _add_node(tid, el_by_id[tid], "requirement")
                            _add_edge(focus_id, rtype, tid)

                            # Find elements that implement this requirement.
                            for eid, el in el_by_id.items():
                                for rt, tgs in el.get("relationships", {}).items():
                                    for tg in tgs:
                                        tgid = (
                                            tg["target"] if isinstance(tg, dict) else tg
                                        )
                                        if tgid == tid and rt == "implements":
                                            _add_node(eid, el, "code")
                                            _add_edge(eid, rt, tid)
                                            # Add log node.
                                            log_id = f"logs_{tid}"
                                            if log_id not in added_nodes:
                                                lines.append(
                                                    f'    {_node_id(log_id)}["logs/{tid}/<br/>structured.jsonl"]'
                                                )
                                                added_nodes.add(_node_id(log_id))
                                            _add_edge(
                                                eid, "StructuredLogEmitter", log_id
                                            )
                                            _add_edge(log_id, "LogAnalyzer", focus_id)

            elif focus_id.startswith("MOD-"):
                # Module → all bugs affecting it.
                _add_node(focus_id, focus_el, "requirement")

                for bug in bug_elements:
                    for rtype, targets in bug.get("relationships", {}).items():
                        for t in targets:
                            tid = t["target"] if isinstance(t, dict) else t
                            if tid == focus_id:
                                _add_node(bug["id"], bug, "bug")
                                _add_edge(bug["id"], rtype, focus_id)

                                # Spec changes derived from this bug.
                                for eid, el in el_by_id.items():
                                    for ref in el.get("derived_from", []):
                                        if ref == bug["id"] and eid != bug["id"]:
                                            _add_node(eid, el, "fix")
                                            _add_edge(eid, "derived_from", bug["id"])

            else:
                _add_node(focus_id, focus_el)
        else:
            # Overview: show abstract loop structure.
            lines.extend(
                [
                    '    REQ["Requirements<br/>(MOD, SCN, NFR)"]',
                    '    CODE["Code<br/>(@implements)"]',
                    '    LOGS["Structured Logs<br/>(JSON-lines)"]',
                    '    BUG["Bug Reports<br/>(SRC-BUG-*)"]',
                    '    SPEC["Spec Updates<br/>(NFR, STP)"]',
                    "",
                    '    REQ -->|"@implements"| CODE',
                    '    CODE -->|"StructuredLogEmitter"| LOGS',
                    '    LOGS -->|"LogAnalyzer"| BUG',
                    '    BUG -->|"derived_from"| REQ',
                    '    BUG -->|"SpecUpdater"| SPEC',
                    '    SPEC -.->|"next deploy"| CODE',
                    "",
                    "    classDef requirement fill:#4a9,stroke:#333,color:#fff",
                    "    classDef code fill:#49e,stroke:#333,color:#fff",
                    "    classDef bug fill:#e44,stroke:#333,color:#fff",
                    "    classDef fix fill:#49e,stroke:#333,color:#fff",
                    "    classDef log fill:#999,stroke:#333,color:#fff",
                    "",
                    "    class REQ requirement",
                    "    class CODE code",
                    "    class LOGS log",
                    "    class BUG bug",
                    "    class SPEC fix",
                ]
            )
            return "\n".join(lines)

        # Add style classes.
        lines.append("")
        lines.append("    classDef requirement fill:#4a9,stroke:#333,color:#fff")
        lines.append("    classDef code fill:#49e,stroke:#333,color:#fff")
        lines.append("    classDef bug fill:#e44,stroke:#333,color:#fff")
        lines.append("    classDef fix fill:#49e,stroke:#333,color:#fff")
        lines.append("    classDef log fill:#999,stroke:#333,color:#fff")

        return "\n".join(lines)
