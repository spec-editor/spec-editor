# Element Statuses

## Lifecycle

```
                    ┌──→ confirmed  (implemented, tests passed)
                    │
draft ──→ reviewed ─┤  3 failed fix attempts
 (analysts)  (coder) │
                    └──→ blocked ──→ PM ──→ product-manager ──→ draft (re-analysis)
```

| Status | Meaning | Who picks up | Who sets |
|--------|---------|-------------|----------|
| `draft` | Needs analysis/rework | **Analysts** (product-manager) | Element creation, PM after blocked |
| `reviewed` | Ready for implementation | **Coder** (coding agent) | Analysts after rework, `_health_check` |
| `confirmed` | Implemented, tests passed | — (final status) | Coder after success, PM/recheck recovery |
| `blocked` | Unfixable, needs analysis | **PM → product-manager → analysts** | `_fix_bugs` after 3 failures |
| `deprecated` | Irrelevant / auto-closed | — | Auto-detect `## RESOLVED` |

## For all element types

`_fix_bugs_parallel` processes **all implementable elements** in `reviewed` status:
- `SRC-BUG-*` — bugs
- `TST-*` — test cases
- `MOD-*` — modules
- `IMP-*` — implementation

## Tags on SRC-BUG-*

| Tag | Meaning |
|-----|---------|
| `blocked_cycles:N` | How many times went through BLOCKED→reactivate cycle |
| `permanent_blocked` | 2+ cycles — needs human intervention |
| `refined_count:N` | How many times PM tried to refine |
| `refined_by_pm` | PM added clarification |
| `recovered_by_pm` | PM verified tests → recovered |
| `recovered_by_recheck` | Recheck verified tests → recovered |
| `auto_deprecated_resolved` | `## RESOLVED` found → auto-closed |
| `needs_clarification` | Needs analyst (not coder) |

## Code references

- `src/storage/models.py:ElementStatus` — enum
- `plugins/feedback/src/spec_editor_feedback/engine.py` — transitions
- `src/agents/persistent_agent.py` — PM handler
