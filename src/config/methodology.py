"""Loading and validation of methodology (methodology.yaml)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AttributeDef(BaseModel):
    """Definition of an additional element attribute."""

    name: str
    type: str = "string"  # "string", "number", "boolean"
    title: str


class ElementTypeDef(BaseModel):
    """Definition of an element type within an aspect."""

    name: str
    title: str
    attributes: list[AttributeDef] = Field(default_factory=list)


class RelationshipTypeDef(BaseModel):
    """Definition of a relationship type between elements."""

    name: str
    title: str
    source_aspects: list[str] = Field(
        description="Aspects from which the relationship can originate",
    )
    target_aspects: list[str] = Field(
        description="Aspects to which the relationship can lead",
    )
    cardinality: str = Field(
        default="many-to-many",
        description="1-to-1, 1-to-many, many-to-many",
    )


class AspectDef(BaseModel):
    """Definition of an aspect in the methodology."""

    name: str
    title: str
    description: str = ""
    default_diagram: str | None = None
    element_types: list[ElementTypeDef] = Field(default_factory=list)
    relationship_types: list[RelationshipTypeDef] = Field(default_factory=list)


class Methodology(BaseModel):
    """Methodology — full description of the requirements structure."""

    name: str
    version: str = "1.0"
    description: str = ""
    aspects: list[AspectDef] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------
# Loading and helper functions
# ------------------------------------------------------------------


def load_methodology(path: Path) -> Methodology:
    """Load methodology from a YAML file.

    Raises:
        FileNotFoundError: file not found
        ValueError: invalid YAML or structure
    """
    if not path.exists():
        raise FileNotFoundError(f"Methodology file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ValueError(f"Methodology file is empty: {path}")

    # Coerce version to string (YAML parses "1.0" as float, but model expects str)
    if "version" in data and not isinstance(data["version"], str):
        data["version"] = str(data["version"])

    return Methodology(**data)


def get_aspect(methodology: Methodology, name: str) -> AspectDef | None:
    """Find an aspect by name."""
    for aspect in methodology.aspects:
        if aspect.name == name:
            return aspect
    return None


def get_relationship_type(
    methodology: Methodology, name: str
) -> RelationshipTypeDef | None:
    """Find a relationship type by name (search across all aspects)."""
    for aspect in methodology.aspects:
        for rt in aspect.relationship_types:
            if rt.name == name:
                return rt
    return None


def get_root_types(methodology: Methodology) -> set[str]:
    """Return all element types that are root-level (no parent required).

    Root types are the first element_type in each aspect (top of hierarchy),
    plus any type that never appears as a child of another type within its
    aspect according to the methodology's element_type ordering.
    """
    roots: set[str] = set()
    for aspect in methodology.aspects:
        if aspect.element_types:
            # First type is root
            roots.add(aspect.element_types[0].name)
    return roots


def get_hierarchy(methodology: Methodology, aspect_name: str) -> dict[str, str | None]:
    """Return parent→child hierarchy for an aspect based on `consists_of`.

    Reads the methodology's consists_of relationships to determine:
    - Which element type is the parent for each child type.
    - Which types are top-level (no parent within the aspect).

    Returns dict: {child_type: parent_type_or_None}
    e.g. for user_interface: {"screen": "section", "widget": "screen", "control": "widget", "section": None}
    """
    aspect = get_aspect(methodology, aspect_name)
    if not aspect:
        return {}

    hierarchy: dict[str, str | None] = {}

    # All element types start as potential top-level
    for et in aspect.element_types:
        hierarchy[et.name] = None

    # Find consists_of relationships within this aspect
    for rt in aspect.relationship_types:
        if rt.name != "consists_of":
            continue
        # consists_of: source_aspects → target_aspects
        # "Module consists of components" means: component is child of module
        # So source_aspect is parent, target_aspect is child
        # But within the SAME aspect: parent_type → child_type
        # We infer from description/title: "section → screen → widget"
        # For now, heuristically: the first type in element_types is top-level,
        # and each subsequent type is child of the previous.

    # Heuristic: element types are listed top-down in methodology
    types_in_order = [et.name for et in aspect.element_types]
    for i in range(1, len(types_in_order)):
        parent_type = types_in_order[i - 1]
        child_type = types_in_order[i]
        if hierarchy.get(child_type) is None:
            hierarchy[child_type] = parent_type

    return hierarchy


def get_element_type(
    methodology: Methodology, aspect_name: str, type_name: str
) -> ElementTypeDef | None:
    """Find an element type in the specified aspect."""
    aspect = get_aspect(methodology, aspect_name)
    if aspect is None:
        return None
    for et in aspect.element_types:
        if et.name == type_name:
            return et
    return None


def format_methodology(methodology: Methodology) -> str:
    """Format methodology into readable text for agent prompts."""

    lines = [
        f"Methodology: {methodology.name} v{methodology.version}",
        f"Description: {methodology.description}" if methodology.description else "",
        "",
    ]

    for aspect in methodology.aspects:
        lines.append(f"## Aspect: {aspect.title} ({aspect.name})")
        if aspect.description:
            lines.append(f"  {aspect.description}")
            lines.append("")
        if aspect.element_types:
            lines.append("  Element types:")
            for et in aspect.element_types:
                lines.append(f"    - {et.name}: {et.title}")
                for attr in et.attributes:
                    lines.append(f"      • {attr.name} ({attr.type}): {attr.title}")

        if aspect.relationship_types:
            lines.append("  Relationship types:")
            for rt in aspect.relationship_types:
                src = ", ".join(rt.source_aspects)
                tgt = ", ".join(rt.target_aspects)
                lines.append(
                    f"    - {rt.name}: {rt.title} [{rt.cardinality}] {src} → {tgt}"
                )
        lines.append("")

    if methodology.skills:
        lines.append(f"Agent skills: {', '.join(methodology.skills)}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Methodology Manager — discover and list available methodologies
# ------------------------------------------------------------------


class MethodologyManager:
    """Discovers and manages installed methodologies.

    Scans the methodologies/ directory for YAML files and the built-in
    methodology.yaml in the project root.
    """

    def __init__(self, methodologies_dir: Path | None = None):
        if methodologies_dir is None:
            from src.config._data_path import data_path

            methodologies_dir = data_path("methodologies")
        self._methodologies_dir = methodologies_dir

    @property
    def methodologies_dir(self) -> Path:
        return self._methodologies_dir

    def list_available(self) -> list[str]:
        """List all discovered methodology names.

        Returns names like ['waterfall', 'agile', 'api_first'].
        """
        names: list[str] = []
        if self._methodologies_dir.is_dir():
            for path in sorted(self._methodologies_dir.glob("*.yaml")):
                names.append(path.stem)
        return names

    def find(self, name: str) -> Path | None:
        """Find a methodology file by name (without .yaml extension).

        Searches:
        1. methodologies/{name}.yaml
        2. methodology.yaml in project root (for 'waterfall' default)

        Returns the Path if found, None otherwise.
        """
        # Check methodologies/ directory
        candidate = self._methodologies_dir / f"{name}.yaml"
        if candidate.is_file():
            return candidate

        # Fallback: root methodology.yaml for 'waterfall'
        if name == "waterfall":
            root_candidate = self._methodologies_dir.parent / "methodology.yaml"
            if root_candidate.is_file():
                return root_candidate

        return None

    def load(self, name: str) -> Methodology:
        """Load a methodology by name.

        Raises:
            FileNotFoundError: methodology not found
            ValueError: invalid YAML structure
        """
        path = self.find(name)
        if path is None:
            available = ", ".join(self.list_available())
            raise FileNotFoundError(
                f"Methodology '{name}' not found. Available: {available}"
            )
        return load_methodology(path)

    def get_default(self) -> Methodology:
        """Load the default methodology (waterfall)."""
        return self.load("waterfall")
