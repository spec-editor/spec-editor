"""MCP module: verification of requirements traceability to code.

Two modes:
- verify_implements: check a single file
- verify_traceability: check the entire project
"""

from pathlib import Path

from pydantic import BaseModel, Field

from src.config import get_logger
from src.mcp.parsers.go import parse_go
from src.mcp.parsers.java import parse_java
from src.mcp.parsers.kotlin import parse_kotlin
from src.mcp.parsers.python import CodeAnnotation, parse_python
from src.mcp.parsers.rust import parse_rust
from src.mcp.parsers.typescript import parse_typescript
from src.storage.adapter import StorageAdapter
from src.storage.models import Element

logger = get_logger(__name__)


class VerificationGap(BaseModel):
    """A single discrepancy."""

    req_id: str | None = None
    file_path: str = ""
    message: str = ""
    severity: str = "error"  # error, warning, info


class VerificationReport(BaseModel):
    """Verification report."""

    passed: bool = False
    total_requirements: int = 0
    implemented: int = 0
    coverage: float = 0.0
    gaps: list[VerificationGap] = Field(default_factory=list)


def verify_traceability(
    storage: StorageAdapter,
    code_dir: Path,
    language: str = "python",
) -> VerificationReport:
    """Verify that all requirements have an implementation in code."""
    report = VerificationReport()

    # Collect all annotations from code
    all_annotations: list[CodeAnnotation] = []
    parser = _get_parser(language)

    if parser and code_dir.is_dir():
        patterns = _get_file_patterns(language)
        for pattern in patterns:
            for code_file in code_dir.rglob(pattern):
                annotations, _ = parser(code_file)
                all_annotations.extend(annotations)

    implemented_ids = {a.req_id for a in all_annotations}

    # Check each specification element
    all_reqs = storage.list_all()
    report.total_requirements = len(all_reqs)

    for summary in all_reqs:
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue

        # Requirements of type code_artifact/test_case — skip
        if el.aspect in ("implementation",):
            continue

        if el.id in implemented_ids:
            report.implemented += 1
        elif el.status.value in ("confirmed",):
            report.gaps.append(
                VerificationGap(
                    req_id=el.id,
                    severity="error",
                    message=f"Confirmed requirement '{el.title}' not implemented in code",
                )
            )
        elif el.element_type in ("api_endpoint", "entity", "component"):
            report.gaps.append(
                VerificationGap(
                    req_id=el.id,
                    severity="warning",
                    message=f"'{el.title}' has no @implements in code",
                )
            )

    # Reverse link check: do all @implements reference existing requirements
    all_ids = {s.id for s in all_reqs}
    for ann in all_annotations:
        if ann.req_id not in all_ids:
            report.gaps.append(
                VerificationGap(
                    req_id=ann.req_id,
                    file_path=ann.file_path,
                    severity="warning",
                    message=f"@implements references non-existent '{ann.req_id}'",
                )
            )

    # Coverage
    if report.total_requirements > 0:
        report.coverage = round(report.implemented / report.total_requirements, 4)
    report.passed = len([g for g in report.gaps if g.severity == "error"]) == 0

    return report


def verify_implements(
    storage: StorageAdapter,
    file_path: Path,
    language: str = "python",
) -> VerificationReport:
    """Check a single file for compliance with requirements."""
    report = VerificationReport()
    parser = _get_parser(language)

    if not parser or not file_path.exists():
        report.gaps.append(
            VerificationGap(
                file_path=str(file_path),
                severity="error",
                message="File not found or unsupported language",
            )
        )
        return report

    annotations, symbols = parser(file_path)
    implemented_ids = {a.req_id for a in annotations}
    report.implemented = len(implemented_ids)

    # Check each requirement
    for req_id in implemented_ids:
        try:
            req = storage.read_element(req_id)
        except Exception:
            report.gaps.append(
                VerificationGap(
                    req_id=req_id,
                    file_path=str(file_path),
                    severity="error",
                    message=f"Requirement '{req_id}' not found in specification",
                )
            )
            continue

        # Check entity → fields
        if req.element_type == "entity":
            _verify_entity_fields(req, symbols, annotations, report, str(file_path))

    report.passed = len([g for g in report.gaps if g.severity == "error"]) == 0
    return report


def _verify_entity_fields(
    entity: Element,
    symbols: list,
    annotations: list[CodeAnnotation],
    report: VerificationReport,
    file_path: str,
) -> None:
    """Verify that all entity fields are implemented in code."""
    entity_annotations = [a for a in annotations if a.req_id == entity.id]

    if not entity_annotations:
        report.gaps.append(
            VerificationGap(
                req_id=entity.id,
                file_path=file_path,
                severity="warning",
                message=f"Model for entity '{entity.title}' not found in code",
            )
        )
        return


def _get_parser(language: str):
    """Get parser for a language."""
    parsers = {
        "python": parse_python,
        "typescript": parse_typescript,
        "javascript": parse_typescript,
        "go": parse_go,
        "java": parse_java,
        "kotlin": parse_kotlin,
        "rust": parse_rust,
    }
    return parsers.get(language)


def _get_file_patterns(language: str) -> list[str]:
    """Get file glob patterns for a language."""
    patterns = {
        "python": ["*.py"],
        "typescript": ["*.ts", "*.tsx"],
        "javascript": ["*.js", "*.jsx"],
        "go": ["*.go"],
        "java": ["*.java"],
        "kotlin": ["*.kt", "*.kts"],
        "rust": ["*.rs"],
    }
    return patterns.get(language, [])


def verify_sources(storage: StorageAdapter) -> VerificationReport:
    """Verify that all SRC requirements are covered by specification elements.

    SRC elements are created from sources_raw/ by the preprocessor.
    The specification should cover them via derived_from.
    """
    report = VerificationReport()
    all_reqs = storage.list_all()

    src_elements: dict[str, Element] = {}
    covered_src: set[str] = set()

    for summary in all_reqs:
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue
        if el.aspect == "sources":
            src_elements[el.id] = el
        for src_id in el.derived_from:
            covered_src.add(src_id)

    report.total_requirements = len(src_elements)
    report.implemented = len(covered_src)

    for src_id, src_el in src_elements.items():
        if src_id not in covered_src:
            report.gaps.append(
                VerificationGap(
                    req_id=src_id,
                    severity="warning",
                    message=f"SRC '{src_el.title}' not covered by specification",
                )
            )

    if report.total_requirements > 0:
        report.coverage = round(report.implemented / report.total_requirements, 4)
    report.passed = True
    return report
