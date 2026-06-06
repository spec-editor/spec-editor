"""Smoke tests — verify all CLI commands register and don't crash on --help."""

import pytest
from click.testing import CliRunner

from src.main import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestCLICommandsExist:
    """All commands should register and show --help without errors."""

    def test_cli_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_init_help(self, runner):
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0

    def test_run_help(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0

    def test_validate_help(self, runner):
        result = runner.invoke(cli, ["validate", "--help"])
        assert result.exit_code == 0

    def test_status_help(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0

    def test_log_help(self, runner):
        result = runner.invoke(cli, ["log", "--help"])
        assert result.exit_code == 0

    def test_export_help(self, runner):
        result = runner.invoke(cli, ["export", "--help"])
        assert result.exit_code == 0

    def test_codegen_help(self, runner):
        result = runner.invoke(cli, ["codegen", "--help"])
        assert result.exit_code == 0

    def test_analyze_help(self, runner):
        result = runner.invoke(cli, ["analyze", "--help"])
        assert result.exit_code == 0

    def test_view_help(self, runner):
        result = runner.invoke(cli, ["view", "--help"])
        assert result.exit_code == 0

    def test_demo_help(self, runner):
        result = runner.invoke(cli, ["demo", "--help"])
        assert result.exit_code == 0

    def test_decisions_help(self, runner):
        result = runner.invoke(cli, ["decisions", "--help"])
        assert result.exit_code == 0

    def test_context_help(self, runner):
        result = runner.invoke(cli, ["context", "--help"])
        assert result.exit_code == 0

    def test_questions_help(self, runner):
        result = runner.invoke(cli, ["questions", "--help"])
        assert result.exit_code == 0

    def test_deprecate_help(self, runner):
        result = runner.invoke(cli, ["deprecate", "--help"])
        assert result.exit_code == 0

    def test_restore_help(self, runner):
        result = runner.invoke(cli, ["restore", "--help"])
        assert result.exit_code == 0

    def test_hooks_help(self, runner):
        result = runner.invoke(cli, ["hooks", "--help"])
        assert result.exit_code == 0

    def test_mcp_help(self, runner):
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0


class TestSmokeCommands:
    """Basic commands with default args should not crash."""

    def test_demo_runs(self, runner, tmp_path):
        result = runner.invoke(cli, ["demo", "-o", str(tmp_path / "demo-out")])
        assert result.exit_code == 0

    def test_init_creates_project(self, runner, tmp_path):
        proj = tmp_path / "test-proj"
        result = runner.invoke(cli, ["init", str(proj)])
        assert result.exit_code == 0
        assert (proj / "methodology.yaml").exists()
        assert (proj / "agents.yaml").exists()
        assert (proj / "source").is_dir()
        assert (proj / "sources_raw").is_dir()

    def test_status_on_empty_project(self, runner, tmp_path):
        proj = tmp_path / "test-proj"
        runner.invoke(cli, ["init", str(proj)])
        result = runner.invoke(cli, ["status", "-p", str(proj)])
        assert result.exit_code == 0

    def test_validate_on_empty_project(self, runner, tmp_path):
        proj = tmp_path / "test-proj"
        runner.invoke(cli, ["init", str(proj)])
        result = runner.invoke(cli, ["validate", "-p", str(proj)])
        assert result.exit_code == 0
