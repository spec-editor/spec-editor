"""Spec Editor — terminal demo with Rich formatting.

Usage:
  asciinema rec docs/demo.cast
  python docs/demo.py
  exit
  agg docs/demo.cast docs/demo.gif
"""

import time

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console(width=100)


def header(text: str) -> None:
    console.print()
    console.print(Panel(text, style="bold magenta", width=80))
    console.print()


def cmd(command: str, comment: str = "") -> None:
    console.print(f"  [bold bright_cyan]$[/] [bold white]{command}[/]", end="")
    if comment:
        console.print(f"  [dim]# {comment}[/]", end="")
    console.print()


def info(text: str) -> None:
    console.print(f"  [dim]→ {text}[/]")


def step(n: int, title: str) -> None:
    console.print()
    console.print(f"  [bold yellow]{n}.[/] [bold]{title}[/]")
    console.print()


def green(text: str) -> None:
    console.print(f"  [green]{text}[/]")


def sleep(s: float = 3.0) -> None:
    time.sleep(s)


# ──────────────────────────────────────
header("Spec Editor — Quick Demo")
sleep(1)

# 1. Demo
step(1, "Instant preview — no API key needed")
cmd("spec-editor demo")
sleep(0.3)
info("Opens pre-generated bookstore spec in your browser")
info("15 structured elements: modules, scenarios, entities, NFRs")
info("This is what YOUR requirements will look like")
sleep()

# 2. Init
step(2, "Create a real project")
cmd("spec-editor init my-project --with-example")
sleep(0.3)
cmd("cd my-project")
sleep(0.2)
cmd("ls")
sleep(0.2)
info("source/     ← put your requirements here")
info("aspects/    ← agents generate structured spec here")
sleep()

# 3. Run — key selling point
step(3, "Run agents — multi-agent debate")
cmd("spec-editor run", "needs DEEPSEEK_API_KEY in .env")
sleep(0.5)
info("Agent 1 (reasoner) — creates modules, entities, scenarios")
info("Agent 2 (reasoner) — adds NFRs, UI, metrics, links")
info("Orchestrator — evaluates, directs the debate")
sleep(0.5)
console.print()
console.print(
    Panel(
        "[bold yellow]⚡ A raw LLM prompt gives ~20-30% of this quality.[/]\n"
        "[dim]spec-editor's multi-agent debate + methodology\n"
        "produces deeply structured, connected specifications.[/]",
        border_style="yellow",
        width=80,
    )
)
sleep()

# 4. View + Validate
step(4, "View and validate the result")
cmd("spec-editor view")
sleep(0.3)
info("Opens interactive Mermaid graph in browser")
sleep(0.5)
cmd("spec-editor validate")
sleep(0.3)
# Simulated validate output
checks = [
    ("OK", "Elements readable"),
    ("OK", "No duplicate IDs"),
    ("OK", "Required fields (aspect, type, title)"),
    ("OK", "Parent/children references"),
    ("OK", "Relationship types vs methodology"),
    ("OK", "Aspect & element types vs methodology"),
]
for status, label in checks:
    console.print(f"  [green]  {status}[/]    {label}")
    sleep(0.1)
green("Passed. 95 elements, no errors.")
sleep()

# 5. MCP — main use case
step(5, "Connect to your AI coding agent via MCP")
cmd("spec-editor mcp &", "start MCP server in background")
sleep(0.5)
console.print()
mcp_config = """{
  "mcpServers": {
    "spec-editor": {
      "command": "spec-editor",
      "args": ["mcp", "-p", "."]
    }
  }
}"""
console.print("  [dim]Add to your agent's MCP config (.mcp.json):[/]")
console.print(Syntax(mcp_config, "json", theme="monokai", background_color="default"))
sleep(0.5)
info("→ Claude Code / Cursor / Zed now knows your requirements")
info("→ Generated code is deeply aligned with your spec,")
info("   not just raw documents")
sleep()

# 6. Export
step(6, "Export to shareable formats")
cmd("spec-editor export -f html")
sleep(0.3)
info("Styled HTML report with full relationship traces")
cmd("spec-editor export -f srs")
sleep(0.3)
info("IEEE 830 Markdown document")
sleep()

# Done
console.print()
console.print(
    Panel(
        "[bold]spec-editor[/]\n"
        "[dim]github.com/spec-editor/spec-editor[/]\n"
        "[dim]pip install spec-editor[/]",
        width=80,
    )
)
sleep(2)
