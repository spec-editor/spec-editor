"""Coverage command — verify @implements coverage of leaf requirements.

Usage:
    spec-editor coverage -p /path/to/project
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _find_leaves(storage: Any) -> list[dict]:
    """Find all leaf elements (no children) in the spec."""
    all_elements = storage.list_all()
    leaves = []
    for summary in all_elements:
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue
        # A leaf is an element with no children AND an implementable type.
        # All element types that represent implementable leaf requirements.
        IMPLEMENTABLE_TYPES = {
            "step", "field", "requirement",
            "detailed_scenario", "code_artifact", "api_endpoint",
            "task_application",
        }
        if el.children:
            continue
        if el.element_type not in IMPLEMENTABLE_TYPES:
            continue
        leaves.append({
                "id": el.id,
                "title": el.title,
                "aspect": el.aspect,
                "type": el.element_type,
            })
    return leaves


def _guess_module(leaf_id: str, storage: Any) -> str:
    """Guess which module a leaf requirement belongs to.

    Walks the parent chain: leaf → parent → ... → MOD-*.
    Falls back to naming conventions.
    """
    # Walk parent chain to find MOD-*
    try:
        el = storage.read_element(leaf_id)
        # Check direct relationships
        for rel_type, entries in el.relationships.items():
            for e in entries:
                if e.target.startswith("MOD-"):
                    return e.target
        # Walk up via parent
        current = el
        for _ in range(5):
            if not current.parent:
                break
            try:
                parent = storage.read_element(current.parent)
            except Exception:
                break
            for rel_type, entries in parent.relationships.items():
                for e in entries:
                    if e.target.startswith("MOD-"):
                        return e.target
            current = parent
    except Exception:
        pass

    # Fallback: naming conventions
    fid = int(leaf_id.split("-")[-1]) if leaf_id[-1].isdigit() else 0
    if leaf_id.startswith("STE-"):
        if fid <= 11: return "MOD-marketplace"
        if fid <= 14: return "MOD-marketplace"
        if fid <= 18: return "MOD-a2a-launchpad"
        if fid <= 25: return "MOD-website"
        return "MOD-website"
    if leaf_id.startswith("FIE-"):
        if fid <= 3: return "MOD-marketplace"
        if fid <= 9: return "MOD-marketplace"
        if fid <= 14: return "MOD-catalog"
        if fid <= 18: return "MOD-marketplace"
        if fid <= 21: return "MOD-marketplace"
        if fid <= 24: return "MOD-a2a-launchpad"
        return "MOD-marketplace"
    if leaf_id.startswith("NFR-"):
        return "non_functional"
    if leaf_id.startswith("TC-"):
        if fid == 1: return "MOD-catalog"
        if fid == 2: return "MOD-marketplace"
        if fid == 3: return "MOD-a2a-launchpad"
        if fid == 4: return "MOD-marketplace"
        return "MOD-website"
    return "unknown"


def _scan_implements(project_path: str) -> dict[str, list[str]]:
    """Scan code for @implements decorators referencing spec IDs.

    Returns dict of spec_id → list of file:line references.
    """
    proj = Path(project_path)
    implements_map: dict[str, list[str]] = {}

    for py_file in proj.rglob("*.py"):
        if "__pycache__" in str(py_file) or ".venv" in str(py_file):
            continue
        if "node_modules" in str(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        for m in re.finditer(r"@implements\(([^)]+)\)", content):
            args_str = m.group(1)
            # Extract all quoted IDs (both single and double quotes)
            ids = re.findall(r"['\"]([^'\"]+)['\"]", args_str)
            line_no = content[:m.start()].count("\n") + 1
            rel_path = str(py_file.relative_to(proj))
            for spec_id in ids:
                implements_map.setdefault(spec_id, []).append(f"{rel_path}:{line_no}")

    return implements_map


from src.cli.commands import cli  # noqa: E402


@cli.command("coverage")
@click.option("-p", "--path", "project_path", default=".", type=click.Path(exists=True))
def coverage_cmd(project_path: str) -> None:
    """Show @implements coverage of leaf requirements."""
    from src.storage.filesystem import FilesystemStorage

    storage = FilesystemStorage(Path(project_path))
    leaves = _find_leaves(storage)
    implements_map = _scan_implements(project_path)

    # Count coverage
    covered = 0
    uncovered = []
    for leaf in leaves:
        lid = leaf["id"]
        if lid in implements_map:
            covered += 1
        else:
            uncovered.append(leaf)

    total = len(leaves)
    pct = (covered / total * 100) if total > 0 else 0

    # Print summary
    table = Table(title=f"Leaf Requirement Coverage — {project_path}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total leaf requirements", str(total))
    table.add_row("Covered (@implements)", str(covered))
    table.add_row("Uncovered", str(len(uncovered)))
    table.add_row("Coverage", f"{pct:.1f}%")
    console.print(table)

    # Show uncovered
    if uncovered:
        console.print("\n[bold yellow]Uncovered leaves:[/bold yellow]")
        for leaf in uncovered[:20]:
            console.print(
                f"  [dim]{leaf['aspect']}/{leaf['type']}[/dim] "
                f"[red]{leaf['id']}[/red]: {leaf['title']}"
            )
        if len(uncovered) > 20:
            console.print(f"  ... and {len(uncovered) - 20} more")

    # Show what the @implements actually reference
    console.print("\n[bold]@implements references found in code:[/bold]")
    for spec_id, refs in sorted(implements_map.items()):
        is_leaf = any(l["id"] == spec_id for l in leaves)
        leaf_tag = "🌿" if is_leaf else "📦 (should reference leaf, not MOD-*)"
        console.print(f"  {leaf_tag} {spec_id}: {len(refs)} refs")

    # ── Group leaves by module ──
    console.print("\n[bold]Coverage by Module:[/bold]")
    module_leaves: dict[str, dict] = {}
    for leaf in leaves:
        lid = leaf["id"]
        # Determine module from leaf ID prefix or relationships
        mod = _guess_module(lid, storage)
        if mod not in module_leaves:
            module_leaves[mod] = {"total": 0, "covered": 0, "leaves": []}
        module_leaves[mod]["total"] += 1
        if lid in implements_map:
            module_leaves[mod]["covered"] += 1
        module_leaves[mod]["leaves"].append(lid)

    for mod in sorted(module_leaves):
        m = module_leaves[mod]
        pct = m["covered"] / m["total"] * 100 if m["total"] else 0
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        console.print(
            f"  {bar} {mod}: {m['covered']}/{m['total']} ({pct:.0f}%)"
        )
