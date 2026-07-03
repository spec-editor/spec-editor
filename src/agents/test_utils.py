"""Shared test utilities — used by engine, agents, and tools.

Extracted from WorkflowEngine and AgentWorker to eliminate duplication.
"""
import subprocess as _sp
from pathlib import Path


def run_pytest(test_file: Path, cwd: str, timeout: int = 120) -> tuple[bool, str]:
    """Run pytest on a single test file.

    Returns (passed: bool, output: str) where output contains
    stdout+stderr trimmed to 2000 chars for use in failure notes.
    """
    try:
        tr = _sp.run(
            [
                "python", "-m", "pytest",
                str(test_file),
                "-q", "--tb=short", "--no-header",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = (tr.stdout + "\n" + tr.stderr).strip()
        # Trim but keep the important parts (first + last lines)
        if len(output) > 2000:
            output = output[:1500] + "\n...\n" + output[-500:]
        return tr.returncode == 0, output
    except _sp.TimeoutExpired:
        return False, f"Test timed out after {timeout}s"
    except Exception as exc:
        return False, f"Test runner error: {exc}"


def find_test_file(leaf_id: str) -> Path | None:
    """Find the test file for a leaf element by naming convention.

    Looks for ``tests/test_{leaf_lower}_*.py`` (e.g. NFR-001 → test_nfr001_*).
    """
    if not leaf_id:
        return None
    prefix = leaf_id.lower().replace("-", "")
    tests_dir = Path("tests")
    if not tests_dir.is_dir():
        return None
    for f in sorted(tests_dir.glob(f"test_{prefix}_*.py")):
        return f
    for f in sorted(tests_dir.glob(f"test_{prefix}.py")):
        return f
    return None
