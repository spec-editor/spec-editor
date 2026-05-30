"""Session manager — persistence and incremental run support.

Tracks which SRC files have been processed, writes session summaries,
and supports `--since` filtering for incremental runs.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class SessionRecord(BaseModel):
    """A single run session record."""

    session_id: str
    methodology: str = "waterfall"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    files_processed: list[str] = Field(default_factory=list)
    elements_created: int = 0
    elements_updated: int = 0
    elements_deleted: int = 0
    summary: str = ""


class SessionManager:
    """Manages session state for incremental spec generation runs.

    State is stored in ``source/session.json`` within the project root.
    """

    def __init__(
        self,
        project_root: Path,
        source_dir_name: str = "source",
    ):
        self._project_root = Path(project_root)
        self._source_dir = self._project_root / source_dir_name
        self._state_path = self._source_dir / "session.json"

        self._current_session: SessionRecord | None = None
        self._history: list[SessionRecord] = []
        self._processed_files: set[str] = set()

        self._load_state()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def source_dir(self) -> Path:
        """Path to the source (SRC) directory."""
        return self._source_dir

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self, methodology: str = "waterfall") -> str:
        """Start a new session. Returns the session ID."""
        session_id = f"sess-{uuid.uuid4().hex[:8]}"
        self._current_session = SessionRecord(
            session_id=session_id,
            methodology=methodology,
        )
        return session_id

    def end_session(self, summary: str = "") -> None:
        """End the current session and persist it to history."""
        if self._current_session is None:
            return

        self._current_session.finished_at = datetime.now(timezone.utc)
        self._current_session.summary = summary

        # Add processed files to global set
        for f in self._current_session.files_processed:
            self._processed_files.add(f)

        # Prepend to history (latest first)
        self._history.insert(0, self._current_session)

        self._current_session = None
        self._save_state()

        # Write human-readable summary
        if self._history:
            self._write_summary_md(self._history[0])

    # ------------------------------------------------------------------
    # Recording during a session
    # ------------------------------------------------------------------

    def record_file_processed(self, filename: str) -> None:
        """Record that a source file was processed in the current session."""
        if self._current_session is not None:
            if filename not in self._current_session.files_processed:
                self._current_session.files_processed.append(filename)

    def record_elements(
        self,
        created: int = 0,
        updated: int = 0,
        deleted: int = 0,
    ) -> None:
        """Record element counts for the current session."""
        if self._current_session is not None:
            self._current_session.elements_created += created
            self._current_session.elements_updated += updated
            self._current_session.elements_deleted += deleted

    # ------------------------------------------------------------------
    # Incremental run support
    # ------------------------------------------------------------------

    def get_last_run_time(self) -> datetime | None:
        """Get the timestamp of the last completed run, or None.

        Returns finished_at if available, otherwise created_at.
        """
        if self._history:
            return self._history[0].finished_at or self._history[0].created_at
        return None

    def get_changed_source_files(
        self,
        since: datetime | None = None,
    ) -> list[str]:
        """Get source files modified since a given datetime.

        If ``since`` is None, returns ALL source files (full run).

        Returns absolute paths as strings.
        """
        source_dir = self._source_dir
        if not source_dir.is_dir():
            return []

        files: list[str] = []
        for f in sorted(source_dir.rglob("*")):
            if not f.is_file():
                continue
            # Skip hidden and session files
            if f.name.startswith(".") or f.name in (
                "session.json",
                "session_summary.md",
            ):
                continue
            if f.suffix not in (
                ".md",
                ".txt",
                ".yaml",
                ".yml",
                ".json",
                ".jsonl",
                ".csv",
            ):
                continue

            if since is None:
                files.append(str(f))
            else:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                if mtime >= since:
                    files.append(str(f))

        return files

    def get_unprocessed_source_files(self) -> list[str]:
        """Get source files that have never been processed in any session.

        Returns absolute paths as strings.
        """
        all_files = self.get_changed_source_files(since=None)
        return [f for f in all_files if Path(f).name not in self._processed_files]

    def is_file_processed(self, filename: str) -> bool:
        """Check if a file was processed in any session."""
        return Path(filename).name in self._processed_files

    # ------------------------------------------------------------------
    # History queries
    # ------------------------------------------------------------------

    def get_history(self, limit: int | None = None) -> list[SessionRecord]:
        """Get session history (latest first)."""
        if limit is not None and limit > 0:
            return self._history[:limit]
        return list(self._history)

    def clear_history(self) -> None:
        """Clear all session history and processed files tracking."""
        self._history.clear()
        self._processed_files.clear()
        self._save_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save current state to session.json."""
        self._source_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "processed_files": sorted(self._processed_files),
            "history": [s.model_dump(mode="json") for s in self._history],
        }
        self._state_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _load_state(self) -> None:
        """Load state from session.json if it exists."""
        if not self._state_path.exists():
            # Create empty state file
            self._save_state()
            return

        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._save_state()
            return

        self._processed_files = set(raw.get("processed_files", []))
        self._history = [SessionRecord(**s) for s in raw.get("history", [])]

    def _write_summary_md(self, session: SessionRecord) -> None:
        """Write a human-readable session summary to source/session_summary.md."""
        lines = [
            f"# Session Summary: {session.session_id}",
            "",
            f"- **Methodology:** {session.methodology}",
            f"- **Started:** {session.created_at.isoformat()}",
            f"- **Finished:** {session.finished_at.isoformat() if session.finished_at else 'N/A'}",
            f"- **Files processed:** {len(session.files_processed)}",
            f"- **Elements created:** {session.elements_created}",
            f"- **Elements updated:** {session.elements_updated}",
            f"- **Elements deleted:** {session.elements_deleted}",
            "",
        ]

        if session.summary:
            lines.append(session.summary)
            lines.append("")

        if session.files_processed:
            lines.append("## Processed Files")
            lines.append("")
            for f in session.files_processed:
                lines.append(f"- `{f}`")
            lines.append("")

        summary_path = self._source_dir / "session_summary.md"
        summary_path.write_text("\n".join(lines), encoding="utf-8")
