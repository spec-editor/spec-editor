"""Implementation Engine — orchestrates all three layers.

Ties together:
    Layer 1 — Architectural Pattern (structure, naming, dependency rules)
    Layer 2 — Coding Templates (Copier/Jinja2 scaffolding + code generation)
    Layer 3 — Architecture Enforcement (test generation + CI compliance)

The engine is the single entry point that code generation callers use.
It replaces raw LLM codegen with a structured pipeline:

    Element → Pattern → Template → LLM (fill logic) → Enforce → Deploy

Usage::

    from src.implementation import ImplementationEngine

    engine = ImplementationEngine(project_path)
    engine.initialize_project()        # scaffold dirs + tests
    files = engine.generate(element)   # generate code for one element
    violations = engine.verify()       # run architecture checks
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.implementation.patterns import ArchPattern, get_pattern, list_patterns
from src.implementation.templates import TemplateEngine, create_template_engine
from src.implementation.enforcement import (
    ArchEnforcer,
    ArchViolation,
    create_arch_enforcer,
)


class ImplementationEngine:
    """Orchestrates architectural pattern, templates, and enforcement.

    Created per project — reads configuration from ``methodology.yaml``
    and ``local.yaml``, then provides a unified API for code generation.
    """

    def __init__(self, project_path: str | Path) -> None:
        self._project_path = Path(project_path)

        # Layer 1: Architectural Pattern
        self._pattern = self._load_pattern()

        # Layer 2: Template Engine
        self._templates = create_template_engine(self._project_path)

        # Layer 3: Architecture Enforcer
        self._enforcer = create_arch_enforcer(self._project_path)

    # ── Public API ──────────────────────────────────────────────

    @property
    def pattern(self) -> ArchPattern:
        """The active architectural pattern."""
        return self._pattern

    @property
    def templates(self) -> TemplateEngine:
        """The active template engine."""
        return self._templates

    @property
    def enforcer(self) -> ArchEnforcer:
        """The active architecture enforcer."""
        return self._enforcer

    def initialize_project(self) -> dict[str, Any]:
        """Set up a new project with the declared pattern.

        Creates:
            1. Directory structure (from pattern layers)
            2. Architecture test file (from enforcement rules)
            3. Optional Copier scaffolding (if template source configured)

        Returns:
            Dict with ``dirs_created``, ``files_written``, ``scaffold_applied``.
        """
        result: dict[str, Any] = {
            "dirs_created": [],
            "files_written": [],
            "scaffold_applied": False,
        }

        # 1. Create directories
        for d in self._pattern.get_directory_structure(self._project_path):
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                result["dirs_created"].append(str(d.relative_to(self._project_path)))

        # 2. Generate architecture test file
        test_content = self._enforcer.generate_rules(
            self._pattern, self._project_path
        )
        test_dir = self._project_path / "tests" / "arch"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "test_architecture.py"
        test_file.write_text(test_content, encoding="utf-8")
        result["files_written"].append(str(test_file.relative_to(self._project_path)))

        # 3. Try Copier scaffolding
        if hasattr(self._templates, "scaffold_project"):
            scaffold_ok = self._templates.scaffold_project(
                self._project_path, self._pattern
            )
            result["scaffold_applied"] = scaffold_ok

        return result

    def generate(
        self,
        element: Any,
        *,
        imp_element: Any = None,
        existing_code: dict[str, str] | None = None,
        llm_callback: Any = None,
    ) -> dict[str, str]:
        """Generate code for a single spec element.

        Pipeline:
            1. Template engine renders skeleton files
            2. For each file, LLM fills in business logic (via callback)
            3. Returns filename → content mapping

        Args:
            element: The spec Element to generate code for (MOD-*, ENT-*, NFR-*)
            imp_element: The linked IMP element with implementation_architect decisions.
                         If None, uses project defaults from methodology.yaml.
            existing_code: Map of existing files in project (for LLM context)
            llm_callback: ``async def callback(prompt: str) -> str``
                          If None, returns template-only output (no LLM).

        Returns:
            Dict mapping relative file paths → file contents.
        """
        # Read architect decisions from the IMP element (not the requirement element)
        ia = getattr(imp_element, "implementation_architect", None) or {}

        # Determine effective pattern (architect decision > project default)
        pattern_name = ia.get("structure") or self._pattern.name
        try:
            effective_pattern = get_pattern(pattern_name) if pattern_name != self._pattern.name else self._pattern
        except ValueError:
            effective_pattern = self._pattern

        # Get template context from the effective pattern
        extra_ctx = effective_pattern.get_template_context(element)

        # Add naming conventions
        naming = effective_pattern.get_naming_conventions()
        for etype_prefix, naming_pattern in naming.items():
            if getattr(element, "id", "").startswith(etype_prefix):
                for layer in effective_pattern.get_layers():
                    extra_ctx[f"{layer['name']}_dir"] = layer["path"]

        # Render templates
        files = self._templates.render(
            element, effective_pattern, extra_context=extra_ctx
        )

        # If no templates matched and we have an LLM callback, use raw generation
        if not files and llm_callback is not None:
            # Build a structured prompt with pattern constraints + architect decisions
            prompt = self._build_llm_prompt(element, imp_element, existing_code or {})
            # LLM integration point — caller handles async
            files = {"generated": "/* LLM generation placeholder */"}

        return files

    def verify(self) -> list[ArchViolation]:
        """Run architecture compliance checks.

        Returns empty list if all checks pass.
        """
        # Ensure test file exists
        test_file = (
            self._project_path / "tests" / "arch" / "test_architecture.py"
        )
        if not test_file.exists():
            test_content = self._enforcer.generate_rules(
                self._pattern, self._project_path
            )
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(test_content, encoding="utf-8")

        return self._enforcer.check(self._project_path)

    def get_generation_context(self, element: Any, imp_element: Any = None) -> dict[str, Any]:
        """Return the full context for code generation.

        Reads implementation decisions from the IMP element (aspect=implementation),
        NEVER from MOD/ENT/NFR business requirement elements.

        Args:
            element: The business requirement element (MOD-*, ENT-*, NFR-*)
            imp_element: The linked IMP element with implementation_architect decisions.
                         If None, uses project defaults from methodology.yaml.

        Returns:
            Dict with ``element``, ``pattern``, ``architect_decisions``,
            ``layers``, ``naming``, ``rules``, ``extra_context`` keys.
        """
        # Read architect decisions from the IMP element ONLY
        ia = getattr(imp_element, "implementation_architect", None) or {}

        # Determine effective pattern (architect decision > project default)
        pattern_name = ia.get("structure") or self._pattern.name
        try:
            effective_pattern = get_pattern(pattern_name) if pattern_name != self._pattern.name else self._pattern
        except ValueError:
            effective_pattern = self._pattern
            pattern_name = self._pattern.name

        template_name = ia.get("template", "")
        target_layer = ia.get("layer", "")

        return {
            "element": {
                "id": getattr(element, "id", ""),
                "title": getattr(element, "title", ""),
                "content": getattr(element, "content", ""),
                "aspect": getattr(element, "aspect", ""),
                "element_type": getattr(element, "element_type", ""),
            },
            "imp_element_id": getattr(imp_element, "id", ""),
            "pattern": pattern_name,
            "architect_decisions": ia,
            "architect_decisions_summary": self._summarize_decisions(ia),
            "layers": effective_pattern.get_layers(),
            "naming": effective_pattern.get_naming_conventions(),
            "rules": effective_pattern.get_dependency_rules(),
            "extra_context": effective_pattern.get_template_context(element),
            "target_layer": target_layer,
            "template_name": template_name,
        }

    # ── Internals ───────────────────────────────────────────────

    @staticmethod
    def _summarize_decisions(ia: dict[str, Any]) -> str:
        """Render architect decisions as a human-readable summary."""
        if not ia:
            return "No architect decisions recorded — use project defaults."

        lines = ["## Implementation Architect Decisions (DO NOT CHANGE)", ""]
        lines.append("| Decision | Value |")
        lines.append("|----------|-------|")

        label_map = {
            "structure": "Structural pattern",
            "domain_style": "Domain style",
            "ddd_type": "DDD classification",
            "template": "Code template",
            "layer": "Target layer",
        }

        for key, label in label_map.items():
            if key in ia:
                lines.append(f"| {label} | `{ia[key]}` |")

        if "ports" in ia and ia["ports"]:
            lines.append(f"| Required ports | {', '.join(f'`{p}`' for p in ia['ports'])} |")
        if "adapters" in ia and ia["adapters"]:
            lines.append(f"| Required adapters | {', '.join(f'`{a}`' for a in ia['adapters'])} |")

        lines.append("")
        lines.append("These decisions were made by the Implementation Architect.")
        lines.append("Follow them exactly. Do not choose different patterns or layers.")
        return "\n".join(lines)

    def _load_pattern(self) -> ArchPattern:
        """Load architectural pattern from methodology.yaml → implementation: section.

        Defaults to 'hexagonal' if no implementation section is configured.
        This ensures code generation works out-of-the-box without requiring
        users to modify their methodology.yaml.
        """
        pattern_name = "hexagonal"  # sensible default

        methodology_yaml = self._project_path / "methodology.yaml"
        if methodology_yaml.exists():
            try:
                import yaml

                data = yaml.safe_load(methodology_yaml.read_text()) or {}
                impl_cfg = data.get("implementation", {})
                pattern_name = impl_cfg.get("pattern", "hexagonal")
            except Exception:
                pass

        return get_pattern(pattern_name)

    def _resolve_pattern(self, imp_element: Any = None) -> ArchPattern:
        """Resolve the effective pattern for an element.

        Reads IMP element's implementation_architect.structure.
        Falls back to project default from methodology.yaml.
        """
        ia = getattr(imp_element, "implementation_architect", None) or {}
        override = ia.get("structure")
        if override and override != self._pattern.name:
            try:
                return get_pattern(override)
            except ValueError:
                pass
        return self._pattern

    def _build_llm_prompt(
        self, element: Any, imp_element: Any = None, existing_code: dict[str, str] | None = None
    ) -> str:
        """Build a structured LLM prompt with architectural constraints.

        Includes Implementation Architect decisions from the IMP element
        as IMMUTABLE constraints. Business requirement element provides
        the WHAT; IMP element provides the HOW.
        """
        ctx = self.get_generation_context(element, imp_element)

        lines = [
            "You are implementing a specification element in a project that follows",
            f"the **{ctx['pattern']}** architectural pattern.",
            "",
            ctx["architect_decisions_summary"],
            "",
            "## Element to implement",
            f"- ID: {ctx['element']['id']}",
            f"- Title: {ctx['element']['title']}",
            f"- Type: {ctx['element']['element_type']}",
            f"- Content: {ctx['element']['content']}",
            "",
        ]

        if ctx["imp_element_id"]:
            lines.append(
                f"Implementation plan: {ctx['imp_element_id']} "
                f"(see its content for rationale)"
            )
            lines.append("")

        lines.append("## Architectural Layers")

        for layer in ctx["layers"]:
            lines.append(
                f"- **{layer['name']}** ({layer['path']}): {layer['description']}"
            )
            lines.append(f"  Allowed dependencies: {layer['allowed_deps'] or ['none']}")

        lines.append("")
        lines.append("## Dependency Rules")
        for rule in ctx["rules"]:
            lines.append(f"- {rule['description']}")

        lines.append("")
        lines.append("## Naming Conventions")
        for prefix, pattern in ctx["naming"].items():
            lines.append(f"- {prefix} → {pattern}")

        if existing_code:
            lines.append("")
            lines.append("## Existing Code (for reference)")
            for fname, content in list(existing_code.items())[:10]:
                lines.append(f"### {fname}")
                lines.append("```python")
                lines.append(content[:500])
                lines.append("```")

        lines.append("")
        lines.append("## Instructions")
        lines.append("1. Place files in the correct layer directory")
        lines.append("2. Follow the naming convention for this element type")
        lines.append("3. Do NOT import from forbidden layers")
        lines.append("4. Use the pattern's base classes and conventions")
        lines.append("5. Write tests in tests/ directory")
        if ctx["target_layer"]:
            lines.append(f"6. This element BELONGS in the **{ctx['target_layer']}** layer")
        if ctx["template_name"]:
            lines.append(f"7. Use the **{ctx['template_name']}** coding template")

        return "\n".join(lines)
