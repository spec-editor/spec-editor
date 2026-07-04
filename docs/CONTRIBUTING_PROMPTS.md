# Contributing Prompts

Prompts are the engine of Spec Editor. Better prompts = better specifications.

## How prompts work

Each methodology has agent prompts (in `prompts/`) that define:
- Agent personality and expertise
- Working rules and constraints
- Output format and quality checks
- Few-shot examples

Prompts are in 5 languages: EN, RU, ES, FR, DE.

## How to contribute

### 1. Fork and run

```bash
git clone https://github.com/spec-editor/spec-editor
cd spec-editor
pip install -e ".[dev]"
```

### 2. Edit prompts

Edit `data/prompts/{lang}.yaml`. Key sections:

- `spec_agent` — main agent instructions with examples
- `orchestrator` — dialogue moderator
- `orchestrator_eval` — per-round evaluation

### 3. Test your changes

```bash
pytest tests/test_prompt_loader.py
```

This verifies:
- All YAML files parse correctly
- All keys are present in all languages
- Format variables are consistent across languages

### 4. Evaluate quality (optional)

Run the evaluation system to measure prompt quality:

```bash
cd ../eval-system
python -m eval-system evaluate --fixture library
```

### 5. Submit PR

Include:
- Before/after comparison of agent output
- Which failure modes your changes fix
- Test results

## Prompt writing tips

1. **Be concrete.** "Create modules" → "For each business capability in the seed, create one module with prefix MOD-"
2. **Show, don't tell.** Add few-shot examples showing correct output format
3. **Anti-patterns.** List what NOT to do — LLMs respond well to negative examples
4. **Relationships are key.** Agents often skip `relates_to` — emphasize it
5. **Quality gates.** Remind agents to run `validate` and `metrics` before `report_complete`

## Language packs

Missing your language? Add `prompts/xx.yaml`:

1. Copy `prompts/en.yaml` as a template
2. Translate instructions (keep examples in English — they show output format)
3. Run `pytest tests/test_prompt_loader.py` to verify
4. Open a PR

All prompt contributors are credited in the changelog.
