"""Requirement deprecation manager — deprecate / restore."""

from pathlib import Path

from src.config import get_logger
from src.providers.base import LLMProvider
from src.storage.adapter import StorageAdapter
from src.storage.models import ElementStatus

logger = get_logger(__name__)

# Prompt for extracting features from a deprecation file
_DEPRECATE_PROMPT = """\
You are analysing a file requesting removal of requirements from the specification.

Below is the list of ALL confirmed/reviewed requirements (ID: title),
and the contents of the deprecation file.

Your task: find which requirement IDs should be marked as deprecated
based on the file contents.

IMPORTANT: look for semantic matches, not exact. "Export" may
match "Export data to PDF" or "Export to CSV".

Return ONLY a JSON array of IDs' that should be deprecated.
If no matches — return an empty array [].

Response format: ONLY JSON, no explanation.
targets: ["NFR-export-pdf", "MOD-notifications"]

=== Result ===
{elements}

=== Analysis Report ===
{file_content}
"""


def _build_element_list(storage: StorageAdapter) -> str:
    """Build a compact list of elements for the LLM."""
    all_elements = storage.list_all()
    lines = []
    for summary in all_elements:
        if summary.status.value in ("confirmed", "reviewed"):
            lines.append(f"{summary.id}: {summary.title}")
    return "\n".join(lines)


def _parse_llm_response(text: str) -> list[str]:
    """Extract a JSON array of IDs from an LLM response."""
    import json

    text = text.strip()
    # Look for a JSON array in the response
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return []


async def deprecate_from_file(
    storage: StorageAdapter,
    provider: LLMProvider,
    file_path: Path,
    dry_run: bool = True,
) -> dict:
    """Read a deprecation file, find and deprecate matching requirements.

    Args:
        storage: element store
        provider: LLM client
        file_path: Path to file with deprecation requirements
        dry_run: True — showing changes without writing

    Returns:
        {'dry_run': bool, 'file': str, 'deprecated': [str, ...], 'not_found': [str, ...]}
    """
    if not file_path.exists():
        return {"error": f"File not found or invalid: {file_path}"}

    file_content = file_path.read_text(encoding="utf-8")
    element_list = _build_element_list(storage)

    if not element_list:
        return {"error": "Only confirmed/reviewed elements can be deprecated"}

    prompt = _DEPRECATE_PROMPT.format(elements=element_list, file_content=file_content)

    from src.providers.base import LLMResponse, Message, MessageRole

    messages = [
        Message(role=MessageRole.SYSTEM, content="You are a deprecation analyst. Identify obsolete requirements."),
        Message(role=MessageRole.USER, content=prompt),
    ]

    response = await provider.complete(messages=messages)
    ids_to_deprecate = _parse_llm_response(response.content or "")

    if not ids_to_deprecate:
        return {
            "dry_run": dry_run,
            "file": str(file_path),
            "deprecated": [],
            "not_found": [],
            "message": "Processing complete",
        }

    deprecated = []
    not_found = []

    for req_id in ids_to_deprecate:
        try:
            element = storage.read_element(req_id)
            if dry_run:
                deprecated.append(
                    {
                        "id": req_id,
                        "title": element.title,
                        "status": element.status.value,
                    }
                )
            else:
                element.status = ElementStatus.DEPRECATED
                storage.write_element(element)
                deprecated.append(
                    {"id": req_id, "title": element.title, "status": "deprecated"}
                )
                logger.info("element_deprecated", id=req_id, title=element.title)
        except KeyError:
            not_found.append(req_id)

    return {
        "dry_run": dry_run,
        "file": str(file_path),
        "deprecated": deprecated,
        "not_found": not_found,
    }


def restore_elements(
    storage: StorageAdapter,
    element_ids: list[str],
) -> dict:
    """Restore deprecated elements back to draft status.

    Returns:
        {'restored': [str, ...], 'not_found': [str, ...], 'not_deprecated': [str, ...]}
    """
    restored = []
    not_found = []
    not_deprecated = []

    for req_id in element_ids:
        try:
            element = storage.read_element(req_id)
            if element.status == ElementStatus.DEPRECATED:
                element.status = ElementStatus.DRAFT
                storage.write_element(element)
                restored.append({"id": req_id, "title": element.title})
                logger.info("element_restored", id=req_id, title=element.title)
            else:
                not_deprecated.append(
                    {
                        "id": req_id,
                        "title": element.title,
                        "current": element.status.value,
                    }
                )
        except KeyError:
            not_found.append(req_id)

    return {
        "restored": restored,
        "not_found": not_found,
        "not_deprecated": not_deprecated,
    }
