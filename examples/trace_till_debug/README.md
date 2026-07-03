# Trace Till Debug — Example Project

A complete example of closing the development cycle:
Requirements → Code → Logs → Bugs → Spec Update → Code Fix.

This is a spec-editor project for **spec-editor.com** — an AGENTIC SERVICES
LANDSCAPE marketplace and information hub for AI agents.

## What's inside

```
trace_till_debug/
├── aspects/              # Generated specification (148 elements)
│   ├── sources/          # SRC-001, SRC-002 — original requirements
│   ├── modules/          # MOD-website, MOD-catalog, MOD-marketplace, ...
│   ├── user_scenarios/   # SCN-browse, SCN-register, SCN-post-listing, ...
│   ├── data_entities/    # 30 entities
│   ├── non_functional/   # NFR-001, NFR-002 — created by feedback loop
│   └── ...
├── skills/               # All 10 agent skills
│   ├── coding.yaml       # coding_agent — generates/modifies code
│   └── spec_editor.yaml  # 9 analysis/coordination skills
├── skills.yaml           # Legacy compat
├── methodology.yaml      # Project methodology
├── agents.yaml           # LLM provider config (DeepSeek)
├── sources_raw/          # Place for source documents and collected logs
└── README.md             # This file
```

## How to use

### 1. Start from the example

```bash
cp -r examples/trace_till_debug my-project
cd my-project
```

### 2. Generate application logs

In your application code, add `StructuredLogEmitter` to each module:

```python
from src.tracing import StructuredLogEmitter
log = StructuredLogEmitter(module_id="MOD-marketplace", scenario_id="SCN-browse")
log.error("handler_failed", error=str(e))
```

Logs will be written to `logs/{module_id}/structured.jsonl`.

### 3. Run the feedback loop

```bash
spec-editor feedback --logs logs/
```

This runs the full cycle:
- **Phase 1** — Collect logs from `logs/` into `sources_raw/`
- **Phase 2** — Analyse logs, detect spikes and bugs
- **Phase 3** — Convert bugs to SRC-BUG-* requirements
- **Phase 4** — Update specification (NFR, STP) from bugs

### 4. PM Agent spawns coding agent

The PM Agent sees new spec changes (`NFR-001`, `NFR-002`) and spawns
the coding agent:

```
PM Agent → coding_agent: "Implement NFR-001: Input validation"
```

The coding agent reads the spec, writes code with `@implements`,
runs tests, and reports completion.

### 5. Verify and archive

```bash
spec-editor feedback --health
spec-editor deprecate SRC-BUG-001
```

## Key metrics

| Metric | Value |
|---|---|
| Total elements | 148 |
| Relationships | 772 |
| Connectivity Index | 2.51 |
| Bugs found | 3 |
| Spec changes | 2 NFRs |

## See also

- `Product/trace_till_debug.md` — full implementation plan
- `Product/ttd_lifecycle.md` — lifecycle description with agent roles
