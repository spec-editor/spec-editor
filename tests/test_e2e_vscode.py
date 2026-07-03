"""E2E tests for VSCode extension: compilation, structure, packaging.

Verifies:
- package.json is valid VSCode extension manifest
- TypeScript compiles to JS without errors
- All commands are properly registered
- Extension can be packaged with vsce

References:
    E2E-VSCODE-001: VSCode extension compilation and packaging
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# =============================================================================
# Helpers
# =============================================================================


def _extension_dir() -> Path:
    return Path(__file__).parent.parent / "packages" / "vscode-extension"


# =============================================================================
# E2E: VSCode extension
# =============================================================================


class TestVscodeExtensionE2E:
    """E2E: VSCode extension compiles and has valid structure."""

    def test_package_json_is_valid(self):
        """package.json parses and has required fields."""
        ext_dir = _extension_dir()
        pkg = json.loads((ext_dir / "package.json").read_text())

        assert pkg["name"] == "spec-editor-vscode"
        assert "main" in pkg, "Missing 'main' entry point"
        assert pkg["engines"]["vscode"] >= "1.85.0"

        # Commands
        commands = pkg["contributes"]["commands"]
        assert len(commands) >= 4, f"Expected 4 commands, got {len(commands)}"

        # Views
        views = pkg["contributes"]["views"]
        assert "spec-editor-sidebar" in views

        # Configuration (now a list of sections, each with title + properties)
        cfg_list = pkg["contributes"]["configuration"]
        assert isinstance(cfg_list, list), f"Expected list, got {type(cfg_list)}"
        total_props = sum(
            len((c if isinstance(c, dict) else {}).get("properties", {}))
            for c in cfg_list
        )
        assert total_props >= 3, f"Expected >=3 config properties, got {total_props}"

    def test_typescript_compiles(self):
        """TypeScript compiles to JavaScript without errors."""
        ext_dir = _extension_dir()
        result = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            capture_output=True,
            text=True,
            cwd=str(ext_dir),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"TypeScript compilation failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_dist_output_exists(self):
        """Compiled JS files exist in dist/."""
        ext_dir = _extension_dir()
        dist = ext_dir / "dist"
        assert dist.is_dir(), "dist/ directory missing"

        js_file = dist / "extension.js"
        assert js_file.is_file(), "dist/extension.js missing"
        assert js_file.stat().st_size > 100, "dist/extension.js is empty"

    def test_vsce_can_validate(self):
        """vsce can read and validate the extension manifest."""
        ext_dir = _extension_dir()
        result = subprocess.run(
            ["npx", "vsce", "ls", "--ignoreFile", "/dev/null"],
            capture_output=True,
            text=True,
            cwd=str(ext_dir),
            timeout=30,
        )
        # vsce ls lists packaged files — return code 0 means valid manifest
        assert result.returncode == 0, f"vsce validation failed:\n{result.stderr}"

    def test_all_commands_have_handlers(self):
        """Every declared command has a registered handler in extension.ts."""
        ext_dir = _extension_dir()
        pkg = json.loads((ext_dir / "package.json").read_text())
        source = (ext_dir / "src" / "extension.ts").read_text()

        for cmd in pkg["contributes"]["commands"]:
            cmd_id = cmd["command"]
            # The command ID should appear in registerCommand() or as a function handler
            assert cmd_id in source, (
                f"Command '{cmd_id}' not referenced in extension.ts"
            )
