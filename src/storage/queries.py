"""Shared storage queries used across the codebase.

Eliminates duplicated loops over list_all() + read_element()
and other repetitive storage access patterns.
"""

from src.storage.adapter import StorageAdapter
from src.storage.models import Element, ElementStatus


def get_orphan_ids(storage: StorageAdapter) -> list[str]:
    """Return IDs of elements with no parent, children, or relationships."""
    ids = []
    for summary in storage.list_all():
        if summary.id.startswith("SRC-"):
            continue
        if not summary.parent and not summary.children and not summary.relationships:
            ids.append(summary.id)
    return ids


def load_all_elements(storage: StorageAdapter) -> list[Element]:
    """Load all elements from storage, skipping unreadable ones."""
    elements = []
    for summary in storage.list_all():
        try:
            elements.append(storage.read_element(summary.id))
        except Exception:
            pass  # skip corrupted/unreadable elements
    return elements


def promote_drafts_to_reviewed(storage: StorageAdapter) -> int:
    """Promote all DRAFT elements to REVIEWED (except SRC).

    Returns the count of promoted elements."""
    count = 0
    for summary in storage.list_all():
        if summary.id.startswith("SRC-"):
            continue
        try:
            element = storage.read_element(summary.id)
            if element.status == ElementStatus.DRAFT:
                element.status = ElementStatus.REVIEWED
                storage.write_element(element)
                count += 1
        except Exception:
            pass
    return count
