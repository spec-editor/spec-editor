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

## 3. Run the agents

```bash
spec-editor run
```

Two AI agents discuss the requirements and produce a structured specification
in `aspects/`. They create modules, scenarios, data models, and non-functional
requirements.

> **Requires API key.** Set `DEEPSEEK_API_KEY` in `.env` or environment.
> Default model: DeepSeek Chat (~$.14/M tokens).

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

## What's next

- [Annotate your code](ARCHITECTURE.md#code-traceability) with `@implements`
- [Export specs](ARCHITECTURE.md#export-formats) to TRLC, OpenAPI, Jira CSV
- [Customize methodology](ARCHITECTURE.md#methodologies) for your domain
- [Contribute prompts](CONTRIBUTING_PROMPTS.md) to improve agent quality
