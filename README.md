<p align="center">
  <h1 align="center">Spec Editor</h1>
  <h3 align="center">AI-powered requirements engineering with methodology support</h3>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue.svg" alt="Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-340+-green.svg" alt="340+ tests"></a>
</p>

<p align="center">
  <a href="docs/demo.gif">▶ Watch the demo (GIF)</a>
</p>

---

## What is Spec Editor?

Spec Editor turns messy requirements documents into structured specifications
using multiple AI agents in a structured dialogue. Then it connects to your
AI coding agent (Claude Code, Cursor, Zed) via MCP so your generated code
stays aligned with your requirements.

**It is:**
- A CLI tool that generates specifications via multi-agent debate
- An MCP server so external AI agents can read your specification
- A code generator that creates skeletons from spec elements (SQLAlchemy, FastAPI, React, pytest)

**It is NOT:**
- A generic code generator (we do templates, not AI code gen)
- A task tracker (use Jira/Linear for that — we export to them)
- A replacement for developers (agents debate, humans decide)

> [!NOTE]
> Spec Editor works with any OpenAI-compatible API (DeepSeek, OpenAI, Anthropic).
> Default: DeepSeek Reasoner (~$0.55/M tokens, 5× cheaper than GPT-4).

---

## Who Is This For?

- **Business analysts** — turn stakeholder interviews and vague docs into structured specs
- **System analysts** — decompose requirements into modules, data models, and API contracts
- **Engineering teams** — need traceability from requirements to deployed code
- **Technical PMs** — tired of Word docs and Jira tickets drifting apart over time
- **AI-assisted developers** — using Cursor, Claude Code, or Zed — give your coding agent full spec context
- **Startups** — need structured requirements without hiring a dedicated systems analyst

---

## Before & After

**Input** — a single paragraph in `source/readme.md`:

> "We need a user authentication system with login, registration, and password reset."

**Output** — structured specification in `aspects/`:

```
aspects/
├── modules/MOD-003.md        Authentication Module
├── user_scenarios/SCN-007.md  User Login (happy path, error states, rate limiting)
├── user_scenarios/SCN-008.md  Password Reset (email flow, token expiry)
├── user_interface/UI-005.md   Login Form (widgets, validation rules)
├── data_entities/ENT-004.md   User entity (fields, constraints, relationships)
└── non_functional/NFR-002.md  Auth latency < 200ms, bcrypt hashing, OWASP compliance
```

Each element is a version-controlled Markdown file with YAML frontmatter —
diffable, mergeable, and connected via bidirectional traceability links.

---

## Why Not Just Prompt an LLM Directly?

A raw LLM prompt produces superficial, flat requirements. Spec Editor's
multi-agent debate and methodology-driven structure produce deeply
connected specifications — much better than what any single LLM prompt
can achieve.

| What happens with raw LLM | What spec-editor does |
|---|---|
| Single perspective | Multi-agent debate with structured rounds |
| No adversarial review | Agents challenge each other — edge cases, contradictions caught |
| Freeform output | Methodology-driven: modules, scenarios, UI, data, NFR, metrics |
| Ephemeral session | Version-controlled artifacts in git (Markdown + YAML) |

---

## Quick Start

```bash
pip install spec-editor

# 1. Instant preview (no API key)
spec-editor demo              # opens pre-generated spec in browser

# 2. Create project and run agents
spec-editor init my-project --with-example   # creates project with sample requirements
cd my-project
spec-editor run                               # needs DEEPSEEK_API_KEY in .env

# 3. Connect to your AI coding agent
spec-editor mcp &             # start MCP server in background
# Add the MCP config to your agent (see below)

# 4. Export to shareable format
spec-editor export -f html    # styled HTML report
spec-editor export -f srs     # IEEE 830 Markdown
spec-editor validate          # check methodology compliance
```

After `spec-editor run` completes, you'll have:
- `aspects/` — structured specification in Markdown + YAML frontmatter
- `source/session_summary.md` — what the agents did and why

---

## Connect to AI Coding Assistants (MCP)

Spec Editor runs an MCP server for any MCP-compatible agent
(Zed, Cursor, Claude Code, Windsurf, etc.).

```bash
spec-editor mcp &   # start in background
```

Add to your agent's MCP config (`.mcp.json`):

```json
{
  "mcpServers": {
    "spec-editor": {
      "command": "spec-editor",
      "args": ["mcp", "-p", "/absolute/path/to/project"]
    }
  }
}
```

### What Your Agent Gets

| Tool | Description |
|------|-------------|
| `get_context_for_file` | Spec context for a code file via `@implements` |
| `search_elements` | Full-text search across requirements |
| `read_element` | Read any specification element by ID |
| `list_all_elements` | Browse entire specification |

Add `@implements("REQ-ID")` decorators to your code — the agent
automatically pulls linked requirements into its context.

Full API reference: [readme_mcp.md](readme_mcp.md)

---

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│                     SPEC EDITOR                              │
│                                                              │
│  SOURCE DOCUMENTS                                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                      │
│  │ PDF/TXT  │ │ Telegram │ │  Voice   │  ...                 │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘                      │
│       │             │            │                            │
│       ▼             ▼            ▼                            │
│  ┌─────────────────────────────────────┐                     │
│  │        Ingestion Pipeline           │                     │
│  │  PDF → text, spam filter, SRC gen   │                     │
│  └─────────────────┬───────────────────┘                     │
│                    ▼                                          │
│  ┌─────────────────────────────────────┐                     │
│  │        AGENT DIALOGUE               │                     │
│  │  ┌──────────┐  ┌──────────┐         │                     │
│  │  │ Agent 1  │  │ Agent 2  │  +Orch  │                     │
│  │  │ modules  │  │scenarios │         │                     │
│  │  └────┬─────┘  └────┬─────┘         │                     │
│  │       │   debate    │               │                     │
│  │       ▼             ▼               │                     │
│  │  ┌─────────────────────────────┐    │                     │
│  │  │  Skill-based helpers        │    │                     │
│  │  │  scenario_decomposer,       │    │                     │
│  │  │  ui_navigator, metrics_linker   │                     │
│  │  └─────────────────────────────┘    │                     │
│  └─────────────────┬───────────────────┘                     │
│                    ▼                                          │
│  ┌─────────────────────────────────────┐                     │
│  │         SPECIFICATION               │                     │
│  │  aspects/modules/    MOD-001.md     │                     │
│  │  aspects/scenarios/  SCN-001.md     │                     │
│  │  aspects/entities/   ENT-001.md     │                     │
│  └──────────────────┬──────────────────┘                     │
│                     ▼                                        │
│  ┌──────────────────────────────────────┐                    │
│  │           MCP SERVER                  │                    │
│  │  19 tools — read_element,            │                    │
│  │  search_elements, list_aspect, ...   │                    │
│  └──────────────────┬───────────────────┘                    │
│                     ▼                                        │
│  ┌──────────────────────────────────────┐                    │
│  │     AI CODING AGENTS                  │                    │
│  │  Claude Code · Cursor · Zed · ...    │                    │
│  │  Code with full spec context          │                    │
│  └──────────────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────┘

```

### Key Features

| Feature | Description |
|---------|-------------|
| **Multi-agent dialogue** | 2 agents + orchestrator debate requirements in structured rounds |
| **Skill-based helpers** | Agents spawn specialised helpers: scenario decomposer, UI navigator, metrics linker |
| **Methodology-driven** | Waterfall decomposes into 8 aspects: modules, scenarios, UI, data, NFR, metrics, implementation, sources |
| **MCP server** | 20+ tools — connect to Claude Code, Cursor, Zed for context-aware code generation |
| **Export formats** | SRS (IEEE 830), TRLC (BMW), OpenAPI 3.0, Jira CSV, styled HTML |
| **Git-native** | Everything is Markdown + YAML in git — version, diff, merge, blame |

---

## Supported Methodologies

Specifications follow a methodology — a YAML-defined structure of aspects,
element types, and relationships.

| Methodology | What it generates | Status |
|-------------|-------------------|--------|
| **waterfall** | Full spec: modules, scenarios, UI, data, non-functional, implementation, metrics, sources | ✅ Free (OSS) |
| **agile** | Sprint backlog: epics → user stories → acceptance criteria + Jira CSV | ✅ Paid |
| **scrum** | Agile + sprints (goal, capacity, focus factor, velocity, DoD) | ✅ Paid |
| **kanban** | Agile + workflow stages (WIP limits, cycle time, throughput) | ✅ Paid |
| **api-first** | OpenAPI 3.0 contract (service → endpoint → schema + auth) | ✅ Paid |

> `waterfall` is free and bundled with the OSS release.
> Agile, scrum, kanban, and api-first are Methodology Packs (coming soon).

---

## CLI Commands

```bash
spec-editor demo                         # Instant preview (no API key)
spec-editor init ./my-project            # Create project
spec-editor run -p ./my-project          # Run agent dialogue
spec-editor view -p ./my-project         # Interactive Mermaid graph
spec-editor validate -p ./my-project     # Validate specification
spec-editor status -p ./my-project       # Show spec status
spec-editor export -p ./my-project       # Export to SRS/TRLC/OpenAPI/Jira/HTML
spec-editor mcp                          # Start MCP server (20+ tools)
```

---

## Export Formats

```bash
spec-editor export -p .                            # SRS document (default)
spec-editor export -p . -f html -o spec.html       # Styled HTML with relationships
spec-editor export -p . -f trlc -o spec.trlc       # TRLC (BMW-compatible)
spec-editor export -p . -f openapi -o api.yaml     # OpenAPI 3.0
spec-editor export -p . -f jira -o backlog.csv     # Jira CSV import
```

| Format | CLI flag | Output | Use case |
|--------|----------|--------|----------|
| **SRS (IEEE 830)** | `-f srs` (default) | Markdown | Stakeholder-ready specification |
| **HTML** | `-f html` | `spec.html` | Styled report with relationship traces |
| **TRLC** (BMW) | `-f trlc` | `.trlc` file | Requirements as code |
| **OpenAPI 3.0** | `-f openapi` | `openapi.yaml` | API contracts from api-first |
| **Jira CSV** | `-f jira` | `.csv` file | Sprint backlog for Jira import |
| **Markdown + YAML** | Native (git) | `aspects/*.md` | Git-native — version, diff, merge |

---

## Configuration

Edit `agents.yaml` to choose your provider:

```yaml
agents:
  agent_1:
    provider: deepseek     # or openai, anthropic
    model: deepseek/deepseek-reasoner
    temperature: 0.7
  agent_2:
    provider: deepseek
    model: deepseek/deepseek-reasoner
    temperature: 0.7
  orchestrator:
    provider: deepseek
    model: deepseek/deepseek-reasoner
```

---

## Contributing

Prompts are the engine of Spec Editor. Better prompts = better specifications.

- **Language packs** — translations for EN, RU, ES, FR, DE. Missing your language? Add `prompts/xx.yaml` and open a PR.
- **LLM-specific tuning** — DeepSeek, GPT-4, Claude each respond differently. Share your tuned prompts.
- **Few-shot examples** — help us add domain-specific examples.

Got ideas? **Open an issue** or **submit a PR** — we review everything.

---

## Documentation

- [Quickstart](docs/QUICKSTART.md) — 5-minute setup
- [Architecture](docs/ARCHITECTURE.md) — pipeline, components, CLI reference
- [MCP API Reference](readme_mcp.md) — MCP server tools
- [Contributing Prompts](docs/CONTRIBUTING_PROMPTS.md) — how to improve agent quality
- [CONTRIBUTING.md](CONTRIBUTING.md) — code contributions
- [CHANGELOG.md](CHANGELOG.md) — release history

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

The core engine (`spec-editor`) is free and open source.
Methodology Packs are source-available, purchased separately.
