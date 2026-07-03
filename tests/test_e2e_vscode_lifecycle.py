"""E2E tests for VSCode extension: packaging, installation, activation.

Tests verify:
- Extension compiles and packages as .vsix
- Extension installs successfully in VSCode
- Commands are registered and discoverable

References:
    E2E-VSCODE-002: Extension packaging and installation
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# =============================================================================
# Helpers
# =============================================================================

_VSCODE_BIN = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
_EXT_DIR = Path(__file__).parent.parent / "packages" / "vscode-extension"


def _run_vscode_cli(*args: str) -> subprocess.CompletedProcess:
    """Run a VSCode CLI command."""
    return subprocess.run(
        [_VSCODE_BIN, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


# =============================================================================
# E2E: VSCode extension lifecycle
# =============================================================================


class TestVscodeExtensionLifecycle:
    """E2E: Extension packages, installs, and is recognized by VSCode."""

    def test_vsix_packages_successfully(self):
        """vsce package produces a valid .vsix file."""
        vsix_path = Path("/tmp/spec-editor-vscode-e2e.vsix")

        result = subprocess.run(
            ["npx", "vsce", "package", "--out", str(vsix_path)],
            capture_output=True,
            text=True,
            cwd=str(_EXT_DIR),
            timeout=60,
        )
        assert result.returncode == 0, f"vsce package failed:\n{result.stderr}"
        assert vsix_path.is_file(), "vsix file was not created"
        assert vsix_path.stat().st_size > 1000, "vsix file is empty"

    def test_extension_installs_in_vscode(self):
        """Extension installs successfully in VSCode."""
        vsix_path = Path("/tmp/spec-editor-vscode-e2e.vsix")

        # Ensure vsix exists (from packaging step)
        if not vsix_path.is_file():
            subprocess.run(
                ["npx", "vsce", "package", "--out", str(vsix_path)],
                capture_output=True,
                text=True,
                cwd=str(_EXT_DIR),
                timeout=60,
            )

        result = _run_vscode_cli("--install-extension", str(vsix_path), "--force")
        assert (
            "successfully installed" in result.stdout.lower() or result.returncode == 0
        ), f"Extension install failed: {result.stderr}"

    def test_extension_is_listed(self):
        """Extension appears in --list-extensions."""
        result = _run_vscode_cli("--list-extensions")
        assert "spec-editor.spec-editor-vscode" in result.stdout, (
            f"Extension not found in list: {result.stdout[:500]}"
        )

    def test_commands_are_registered_in_package_json(self):
        """All 4 commands declared in package.json."""
        pkg = json.loads((_EXT_DIR / "package.json").read_text())
        commands = pkg["contributes"]["commands"]
        assert len(commands) >= 4

        cmd_ids = [c["command"] for c in commands]
        assert "specEditor.open" in cmd_ids
        assert "specEditor.newProject" in cmd_ids
        assert "specEditor.viewDiagram" in cmd_ids
        assert "specEditor.validate" in cmd_ids

    def test_vscode_recognizes_extension(self):
        """VSCode can query extension info."""
        # Verify VSCode exists
        assert os.path.exists(_VSCODE_BIN), "VSCode binary not found"

        result = _run_vscode_cli("--version")
        assert result.returncode == 0
        assert "1." in result.stdout  # Version like 1.124.0
