"""Log Collector — syncs structured JSON-lines logs into the project.

Watches a source ``logs/`` directory (produced by :class:`StructuredLogEmitter`)
and copies new lines into the spec-editor project's ``sources_raw/`` folder,
one file per module per day.  Tracks read position per source file so that
only new data is copied each run.

Usage::

    from spec_editor_cycle.collector import LogCollector

    collector = LogCollector(
        source_dir="app/logs",
        target_dir="my-project/sources_raw",
    )

    # One-shot sync
    result = collector.sync()
    # → {"collected": 1247, "files": ["logs_MOD-001_2025-06-21.jsonl"], ...}

    # Continuous watch (blocks until interrupted)
    collector.watch(interval_sec=60)
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

_STATE_FILE = ".log_collector_state.json"


class LogCollector:
    """Copies new JSON-lines from a source log tree into a target directory.

    Source structure (produced by :class:`StructuredLogEmitter`)::

        logs/
        ├── MOD-001/
        │   └── structured.jsonl
        ├── MOD-002/
        │   └── structured.jsonl
        └── ...

    Target structure (one file per module per day)::

        sources_raw/
        ├── logs_MOD-001_2025-06-21.jsonl
        ├── logs_MOD-002_2025-06-21.jsonl
        └── ...
    """

    def __init__(self, source_dir: str | Path, target_dir: str | Path) -> None:
        self._source_dir = Path(source_dir)
        self._target_dir = Path(target_dir)
        self._target_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._target_dir / _STATE_FILE
        self._positions: dict[str, int] = self._load_state()
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(self) -> dict:
        """Copy new log lines from source to target.  Idempotent.

        Returns a summary dict::

            {
                "collected": 1247,          # total new lines copied
                "files": [                  # target files written to
                    "logs_MOD-001_2025-06-21.jsonl",
                    ...
                ],
                "modules": ["MOD-001", "MOD-002"],
                "errors": [],
            }
        """
        collected_total = 0
        files_written: list[str] = []
        modules_seen: set[str] = set()
        errors: list[str] = []

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if not self._source_dir.is_dir():
            errors.append(f"Source directory not found: {self._source_dir}")
            return self._result(0, [], [], errors)

        for module_dir in sorted(self._source_dir.iterdir()):
            if not module_dir.is_dir():
                continue

            module_id = module_dir.name
            modules_seen.add(module_id)
            source_file = module_dir / "structured.jsonl"

            if not source_file.is_file():
                continue

            # Determine where we left off.
            key = str(source_file.resolve())
            last_pos = self._positions.get(key, 0)
            current_size = source_file.stat().st_size

            # Handle file rotation: if the file shrank, start over.
            if current_size < last_pos:
                last_pos = 0

            # Nothing new.
            if current_size <= last_pos:
                continue

            # Read only new bytes.
            try:
                with open(source_file, "r", encoding="utf-8") as fh:
                    fh.seek(last_pos)
                    new_data = fh.read()
            except OSError as exc:
                errors.append(f"{source_file}: {exc}")
                continue

            # Write to today's target file.
            target_name = f"logs_{module_id}_{today}.jsonl"
            target_file = self._target_dir / target_name

            # Count valid JSON lines and append them.
            lines = new_data.splitlines()
            valid_lines = 0
            try:
                with open(target_file, "a", encoding="utf-8") as out:
                    for line in lines:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Validate it's parseable JSON before writing.
                        try:
                            json.loads(stripped)
                        except json.JSONDecodeError:
                            errors.append(
                                f"{source_file}: skipping malformed line: "
                                f"{stripped[:80]}..."
                            )
                            continue
                        out.write(stripped + "\n")
                        valid_lines += 1
            except OSError as exc:
                errors.append(f"{target_file}: {exc}")
                # Don't update position — retry next sync.
                continue

            # Update position.
            self._positions[key] = current_size
            collected_total += valid_lines
            files_written.append(target_name)

        # Persist updated positions.
        self._save_state()

        return self._result(
            collected_total,
            sorted(files_written),
            sorted(modules_seen),
            errors,
        )

    def watch(self, interval_sec: int = 60) -> None:
        """Run :meth:`sync` in a loop every *interval_sec* seconds.

        Blocks until :meth:`stop` is called or SIGTERM/SIGINT is received.
        """
        self._running = True

        # Graceful shutdown on signals.
        def _handle_signal(signum: int, _frame: object) -> None:
            print(
                f"\n[LogCollector] Received signal {signum}, stopping...",
                flush=True,
            )
            self._running = False

        original_sigterm = signal.signal(signal.SIGTERM, _handle_signal)
        original_sigint = signal.signal(signal.SIGINT, _handle_signal)

        try:
            while self._running:
                result = self.sync()
                if result["collected"] > 0:
                    print(
                        f"[LogCollector] Synced {result['collected']} lines "
                        f"from {len(result['modules'])} module(s)",
                        flush=True,
                    )
                elif result["errors"]:
                    for err in result["errors"]:
                        print(f"[LogCollector] Error: {err}", flush=True)

                if not self._running:
                    break

                # Sleep in small chunks so we can respond to signals quickly.
                deadline = time.monotonic() + interval_sec
                while self._running and time.monotonic() < deadline:
                    time.sleep(1)
        finally:
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)
            self._save_state()
            print("[LogCollector] Stopped.", flush=True)

    def stop(self) -> None:
        """Signal the watch loop to exit at the next iteration."""
        self._running = False

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, int]:
        """Load file positions from the state file."""
        if not self._state_path.is_file():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {str(k): int(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, OSError):
            return {}

    def _save_state(self) -> None:
        """Save current file positions to the state file."""
        try:
            self._state_path.write_text(
                json.dumps(self._positions, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # best-effort

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result(
        collected: int,
        files: list[str],
        modules: list[str],
        errors: list[str],
    ) -> dict:
        return {
            "collected": collected,
            "files": files,
            "modules": modules,
            "errors": errors,
        }
