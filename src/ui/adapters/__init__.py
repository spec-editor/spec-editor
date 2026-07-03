"""Editor adapters for project discovery, filesystem access, git, and UI.

Exports:
    IEditorAdapter — abstract base class
    StandaloneAdapter — CLI/standalone mode (.spec-project marker, git CLI, direct FS)
"""

from src.ui.adapters.base import IEditorAdapter
from src.ui.adapters.standalone import StandaloneAdapter
from src.ui.adapters.vscode import VscodeAdapter

__all__ = ["IEditorAdapter", "StandaloneAdapter", "VscodeAdapter"]
