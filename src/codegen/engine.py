"""Spec-driven code generation engine.

Renders specification elements into code skeletons using Jinja2 templates.
Element types are mapped to templates via codegen.yaml configuration.
"""

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from src.storage.models import Element


class CodeGenerator:
    """Generates code skeletons from specification elements.

    Uses Jinja2 templates loaded from the templates/ directory.
    Configuration via codegen.yaml maps element types to templates.
    """

    def __init__(self, config: dict | None = None, templates_dir: Path | None = None):
        if templates_dir is None:
            templates_dir = Path(__file__).parent / "templates"

        self._templates_dir = templates_dir
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        if config is None:
            config = self._load_default_config()
        self._config = config

        # Build element_type → template_name mapping
        self._mapping: dict[str, str] = {}
        for elem_type, mapping in config.get("mappings", {}).items():
            tmpl = mapping.get("template", "")
            if tmpl:
                self._mapping[elem_type] = tmpl

    def get_template(self, element_type: str) -> str | None:
        """Get the template name for an element type, or None if unmapped."""
        return self._mapping.get(element_type)

    def render_element(self, element: Element, template_name: str) -> str:
        """Render a single element using the named template.

        Returns empty string if the template is not found.
        """
        try:
            tmpl = self._env.get_template(template_name)
        except TemplateNotFound:
            return ""

        context = {
            "element": element,
            "id": element.id,
            "title": element.title,
            "element_type": element.element_type,
            "content": element.content,
            "status": element.status.value,
            "aspect": element.aspect,
        }
        rendered = tmpl.render(**context)
        return rendered.strip() + "\n"

    def generate_element(
        self, element: Element, output_dir: Path, dry_run: bool = False
    ) -> dict:
        """Generate code for a single element into output_dir.

        Returns a result dict with status and file path.
        """
        template_name = self.get_template(element.element_type)
        if template_name is None:
            return {
                "element_id": element.id,
                "status": "skipped",
                "reason": f"no template for element type '{element.element_type}'",
                "file": "",
            }

        code = self.render_element(element, template_name)
        if not code:
            return {
                "element_id": element.id,
                "status": "error",
                "reason": f"template '{template_name}' not found",
                "file": "",
            }

        # Derive filename from element title (snake_case)
        filename = self._title_to_filename(element.title, template_name)
        filepath = output_dir / filename

        if dry_run:
            return {
                "element_id": element.id,
                "status": "dry_run",
                "file": str(filepath),
                "template": template_name,
            }

        output_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_text(code, encoding="utf-8")
        return {
            "element_id": element.id,
            "status": "created",
            "file": str(filepath),
            "template": template_name,
        }

    def generate_all(
        self, elements: list[Element], output_dir: Path, dry_run: bool = False
    ) -> list[dict]:
        """Generate code for all elements into output_dir."""
        return [
            self.generate_element(el, output_dir, dry_run=dry_run) for el in elements
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _title_to_filename(title: str, template_name: str) -> str:
        """Convert element title to a filename based on the template."""
        # Infer extension from template name
        if "typescript" in template_name or template_name.endswith(".ts.j2"):
            ext = ".ts"
        elif "react" in template_name or template_name.endswith(".tsx.j2"):
            ext = ".tsx"
        else:
            ext = ".py"

        # Simple snake_case conversion
        slug = title.lower().replace(" ", "_").replace("-", "_")
        # Remove non-alphanumeric (keep underscore)
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        return f"{slug}{ext}"

    @staticmethod
    def _load_default_config() -> dict:
        """Load the default codegen.yaml from the project root."""
        from importlib import resources

        config_path = resources.files("data") / "codegen.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                root = yaml.safe_load(f) or {}
            # codegen.yaml nests mapping under the 'codegen' key
            return root.get("codegen", root)
        return {}
