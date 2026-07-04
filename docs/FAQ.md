# FAQ

## General

### What does Spec Editor actually do?

It takes unstructured requirements (PDFs, text documents, chat logs) and
produces a structured, traceable specification with modules, scenarios,
data models, UI sections, and non-functional requirements. It uses multiple
AI agents in debate to catch edge cases and contradictions.

### How is this different from just prompting ChatGPT?

A single LLM prompt gives you flat, superficial output. Spec Editor runs
a multi-agent debate: two agents challenge each other's work, an orchestrator
moderates, and specialized helpers decompose scenarios, link metrics, and
navigate UI flows. The result is deeply connected — not just a list of bullets.

### How is this different from Jira / Confluence / Notion?

Those are *documentation* tools. Spec Editor is a *requirements engineering*
tool. It generates the structure, enforces methodology, and maintains
bidirectional traceability to code. You can export to Jira CSV — they
complement each other.

---

## Setup & Usage

### Do I need an API key?

Yes, for `spec-editor run`. Set `DEEPSEEK_API_KEY` in your `.env` file.
Get one at [platform.deepseek.com](https://platform.deepseek.com).

You can try `spec-editor demo` without an API key — it opens a
pre-generated example specification in your browser.

### What does it cost to run?

A typical `spec-editor run` costs $0.05–0.30 with DeepSeek Reasoner.
With GPT-4 it would be 3–5× more. Exact cost depends on the size of
your source documents and the number of dialogue rounds.

### Can I use OpenAI or Anthropic instead of DeepSeek?

Yes. Edit `agents.yaml`:

```yaml
agents:
  agent_1:
    provider: openai
    model: gpt-4o
  agent_2:
    provider: openai
    model: gpt-4o
  orchestrator:
    provider: openai
    model: gpt-4o
```

Set `OPENAI_API_KEY` in `.env`. Works with any OpenAI-compatible API.

### Does it work offline?

Not yet for AI generation. The offline `.license` file validation
works without internet, but the agents need an LLM API.

---

## Features

### What programming languages does traceability support?

Python, TypeScript, JavaScript, Go, Java, Kotlin, Rust. Add
`@implements("REQ-ID")` decorators/annotations to link code to requirements.

### Can I create my own methodology?

Yes. Methodologies are YAML files defining aspects, element types,
and relationships. See `data/methodologies/waterfall.yaml` for the format.
Place custom methodologies in your project's `methodology.yaml`.

### Does it integrate with my IDE?

Yes, via MCP (Model Context Protocol). Spec Editor exposes 20+ tools
that any MCP-compatible agent can use: Claude Code, Cursor, Zed,
Windsurf, and others.

### Can it generate code?

It generates code *skeletons* from templates (SQLAlchemy models, FastAPI
routers, React components, pytest tests). It does NOT do AI code generation —
that's what your coding agent (Claude Code, Cursor, etc.) does, using
spec-editor's MCP tools for context.

---

## Troubleshooting

### "methodology.yaml not found"

Run `spec-editor init` first — it creates the project structure.

### "LLM request timed out"

The API request took too long. Try:
- Check your API key is valid
- Reduce the source document size
- Increase timeout: `SPEC_EDITOR__LLM_REQUEST_TIMEOUT=120`

### "No module named 'src.licensing.gumroad'"

This is expected in the OSS version. GumRoad license validation requires
the Pro plugin. The Free tier uses offline or noop validation by default.
