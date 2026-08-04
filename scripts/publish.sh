#!/bin/bash
# Publish spec-editor: pip package + VSCode extension + git push.
# Usage: bash scripts/publish.sh
#
# Steps:
#   1. Build Python package
#   2. Ask for confirmation
#   3. Publish to PyPI (asks for API token)
#   4. Build & publish VSCode extension (asks for PAT)
#   5. Git commit version bumps & push
#
# All secret prompts (tokens) are interactive — not stored anywhere.

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION=$(cat VERSION)
echo "============================================"
echo "  spec-editor release v$VERSION"
echo "============================================"
echo ""

# ── Step 1: Confirm version ──
read -p "Release version $VERSION? [y/N] " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# ── Step 2: Build Python package ──
echo ""
echo "[1/5] Building Python package..."
.venv/bin/python -m build
echo "  -> dist/spec_editor-$VERSION-py3-none-any.whl"
echo "  -> dist/spec_editor-$VERSION.tar.gz"

# ── Step 3: Publish to PyPI ──
echo ""
echo "[2/5] Publishing to PyPI..."
echo "  (you will be prompted for your PyPI API token)"
.venv/bin/python -m twine upload "dist/spec_editor-$VERSION-py3-none-any.whl" "dist/spec_editor-$VERSION.tar.gz"
echo "  -> https://pypi.org/project/spec-editor/$VERSION/"

# ── Step 4: Build & publish VSCode extension ──
echo ""
echo "[3/5] Building VSCode extension..."
cd packages/vscode-extension
npm run build
echo ""

echo "[4/5] Publishing VSCode extension..."
echo "  (you will be prompted for your VSCode Marketplace PAT)"
npx vsce publish
echo "  -> https://marketplace.visualstudio.com/items?itemName=spec-editor.spec-editor-vscode"
cd "$ROOT"

# ── Step 5: Git commit & push ──
echo ""
echo "[5/5] Git commit & push..."
git add VERSION pyproject.toml packages/vscode-extension/package.json \
        packages/vscode-extension/spec-editor-vscode-*.vsix 2>/dev/null || true
if git diff --cached --quiet; then
    echo "  Nothing to commit."
else
    git commit -m "Release v$VERSION"
fi

read -p "Push to origin/main? [y/N] " PUSH
if [[ "$PUSH" =~ ^[Yy]$ ]]; then
    git push origin main
    echo "  -> Pushed to origin/main"
else
    echo "  Skipped push. Run 'git push' manually when ready."
fi

echo ""
echo "============================================"
echo "  Release v$VERSION complete!"
echo "============================================"
