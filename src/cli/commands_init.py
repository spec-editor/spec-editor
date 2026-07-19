"""CLI subcommand."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.cli.commands import (
    _BUILTIN_METHODOLOGIES,
    _EXAMPLE_TEMPLATE,
    _README_TEMPLATE,
    cli,
    console,
)
from src.config.methodology import load_methodology
from src.config.settings import AgentConfig, AgentsConfig

from src.agents.constants import (
    AGENT_1,
    AGENT_2,
    ALL_PROVIDERS,
    CONFIG_KEY_AGENTS,
    CONFIG_KEY_MAX_ROUNDS,
    CONFIG_KEY_MAX_TIME_MINUTES,
    DEFAULT_CHAT_MODEL,
    DEFAULT_METHODOLOGY,
    DEFAULT_PROVIDER,
    DEFAULT_REASONING_MODEL,
    DEFAULT_REDIS_URL,
    ORCHESTRATOR,
    PROVIDER_ENV_VARS,
)

# ======================================================================
# init
# ======================================================================

_EXAMPLE_TEMPLATE = """\
# Online Bookstore — Requirements

A web application for selling books online. Customers browse a catalog,
add items to cart, and complete purchases with credit card payment.

## Purpose
Replace the existing spreadsheet-based order system with a self-service
web store for retail customers.

## Key Features
- Book catalog with search by title, author, and category
- Shopping cart with quantity management
- Checkout: shipping address, credit card payment, order confirmation
- User accounts: registration, login, order history
- Admin dashboard: inventory management, order processing, discount codes

## Constraints
- Page load time < 2 seconds under normal load
- Support 1000 concurrent users during peak hours
- PCI-DSS compliance for credit card payments
- GDPR compliance for user data (export, deletion)
- Inventory must not oversell (concurrent checkout safety)
"""


def _check_docker() -> bool:
    """Check if Docker is installed and running."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_code_available() -> bool:
    """Check if VSCode CLI is available."""
    import shutil
    return shutil.which("code") is not None


def _check_project_initialized(project_path: Path) -> bool:
    """Check if a spec-editor project is already initialized."""
    return (project_path / "methodology.yaml").exists()


def _read_existing_config(project_path: Path) -> dict[str, Any]:
    """Read existing configuration for smart defaults on re-init."""
    defaults: dict[str, Any] = {}

    agents_yaml = project_path / "agents.yaml"
    if agents_yaml.exists():
        try:
            config = yaml.safe_load(agents_yaml.read_text())
            agents = config.get(CONFIG_KEY_AGENTS, {})
            defaults["agent_1_provider"] = agents.get(AGENT_1, {}).get("provider", DEFAULT_PROVIDER)
            defaults["agent_1_model"] = agents.get(AGENT_1, {}).get("model", DEFAULT_REASONING_MODEL)
            defaults["agent_2_model"] = agents.get(AGENT_2, {}).get("model", DEFAULT_CHAT_MODEL)
        except Exception:
            pass

    local_yaml = project_path / "local.yaml"
    if local_yaml.exists():
        try:
            config = yaml.safe_load(local_yaml.read_text())
            defaults["report_errors"] = config.get("report_errors", True)
            defaults["use_web_ui"] = config.get("use_web_ui", False)
        except Exception:
            pass

    return defaults


@cli.command()
@click.argument("path", type=click.Path(), default=".")
@click.option("--methodology", "-m", default=DEFAULT_METHODOLOGY, help="Methodology name")
@click.option("--non-interactive", is_flag=True, help="Skip interactive prompts, use defaults")
@click.option("--with-example", is_flag=True, help="Include a sample requirements document to try spec-editor run")
def init(
    path: str,
    methodology: str,
    non_interactive: bool = False,
    with_example: bool = False,
) -> None:
    """Initialize a new specification project at PATH.

    Interactive mode asks about LLM provider, API key, error reporting,
    and web UI.  On re-init, existing values are shown as defaults.

    \b
    Interactive:  spec-editor init
    Non-interactive: spec-editor init --non-interactive
    """
    project_path = Path(path).resolve()
    is_reinit = _check_project_initialized(project_path)

    if is_reinit:
        console.print(f"\n[bold cyan]Re-initializing existing project:[/bold cyan] {project_path}")
    else:
        console.print(f"\n[bold green]Creating new project:[/bold green] {project_path}")

    # ── Pre-flight checks ──
    method_path = _BUILTIN_METHODOLOGIES / f"{methodology}.yaml"
    if not method_path.exists():
        available = [p.stem for p in _BUILTIN_METHODOLOGIES.glob("*.yaml")]
        console.print(f"[red]Methodology '{methodology}' not found.[/red] Available: {', '.join(available)}")
        raise SystemExit(1)

    # ── Interactive questions ──
    existing = _read_existing_config(project_path) if is_reinit else {}

    if non_interactive:
        provider = existing.get("agent_1_provider", DEFAULT_PROVIDER)
        model = existing.get("agent_1_model", DEFAULT_REASONING_MODEL)
        chat_model = existing.get("agent_2_model", DEFAULT_CHAT_MODEL)
        api_key = ""
        report_errors = existing.get("report_errors", True)
        use_web_ui = existing.get("use_web_ui", False)
    else:
        console.print("\n[bold]LLM Configuration[/bold]")
        provider = click.prompt(
            "  Provider",
            type=click.Choice(list(ALL_PROVIDERS)),
            default=existing.get("agent_1_provider", DEFAULT_PROVIDER),
        )
        model = click.prompt(
            "  Reasoning model",
            default=existing.get("agent_1_model", DEFAULT_REASONING_MODEL),
        )
        chat_model = click.prompt(
            "  Chat model (for simpler tasks)",
            default=existing.get("agent_2_model", DEFAULT_CHAT_MODEL),
        )
        api_key = click.prompt(
            "  API key",
            default="",
            hide_input=True,
            show_default=False,
        ) or ""

        console.print("\n[bold]Error Reporting[/bold]")
        report_errors = click.confirm(
            "  Report stack traces on errors?",
            default=existing.get("report_errors", True),
        )

        console.print("\n[bold]Web Interface[/bold]")
        docker_available = _check_docker()
        if not docker_available:
            console.print("  [yellow]Docker not detected — web UI unavailable[/yellow]")
            use_web_ui = False
        else:
            use_web_ui = click.confirm(
                "  Start web UI locally (requires Docker)?",
                default=existing.get("use_web_ui", False),
            )

    # ── Create directory structure ──
    project_path.mkdir(parents=True, exist_ok=True)
    aspects_dir = project_path / "aspects"
    aspects_dir.mkdir(exist_ok=True)
    for d in ["source", "sources_raw", "logs", "tests"]:
        (project_path / d).mkdir(exist_ok=True)

    # ── Copy methodology + skills + workflows ──
    try:
        method = load_methodology(method_path)
    except Exception as exc:
        console.print(f"[red]Error loading methodology:[/red] {exc}")
        raise SystemExit(1)

    shutil.copy(method_path, project_path / "methodology.yaml")

    # ── Seed example source document ──
    if with_example:
        example = (project_path / "source" / "readme.md")
        example.write_text(_EXAMPLE_TEMPLATE, encoding="utf-8")
        console.print("  [green]✓[/green] Example requirements document added to source/")

    skills_path = _BUILTIN_METHODOLOGIES.parent / "skills.yaml"
    if skills_path.exists():
        shutil.copy(skills_path, project_path / "skills.yaml")

    skills_dir = _BUILTIN_METHODOLOGIES.parent.parent / "skills"
    if skills_dir.is_dir():
        dest = project_path / "skills"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skills_dir, dest)

    workflows_dir = _BUILTIN_METHODOLOGIES.parent.parent / "workflows"
    if workflows_dir.is_dir():
        dest = project_path / "workflows"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(workflows_dir, dest)

    # ── Write agents.yaml ──
    agents_config = _create_default_agents_config()
    agents_config.agent_1.provider = provider
    agents_config.agent_1.model = model
    agents_config.agent_2.model = chat_model

    agents_yaml = {
        CONFIG_KEY_AGENTS: {
            AGENT_1: agents_config.agent_1.model_dump(),
            AGENT_2: agents_config.agent_2.model_dump(),
            ORCHESTRATOR: agents_config.orchestrator.model_dump(),
        },
        CONFIG_KEY_MAX_ROUNDS: agents_config.max_rounds,
        CONFIG_KEY_MAX_TIME_MINUTES: agents_config.max_time_minutes,
    }
    with open(project_path / "agents.yaml", "w", encoding="utf-8") as f:
        yaml.dump(agents_yaml, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ── Write .env with API key ──
    if api_key:
        env_var = PROVIDER_ENV_VARS.get(provider, "LLM_API_KEY")
        env_path = project_path / ".env"
        existing_env = env_path.read_text() if env_path.exists() else ""
        if env_var not in existing_env:
            with open(env_path, "a") as f:
                f.write(f"{env_var}={api_key}\n")

    # ── Write local.yaml ──
    local_yaml_path = project_path / "local.yaml"
    existing_local: dict[str, Any] = {}
    if local_yaml_path.exists():
        try:
            existing_local = yaml.safe_load(local_yaml_path.read_text()) or {}
        except Exception:
            pass
    existing_local["project_path"] = str(project_path)
    existing_local.setdefault("queue_url", DEFAULT_REDIS_URL)
    existing_local["report_errors"] = report_errors
    existing_local["use_web_ui"] = use_web_ui
    with open(local_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(existing_local, f, allow_unicode=True, default_flow_style=False)

    # ── Scaffold implementation directories ──
    try:
        from src.implementation.engine import ImplementationEngine
        impl_engine = ImplementationEngine(str(project_path))
        impl_engine.initialize_project()
    except Exception:
        pass

    # ── Config validation ──
    _validate_project_config(project_path, existing_local)

    # ── Post-init summary ──
    console.print(f"\n[bold green]Project initialized:[/bold green] {project_path}")
    console.print(f"  Methodology: {method.name} v{method.version}")
    console.print(f"  Reasoning:   {provider} / {model}")
    console.print(f"  Chat:        {provider} / {chat_model}")
    console.print(f"  Error reports: {'Yes' if report_errors else 'No'}")

    # Available paths
    table = Table(title="Available paths")
    table.add_column("Option", style="cyan")
    table.add_column("Command", style="white")
    table.add_column("Description", style="dim")

    table.add_row("A) CLI", f"cd {path} && spec-editor run", "Run the full cycle pipeline")
    table.add_row("B) VSCode", "spec-editor install-vscode", "GUI with diagrams, tree, validation")
    table.add_row("C) Web UI", "http://localhost:3000", "Browser-based interface") if use_web_ui else None
    table.add_row("Analyze", f"spec-editor analyze -t \"...\"", "Quick requirement analysis")
    table.add_row("Status", f"spec-editor status", "View project state")
    console.print(table)

    # Auto-install VSCode extension if code CLI is available
    if _check_code_available():
        console.print("\n[bold]VSCode detected![/bold] Installing extension...")
        try:
            result = subprocess.run(
                ["spec-editor", "install-vscode"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                console.print("[green]VSCode extension installed. Reload window to activate.[/green]")
        except Exception:
            pass  # silent — user can run manually

    if not is_reinit:
        console.print("\n[bold]Quick start:[/bold]")
        console.print(f"  cd {path} && spec-editor run")


def _create_default_agents_config() -> AgentsConfig:
    from src.agents.constants import DEFAULT_REASONING_MODEL, DEFAULT_CHAT_MODEL
    return AgentsConfig(
        agent_1=AgentConfig(
            provider="deepseek", model=DEFAULT_REASONING_MODEL, temperature=0.7
        ),
        agent_2=AgentConfig(
            provider="deepseek", model=DEFAULT_CHAT_MODEL, temperature=0.7
        ),
        orchestrator=AgentConfig(
            provider="deepseek", model=DEFAULT_REASONING_MODEL, temperature=0.3
        ),
        max_rounds=20,
        max_time_minutes=30,
    )


def _validate_project_config(project_path: Path, config: dict[str, Any]) -> None:
    """Validate project configuration for common misconfigurations.

    Checks:
        - auth.backend=casbin but no policy configured
        - templates.backend=copier but no source URL
        - enforcement.backend requires extra dependencies
    """
    warnings: list[str] = []

    # Auth validation
    auth_cfg = config.get("auth", {})
    if auth_cfg.get("backend") == "casbin":
        casbin_cfg = auth_cfg.get("casbin", {})
        if not casbin_cfg.get("policy") and not casbin_cfg.get("policy_file"):
            warnings.append(
                "auth.backend is 'casbin' but no policy or policy_file configured. "
                "All access will be denied until a policy is added."
            )

    # Templates validation
    tmpl_cfg = config.get("templates", {})
    if tmpl_cfg.get("backend") == "copier":
        if not tmpl_cfg.get("source"):
            warnings.append(
                "templates.backend is 'copier' but no 'source' URL configured. "
                "Set source to a git URL or local path."
            )

    # Enforcement validation
    enf_cfg = config.get("enforcement", {})
    enf_backend = enf_cfg.get("backend", "pytest")
    if enf_backend == "pytest_arch":
        try:
            import pytest_arch  # noqa: F401
        except ImportError:
            warnings.append(
                "enforcement.backend is 'pytest_arch' but pytest-arch is not installed. "
                "Install with: pip install pytest-arch"
            )
    elif enf_backend == "import_linter":
        try:
            import importlinter  # noqa: F401
        except ImportError:
            warnings.append(
                "enforcement.backend is 'import_linter' but import-linter is not installed. "
                "Install with: pip install import-linter"
            )

    # Events validation
    events_cfg = config.get("events", {})
    if events_cfg.get("backend") == "nats":
        try:
            import nats  # noqa: F401
        except ImportError:
            warnings.append(
                "events.backend is 'nats' but nats-py is not installed. "
                "Install with: pip install nats-py"
            )

    # Secrets validation
    secrets_cfg = config.get("secrets", {})
    if secrets_cfg.get("backend") == "aws_secrets":
        try:
            import boto3  # noqa: F401
        except ImportError:
            warnings.append(
                "secrets.backend is 'aws_secrets' but boto3 is not installed. "
                "Install with: pip install boto3"
            )
    elif secrets_cfg.get("backend") == "vault":
        try:
            import hvac  # noqa: F401
        except ImportError:
            warnings.append(
                "secrets.backend is 'vault' but hvac is not installed. "
                "Install with: pip install hvac"
            )

    if warnings:
        console.print("\n[yellow]⚠ Configuration warnings:[/yellow]")
        for w in warnings:
            console.print(f"  [yellow]• {w}[/yellow]")
