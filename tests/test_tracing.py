"""Tests for FeatureLogger — scenario-filtered structured logging."""

import io
import logging

import pytest
import structlog

from src.tracing import FeatureLogger, implements

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_feature_logger():
    """Reset global state before each test."""
    FeatureLogger.disable_all()
    yield
    FeatureLogger.disable_all()


@pytest.fixture
def log_output():
    """Capture structlog output into a StringIO buffer."""
    stream = io.StringIO()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)

    yield stream

    root_logger.handlers.clear()


# ------------------------------------------------------------------
# @implements — static decorator
# ------------------------------------------------------------------


class TestImplements:
    def test_decorator_sets_attribute(self):
        @implements("MOD-001")
        class MyClass:
            pass

        assert MyClass.__implements__ == "MOD-001"

    def test_decorator_on_function(self):
        @implements("SRC-005")
        def my_func():
            pass

        assert my_func.__implements__ == "SRC-005"


# ------------------------------------------------------------------
# FeatureLogger — basic filtering
# ------------------------------------------------------------------


class TestFeatureLoggerBasic:
    def test_disabled_by_default(self, log_output):
        log = FeatureLogger("SCN-002")
        log.info("test_event", key="value")
        output = log_output.getvalue()
        assert output == ""

    def test_enabled_scenario_logs(self, log_output):
        FeatureLogger.enable("SCN-002")
        log = FeatureLogger("SCN-002")
        log.info("test_event", key="value")
        output = log_output.getvalue()
        assert "test_event" in output
        assert "SCN-002" in output

    def test_different_scenario_not_logged(self, log_output):
        FeatureLogger.enable("SCN-002")
        log = FeatureLogger("SCN-003")
        log.info("test_event", key="value")
        output = log_output.getvalue()
        assert output == ""

    def test_disable_removes_scenario(self, log_output):
        FeatureLogger.enable("SCN-002")
        FeatureLogger.disable("SCN-002")
        log = FeatureLogger("SCN-002")
        log.info("test_event")
        assert log_output.getvalue() == ""

    def test_enable_all(self, log_output):
        FeatureLogger.enable_all()
        log = FeatureLogger("SCN-999")
        log.info("test_event")
        output = log_output.getvalue()
        assert "test_event" in output
        assert "SCN-999" in output

    def test_disable_all(self, log_output):
        FeatureLogger.enable("SCN-002")
        FeatureLogger.enable("SCN-003")
        FeatureLogger.disable_all()
        log1 = FeatureLogger("SCN-002")
        log2 = FeatureLogger("SCN-003")
        log1.info("event1")
        log2.info("event2")
        assert log_output.getvalue() == ""

    def test_enabled_returns_set(self):
        FeatureLogger.enable("SCN-002")
        FeatureLogger.enable("SCN-003")
        assert FeatureLogger.enabled() == {"SCN-002", "SCN-003"}


# ------------------------------------------------------------------
# FeatureLogger — log levels
# ------------------------------------------------------------------


class TestFeatureLoggerLevels:
    def test_debug_level(self, log_output):
        FeatureLogger.enable("SCN-001")
        log = FeatureLogger("SCN-001")
        log.debug("debug_event")
        assert "debug_event" in log_output.getvalue()

    def test_info_level(self, log_output):
        FeatureLogger.enable("SCN-001")
        log = FeatureLogger("SCN-001")
        log.info("info_event")
        assert "info_event" in log_output.getvalue()

    def test_warning_level(self, log_output):
        FeatureLogger.enable("SCN-001")
        log = FeatureLogger("SCN-001")
        log.warning("warn_event")
        assert "warn_event" in log_output.getvalue()

    def test_error_level(self, log_output):
        FeatureLogger.enable("SCN-001")
        log = FeatureLogger("SCN-001")
        log.error("error_event")
        assert "error_event" in log_output.getvalue()

    def test_scenario_id_in_output(self, log_output):
        FeatureLogger.enable("SCN-002")
        log = FeatureLogger("SCN-002")
        log.info("some_event", extra="data")
        output = log_output.getvalue()
        assert "SCN-002" in output
        assert "some_event" in output


# ------------------------------------------------------------------
# FeatureLogger — context manager
# ------------------------------------------------------------------


class TestFeatureLoggerContext:
    def test_context_forces_logging(self, log_output):
        # SCN-002 is NOT globally enabled
        log = FeatureLogger("SCN-002")

        with FeatureLogger.context("SCN-002"):
            log.info("forced_event")
            assert "forced_event" in log_output.getvalue()

    def test_context_only_matches_same_id(self, log_output):
        log = FeatureLogger("SCN-003")

        with FeatureLogger.context("SCN-002"):
            log.info("should_not_appear")
            assert log_output.getvalue() == ""

    def test_context_restores_after_exit(self, log_output):
        log = FeatureLogger("SCN-002")

        with FeatureLogger.context("SCN-002"):
            log.info("inside")

        log.info("outside")
        output = log_output.getvalue()
        assert "inside" in output
        assert "outside" not in output

    def test_nested_contexts(self, log_output):
        log2 = FeatureLogger("SCN-002")
        log3 = FeatureLogger("SCN-003")

        with FeatureLogger.context("SCN-002"):
            log2.info("outer_scn2")

            with FeatureLogger.context("SCN-003"):
                log3.info("inner_scn3")
                log2.info("scn2_in_inner")  # should NOT appear (inner forces SCN-003)

        output = log_output.getvalue()
        assert "outer_scn2" in output
        assert "inner_scn3" in output
        assert "scn2_in_inner" not in output


# ------------------------------------------------------------------
# FeatureLogger — edge cases
# ------------------------------------------------------------------


class TestFeatureLoggerEdgeCases:
    def test_empty_scenario_id_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            FeatureLogger("")

    def test_multiple_enabled_scenarios(self, log_output):
        FeatureLogger.enable("SCN-001")
        FeatureLogger.enable("SCN-002")

        log1 = FeatureLogger("SCN-001")
        log2 = FeatureLogger("SCN-002")
        log3 = FeatureLogger("SCN-003")

        log1.info("event1")
        log2.info("event2")
        log3.info("event3")

        output = log_output.getvalue()
        assert "event1" in output
        assert "event2" in output
        assert "event3" not in output

    def test_detailed_scenario_id(self, log_output):
        FeatureLogger.enable("DSC-002-01")
        log = FeatureLogger("DSC-002-01")
        log.info("step_event")
        assert "DSC-002-01" in log_output.getvalue()

    def test_step_scenario_id(self, log_output):
        FeatureLogger.enable("STP-002-01-01")
        log = FeatureLogger("STP-002-01-01")
        log.info("micro_step")
        assert "STP-002-01-01" in log_output.getvalue()
