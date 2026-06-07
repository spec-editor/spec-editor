#!/bin/bash
# Spec Editor — GIF demo script.
# Records the full workflow: demo → run → mcp → export.
#
# Usage:
#   terminalizer record -c docs/terminalizer.yml docs/demo.yml
#   # or: vhs docs/demo.tape
#
# Requirements:
#   pip install spec-editor
#   DEEPSEEK_API_KEY in .env (only for step 3)

set -e

echo "═══════════════════════════════════════════"
echo "  Spec Editor — Quick Demo"
echo "═══════════════════════════════════════════"
echo ""

# ── 1. Aha-moment: see a pre-built spec without API key ──
echo "==> 1. Demo (no API key)"
echo "$ spec-editor demo"
sleep 1
echo ""
echo "  → Opens a pre-generated bookstore spec in your browser."
echo "  → 15 structured elements: modules, scenarios, entities, NFRs."
echo "  → This is what YOUR requirements will look like."
sleep 1
echo ""

# ── 2. Create a real project ──
echo "==> 2. Create project"
echo "$ spec-editor init my-project --with-example"
echo "$ cd my-project"
echo "$ ls"
echo "  source/     ← put your requirements here"
echo "  aspects/    ← agents generate structured spec here"
sleep 1
echo ""

# ── 3. Run agents (needs API key) ──
echo "==> 3. Run agents"
echo "$ spec-editor run"
echo ""
echo "  Agent 1 (reasoner) ← creates modules, entities, scenarios"
echo "  Agent 2 (reasoner) ← adds NFRs, UI, metrics, links"
echo "  Orchestrator       ← evaluates, directs"
echo ""
echo "  ⚡ A raw LLM prompt gives ~20-30% of this quality."
echo "     spec-editor's multi-agent debate + methodology"
echo "     produces deeply structured, connected specs."
sleep 2
echo ""

# ── 4. See the result ──
echo "==> 4. View & validate"
echo "$ spec-editor view       # interactive Mermaid graph"
echo "$ spec-editor validate   # checks methodology compliance"
echo "  ✓ Elements readable"
echo "  ✓ No duplicate IDs"
echo "  ✓ Required fields"
echo "  ✓ References valid"
echo "  ✓ Relationship types"
echo "  ✓ Methodology compliance"
echo "  Passed. 95 elements, no errors."
sleep 1
echo ""

# ── 5. MCP — connect to AI coding agent ──
echo "==> 5. Start MCP server"
echo "$ spec-editor mcp &"
echo ""
echo "  Add to your agent's MCP config (.mcp.json):"
echo '  {'
echo '    "mcpServers": {'
echo '      "spec-editor": {'
echo '        "command": "spec-editor",'
echo '        "args": ["mcp", "-p", "."]'
echo '      }'
echo '    }'
echo '  }'
echo ""
echo "  → Now Claude Code / Cursor / Zed knows your requirements."
echo "  → Generated code is deeply aligned with your spec,"
echo "     not just raw docs."
sleep 2
echo ""

# ── 6. Export ──
echo "==> 6. Export"
echo "$ spec-editor export -f html   # styled HTML report"
echo "$ spec-editor export -f srs    # IEEE 830 Markdown"
echo ""
echo "  → Both include full relationship traces."
sleep 1
echo ""

echo "═══════════════════════════════════════════"
echo "  Done."
echo ""
echo "  spec-editor: github.com/spec-editor/spec-editor"
echo "  pip install spec-editor"
echo "═══════════════════════════════════════════"
