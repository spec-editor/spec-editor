"""Coding Templates — Layer 2 of the Implementation Framework.

Integrates with **Copier** (https://copier.readthedocs.io) for
project scaffolding and code generation from templates.

Templates provide the *structural skeleton* — the LLM fills in
the business logic. This constrains LLM output to follow
architectural conventions, naming standards, and code style.

Backends:
    - ``copier`` — Full Copier integration (scaffold + update)
    - ``jinja2`` — Simple Jinja2 template rendering (no Copier dependency)
    - ``none`` — No template (raw LLM codegen, current behavior)

Usage::

    from src.implementation.templates import create_template_engine

    engine = create_template_engine(project_path)
    generated = engine.render("MOD-001", element, pattern)
    # → {"src/domain/mod_001_service.py": "class Mod001Service: ...", ...}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class TemplateEngine(ABC):
    """Abstract code generation template engine.

    Takes a spec element + architectural pattern and produces
    one or more source files (filename → content mapping).
    """

    @abstractmethod
    def render(
        self,
        element: Any,
        pattern: Any,  # ArchPattern
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Generate source files for an element.

        Args:
            element: The spec Element (has id, title, content, aspect, etc.)
            pattern: The ArchPattern defining structure and naming
            extra_context: Additional template variables

        Returns:
            Dict mapping relative file paths → file contents.
        """
        ...

    def scaffold_project(
        self,
        project_path: Path,
        pattern: Any,
        *,
        template_source: str = "",
    ) -> bool:
        """Create initial project scaffolding from a template.

        Optional — returns False if not supported by this engine.
        """
        return False


# ── Backend implementations ────────────────────────────────────────


class Jinja2TemplateEngine(TemplateEngine):
    """Simple Jinja2-based template rendering.

    Templates are loaded from ``templates/`` directory in the project
    root or from a specified source path. Uses minimal dependencies.

    Template variables provided:
        - element: the full Element object
        - pattern: the ArchPattern name
        - layer: target layer name (domain/ports/adapters)
        - naming: file naming conventions
        - ctx: extra context from pattern.get_template_context()
    """

    def __init__(self, templates_dir: str | Path = "") -> None:
        self._templates_dir = Path(templates_dir) if templates_dir else None

    def render(
        self,
        element: Any,
        pattern: Any,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Generate files using Jinja2 templates.

        Looks for templates named by element type prefix:
        ``MOD_module.py.jinja``, ``ENT_entity.py.jinja``, etc.

        If no matching template found, returns empty dict
        (caller falls back to LLM codegen).
        """
        try:
            from jinja2 import Environment, BaseLoader, TemplateNotFound
        except ImportError:
            return {}  # silent fallback

        # Build template context
        element_type = getattr(element, "element_type", "")
        ctx: dict[str, Any] = {
            "element": element,
            "pattern_name": pattern.name if hasattr(pattern, "name") else str(pattern),
            "naming": pattern.get_naming_conventions(),
            "extra": extra_context or {},
        }
        if hasattr(pattern, "get_template_context"):
            ctx.update(pattern.get_template_context(element))

        # Find templates directory
        template_paths: list[Path] = []
        if self._templates_dir:
            template_paths.append(self._templates_dir)
        # Also check project-local templates/
        try:
            proj = Path(element.__dict__.get("_project_path", ""))
            if proj.is_dir():
                template_paths.append(proj / "templates")
        except Exception:
            pass
        # Default: spec-editor built-in templates
        import os as _os

        builtin = Path(__file__).resolve().parent / "builtin_templates"
        if builtin.is_dir():
            template_paths.append(builtin)

        if not template_paths:
            return {}

        env = Environment(loader=BaseLoader())

        # Find matching templates for this element
        # Build multiple prefix candidates: element_type, ID prefix, aspect-based
        element_id = getattr(element, "id", "")
        prefixes: list[str] = []
        # 1. ID prefix (MOD-001 → "MOD")
        if "-" in element_id:
            prefixes.append(element_id.split("-")[0])
        # 2. element_type (e.g., "module")
        et = getattr(element, "element_type", "")
        if et:
            prefixes.append(et)
            if "_" in et:
                prefixes.append(et.split("_")[-1])
        # 3. Aspect (e.g., "modules")
        aspect = getattr(element, "aspect", "")
        if aspect:
            prefixes.append(aspect)
        # Deduplicate preserving order
        seen: set[str] = set()
        prefixes = [p for p in prefixes if p and (p not in seen and not seen.add(p))]  # type: ignore[func-returns-value]

        result: dict[str, str] = {}
        for tmpl_dir in template_paths:
            if not tmpl_dir.is_dir():
                continue
            import os as _os

            for fname in _os.listdir(str(tmpl_dir)):
                if not fname.endswith(".jinja"):
                    continue
                # Match template to element — try each prefix
                matched_prefix = ""
                for pfx in prefixes:
                    if fname.startswith(f"{pfx}_") or fname.startswith("generic_"):
                        matched_prefix = pfx
                        break
                if not matched_prefix:
                    continue

                tmpl_path = tmpl_dir / fname
                template = env.from_string(tmpl_path.read_text())

                # Determine output filename
                output_name = fname.replace(".jinja", "").replace(
                    f"{matched_prefix}_", f"{element_id.lower()}_"
                ).replace("generic_", f"{element_id.lower()}_")

                # Apply naming convention only as a fallback —
                # template filename takes precedence.
                naming = pattern.get_naming_conventions()
                for etype_prefix, naming_pattern in naming.items():
                    if getattr(element, "id", "").startswith(etype_prefix):
                        convention_name = naming_pattern.format(
                            id_lower=element.id.lower()
                        )
                        # Only override if the template name is generic
                        if fname.startswith("generic_"):
                            output_name = convention_name
                        break

                try:
                    content = template.render(**ctx)
                    result[output_name] = content
                except Exception:
                    # Template rendering error — skip this file
                    pass

        return result


class CopierTemplateEngine(TemplateEngine):
    """Copier-based template engine.

    Uses Copier (https://copier.readthedocs.io) for:
        - Project scaffolding (``copier copy``)
        - Updates (``copier update``) — diff-aware, preserves user changes
        - Template variables (``copier.yml``)

    Templates can be:
        - Local directory: ``./templates/python-hexagonal/``
        - Git repository: ``gh:acme-corp/python-hexagonal-template``
        - Git with subdirectory: ``gh:acme-corp/templates//python-hexagonal``
    """

    def __init__(self, template_source: str = "") -> None:
        self._source = template_source

    def scaffold_project(
        self,
        project_path: Path,
        pattern: Any,
        *,
        template_source: str = "",
    ) -> bool:
        """Scaffold a new project from a Copier template."""
        source = template_source or self._source
        if not source:
            return False

        try:
            import subprocess
            import sys

            pattern_name = pattern.name if hasattr(pattern, "name") else "none"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "copier",
                    "copy",
                    source,
                    str(project_path),
                    "--data",
                    f"pattern={pattern_name}",
                    "--data",
                    f"project_name={project_path.name}",
                    "--defaults",
                    "--quiet",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.returncode == 0
        except Exception:
            return False

    def render(
        self,
        element: Any,
        pattern: Any,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """For individual element generation, delegates to LLM with template context.

        Copier is primarily a project-level tool. For per-element generation,
        we provide the template context (variables, patterns, naming) to the
        LLM and let Jinja2 handle the actual rendering as a fallback.
        """
        # Use Jinja2 as the per-element renderer, with Copier variable context
        jinja_engine = Jinja2TemplateEngine()
        return jinja_engine.render(element, pattern, extra_context=extra_context)


class NoopTemplateEngine(TemplateEngine):
    """No template — passes through to raw LLM codegen (current behavior)."""

    def render(
        self,
        element: Any,
        pattern: Any,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        return {}  # empty = "generate from scratch"


# ── Factory ─────────────────────────────────────────────────────────


def create_template_engine(project_path: str | Path) -> TemplateEngine:
    """Create a TemplateEngine from project configuration.

    Reads ``local.yaml`` → ``templates:`` section:

    .. code-block:: yaml

        templates:
          backend: copier            # copier | jinja2 | none
          source: gh:acme-corp/python-hexagonal-template
          # OR: ./templates/python-hexagonal/
          element_templates:
            MOD-*: "module_service.py.jinja"
            ENT-*: "domain_entity.py.jinja"
            NFR-*: "middleware.py.jinja"

    Falls back to ``none`` (raw LLM codegen) if no config.
    """
    import os

    proj = Path(project_path)
    backend_name = "none"
    backend_config: dict[str, Any] = {}

    local_yaml = proj / "local.yaml"
    if local_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(local_yaml.read_text()) or {}
            tmpl_cfg = data.get("templates", {})
            backend_name = tmpl_cfg.get("backend", "none")
            backend_config = tmpl_cfg
        except Exception:
            pass

    backend_name = os.environ.get("SPEC_EDITOR__TEMPLATE_BACKEND", backend_name)

    if backend_name == "copier":
        source = backend_config.get("source", "")
        return CopierTemplateEngine(template_source=source)
    elif backend_name == "jinja2":
        templates_dir = backend_config.get("templates_dir", "")
        if templates_dir and not Path(templates_dir).is_absolute():
            templates_dir = str(proj / templates_dir)
        return Jinja2TemplateEngine(templates_dir=templates_dir)
    else:
        return NoopTemplateEngine()
