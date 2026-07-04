# Quickstart

Get from zero to a structured specification in 5 minutes.

## 1. Install

```bash
pip install spec-editor
```

## 2. Create a project

```bash
spec-editor init my-project --with-example
cd my-project
```

This creates a project with a sample requirements document in `source/readme.md`.

For a blank project, omit `--with-example`. Use `--non-interactive` to skip prompts.

## 3. Run the agents

```bash
spec-editor run
```

Two AI agents (DeepSeek Reasoner) discuss the requirements and produce
a structured specification in `aspects/`. They create modules, scenarios,
data models, UI, metrics, and non-functional requirements.

> **Requires API key.** Set `DEEPSEEK_API_KEY` in `.env` or environment.

## 4. See the result

```bash
spec-editor view     # Interactive graph in browser
spec-editor status   # Summary table
spec-editor validate # Check for errors
```

## 5. Try the demo (no API key needed)

```bash
spec-editor demo
```

Opens a pre-generated bookstore specification in your browser.
See what the output looks like before you run the agents.

## 6. Connect to your AI coding agent

```bash
spec-editor mcp &    # Start MCP server in background
```

Add to your agent's MCP config (`.mcp.json`):

```json
{
  "mcpServers": {
    "spec-editor": {
      "command": "spec-editor",
      "args": ["mcp", "-p", "."]
    }
  }
}
```

Now Claude Code, Cursor, or Zed can read your specification
for context-aware code generation.

## 7. Export

```bash
spec-editor export -f html    # Styled HTML report
spec-editor export -f srs     # IEEE 830 Markdown
spec-editor export -f trlc    # BMW-compatible
```

## What's next

- [View the architecture](ARCHITECTURE.md) — pipeline, components, CLI reference
- [Connect via MCP](readme_mcp.md) — full API reference
- [Contribute prompts](CONTRIBUTING_PROMPTS.md) — improve agent quality
