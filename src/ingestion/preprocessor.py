"""Preprocessor — requirement classification and fact extraction.

Pipeline: raw text → RequirementClassifier → FactExtractor → filtered_*.txt
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.providers.base import LLMProvider, Message, MessageRole
from src.tracing import implements

# ======================================================================
# Data models
# ======================================================================


@dataclass
class ClassificationResult:
    """Text classification result."""

    is_requirement: bool
    confidence: float = 0.0
    reasoning: str = ""


@dataclass
class ExtractedFact:
    """Extracted requirement fact."""

    title: str = ""
    description: str = ""
    aspect: str = ""
    priority: str = "medium"


@dataclass
class ProcessedFile:
    """Result of processing a single file."""

    source_file: str
    fact: ExtractedFact | None = None
    is_spam: bool = False


# ======================================================================
# Prompts
# ======================================================================

_CLASSIFY_PROMPT = """\
You are a software system requirements analyser.
Read the text and answer: is this a system requirement?

ANSWER ONLY one of:
- YES — this is a requirement (feature, request, constraint)
- NO — this is not a requirement (spam, greetings, off-topic)

If YES — you can add confidence in parentheses: YES (confidence: 0.9)

Text:
{text}
"""

_EXTRACT_PROMPT = """\
Extract a structured requirement from the text.

Return JSON with fields:
- title: short title (5-10 words)
- description: full description of what needs to be done
- aspect: one of [modules, user_scenarios, user_interface, data_entities, non_functional]
- priority: low / medium / high

ONLY JSON, no explanation.

Text:
{text}
"""


# ======================================================================
# Classifier
# ======================================================================


class RequirementClassifier:
    """Classifier: requirement or spam."""

    _BATCH_PROMPT = """\
You are a requirements analyser. Below is a list of messages (each with ID).
For each, answer YES (if requirement/feature/request) or NO (spam/flood).

Return a JSON object: {{"ID": "YES", ...}}
ONLY JSON, no explanation.

Messages:
{messages}
"""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def classify(self, text: str) -> ClassificationResult:
        """Determine whether text is a requirement."""
        messages = [
            Message(
                role=MessageRole.SYSTEM, content="You are a requirements analyser."
            ),
            Message(role=MessageRole.USER, content=_CLASSIFY_PROMPT.format(text=text)),
        ]
        import asyncio

        async def _run():
            return await self._provider.complete(messages=messages)

        response = asyncio.run(_run())
        return self._parse_response(response.content or "")

    def classify_batch(
        self, items: list[tuple[str, str]]
    ) -> dict[str, ClassificationResult]:
        """Classify a batch of messages in a single LLM call.

        For each item we take the first 1000 characters — enough
        to understand whether it is a requirement or spam.
        """
        batch_text = "\n".join(f"[{fid}] {text[:1000]}" for fid, text in items[:30])
        prompt = self._BATCH_PROMPT.format(messages=batch_text)
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content="You are a requirements analyser. Answer with JSON only.",
            ),
            Message(role=MessageRole.USER, content=prompt),
        ]
        import asyncio

        async def _run():
            return await self._provider.complete(messages=messages)

        response = asyncio.run(_run())
        return self._parse_batch(response.content or "", items)

    @staticmethod
    def _parse_batch(text: str, items: list) -> dict[str, ClassificationResult]:
        """Parse the JSON response from batch classification."""
        import json

        results = {}
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                for fid, _ in items:
                    answer = str(data.get(fid, "НЕТ")).upper()
                    results[fid] = ClassificationResult(
                        is_requirement=answer.startswith("ДА"),
                        confidence=0.8 if answer.startswith("ДА") else 0.1,
                    )
                return results
            except json.JSONDecodeError:
                pass
        # Fallback: everything is not a requirement
        for fid, _ in items:
            results[fid] = ClassificationResult(is_requirement=False, confidence=0.0)
        return results

    @staticmethod
    def _parse_response(text: str) -> ClassificationResult:
        text_upper = text.strip().upper()
        # Handle both Russian (ДА/НЕТ) and English (YES/NO) responses
        is_req = text_upper.startswith("ДА") or text_upper.startswith("YES")

        # Extract confidence
        confidence = 1.0 if is_req else 0.0
        match = re.search(r"confidence:\s*([\d.]+)", text.lower())
        if match:
            confidence = float(match.group(1))

        return ClassificationResult(
            is_requirement=is_req,
            confidence=confidence,
            reasoning=text.strip(),
        )


# ======================================================================
# Fact Extractor
# ======================================================================


class FactExtractor:
    """Extracts structured facts from requirement text."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def extract(self, text: str) -> ExtractedFact:
        """Extract a fact from text."""
        messages = [
            Message(
                role=MessageRole.SYSTEM, content="You are a requirements extractor."
            ),
            Message(role=MessageRole.USER, content=_EXTRACT_PROMPT.format(text=text)),
        ]

        import asyncio

        async def _run():
            return await self._provider.complete(messages=messages)

        response = asyncio.run(_run())
        return self._parse_response(response.content or "", source_text=text)

    @staticmethod
    def _parse_response(text: str, source_text: str = "") -> ExtractedFact:
        # Look for JSON in the response
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return ExtractedFact(
                    title=data.get("title", ""),
                    description=data.get("description", ""),
                    aspect=data.get("aspect", ""),
                    priority=data.get("priority", "medium"),
                )
            except json.JSONDecodeError:
                pass
        # Fallback: take first line of source text as title
        first_line = (source_text or text).strip().split("\n")[0][:100]
        return ExtractedFact(title=first_line, description=source_text or text)


# ======================================================================
# Source Preprocessor
# ======================================================================


@implements("MOD-007-C2")
class SourcePreprocessor:
    """Scans the source_raw/ folder, filters spam, extracts requirements.

    Results are saved to source/ (clean .txt files).
    """

    def __init__(
        self,
        source_dir: Path,
        output_dir: Path,
        classifier: RequirementClassifier,
        extractor: FactExtractor,
    ):
        self._raw_dir = Path(source_dir) / "source_raw"
        self._output_dir = Path(output_dir) / "source"
        self._classifier = classifier
        self._extractor = extractor
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def read_file(file_path: Path) -> str:
        """Read a file in any supported format."""
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            return SourcePreprocessor._read_pdf(file_path)
        elif suffix in (".html", ".htm"):
            return SourcePreprocessor._read_html(file_path)
        else:
            return file_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _read_pdf(file_path: Path) -> str:
        """Extract text from PDF in Markdown format."""
        try:
            from PyPDF2 import PdfReader

            reader = PdfReader(str(file_path))
            lines = []
            for page in reader.pages:
                text = page.extract_text()
                if not text:
                    continue
                # Fix broken words: hy-\nphen → hyphen, Chi\ncago → Chicago
                text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
                text = re.sub(r"([a-z])\n([a-z])", r"\1\2", text)

                for raw_line in text.split("\n"):
                    stripped = raw_line.strip()
                    if not stripped:
                        lines.append("")
                        continue
                    if stripped.isupper() and len(stripped) < 80:
                        lines.append(f"## {stripped.title()}")
                    elif re.match(r"^[\d]+\.\s", stripped):
                        lines.append(f"1. {stripped}")
                    elif stripped.startswith(("•", "-", "*")):
                        lines.append(f"- {stripped.lstrip('•-* ')}")
                    else:
                        lines.append(stripped)
                lines.append("")
            return "\n".join(lines) if lines else ""
        except ImportError:
            return f"[PDF: pip install PyPDF2]"
        except Exception as e:
            return f"[PDF: error — {e}]"

    @staticmethod
    def _read_html(file_path: Path) -> str:
        """Convert HTML to Markdown."""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(
                file_path.read_text(encoding="utf-8", errors="replace"), "html.parser"
            )
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            md_lines = []
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
                text = tag.get_text(strip=True)
                if not text:
                    continue
                if tag.name == "h1":
                    md_lines.append(f"# {text}")
                elif tag.name == "h2":
                    md_lines.append(f"## {text}")
                elif tag.name == "h3":
                    md_lines.append(f"### {text}")
                elif tag.name == "h4":
                    md_lines.append(f"#### {text}")
                elif tag.name == "li":
                    md_lines.append(f"- {text}")
                elif tag.name in ("td", "th"):
                    md_lines.append(f"| {text} ")
                else:
                    md_lines.append(text)

            return (
                "\n".join(md_lines)
                if md_lines
                else file_path.read_text(encoding="utf-8", errors="replace")
            )
        except ImportError:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return file_path.read_text(encoding="utf-8", errors="replace")

    @implements("SRC-011")
    def process(self) -> list[ProcessedFile]:
        """Process all unread files in source_raw/.

        Groups messages by day, classifies in batches.
        """
        results: list[ProcessedFile] = []
        source_files = sorted(
            f
            for f in self._raw_dir.iterdir()
            if f.is_file()
            and not f.name.startswith("filtered_")
            and not f.name.startswith("_spam_")
        )

        if not source_files:
            return results

        # Group by day (from timestamp in filename)
        import re
        from collections import defaultdict

        days: dict[str, list[Path]] = defaultdict(list)
        for fp in source_files:
            ts_match = re.search(r"_(\d{10})_", fp.name)
            if ts_match:
                from datetime import datetime, timezone

                ts = int(ts_match.group(1))
                day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            else:
                day = "unknown"
            days[day].append(fp)

        # Process each day
        req_count = 0
        spam_count = 0

        for day, files in sorted(days.items()):
            print(f"  Day {day}: {len(files)} messages", flush=True)

            # Read all files for the day
            batch = []
            for fp in files:
                text = self.read_file(fp)
                batch.append((fp.name, text))

            # Batch classification (30 per call)
            all_class: dict[str, ClassificationResult] = {}
            for i in range(0, len(batch), 30):
                chunk = batch[i : i + 30]
                classifications = self._classifier.classify_batch(chunk)
                all_class.update(classifications)

            # Process results
            for fp in files:
                name = fp.name
                cls_result = all_class.get(name)
                if cls_result is None:
                    continue

                if not cls_result.is_requirement:
                    spam_path = self._raw_dir / f"_spam_{name}"
                    fp.rename(spam_path)
                    spam_count += 1
                    results.append(ProcessedFile(source_file=name, is_spam=True))
                    continue

                # Extract facts — use FULL text
                text = self.read_file(fp)

                # Save converted .md for PDF/HTML
                suffix = fp.suffix.lower()
                if suffix in (".pdf", ".html", ".htm"):
                    md_path = self._raw_dir / f"{fp.stem}.md"
                    if not md_path.exists():
                        md_path.write_text(text, encoding="utf-8")

                fact = self._extractor.extract(text)
                req_count += 1

                import time

                ts = int(time.time())
                filtered_name = f"filtered_{fp.stem}_{ts}.md"
                filtered_path = self._output_dir / filtered_name
                filtered_path.write_text(
                    f"# Extracted requirement\n\n"
                    f"**source:** {name}\n"
                    f"**title:** {fact.title}\n"
                    f"**aspect:** {fact.aspect}\n"
                    f"**priority:** {fact.priority}\n"
                    f"**confidence:** {cls_result.confidence}\n\n"
                    f"{fact.description}\n",
                    encoding="utf-8",
                )
                results.append(ProcessedFile(source_file=name, fact=fact))

            print(f"    → {len(files) - spam_count} requirements, {spam_count} spam")

        print(
            f"\nTotal: {req_count} requirements, {spam_count} spam from {len(source_files)}"
        )
        return results
