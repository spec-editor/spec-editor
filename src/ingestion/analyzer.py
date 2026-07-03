"""Analyzer — comparison of new requirements against the specification.

Pipeline: filtered_*.txt → DiffEngine → duplicates/conflicts → new_<ts>.txt
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion.preprocessor import ExtractedFact, ProcessedFile
from src.storage.adapter import StorageAdapter
from src.storage.models import ElementStatus
from src.tracing import implements

# ======================================================================
# Data models
# ======================================================================


@dataclass
class DiffResult:
    """Diff result of comparing a new requirement against the specification."""

    is_duplicate: bool = False
    matched_id: str | None = None
    matched_title: str = ""
    conflicts: list[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """Analysis report — ready to be written to new_<ts>.txt."""

    source_file: str = ""
    new_requirements: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ======================================================================
# Diff Engine
# ======================================================================


class DiffEngine:
    """Compares a new requirement against existing specification elements."""

    def __init__(self, storage: StorageAdapter):
        self._storage = storage

    def analyze(self, title: str, description: str) -> DiffResult:
        """Check if such a requirement already exists.

        Uses keyword-based matching: splits title/description into words,
        searches for matches in existing elements.
        """
        keywords = self._extract_keywords(title + " " + description)

        if not keywords:
            return DiffResult()

        # Search for elements by keywords
        best_match: tuple[str, str, float] | None = None
        for keyword in keywords[:5]:  # search using first 5 keywords
            matches = self._storage.search(keyword)
            for summary in matches:
                if summary.status.value in ("deprecated",):
                    continue
                score = self._similarity(keywords, summary.title)
                if best_match is None or score > best_match[2]:
                    best_match = (summary.id, summary.title, score)

        if best_match and best_match[2] > 0.3:
            # Check for conflicts
            try:
                element = self._storage.read_element(best_match[0])
                conflicts = ConflictDetector.detect(
                    new_title=title,
                    new_description=description,
                    existing_title=element.title,
                    existing_content=element.content,
                )
            except Exception:
                conflicts = []

            return DiffResult(
                is_duplicate=True,
                matched_id=best_match[0],
                matched_title=best_match[1],
                conflicts=conflicts,
            )

        return DiffResult()

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract meaningful words from text (without stop words)."""
        stop_words = {
            "for",
            "this",
            "how",
            "what",
            "where",
            "when",
            "all",
            "of all",
            "need",
            "must",
            "add",
            "make",
            "exists",
            "be",
            "which",
            "the",
            "a",
            "an",
            "is",
            "are",
            "to",
            "of",
            "in",
            "very",
            "more",
            "less",
            "data",
            "total",
        }
        words = re.findall(r"[a-z0-9]{3,}", text.lower())
        return [w for w in words if w not in stop_words]

    @staticmethod
    def _similarity(keywords: list[str], text: str) -> float:
        """Similarity score: proportion of keywords found in text."""
        text_lower = text.lower()
        if not keywords:
            return 0.0
        matches = sum(1 for kw in keywords if kw in text_lower)
        return matches / len(keywords)


# ======================================================================
# Conflict Detector
# ======================================================================


class ConflictDetector:
    """Detects contradictions between a new and existing requirement."""

    # Word pairs that indicate a conflict
    _CONTRADICTIONS = [
        ("telegram", "email"),
        ("only email", "telegram"),
        ("only telegram", "email"),
        ("delete", "add"),
        ("reject", ""),
        ("not needed", "needed"),
        ("synchronous", "asynchronous"),
        ("rest", "graphql"),
        ("postgres", "mongo"),
        ("monolith", "microservice"),
    ]

    @classmethod
    def detect(
        cls,
        new_title: str,
        new_description: str,
        existing_title: str,
        existing_content: str,
    ) -> list[str]:
        """Find contradictions between new and existing requirement."""
        conflicts: list[str] = []
        new_text = (new_title + " " + new_description).lower()
        existing_text = (existing_title + " " + existing_content).lower()

        for word_a, word_b in cls._CONTRADICTIONS:
            if word_a in new_text and word_b in existing_text:
                conflicts.append(
                    f'New requires "{word_a}", but existing requires "{word_b}"'
                )
            elif word_b in new_text and word_a in existing_text:
                conflicts.append(
                    f'New requires "{word_b}", but existing requires "{word_a}"'
                )

        return conflicts


# ======================================================================
# Analyzer
# ======================================================================


@implements("SRC-013")
@implements("MOD-007-C4")
class Analyzer:
    """Orchestrates comparison of new requirements against the specification
    and creates draft elements in the sources/ aspect."""

    def __init__(
        self,
        storage: StorageAdapter,
        output_dir: Path,
    ):
        self._storage = storage
        self._output_dir = Path(output_dir)
        self._diff = DiffEngine(storage)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._find_next_id()

    def _find_next_id(self) -> int:
        """Find the next free ID for SRC elements."""
        existing = [s.id for s in self._storage.list_all() if s.id.startswith("SRC-")]
        if not existing:
            return 1
        nums = []
        for eid in existing:
            try:
                nums.append(int(eid.split("-")[1]))
            except (ValueError, IndexError):
                pass
        return max(nums) + 1 if nums else 1

    def analyze(self, processed: list[ProcessedFile]) -> AnalysisReport:
        """Analyze and create draft elements in aspects/sources/."""
        import time

        from src.storage.models import Element, ElementStatus, Provenance

        report = AnalysisReport()
        new_reqs = []
        duplicates = []
        all_conflicts = []
        created = 0

        for pf in processed:
            if pf.is_spam or pf.fact is None:
                continue

            fact = pf.fact
            diff = self._diff.analyze(fact.title, fact.description)

            if diff.is_duplicate:
                duplicates.append(
                    {
                        "title": fact.title,
                        "matched_id": diff.matched_id,
                        "matched_title": diff.matched_title,
                        "source": pf.source_file,
                    }
                )
                if diff.conflicts:
                    all_conflicts.extend(diff.conflicts)
                continue

            # Create draft element via standard write_element
            src_id = f"SRC-{self._next_id:03d}"
            self._next_id += 1

            element = Element(
                aspect="sources",
                element_type="source",
                id=src_id,
                title=fact.title,
                content=fact.description,
                status=ElementStatus.DRAFT,
                provenance=Provenance(source=pf.source_file),
            )
            self._storage.write_element(element)
            created += 1

            new_reqs.append(
                {
                    "id": src_id,
                    "title": fact.title,
                    "description": fact.description,
                    "aspect": fact.aspect,
                    "priority": fact.priority,
                    "source_file": pf.source_file,
                }
            )

        # Generate new_<timestamp>.txt file
        ts = int(time.time())
        report_path = self._output_dir / f"new_{ts}.txt"
        lines = ["# New requirements and conflicts", f"# Generated: {ts}", ""]

        if new_reqs:
            lines.append(f"##   ({created} created in aspects/sources/)")
            for req in new_reqs:
                lines.append(f"- [{req['priority']}] {req['id']}: {req['title']}")
                lines.append(f"  {req['description']}")
                lines.append(f"  aspect: {req['aspect']}")
                lines.append(f"  source: {req['source_file']}")
                lines.append("")

        if duplicates:
            lines.append("## Duplicates (already in specification)")
            for dup in duplicates:
                lines.append(
                    f"- {dup['title']} → {dup['matched_id']} ({dup['matched_title']})"
                )

        if all_conflicts:
            lines.append("## Conflicts")
            for c in all_conflicts:
                lines.append(f"- ⚠ {c}")

        if new_reqs:
            lines.append("")
            lines.append("## Recommendation")
            lines.append(
                "Run spec-editor run for agent discussion of new requirements."
            )

        report_path.write_text("\n".join(lines), encoding="utf-8")

        report = AnalysisReport(
            new_requirements=new_reqs,
            duplicates=duplicates,
            conflicts=all_conflicts,
            suggestions=([" spec-editor run agent limit reached "] if new_reqs else []),
        )
        return report
