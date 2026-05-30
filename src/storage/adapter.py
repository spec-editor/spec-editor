"""Abstract storage interface (StorageAdapter)."""

from abc import ABC, abstractmethod

from src.storage.models import Element, ElementSummary


class StorageAdapter(ABC):
    """Requirements element storage interface.

    Allows replacing file storage with a database without changing agent code.
    """

    @abstractmethod
    def read_element(self, element_id: str) -> Element:
        """Read a full element by ID."""
        ...

    @abstractmethod
    def write_element(self, element: Element) -> None:
        """Create or update an element."""
        ...

    @abstractmethod
    def delete_element(self, element_id: str) -> None:
        """Delete an element by ID."""
        ...

    @abstractmethod
    def list_aspect(self, aspect_name: str) -> list[ElementSummary]:
        """Get a list of all elements in an aspect (summary form)."""
        ...

    @abstractmethod
    def list_all(self) -> list[ElementSummary]:
        """Get a list of all project elements (summary form)."""
        ...

    @abstractmethod
    def find_related(self, element_id: str) -> list[ElementSummary]:
        """Find all elements related to this one."""
        ...

    @abstractmethod
    def search(self, query: str) -> list[ElementSummary]:
        """Full-text search across elements."""
        ...

    @abstractmethod
    def get_element_path(self, element_id: str) -> str | None:
        """Get the file path of an element by ID (for debugging)."""
        ...

    @abstractmethod
    def exists(self, element_id: str) -> bool:
        """Check if an element exists."""
        ...
