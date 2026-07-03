"""Feature-aware tracing with requirement linking and scenario filtering.

Links code symbols to specification elements via @implements, and provides
runtime log filtering by user scenario ID (SCN-* / DSC-* / STP-*).

=== Static traceability ===

    from src.tracing import implements

    @implements("MOD-001")
    class MyModule:
        ...

=== Runtime feature logging ===

    from src.tracing import FeatureLogger

    log = FeatureLogger("SCN-002")

    # Enable at startup (env, config, or CLI flag):
    FeatureLogger.enable("SCN-002")
    FeatureLogger.enable("SCN-003")

    # All log calls pass through the filter:
    log.info("operator_clicked_preview", site_id=123)
    # → appears in logs ONLY if SCN-002 is enabled

    log.debug("render_in_progress", template="landing")
    # → same filter

=== Dynamic enable/disable at runtime ===

    FeatureLogger.enable("SCN-002")   # start tracing a scenario
    FeatureLogger.disable("SCN-002")  # stop tracing
    FeatureLogger.enabled()           # → {"SCN-002", "SCN-003"}

=== Context manager for block-level tracing ===

    with FeatureLogger.context("SCN-002"):
        # All FeatureLogger calls inside this block are force-enabled
        do_something()

This enables "online monitoring" — you filter the log stream by feature
instead of drowning in the full output.

=== Structured JSON-lines logging with traceability ===

    from src.tracing import StructuredLogEmitter

    log = StructuredLogEmitter(module_id="MOD-001", scenario_id="SCN-002")

    @implements("MOD-001")
    class MyHandler:
        def handle(self, request):
            log.info("request_started", method=request.method)
            # → writes JSON line to logs/MOD-001/structured.jsonl
            # → auto-detects element_id="MOD-001" from @implements

=== Function tracing decorator ===

    from src.tracing import traced

    @traced("MOD-001")
    async def handle_request(method: str, params: dict):
        ...
    # → auto-logs: call, completion (with duration), or exception
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import os
import sys
import threading
import time
import traceback as tb
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import structlog

T = TypeVar("T", bound=type)

# ------------------------------------------------------------------
# Static: @implements decorator
# ------------------------------------------------------------------


def implements(req_id: str) -> Callable[[T], T]:
    """Decorator linking a class/function to its specification requirement.

    The req_id is extracted by the AST parser (src/mcp/parsers/python.py)
    for bidirectional traceability verification.
    """

    def decorator(target: T) -> T:
        target.__implements__ = req_id
        return target

    return decorator


# ------------------------------------------------------------------
# Runtime: FeatureLogger — scenario-filtered structured logging
# ------------------------------------------------------------------

# Global set of enabled scenario IDs.
# FeatureLogger checks this before emitting.
_enabled_scenarios: set[str] = set()

# Context variable: when set, forces logging for a specific scenario
# regardless of global enable. Used by FeatureLogger.context().
_force_scenario: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_force_scenario", default=None
)

# Context variable: set by the cycle loop to tag log records with
# the current iteration number (1, 2, 3, ...). Used for debugging
# convergence — know which iteration produced which log lines.
_current_iteration: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_iteration", default=""
)


def set_iteration_id(iteration_id: str) -> None:
    """Set the current cycle loop iteration ID for log tagging."""
    _current_iteration.set(iteration_id)


class FeatureLogger:
    """Structlog wrapper that filters messages by scenario ID.

    Only emits logs when:
    - The logger's scenario_id is in the globally enabled set, OR
    - The scenario_id matches the current forced context.

    Usage:
        log = FeatureLogger("SCN-002")
        log.info("event_name", key="value")
    """

    def __init__(self, scenario_id: str) -> None:
        if not scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        self._scenario_id = scenario_id
        self._logger = structlog.get_logger(__name__)

    # ---- public API mirroring structlog ----

    def debug(self, event: str, **kwargs: object) -> None:
        if self._should_log():
            self._logger.debug(event, scenario_id=self._scenario_id, **kwargs)

    def info(self, event: str, **kwargs: object) -> None:
        if self._should_log():
            self._logger.info(event, scenario_id=self._scenario_id, **kwargs)

    def warning(self, event: str, **kwargs: object) -> None:
        if self._should_log():
            self._logger.warning(event, scenario_id=self._scenario_id, **kwargs)

    def error(self, event: str, **kwargs: object) -> None:
        if self._should_log():
            self._logger.error(event, scenario_id=self._scenario_id, **kwargs)

    def exception(self, event: str, **kwargs: object) -> None:
        if self._should_log():
            self._logger.exception(event, scenario_id=self._scenario_id, **kwargs)

    # ---- filtering logic ----

    def _should_log(self) -> bool:
        forced = _force_scenario.get()
        if forced is not None:
            return forced == self._scenario_id
        if "*" in _enabled_scenarios:
            return True
        return self._scenario_id in _enabled_scenarios

    # ---- global state management ----

    @staticmethod
    def enable(scenario_id: str) -> None:
        """Enable logging for a specific scenario ID."""
        _enabled_scenarios.add(scenario_id)

    @staticmethod
    def disable(scenario_id: str) -> None:
        """Disable logging for a specific scenario ID."""
        _enabled_scenarios.discard(scenario_id)

    @staticmethod
    def enabled() -> set[str]:
        """Return the set of currently enabled scenario IDs."""
        return set(_enabled_scenarios)

    @staticmethod
    def enable_all() -> None:
        """Enable logging for ALL scenarios (pass-through mode)."""
        _enabled_scenarios.clear()
        _enabled_scenarios.add("*")

    @staticmethod
    def disable_all() -> None:
        """Disable all scenario-specific logging."""
        _enabled_scenarios.clear()

    # ---- context manager ----

    @staticmethod
    def context(scenario_id: str):
        """Context manager that force-enables logging for a scenario block.

        Usage:
            with FeatureLogger.context("SCN-002"):
                helper = FeatureLogger("SCN-002")
                helper.info("inside_context")  # always emitted
        """
        return _ForceScenarioContext(scenario_id)


class _ForceScenarioContext:
    """Context manager that sets the forced scenario for the block."""

    def __init__(self, scenario_id: str) -> None:
        self._scenario_id = scenario_id
        self._token: contextvars.Token | None = None

    def __enter__(self) -> None:
        self._token = _force_scenario.set(self._scenario_id)

    def __exit__(self, *args: object) -> None:
        if self._token is not None:
            _force_scenario.reset(self._token)
            self._token = None


# ------------------------------------------------------------------
# StructuredLogEmitter — JSON-lines logs partitioned by module
# ------------------------------------------------------------------

# Global registry of file locks: one lock per output file path.
# Multiple StructuredLogEmitter instances with the same module_id
# share a lock to avoid interleaved writes.
_file_locks: dict[str, threading.Lock] = {}
_file_locks_lock = threading.Lock()


def _get_file_lock(filepath: str) -> threading.Lock:
    """Get or create a lock for a log file path. Thread-safe."""
    with _file_locks_lock:
        if filepath not in _file_locks:
            _file_locks[filepath] = threading.Lock()
        return _file_locks[filepath]


# ======================================================================
# LogConfigBackend — pluggable runtime logging control
# ======================================================================

_LOG_CONFIG_TTL = 5.0  # seconds between config re-reads


class LogConfigBackend(ABC):
    """Abstract backend for runtime logging configuration.

    Subclass to integrate with Redis, Unleash, or any other
    dynamic configuration source.  Implementations should return
    a dict with optional keys:

        level: str        — minimum severity (debug, info, warning, error)
        modules: list     — only log these MOD-* IDs (empty = all)
        elements: list    — only log events linked to these element IDs (empty = all)
        silenced: list    — never log these MOD-* IDs
    """

    @abstractmethod
    def get_config(self) -> dict[str, Any]:
        """Return the current logging configuration dict."""
        ...


class LocalYamlBackend(LogConfigBackend):
    """Read logging config from ``local.yaml`` with TTL caching.

    The ``logging`` section of local.yaml supports::

        logging:
          level: info          # debug | info | warning | error
          modules: []          # empty = all; ["MOD-001"] = only these
          elements: []         # empty = all; ["NFR-001"] = only from this spec element
          silenced: []         # never log these modules
    """

    def __init__(self, project_path: str | Path) -> None:
        self._project_path = Path(project_path)
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._ttl: float = _LOG_CONFIG_TTL

    def get_config(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._cache_ts < self._ttl:
            return self._cache

        yaml_path = self._project_path / "local.yaml"
        try:
            if yaml_path.is_file():
                import yaml as _yaml
                raw = _yaml.safe_load(yaml_path.read_text()) or {}
                self._cache = raw.get("logging", {})
            else:
                self._cache = {}
        except Exception:
            self._cache = {}
        self._cache_ts = now
        return self._cache


# Global backend registry
_log_config_backend: LogConfigBackend | None = None
_log_config_global_cache: dict[str, Any] = {}
_log_config_global_ts: float = 0.0


def set_log_config_backend(backend: LogConfigBackend) -> None:
    """Set the global logging configuration backend.

    All StructuredLogEmitter instances check this backend before writing.
    """
    global _log_config_backend, _log_config_global_cache, _log_config_global_ts
    _log_config_backend = backend
    _log_config_global_cache = {}
    _log_config_global_ts = 0.0


def _should_log(module_id: str, element_id: str, severity: str) -> bool:
    """Check whether a log event should be emitted based on current config."""
    global _log_config_backend, _log_config_global_cache, _log_config_global_ts
    now = time.monotonic()
    if now - _log_config_global_ts >= _LOG_CONFIG_TTL:
        if _log_config_backend is not None:
            _log_config_global_cache = _log_config_backend.get_config()
        _log_config_global_ts = now

    cfg = _log_config_global_cache
    if not cfg:
        return True  # no config = log everything

    # Check silenced modules
    silenced = cfg.get("silenced", [])
    if module_id in silenced:
        return False

    # Check module allowlist
    allowed_modules = cfg.get("modules", [])
    if allowed_modules and module_id not in allowed_modules:
        return False

    # Check element allowlist
    allowed_elements = cfg.get("elements", [])
    if allowed_elements and element_id not in allowed_elements:
        return False

    # Check level
    min_level = cfg.get("level", "debug")
    _level_order = {"debug": 0, "info": 1, "warning": 2, "error": 3}
    if _level_order.get(severity.lower(), 0) < _level_order.get(min_level, 0):
        return False

    return True


class StructuredLogEmitter:
    """Emits structured JSON-lines logs with traceability to specification elements.

    Each log line is a JSON object written to ``logs/{module_id}/structured.jsonl``.
    The ``element_id`` field is auto-detected from ``@implements`` on the calling
    class by walking the Python call stack.

    Logging can be dynamically controlled per module, element, and severity
    via a pluggable ``LogConfigBackend``.  The default backend reads from
    ``local.yaml`` with a TTL cache (re-reads every 5 seconds).

    Output format (one JSON object per line)::

        {
          "module_id": "MOD-001",
          "scenario_id": "SCN-002",
          "element_id": "MOD-001",
          "event": "handler_error",
          "severity": "error",
          "ts": "2025-06-21T10:00:00.123456+00:00",
          "error": "KeyError: 'params'",
          "traceback": "Traceback (most recent call last):\\n  ..."
        }

    All extra ``**kwargs`` passed to the log method become top-level fields.
    Values are JSON-serialised; non-serialisable values are converted to strings.

    Thread-safety: writes within a single module_id are serialised via a shared
    lock so that concurrent threads never interleave JSON lines.

    Usage::

        from src.tracing import StructuredLogEmitter

        log = StructuredLogEmitter(module_id="MOD-001", scenario_id="SCN-002")

        @implements("MOD-001")
        class MyHandler:
            def handle(self, request):
                log.info("request_started", method=request.method)
                # → logs/MOD-001/structured.jsonl
    """

    # Severity constants for type-safe use.
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    def __init__(
        self,
        module_id: str,
        scenario_id: str = "",
        log_dir: str | Path = "logs",
        auto_element: bool = True,
    ) -> None:
        """Create a structured log emitter for a module.

        Args:
            module_id: The MOD-* identifier of the owning module (required).
            scenario_id: Optional SCN-* scenario being executed.
            log_dir: Root directory for log output.
                     Each module writes to ``{log_dir}/{module_id}/structured.jsonl``.
            auto_element: If True (default), walk the call stack to detect
                          ``@implements`` on the calling class and populate
                          ``element_id`` automatically.
        """
        if not module_id:
            raise ValueError("module_id must be a non-empty string")

        self._module_id = module_id
        self._scenario_id = scenario_id
        self._auto_element = auto_element

        log_path = Path(log_dir)
        self._file_dir = log_path / module_id
        self._file_path = self._file_dir / "structured.jsonl"
        self._lock = _get_file_lock(str(self._file_path.resolve()))

    # -- public properties -------------------------------------------------

    @property
    def module_id(self) -> str:
        """The module this emitter is bound to."""
        return self._module_id

    @property
    def scenario_id(self) -> str:
        """The scenario this emitter is bound to (may be empty)."""
        return self._scenario_id

    @property
    def file_path(self) -> Path:
        """Full path to the JSON-lines log file."""
        return self._file_path

    # -- public log methods ------------------------------------------------

    def debug(self, event: str, **kwargs: Any) -> None:
        """Emit a debug-level log line."""
        self._emit(self.DEBUG, event, None, kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        """Emit an info-level log line."""
        self._emit(self.INFO, event, None, kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        """Emit a warning-level log line."""
        self._emit(self.WARNING, event, None, kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        """Emit an error-level log line.

        If called inside an ``except`` block, the current exception's
        message and traceback are included automatically.
        """
        exc_info = self._capture_exc()
        self._emit(self.ERROR, event, exc_info, kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        """Emit an error-level log line. Always captures the current exception.

        Unlike :meth:`error`, this *always* includes traceback info.
        Use inside an ``except`` block.
        """
        exc_info = self._capture_exc() or ("<no active exception>", "")
        self._emit(self.ERROR, event, exc_info, kwargs)

    # -- internals ---------------------------------------------------------

    def _emit(
        self,
        severity: str,
        event: str,
        exc_info: tuple[str, str] | None,
        kwargs: dict[str, Any],
    ) -> None:
        """Build the log record, serialise to JSON, and write to file."""

        # ── Runtime log filtering via pluggable backend ──
        element_id = self._detect_element_id() if self._auto_element else ""
        if not _should_log(self._module_id, element_id, severity):
            return

        ts = datetime.now(timezone.utc).isoformat()

        record: dict[str, Any] = {
            "module_id": self._module_id,
            "scenario_id": self._scenario_id,
            "element_id": element_id,
            "event": event,
            "severity": severity,
            "ts": ts,
        }

        # ── Iteration ID (set by cycle loop per-iteration) ──
        iteration_id = _current_iteration.get("")
        if iteration_id:
            record["iteration_id"] = iteration_id

        # Attach exception info if present.
        if exc_info is not None:
            record["error"] = exc_info[0]
            if exc_info[1]:
                record["traceback"] = exc_info[1]

        # Merge user-supplied kwargs (JSON-safe).
        for key, value in kwargs.items():
            record[key] = self._safe_json_value(value)

        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        self._write_line(line)

    def _write_line(self, line: str) -> None:
        """Write a single line to the log file. Thread-safe via lock."""
        self._file_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(line)

    # -- element detection -------------------------------------------------

    @staticmethod
    def _detect_element_id() -> str:
        """Walk the call stack to find ``@implements`` on the calling class.

        Searches stack frames from innermost to outermost.  For every frame
        that has a ``self`` local, checks the class (and its MRO) for a
        ``__implements__`` attribute set by the :func:`implements` decorator.

        Returns the first match found, or an empty string.
        """
        try:
            for frame_info in inspect.stack():
                frame = frame_info.frame
                self_obj = frame.f_locals.get("self")
                if self_obj is None:
                    continue
                for cls in type(self_obj).__mro__:
                    impl = getattr(cls, "__implements__", None)
                    if impl is not None:
                        return impl
        finally:
            # Avoid reference cycles that delay GC.
            del frame_info
        return ""

    # -- exception capture -------------------------------------------------

    @staticmethod
    def _capture_exc() -> tuple[str, str] | None:
        """Return (message, traceback) for the active exception, or None."""
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is None:
            return None
        message = f"{exc_type.__name__}: {exc_value}"
        trace = tb.format_exc()
        return message, trace

    # -- JSON helpers ------------------------------------------------------

    @staticmethod
    def _safe_json_value(value: Any) -> Any:
        """Convert a value to something JSON-serialisable.

        Primitives pass through; everything else becomes ``str(value)``.
        """
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, (list, tuple)):
            return [StructuredLogEmitter._safe_json_value(v) for v in value]
        if isinstance(value, dict):
            return {
                str(k): StructuredLogEmitter._safe_json_value(v)
                for k, v in value.items()
            }
        return str(value)


# ------------------------------------------------------------------
# @traced — function-level auto-logging decorator
# ------------------------------------------------------------------


def traced(
    module_id: str,
    scenario_id: str = "",
    log_dir: str | Path = "logs",
) -> Callable:
    """Decorator that auto-logs function entry, exit, and exceptions.

    Uses :class:`StructuredLogEmitter` internally.  Works on both
    synchronous and ``async def`` functions.

    On call:
        ``log.info("{func_name}_called", **bound_arguments)``

    On return:
        ``log.info("{func_name}_completed", duration_ms=...)``

    On exception:
        ``log.error("{func_name}_failed", error=..., duration_ms=...)``
        — then re-raises.

    Args:
        module_id: The MOD-* identifier to tag all log lines with.
        scenario_id: Optional SCN-* scenario.
        log_dir: Root directory for log output (default: ``"logs"``).

    Usage::

        @traced("MOD-001")
        async def handle_request(method: str, params: dict):
            ...
    """
    log = StructuredLogEmitter(
        module_id=module_id,
        scenario_id=scenario_id,
        log_dir=log_dir,
    )

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                log.info(f"{func.__name__}_called", **bound.arguments)
                start = time.monotonic()
                try:
                    result = await func(*args, **kwargs)
                    duration_ms = round((time.monotonic() - start) * 1000, 2)
                    log.info(
                        f"{func.__name__}_completed",
                        duration_ms=duration_ms,
                    )
                    return result
                except Exception:
                    duration_ms = round((time.monotonic() - start) * 1000, 2)
                    log.error(
                        f"{func.__name__}_failed",
                        duration_ms=duration_ms,
                    )
                    raise

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                log.info(f"{func.__name__}_called", **bound.arguments)
                start = time.monotonic()
                try:
                    result = func(*args, **kwargs)
                    duration_ms = round((time.monotonic() - start) * 1000, 2)
                    log.info(
                        f"{func.__name__}_completed",
                        duration_ms=duration_ms,
                    )
                    return result
                except Exception:
                    duration_ms = round((time.monotonic() - start) * 1000, 2)
                    log.error(
                        f"{func.__name__}_failed",
                        duration_ms=duration_ms,
                    )
                    raise

            return sync_wrapper

    return decorator
