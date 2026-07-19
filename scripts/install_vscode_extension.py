#!/usr/bin/env python3
"""Post-install hook: install VSCode extension if VSCode is present.

Runs automatically after ``pip install spec-editor``.
Skips silently if ``code`` CLI is not found.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _find_vsix() -> Path | None:
    """Locate the bundled .vsix file."""
    candidates = [
        Path(__file__).resolve().parent / "spec-editor-vscode-0.1.0.vsix",
        # Also check relative to the package data directory
        Path(sys.prefix) / "share" / "spec-editor" / "spec-editor-vscode-0.1.0.vsix",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _code_available() -> bool:
    """Check if 'code' CLI is on PATH."""
    return shutil.which("code") is not None


def _extension_installed() -> bool:
    """Check if spec-editor VSCode extension is already installed."""
    try:
        result = subprocess.run(
            ["code", "--list-extensions", "--show-versions"],
            capture_output=True, text=True, timeout=5,
        )
        return "spec-editor.spec-editor-vscode" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def install_vscode_extension() -> bool:
    """Install the bundled VSCode extension. Returns True on success."""
    if not _code_available():
        return False

    vsix = _find_vsix()
    if not vsix:
        return False

    if _extension_installed():
        # Already installed — try force-reinstall to update
        pass

    try:
        result = subprocess.run(
            ["code", "--install-extension", str(vsix), "--force"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


if __name__ == "__main__":
    if install_vscode_extension():
        print("spec-editor: VSCode extension installed successfully")
    else:
        # Silent skip — VSCode not available is normal
        pass
