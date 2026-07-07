<p align="center">
  <h1 align="center">Spec Editor</h1>
  <h3 align="center">Active Memory Layer for Requirements & Code — powered by AI agents</h3>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue.svg" alt="Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-340+-green.svg" alt="340+ tests"></a>
  <a href="#vs-code-extension"><img src="https://img.shields.io/badge/VS_Code-extension-blue.svg" alt="VS Code extension"></a>
  <a href="#web-ui"><img src="https://img.shields.io/badge/Web_UI-included-green.svg" alt="Web UI"></a>
</p>

<p align="center">
  <a href="docs/demo.gif">▶ Watch the demo (GIF)</a>
</p>

---

**Spec Editor is an active memory layer for AI-driven development.**
Unlike passive docs, this memory is alive — AI agents continuously debate,
cross-reference, and evolve the project knowledge.

```bash
pip install spec-editor && spec-editor init --with-example && spec-editor run
```

> *Every AI coding agent suffers from amnesia between sessions. Spec Editor gives them — and your team — a shared, persistent memory that grows smarter with every run. Not a wiki. Not a task tracker. An active, self-maintaining knowledge base that debates its own completeness.*

---

## Why Now?

Cursor, Copilot, and Claude Code have made AI-assisted coding mainstream. But they all share one critical flaw: **no memory between sessions**. Every conversation starts from zero.

Spec Editor is the missing layer — a **team memory for the AI era**. Solo developers get a structured analysis process they'd otherwise skip. Teams get a single source of truth that stays in sync with code via `@implements` traceability.

> Built with 2 years of LLM engineering experience and 20+ years in software development.

---

## What is Spec Editor?

Spec Editor is an **active memory system** for your project. AI agents don't just
write to it — they debate, cross-validate, and continuously refine the knowledge.
Every specification element is a version-controlled artifact that any AI coding
agent can query via MCP.

```
         ┌─────────────────────────────────────┐
         │           ACTIVE MEMORY              │
         │                                      │
         │   ┌──────┐  ┌──────┐  ┌─────────┐   │
         │   │Agent 1│  │Agent 2│  │Orchestr.│  │  ← debate & refine
         │   └──┬───┘  └───┬───┘  └────┬────┘   │
         │      │          │           │        │
         │      ▼          ▼           ▼        │
         │   ┌──────────────────────────────┐   │
         │   │  Structured Knowledge Base   │   │  ← YAML + Markdown + Git
         │   │  MOD-001  SCN-007  ENT-004   │   │
         │   └──────────────────────────────┘   │
         │                                      │
         │   MCP → Cursor, Claude, Zed, ...     │  ← any agent can query
         └─────────────────────────────────────┘
```

Built around a **pluggable methodology system** — define any set of aspects
(for ex. modules, scenarios, UI, entities, NFRs), their relationships, and the
agent skills that populate them. Create your own or use the built-in waterfall.

**It is:**
- An **active memory layer** — AI agents debate, maintain, and evolve project knowledge
- A methodology engine — define your own aspects, relationships, and agent skills in YAML
- An architectural code generator — produces structured code from patterns (hexagonal, DDD, MVC)
- An MCP server — 20+ tools for external AI agents to read and search your specification
- A VS Code extension + web UI — browse specs visually, no terminal required

**It is NOT:**
- A replacement for human decision-making — agents debate, humans decide

> [!NOTE]
> Works with any OpenAI-compatible API. Default: DeepSeek Reasoner (~$0.55/M tokens).
> **Free & Open Source** — Apache 2.0. No per-seat pricing, no vendor lock-in, your data stays in your Git repo.

---

## Who Is This For?

- **Business analysts** — turn stakeholder interviews and vague docs into structured specs
- **System analysts** — decompose requirements into modules, data models, and API contracts
- **Engineering teams** — need traceability from requirements to deployed code
- **Technical PMs** — tired of Word docs and Jira tickets drifting apart over time
- **AI-assisted developers** — using Cursor, Claude Code, or Zed — give your coding agent full spec context
- **AI agent developers** — give your agents a shared, persistent memory of project requirements, decisions, and code contracts via MCP
- **Vibe-coders** — gives you a secret sauce of technical architecture and professional-grade requirements

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
| No persistent memory | **Full project memory** — every decision, requirement, and relationship is versioned in Git |

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
| `search_elements` | Full-text and semantic search across requirements |
| `read_element` | Read any specification element by ID |
| `list_all_elements` | Browse entire specification |

Add `@implements("REQ-ID")` decorators to your code — the agent
automatically pulls linked requirements into its context. This gives
AI coding assistants supercharged debugging: they see not just your code,
but the exact requirements it was built to satisfy. Bugs get traced
back to spec elements instantly.

Full API reference: [readme_mcp.md](readme_mcp.md)

---

## VS Code Extension

Install from the `.vsix` file included in the repository:

```bash
code --install-extension packages/vscode-extension/spec-editor-vscode-0.1.0.vsix
```

**What you get:**
- **Tree view** — browse aspects and all spec elements
- **Validation panel** — see errors and warnings inline as you work
- **Mermaid diagrams** — visualize relationships between elements

The extension automatically connects to the MCP server started by `spec-editor mcp`.

---

## Web UI (Experimental)

Launch a browser-based interface to explore your specification visually:

```bash
cd packages/frontend/out
python3 -m http.server 3000
# Open http://localhost:3000
```

Or with Docker (configured during `spec-editor init`):

```bash
docker compose up -d
```

Ideal for team reviews, stakeholder walkthroughs, and non-technical users.
A web-cloud version is coming!

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
│  └──────────────────┬───────────────────┘                    │
│                     ▼                                        │
│  ┌──────────────────────────────────────┐                    │
│  │    VS CODE EXTENSION + WEB UI        │                    │
│  │  Tree view · Diagrams · Validation   │                    │
│  │  Browser UI for non-technical users  │                    │
│  └──────────────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────┘

```

### Key Features

| Feature | Description |
|---------|-------------|
| **Multi-agent dialogue** | 2 agents + orchestrator debate requirements in structured rounds |
| **Pluggable methodologies** | Define any set of aspects, relationships, and agent skills in YAML — not locked into one framework |
| **Skill-based helpers** | Agents spawn specialised helpers: scenario decomposer, UI navigator, metrics linker |
| **Architectural codegen** | Generates code following patterns: hexagonal, DDD, clean architecture, MVC |
| **MCP server** | 20+ tools — connect to Claude Code, Cursor, Zed for context-aware code generation |
| **Export formats** | SRS (IEEE 830), TRLC (BMW), OpenAPI 3.0, Jira CSV, styled HTML |
| **Git-native** | Everything is Markdown + YAML in git — version, diff, merge, blame |
| **Pluggable subsystems** | Swappable backends for ingestion, visualization, storage, secrets, events, auth, and notifications |

---

## Supported Methodologies

Specifications follow a **methodology** — a YAML-defined structure of aspects,
element types, cross-aspect relationships, and agent skills. Create your own
or use the built-in ones:

| Methodology | What it generates | Status |
|-------------|-------------------|--------|
| **waterfall** | Full spec: modules, scenarios, UI, entities, non-functional, implementation, metrics, sources | ✅ Bundled |
| **agile** | Sprint backlog: epics → user stories → acceptance criteria + Jira CSV | 🔜 Coming |
| **scrum** | Agile + sprints (goal, capacity, focus factor, velocity, DoD) | 🔜 Coming |
| **kanban** | Agile + workflow stages (WIP limits, cycle time, throughput) | 🔜 Coming |
| **api-first** | OpenAPI 3.0 contract (service → endpoint → schema + auth) | 🔜 Coming |

Create your own methodology in YAML — define aspects, element types,
cross-aspect relationships, and agent skills. See `data/methodology.yaml`
for the waterfall example.

### Reverse Engineering

Already have code but no spec? Use `reengineer` mode to extract requirements
from an existing codebase:

```bash
spec-editor reengineer ./my-codebase   # reads @implements, docstrings, types
spec-editor run                        # agents fill in the gaps
```

Supported languages: Python, TypeScript, JavaScript, Go, Java, Kotlin, Rust.

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

More configuration options are available through the VS Code extension:
`Ctrl+Shift+P` → type `Spec Editor` to access settings, project switching,
and MCP controls.

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
- [Extension Integration](docs/EXTENSION_INTEGRATION.md) — VS Code + MCP setup
- [Contributing Prompts](docs/CONTRIBUTING_PROMPTS.md) — how to improve agent quality
- [CONTRIBUTING.md](CONTRIBUTING.md) — code contributions
- [CHANGELOG.md](CHANGELOG.md) — release history

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
