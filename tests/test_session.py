"""Tests for SessionManager: session persistence and incremental runs."""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.storage.session import SessionManager, SessionRecord

# ======================================================================
# Helpers
# ======================================================================


def _touch(path: Path, content: str = "", mtime: float | None = None) -> None:
    """Create a file and set its modification time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(str(path), (mtime, mtime))


# ======================================================================
# SessionRecord tests
# ======================================================================


class TestSessionRecord:
    """SessionRecord data model tests."""

    def test_defaults(self):
        sr = SessionRecord(session_id="sess-001")
        assert sr.session_id == "sess-001"
        assert sr.methodology == "waterfall"
        assert sr.files_processed == []
        assert sr.elements_created == 0
        assert sr.elements_updated == 0
        assert sr.elements_deleted == 0

    def test_serialize_deserialize(self):
        sr = SessionRecord(
            session_id="sess-001",
            methodology="agile",
            files_processed=["req1.md", "req2.md"],
            elements_created=5,
            elements_updated=2,
            summary="Generated 5 user stories from 2 SRCs",
        )
        data = sr.model_dump()
        restored = SessionRecord(**data)
        assert restored.session_id == "sess-001"
        assert restored.files_processed == ["req1.md", "req2.md"]
        assert restored.summary == "Generated 5 user stories from 2 SRCs"

    def test_timestamps_are_utc_aware(self):
        sr = SessionRecord(session_id="sess-001")
        assert sr.created_at.tzinfo is not None
        assert sr.created_at.tzinfo == timezone.utc


# ======================================================================
# SessionManager tests
# ======================================================================


class TestSessionManagerInit:
    """SessionManager initialisation and state file creation."""

    def test_init_creates_session_file(self, tmp_path):
        SessionManager(project_root=tmp_path)
        assert (tmp_path / "source" / "session.json").exists()

    def test_init_loads_existing_state(self, tmp_path):
        # Pre-create state file
        mgr1 = SessionManager(project_root=tmp_path)
        mgr1.start_session(methodology="agile")
        mgr1.record_file_processed("req1.md")
        mgr1.end_session()  # must end session to persist

        # New manager should load existing state
        mgr2 = SessionManager(project_root=tmp_path)
        history = mgr2.get_history()
        assert len(history) == 1
        assert "req1.md" in history[0].files_processed

    def test_empty_state_has_no_history(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        assert mgr.get_history() == []


class TestSessionManagerStartEnd:
    """Starting and ending sessions."""

    def test_start_session(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        sid = mgr.start_session(methodology="agile")
        assert sid.startswith("sess-")
        assert mgr._current_session is not None
        assert mgr._current_session.methodology == "agile"

    def test_end_session_persists(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        mgr.start_session()
        mgr.record_file_processed("req1.md")
        mgr.record_file_processed("req2.md")
        mgr.record_elements(created=3, updated=1)
        mgr.end_session(summary="Test run completed")

        # Verify persisted
        mgr2 = SessionManager(project_root=tmp_path)
        history = mgr2.get_history()
        assert len(history) == 1
        session = history[0]
        assert session.files_processed == ["req1.md", "req2.md"]
        assert session.elements_created == 3
        assert session.elements_updated == 1
        assert session.summary == "Test run completed"

    def test_end_session_without_start_noop(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        # Should not raise
        mgr.end_session(summary="No active session")

    def test_summary_file_written(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        mgr.start_session(methodology="waterfall")
        mgr.record_file_processed("req1.md")
        mgr.record_elements(created=5)
        mgr.end_session(summary="Generated 5 modules")

        summary_path = tmp_path / "source" / "session_summary.md"
        assert summary_path.exists()
        content = summary_path.read_text()
        assert "waterfall" in content
        assert "Generated 5 modules" in content
        assert "req1.md" in content


class TestSessionManagerIncremental:
    """Incremental run support: detecting changed files since last run."""

    def test_files_since_date(self, tmp_path):
        source_dir = tmp_path / "source"
        now = datetime.now(timezone.utc)

        # Create files with different modification times
        _touch(source_dir / "old.md", mtime=(now - timedelta(days=7)).timestamp())
        _touch(source_dir / "recent.md", mtime=(now - timedelta(hours=1)).timestamp())
        _touch(source_dir / "new.md", mtime=now.timestamp())

        mgr = SessionManager(project_root=tmp_path)
        since = now - timedelta(days=1)

        changed = mgr.get_changed_source_files(since=since)
        filenames = {Path(f).name for f in changed}
        assert "recent.md" in filenames
        assert "new.md" in filenames
        assert "old.md" not in filenames

    def test_find_new_files_since_last_run(self, tmp_path):
        source_dir = tmp_path / "source"

        # First run: create and process old.md
        _touch(source_dir / "old.md", content="old content")
        mgr = SessionManager(project_root=tmp_path)
        mgr.start_session()
        mgr.record_file_processed("old.md")
        mgr.end_session()

        # After end_session, finished_at is set to now.
        # Wait a tiny bit so new files have strictly later mtime.
        time.sleep(0.02)

        # Create a new file and re-touch old.md
        _touch(source_dir / "new.md", content="new content")
        os.utime(str(source_dir / "old.md"), None)  # None = now

        since = mgr.get_last_run_time()
        assert since is not None
        changed = mgr.get_changed_source_files(since=since)
        filenames = {Path(f).name for f in changed}
        assert "old.md" in filenames
        assert "new.md" in filenames

    def test_no_files_since_none(self, tmp_path):
        source_dir = tmp_path / "source"
        _touch(source_dir / "file.md")

        mgr = SessionManager(project_root=tmp_path)
        # No 'since' means process all
        changed = mgr.get_changed_source_files(since=None)
        assert len(changed) == 1

    def test_get_last_run_time_no_history(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        assert mgr.get_last_run_time() is None

    def test_is_file_processed(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        mgr.start_session()
        mgr.record_file_processed("req1.md")
        mgr.end_session()

        assert mgr.is_file_processed("req1.md") is True
        assert mgr.is_file_processed("req2.md") is False

    def test_get_unprocessed_source_files(self, tmp_path):
        source_dir = tmp_path / "source"
        _touch(source_dir / "req1.md")
        _touch(source_dir / "req2.md")
        _touch(source_dir / "req3.md")

        mgr = SessionManager(project_root=tmp_path)
        mgr.start_session()
        mgr.record_file_processed("req1.md")
        mgr.end_session()

        unprocessed = mgr.get_unprocessed_source_files()
        filenames = {Path(f).name for f in unprocessed}
        assert "req2.md" in filenames
        assert "req3.md" in filenames
        assert "req1.md" not in filenames


class TestSessionManagerHistory:
    """Querying session history."""

    def test_get_history_returns_latest_first(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)

        mgr.start_session()
        mgr.end_session(summary="Run 1")
        time.sleep(0.01)  # ensure different timestamps

        mgr.start_session()
        mgr.end_session(summary="Run 2")

        history = mgr.get_history()
        assert len(history) == 2
        assert history[0].summary == "Run 2"  # latest first
        assert history[1].summary == "Run 1"

    def test_get_history_limit(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)

        for i in range(5):
            mgr.start_session()
            mgr.end_session(summary=f"Run {i}")

        assert len(mgr.get_history(limit=3)) == 3
        assert len(mgr.get_history(limit=10)) == 5

    def test_clear_history(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        mgr.start_session()
        mgr.end_session(summary="Run 1")

        mgr.clear_history()
        assert mgr.get_history() == []


class TestSessionManagerSourceDir:
    """Source directory detection."""

    def test_source_dir_default(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path)
        assert mgr.source_dir == tmp_path / "source"

    def test_source_dir_custom(self, tmp_path):
        mgr = SessionManager(project_root=tmp_path, source_dir_name="sources_raw")
        assert mgr.source_dir == tmp_path / "sources_raw"
