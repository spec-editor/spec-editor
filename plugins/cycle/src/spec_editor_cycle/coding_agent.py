"""Spec context builder — builds Markdown context from specification elements.

Used by OpenCodeProvider to provide coding agents with full spec context
including bugs, modules, NFRs, and data entities.

Usage::

    from spec_editor_cycle.coding_agent import build_spec_context

    context = build_spec_context(storage, "Fix SRC-BUG-001")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------
# Spec context builder — includes bugs, modules, NFRs, entities
# ------------------------------------------------------------------


def build_spec_context(storage: Any, task: str) -> str:
    """Build a Markdown context document from the specification.

    Priority order:
    1. Active bugs (SRC-BUG-* with status=draft) — error samples included
    2. Affected modules (marked 🔴 if they have active bugs)
    3. NFRs derived from bugs
    4. Data entities
    """
    all_elements = storage.list_all()
    lines = [
        "# Specification Context for Coding Agent",
        "",
        f"**Task:** {task}",
        "",
    ]

    # ═══════════════════════════════════════════════════════════════
    # 1. Bugs — the most important section
    # ═══════════════════════════════════════════════════════════════
    bug_summaries = [s for s in all_elements if s.id.startswith("SRC-BUG-")]
    draft_bugs: list[Any] = []
    all_bugs: list[Any] = []

    for bs in bug_summaries:
        try:
            bug = storage.read_element(bs.id)
            if bug.status.value == "draft":
                draft_bugs.append(bug)
            all_bugs.append(bug)
        except Exception:
            pass

    if draft_bugs:
        lines.append("## 🐛 Active Bugs (MUST FIX)")
        lines.append("")
        for bug in draft_bugs:
            lines.append(f"### {bug.id}: {bug.title}")
            lines.append(
                f"**Severity:** {_get_tag(bug, 'critical') or _get_tag(bug, 'high') or _get_tag(bug, 'medium') or 'unknown'}"
            )
            lines.append(f"**Affected:** {', '.join(bug.derived_from)}")
            lines.append("")
            if bug.content:
                lines.append(bug.content[:3000])
            lines.append("")
            lines.append("---")
            lines.append("")
    elif all_bugs:
        lines.append("## Recent Bugs (already reviewed)")
        lines.append("")
        for bug in all_bugs[-5:]:
            lines.append(f"- {bug.id}: {bug.title} — {bug.status.value}")
        lines.append("")

    # ═══════════════════════════════════════════════════════════════
    # 2. Modules — affected ones first
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Modules")
    lines.append("")
    modules = [s for s in all_elements if s.element_type == "module"]

    bug_module_ids = set()
    for bug in draft_bugs:
        for ref in bug.derived_from:
            if ref.startswith("MOD-"):
                bug_module_ids.add(ref)

    for mod_summary in modules:
        is_affected = mod_summary.id in bug_module_ids
        try:
            mod = storage.read_element(mod_summary.id)
            marker = " 🔴 HAS ACTIVE BUGS" if is_affected else ""
            lines.append(f"### {mod.id}: {mod.title}{marker}")
            if mod.content:
                lines.append(mod.content[:500])
            lines.append("")

            for rel_type, entries in mod.relationships.items():
                if entries:
                    refs = ", ".join(e.target for e in entries[:5])
                    lines.append(f"  {rel_type}: {refs}")
            lines.append("")
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # 3. NFRs
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Non-Functional Requirements")
    lines.append("")
    nfrs = [
        s
        for s in all_elements
        if s.element_type == "requirement" and s.aspect == "non_functional"
    ]
    for nfr_summary in nfrs[:10]:
        try:
            nfr = storage.read_element(nfr_summary.id)
            lines.append(f"### {nfr.id}: {nfr.title}")
            if nfr.content:
                lines.append(nfr.content[:300])
            lines.append("")
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # 4. Data Entities
    # ═══════════════════════════════════════════════════════════════
    lines.append("## Data Entities")
    lines.append("")
    entities = [s for s in all_elements if s.element_type == "entity"]
    for ent_summary in entities[:20]:
        lines.append(f"- {ent_summary.id}: {ent_summary.title}")
    lines.append("")

    return "\n".join(lines)


def _get_tag(element: Any, tag: str) -> str:
    """Get a tag value from an element's tags list."""
    for t in element.tags:
        if t == tag:
            return tag
    return ""


# ------------------------------------------------------------------
# Focused context per module + dependency ordering
# ------------------------------------------------------------------


def build_module_context(storage: Any, module_id: str) -> str:
    """Build a focused spec context for a single module.

    Includes only what is relevant to THIS module: the module itself,
    scenarios it implements, modules it depends on, NFRs that apply,
    data entities, and existing implementation artefacts.
    """
    all_elements = storage.list_all()

    try:
        mod = storage.read_element(module_id)
    except KeyError:
        return f"# Module {module_id} not found"

    lines = [
        f"# Spec Context: {module_id} — {mod.title}",
        "",
        f"**Task:** Generate code for {module_id}",
        "",
        "## Module",
        "",
    ]

    if mod.content:
        lines.append(mod.content)
        lines.append("")

    implements_ids = []
    depends_on_ids = []
    for rel_type, entries in mod.relationships.items():
        if entries:
            refs = [e.target for e in entries]
            lines.append(f"**{rel_type}:** {', '.join(refs)}")
            if rel_type == "implements":
                implements_ids.extend(refs)
            elif rel_type == "depends_on":
                depends_on_ids.extend(refs)
    lines.append("")

    # Scenarios.
    if implements_ids:
        lines.append("## Scenarios (implements)")
        lines.append("")
        for sid in implements_ids:
            try:
                el = storage.read_element(sid)
                lines.append(f"### {el.id}: {el.title}")
                if el.content:
                    lines.append(el.content[:500])
                lines.append("")
            except KeyError:
                pass

    # Dependencies.
    if depends_on_ids:
        lines.append("## Dependencies (depends_on)")
        lines.append("")
        for did in depends_on_ids:
            try:
                dep = storage.read_element(did)
                lines.append(f"### {dep.id}: {dep.title}")
                if dep.content:
                    lines.append(dep.content[:300])
                lines.append("")
            except KeyError:
                pass

    # NFRs that apply to this module.
    nfrs = [
        s
        for s in all_elements
        if s.element_type == "requirement" and s.aspect == "non_functional"
    ]
    relevant_nfrs = []
    for nfr_summary in nfrs:
        try:
            nfr = storage.read_element(nfr_summary.id)
            for _rel_type, entries in nfr.relationships.items():
                for e in entries:
                    if e.target == module_id:
                        relevant_nfrs.append(nfr)
                        break
        except Exception:
            pass

    if relevant_nfrs:
        lines.append("## NFRs (applies_to this module)")
        lines.append("")
        for nfr in relevant_nfrs:
            lines.append(f"### {nfr.id}: {nfr.title}")
            if nfr.content:
                lines.append(nfr.content[:300])
            lines.append("")

    # Data entities (all, limited).
    entities = [s for s in all_elements if s.element_type == "entity"]
    if entities:
        lines.append("## Data Entities")
        lines.append("")
        for ent_summary in entities[:15]:
            try:
                ent = storage.read_element(ent_summary.id)
                lines.append(f"- {ent.id}: {ent.title}")
            except Exception:
                lines.append(f"- {ent_summary.id}: {ent_summary.title}")
        lines.append("")

    return "\n".join(lines)


def get_module_generation_order(storage: Any) -> list[str]:
    """Return module IDs in dependency order (foundational first)."""
    all_elements = storage.list_all()
    modules = [s for s in all_elements if s.element_type == "module"]

    deps = {}
    for mod_summary in modules:
        try:
            mod = storage.read_element(mod_summary.id)
            mod_deps = set()
            for rel_type, entries in mod.relationships.items():
                if rel_type == "depends_on":
                    for e in entries:
                        mod_deps.add(e.target)
            deps[mod.id] = mod_deps
        except Exception:
            deps[mod_summary.id] = set()

    order = []
    remaining = set(deps.keys())
    while remaining:
        ready = {m for m in remaining if not (deps.get(m, set()) & remaining)}
        if not ready:
            order.extend(sorted(remaining))
            break
        order.extend(sorted(ready))
        remaining -= ready

    return order


# ------------------------------------------------------------------
# Legacy compat — removed Aider functions, kept for import compatibility
# ------------------------------------------------------------------


def _ensure_api_key(project: Path) -> None:
    """Ensure DEEPSEEK_API_KEY is in the environment. (Legacy compat.)"""
    if "DEEPSEEK_API_KEY" in os.environ:
        return
    env_file = project / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()
