"""spec-editor-cycle — Trace-Till-Debug cycle plugin.

Adds production log analysis, bug ingestion, spec updates, and
persistent agent workers for coding, testing, and deployment.
"""

# Public API
# Re-export from cycle modules
from spec_editor_cycle.analyzer import LogAnalyzer  # noqa: F401
from spec_editor_cycle.collector import LogCollector  # noqa: F401
from spec_editor_cycle.models import (  # noqa: F401
    BaselineEntry,
    BugReport,
    CycleLoopState,
)
from spec_editor_cycle.plugin import CyclePlugin  # noqa: F401
