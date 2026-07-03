"""Acceptance Tester Agent — semantic spec-to-code verification.

Reads leaf requirements from the spec, reads the generated code,
and checks whether the implementation actually fulfills the requirement.
Uses a cheap model (deepseek-chat) — no reasoning needed, just comparison.

Usage::

    from spec_editor_cycle.tester import AcceptanceTester

    tester = AcceptanceTester(storage, project_path)
    gaps = await tester.run()
    # → [{"leaf_id": "SCN-register", "status": "gap", "reason": "No POST /register"}, ...]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.tracing import StructuredLogEmitter

# ── Leaf requirements that should have corresponding code ──
IMPLEMENTABLE_ASPECTS = {
    "user_scenarios",  # SCN-*  → API endpoints / page handlers
    "user_interface",  # UI-*   → HTML pages / components
    "modules",  # MOD-*  → Python modules / classes
    "non_functional",  # NFR-*  → config / middleware
    "data_entities",  # ENT-*  → models / schemas
}


class AcceptanceTester:
    """Compares spec leaf requirements against generated code.

    Args:
        storage: StorageAdapter for reading spec elements.
        project_path: Path to the project root.
        model: LLM model for semantic comparison (default: deepseek-chat).
    """

    def __init__(
        self,
        storage: Any,
        project_path: str | Path,
        model: str | None = None,
    ) -> None:
        self._storage = storage
        self._project_path = Path(project_path)
        self._model = model or os.environ.get(
            "SPEC_EDITOR__AGENT_2__MODEL", "deepseek/deepseek-chat"
        )
        self._log = StructuredLogEmitter(
            module_id="MOD-tester",
            scenario_id="SCN-acceptance",
            log_dir=str(self._project_path / "logs"),
            auto_element=False,
        )

    async def run(self, max_leaves: int = 5) -> dict:
        """Run acceptance tests on leaf requirements.

        Picks up to *max_leaves* leaves without @implements coverage
        and checks if their intent is reflected in the code.

        Returns:
            dict with ``gaps_found``, ``gaps``, ``leaves_checked``.
        """
        # Find implementable leaves
        leaves = self._get_implementable_leaves()
        self._log.info("tester_start", total_leaves=len(leaves))

        # Find which leaves already have @implements in code
        impl_map = self._scan_implements()
        uncovered = [l for l in leaves if l["id"] not in impl_map]

        if not uncovered:
            self._log.info("tester_all_covered")
            return {
                "status": "ok",
                "gaps_found": 0,
                "gaps": [],
                "leaves_checked": len(leaves),
            }

        # Also check covered leaves for semantic gaps
        to_check = uncovered[:max_leaves]
        covered_sample = [l for l in leaves if l["id"] in impl_map][:2]
        to_check.extend(covered_sample)

        self._log.info("tester_checking", count=len(to_check))

        gaps = []
        passed = []
        for leaf in to_check:
            gap = await self._check_leaf(leaf, impl_map.get(leaf["id"], []))
            if gap:
                gaps.append(gap)
                self._create_bug_if_needed(gap)
            else:
                passed.append(leaf["id"])
                self._mark_tested(leaf["id"])

        self._log.info(
            "tester_done", gaps=len(gaps), passed=len(passed), checked=len(to_check)
        )
        return {
            "status": "issues_found" if gaps else "ok",
            "gaps_found": len(gaps),
            "gaps": gaps,
            "leaves_checked": len(to_check),
            "passed": passed,
            "needs_fix": len(gaps) > 0,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_implementable_leaves(self) -> list[dict]:
        """Get leaf requirements that should have code implementations."""
        from spec_editor_cycle.engine import _find_leaves_inline

        return _find_leaves_inline(self._storage)

    def _scan_implements(self) -> dict[str, list[str]]:
        """Scan all Python files for @implements decorators."""
        from spec_editor_cycle.engine import _scan_implements_inline

        return _scan_implements_inline(str(self._project_path))

    async def _check_leaf(self, leaf: dict, files: list[str]) -> dict | None:
        """Check if one leaf requirement is implemented in code.

        Returns a gap dict if the implementation is missing or incomplete,
        or None if the requirement is satisfied.
        """
        leaf_id = leaf["id"]
        title = leaf.get("title", "")
        content = leaf.get("content", "")
        aspect = leaf.get("aspect", "")

        # Read the content of files that claim to implement this
        code_snippets = []
        for fpath in files:
            full = self._project_path / fpath
            if full.is_file():
                try:
                    code_snippets.append(f"# {fpath}\n{full.read_text()[:3000]}")
                except Exception:
                    pass

        # If no files claim to implement, sample relevant source dirs
        if not code_snippets:
            code_snippets = self._sample_relevant_code(leaf_id, aspect)

        if not code_snippets:
            return {
                "leaf_id": leaf_id,
                "title": title,
                "status": "gap",
                "reason": f"No code found for {leaf_id}",
                "severity": "high",
            }

        # Use LLM to compare requirement vs code
        verdict = await self._llm_compare(leaf, code_snippets)
        if verdict.get("status") == "gap":
            return {
                "leaf_id": leaf_id,
                "title": title,
                "status": "gap",
                "reason": verdict.get("reason", "Implementation incomplete"),
                "severity": verdict.get("severity", "medium"),
            }

        return None

    def _sample_relevant_code(self, leaf_id: str, aspect: str) -> list[str]:
        """Find relevant source files for a leaf requirement.
        Scans Python (src/, mod_*), TypeScript (packages/*/src),
        Rust (packages/zed-extension), HTML (build/static).
        """
        samples = []
        proj = self._project_path

        # Python: src/ and mod_*
        for base in [proj / "src", proj / "build"] + sorted(proj.glob("mod_*")):
            if not base.is_dir():
                continue
            for py_file in sorted(base.rglob("*.py"))[:2]:
                sp = str(py_file)
                if "__pycache__" in sp or ".venv" in sp:
                    continue
                try:
                    samples.append(
                        f"# {py_file.relative_to(proj)}\n{py_file.read_text()[:2000]}"
                    )
                except Exception:
                    pass

        # TypeScript/JS: packages/*/src
        packages_dir = proj / "packages"
        if packages_dir.is_dir():
            for ext_dir in sorted(packages_dir.iterdir()):
                if not ext_dir.is_dir():
                    continue
                src_sub = ext_dir / "src"
                search_dir = src_sub if src_sub.is_dir() else ext_dir
                for ts_file in sorted(search_dir.rglob("*.ts"))[:2] + sorted(search_dir.rglob("*.tsx"))[:2]:
                    sp = str(ts_file)
                    if "node_modules" in sp or "target" in sp:
                        continue
                    try:
                        samples.append(
                            f"# {ts_file.relative_to(proj)}\n{ts_file.read_text()[:2000]}"
                        )
                    except Exception:
                        pass

        # Rust: packages/zed-extension
        zed_dir = proj / "packages" / "zed-extension"
        if zed_dir.is_dir():
            for rs_file in sorted(zed_dir.rglob("*.rs"))[:3]:
                if "target" in str(rs_file):
                    continue
                try:
                    samples.append(
                        f"# {rs_file.relative_to(proj)}\n{rs_file.read_text()[:2000]}"
                    )
                except Exception:
                    pass

        # HTML for UI
        if aspect == "user_interface":
            static_dir = proj / "build" / "static"
            if static_dir.is_dir():
                for html_file in sorted(static_dir.glob("*.html"))[:3]:
                    try:
                        samples.append(
                            f"# {html_file.relative_to(proj)}\n{html_file.read_text()[:3000]}"
                        )
                    except Exception:
                        pass

        return samples[:8]

    async def _llm_compare(self, leaf: dict, code_snippets: list[str]) -> dict:
        """Ask LLM to compare a requirement against code.

        Uses litellm for API call. Returns verdict dict.
        """
        import litellm

        code_text = "\n\n".join(code_snippets)
        leaf_text = (
            f"ID: {leaf['id']}\n"
            f"Title: {leaf.get('title', '')}\n"
            f"Type: {leaf.get('element_type', '')} / {leaf.get('aspect', '')}\n"
            f"Content:\n{leaf.get('content', '')}"
        )

        prompt = (
            "You are a QA acceptance tester. Compare a specification requirement "
            "against the implemented code. Answer in JSON.\n\n"
            f"REQUIREMENT:\n{leaf_text[:2000]}\n\n"
            f"CODE:\n{code_text[:4000]}\n\n"
            "Does the code fully implement the requirement?\n"
            'Respond: {"status": "ok"} if fully implemented.\n'
            'Respond: {"status": "gap", "reason": "...", "severity": "high|medium|low"} '
            "if missing or incomplete. Be specific about what is missing. "
            "Check for: API endpoints, UI pages, data models, logic, "
            "error handling, configuration. "
            "IMPORTANT: Even if there is a skeleton/placeholder that looks like it "
            "might work, check if it actually fulfills the requirement semantics. "
            "A static HTML page with no API calls is NOT a dynamic interactive page."
        )

        try:
            api_key = self._get_api_key_from_env()

            # Retry transient network errors (SSL EOF, timeouts, connection resets)
            max_retries = 3
            last_error = ""
            for attempt in range(max_retries):
                try:
                    response = await litellm.acompletion(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        api_key=api_key,
                        max_tokens=500,
                        temperature=0.0,
                    )
                    text = response.choices[0].message.content.strip()
                    # Extract JSON
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                    return json.loads(text)
                except Exception as inner_exc:
                    last_error = str(inner_exc)
                    # Only retry on transient network errors
                    msg_lower = last_error.lower()
                    is_transient = any(
                        kw in msg_lower
                        for kw in ("ssl:", "eof", "timeout", "connection", "reset", "unexpected_eof")
                    )
                    if not is_transient or attempt == max_retries - 1:
                        raise
                    self._log.warning(
                        "llm_compare_retry",
                        attempt=attempt + 1,
                        error=last_error[:120],
                    )
                    import asyncio
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s backoff

        except Exception as exc:
            self._log.error("llm_compare_failed", error=str(exc))
            return {"status": "error", "reason": str(exc)}

    def _get_api_key_from_env(self) -> str:
        """Resolve DEEPSEEK_API_KEY using shared secrets provider + .env fallback.

        Mirrors providers.py:_ensure_api_key for consistency.
        """
        import os

        if "DEEPSEEK_API_KEY" in os.environ:
            return os.environ["DEEPSEEK_API_KEY"]

        # Try Secrets Provider first
        try:
            from src.secrets import create_secret_provider
            secrets = create_secret_provider(str(self._project_path))
            api_key = secrets.get_secret("DEEPSEEK_API_KEY")
            if api_key:
                os.environ["DEEPSEEK_API_KEY"] = api_key
                return api_key
        except Exception:
            pass

        # Fallback: parse .env file
        env_file = self._project_path / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "DEEPSEEK_API_KEY" in line and "=" in line:
                    key = line.split("=", 1)[1].strip()
                    os.environ["DEEPSEEK_API_KEY"] = key
                    return key
        return os.environ.get("DEEPSEEK_API_KEY", "")

    def _mark_tested(self, leaf_id: str) -> None:
        """Mark a leaf requirement as tested (confirmed)."""
        from src.storage.models import ElementStatus

        try:
            el = self._storage.read_element(leaf_id)
            if el.status.value != "confirmed":
                el.status = ElementStatus.CONFIRMED
                self._storage.write_element(el)
                self._log.info("marked_tested", leaf_id=leaf_id)
        except Exception as exc:
            self._log.error("mark_tested_failed", leaf_id=leaf_id, error=str(exc))

    def _create_bug_if_needed(self, gap: dict) -> None:
        """Create SRC-BUG-* for a found gap."""
        from src.storage.models import Element, ElementStatus, Provenance

        leaf_id = gap["leaf_id"]
        title = f"QA: {leaf_id} — {gap.get('reason', 'gap')[:50]}"
        # Check duplicates by leaf_id
        for s in self._storage.list_all():
            if not s.id.startswith("SRC-BUG-"):
                continue
            try:
                existing = self._storage.read_element(s.id)
                if existing.derived_from and leaf_id in existing.derived_from:
                    if existing.status.value in ("draft", "deprecated"):
                        return
            except Exception:
                pass

        all_els = self._storage.list_all()
        existing_ids = [
            int(s.id.split("-")[-1]) for s in all_els if s.id.startswith("SRC-BUG-")
        ]
        # Use atomic counter to avoid collisions within same run
        if not hasattr(self, "_bug_counter"):
            self._bug_counter = max(existing_ids) if existing_ids else 1
        self._bug_counter += 1
        bug_id = f"SRC-BUG-{self._bug_counter:03d}"

        # Build tags — detect vague requirements
        tags = ["bug", "qa", gap.get("severity", "medium")]
        reason_lower = gap.get("reason", "").lower()
        if any(
            w in reason_lower
            for w in (
                "vague",
                "not specific",
                "unclear",
                "insufficient",
                "lacks specific",
                "too broad",
                "non-functional requirement that",
            )
        ):
            tags.append("needs_clarification")

        el = Element(
            aspect="sources",
            element_type="source",
            id=bug_id,
            title=title[:80],
            status=ElementStatus.DRAFT,
            content=(
                f"Acceptance test gap for {gap['leaf_id']}: {gap['title']}\n\n"
                f"Reason: {gap.get('reason', '')}\n\n"
                f"Severity: {gap.get('severity', 'medium')}\n\n"
                f"ACTION: Implement {gap['leaf_id']} according to the spec."
            ),
            derived_from=[gap["leaf_id"]],
            provenance=Provenance(source="acceptance_tester", confidence=0.85),
            tags=tags,
        )
        self._storage.write_element(el)
        self._log.info("bug_created", bug_id=bug_id, leaf=gap["leaf_id"])
