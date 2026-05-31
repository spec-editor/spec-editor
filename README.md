<p align="center">
  <h1 align="center">Spec Editor</h1>
  <h3 align="center">AI-powered requirements engineering with bidirectional code traceability</h3>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue.svg" alt="Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-336-green.svg" alt="336 tests"></a>
  <a href="#early-stage"><img src="https://img.shields.io/badge/version-v0.2_alpha-orange.svg" alt="v0.2 alpha"></a>
</p>

> [!WARNING]
> **Early stage.** Spec Editor is under active development. Prompts are shipped
> as sensible defaults — you'll get better results by customising them for your
> domain, methodology, and preferred LLM. See [Help Wanted](#help-wanted) below
> if you'd like to contribute better prompts.

---

## What is Spec Editor?

Spec Editor turns messy requirements documents into structured specifications
using multiple AI agents in a structured dialogue. It also verifies that your
code actually implements the requirements — bidirectional traceability from
document to code and back.

**It is:**
- A CLI tool that generates specifications via multi-agent debate
- A code annotator that links source code to requirements (`@implements`)
- An MCP server so external AI agents can read your specification
- A code generator that creates skeletons from spec elements (SQLAlchemy, FastAPI, React, pytest)

**It is NOT:**
- A generic code generator (we do templates, not AI code gen)
- A task tracker (use Jira/Linear for that — we export to them)
- A replacement for developers (agents debate, humans decide)

> [!NOTE]
> Spec Editor works with any OpenAI-compatible API (DeepSeek, OpenAI, Anthropic).
> Default: DeepSeek ($0.14/M tokens) — 100× cheaper than GPT-4.

---

## Why Not Just Prompt an LLM Directly?

| What happens | The problem |
|-------------|-------------|
| Single perspective | LLM agrees with itself — no critical challenge |
| No adversarial review | Edge cases, security gaps, contradictions missed |
| Freeform output | Hard to version, diff, or audit |
| Ephemeral sessions | Context lost between runs |
| No code traceability | Requirements rot — nobody knows what's implemented |

**Spec Editor solves this by:**
- Running **multiple agents** with different viewpoints in structured rounds
- Supporting **blind voting** — agents respond without seeing each other
- Producing **version-controlled artifacts** in git (markdown + YAML)
- Persisting **session history** for incremental runs
- Linking code to requirements via **@implements** annotations

---

## Quick Start

> **See it in action:** `spec-editor demo` — opens a pre-generated spec in your browser.
> No API key needed. 5 seconds.

## Quick Start

```bash
# Install
pip install spec-editor

# Create a new project
spec-editor init my-project --methodology waterfall

# Drop a requirements document in source/
echo "Users should be able to log in with email and password" > my-project/source/login.md

# Run the agents
cd my-project && spec-editor run

# Check what was generated
spec-editor status

# Annotate your code with @implements
spec-editor annotate --code-dir ./src --language python

# Verify traceability
spec-editor verify-traceability --code-dir ./src --language python
```

After `spec-editor run` completes, you'll have:
- `aspects/` — structured specification in markdown + YAML frontmatter
- `source/session_summary.md` — what the agents did and why

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
│  │  │  Blind voting (optional)    │    │                     │
│  │  │  4 strategies: CONSENSUS,   │    │                     │
│  │  │  MAJORITY, WEIGHTED, DEBATE │    │                     │
│  │  └─────────────────────────────┘    │                     │
│  └─────────────────┬───────────────────┘                     │
│                    ▼                                          │
│  ┌─────────────────────────────────────┐                     │
│  │         SPECIFICATION               │                     │
│  │  aspects/modules/    MOD-001.md     │                     │
│  │  aspects/scenarios/  SCN-001.md     │                     │
│  │  aspects/entities/   ENT-001.md     │                     │
│  └─────────────────┬───────────────────┘                     │
│                    ▼                                          │
│  ┌─────────────────────────────────────┐                     │
│  │       CODE TRACEABILITY              │                     │
│  │  @implements("MOD-001")              │                     │
│  │  class AuthModule: ...               │                     │
│  │                                      │                     │
│  │  7 languages: Python, TS, JS,        │                     │
│  │  Go, Java, Kotlin, Rust              │                     │
│  └─────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

### Key Features

| Feature | Description |
|---------|-------------|
| **Multi-agent dialogue** | 2 agents + orchestrator debate requirements in structured rounds |
| **Blind voting** | Agents respond independently, preventing anchoring bias (from spec2ship) |
| **4 voting strategies** | CONSENSUS, MAJORITY, WEIGHTED, DEBATE + adaptive auto-select |
| **Ingestion pipeline** | PDF, Telegram, voice → SRC (Source Requirement Candidates) |
| **Code annotation** | `@implements("REQ-001")` — link code to requirements automatically |
| **Traceability verification** | Check coverage: "87% of confirmed requirements have @implements" |
| **AST-based parsers** | 7 languages via tree-sitter (Python, TS, JS, Go, Java, Kotlin, Rust) |
| **Code generation** | Jinja2 templates: SQLAlchemy, FastAPI, pytest, React, TypeScript |
| **MCP server** | 19 tools — external AI agents read/write your specification |
| **Incremental runs** | `--since 2026-05-01` — process only new/changed source documents |
| **Session persistence** | `session.json` + `session_summary.md` — full audit trail |
| **Git-native** | Everything is markdown + YAML in git — version, diff, merge, blame |

---

## Supported Methodologies

Specifications follow a methodology — a YAML-defined structure of aspects,
element types, and relationships.

| Methodology | What it generates | Status |
|-------------|-------------------|--------|
| **waterfall** | Full spec: modules, scenarios, UI, data, non-functional, implementation, metrics (7 aspects) | ✅ Free (OSS) |
| **agile** | Sprint backlog: epics → user stories → acceptance criteria + INVEST validator + Jira CSV | ✅ |
| **scrum** | Agile + sprints (goal, capacity, focus factor, velocity, DoD) | ✅ |
| **kanban** | Agile + workflow stages (WIP limits, cycle time, throughput) | ✅ |
| **api-first** | OpenAPI 3.0 contract (service → endpoint → schema + auth) | ✅ |

> [!TIP]
> `waterfall` is free and bundled with the OSS release.
> `agile`, `scrum`, `kanban`, `api-first` are **Methodology Packs** (paid, $99–149 each).
> See [spec-editor.com](https://spec-editor.com) for details.

---

## Code Traceability (7 Languages)

```python
# @implements("MOD-001")                     ← annotation
class AuthModule:                            ← symbol
    def login(self, email, password): ...
```

```go
// @implements("SCN-001")
func LoginUser(email, password string) error { ... }
```

```rust
// @implements("ENT-001")
pub struct User { pub id: i64, pub name: String }
```

```java
@Implements("API-001")
public UserResponse getUser(Long id) { ... }
```

| Language | Parser | Annotation style |
|----------|--------|-----------------|
| Python | `ast` (stdlib) | `@implements(...)` decorator or comment |
| TypeScript / JS | `tree-sitter` | `@Implements(...)` decorator or `// @implements(...)` |
| Go | `tree-sitter` | `// @implements(...)` comment |
| Java | `tree-sitter` | `@Implements(...)` annotation or `// @implements(...)` |
| Kotlin | `tree-sitter` | `@Implements(...)` annotation or `// @implements(...)` |
| Rust | `tree-sitter` | `// @implements(...)` comment |

---

## CLI Commands

```bash
spec-editor init ./my-project --methodology waterfall    # Create project
spec-editor run -p ./my-project                          # Run agent dialogue
spec-editor status -p ./my-project                       # Show spec status
spec-editor validate -p ./my-project                     # Validate specification
spec-editor export -p ./my-project --format trlc         # Export to TRLC format

spec-editor annotate -p ./my-project --code-dir ./src -l python   # Annotate code
spec-editor verify-traceability -p ./my-project -c ./src -l go    # Verify coverage
spec-editor codegen -p ./my-project --output ./generated          # Generate code skeletons

spec-editor mcp                                           # Start MCP server (19 tools)
spec-editor questions                                     # Manage agent questions
```

---

## Export Formats

```bash
spec-editor export -p .                            # SRS document (default)
spec-editor export -p . -f trlc -o spec.trlc       # TRLC (BMW-compatible)
spec-editor export -p . -f openapi -o api.yaml     # OpenAPI 3.0
spec-editor export -p . -f jira -o backlog.csv     # Jira CSV import
```

| Format | CLI flag | Output | Use case |
|--------|----------|--------|----------|
| **SRS (IEEE 830)** | `-f srs` (default) | Markdown | Stakeholder-ready specification |
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
    model: deepseek-chat
    temperature: 0.7
  agent_2:
    provider: deepseek
    model: deepseek-chat
    temperature: 0.7
  orchestrator:
    provider: deepseek
    model: deepseek-chat
```

Set `SPEC_EDITOR__LOG_LEVEL=DEBUG` for verbose output including tool calls.

---

## Dependencies

**Runtime:** Python 3.11+, Pydantic 2.x, LiteLLM, Jinja2, PyYAML,
Click, Rich, Structlog, python-frontmatter, tree-sitter, tree-sitter-languages.

**Optional:** pytest (for running tests), ruff (for linting).

Install all: `pip install spec-editor`

---


## Benchmark

Spec Editor includes an automated evaluation system that measures agent output
quality against golden graphs — hand-crafted reference specifications.

### Evaluation Fixtures

| Fixture | Elements | Aspects | Description |
|---------|----------|---------|-------------|
| **library** | 12 | modules, scenarios, entities, NFR | Public library management |
| **store** | 13 | modules, scenarios, entities, NFR | E-commerce platform |
| **site-matrix** | 21 | modules, scenarios, entities, NFR, UI, decisions | 1000-site control plane |

### Evaluation Criteria

The LLM-as-Judge evaluates agent output on 5 weighted criteria:

| Criterion | Weight | What it measures |
|-----------|--------|-----------------|
| **completeness** | 25% | Expected elements present? |
| **correctness** | 25% | Titles/descriptions match seed? |
| **connectivity** | 20% | Relationships present and valid? |
| **consistency** | 15% | No contradictions between elements? |
| **clarity** | 15% | Clear and unambiguous? |

### Baseline Scores

| Fixture | Overall | Completeness | Correctness | Connectivity |
|---------|---------|-------------|-------------|-------------|
| library | 100% | 100/100 | 100/100 | 100/100 |
| store | 100% | 100/100 | 100/100 | 100/100 |

> **Note:** Baseline scores = golden-to-golden evaluation. Agent-generated scores
> vary by model and prompts. Run `eval-system bench` to see current results.

## Help Wanted

Prompts are the engine of Spec Editor. Better prompts = better specifications.

**We need help with:**
- **Language packs** — translations that sound native, not machine-translated. Current languages: EN, RU, ES, FR, DE. Missing your language? Add `prompts/xx.yaml` and open a PR.
- **Methodology prompts** — each methodology (scrum, regulatory, api-first) needs its own prompt tuning. The defaults work, but domain-specific prompts produce dramatically better results.
- **LLM-specific tuning** — DeepSeek, GPT-4, and Claude each respond differently to the same prompt. If you've tuned prompts for your preferred model, share them.
- **Few-shot examples** — adding 2–3 worked examples to each prompt significantly improves output quality. We have none yet.

**How to contribute prompts:**
1. Fork the repo
2. Edit `prompts/{lang}.yaml` or add a new language file
3. Run `pytest tests/test_prompt_loader.py` to verify format variables are consistent
4. Open a PR with a before/after comparison of agent output

All prompt contributions are credited in the changelog. Good prompts make everyone's specs better.

---

## Documentation

- [Quickstart](docs/QUICKSTART.md) — 5-minute setup
- [Architecture](docs/ARCHITECTURE.md) — pipeline, components, CLI reference
- [Contributing Prompts](docs/CONTRIBUTING_PROMPTS.md) — how to improve agent quality
- [CONTRIBUTING.md](CONTRIBUTING.md) — code contributions
- [CHANGELOG.md](CHANGELOG.md) — release history
- [readme_mcp.md](readme_mcp.md) — MCP server API reference

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

The core engine (`spec-editor`) is free and open source.
Methodology Packs (`agile`, `scrum`, `kanban`, `api-first`) are source-available,
purchased separately.
