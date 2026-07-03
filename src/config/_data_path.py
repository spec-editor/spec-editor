"""Data path resolver — works regardless of CWD.

All paths to bundled data/ files should use :func:`data_path`
instead of ``importlib.resources.files("data")`` so that the CLI
can be run from any project directory, not just spec-editor2.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent  # src/config → project root


def data_path(relative: str = "") -> Path:
    """Resolve a path inside the spec-editor ``data/`` directory.

    Args:
        relative: Path relative to ``data/`` (e.g. ``"prompts/en.yaml"``).

    Returns:
        Absolute path to the bundled resource.
    """
    return _ROOT / "data" / relative
