"""CLI subcommand."""

import shutil
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console

from src.cli.commands import (
    _BUILTIN_METHODOLOGIES,
    _EXAMPLE_TEMPLATE,
    _README_TEMPLATE,
    cli,
    console,
)
from src.config.methodology import load_methodology
from src.config.settings import AgentConfig, AgentsConfig

# ======================================================================
# init
# ======================================================================


@cli.command()
@click.argument("path", type=click.Path())
@click.option("--methodology", "-m", default="waterfall", help="Methodology name")
@click.option(
    "--agents",
    "-a",
    default=None,
    type=click.Path(exists=True),
    help="YAML with agent configuration",
)
@click.option(
    "--with-example",
    is_flag=True,
    help="Include a sample requirements document to try spec-editor run",
)
@click.option(
    "--provider", default=None, help="LLM provider (openai, anthropic, deepseek, etc.)"
)
@click.option(
    "--model", default=None, help="Model name (e.g. gpt-4o, claude-sonnet-4-20250514)"
)
@click.option("--temperature", type=float, default=None, help="Temperature (0.0-2.0)")
@click.option("--max-tokens", type=int, default=None, help="Max tokens per response")
@click.option(
    "--api-key", default=None, envvar="LLM_API_KEY", help="API key for LLM provider"
)
def init(
    path: str,
    methodology: str,
    agents: str | None,
    with_example: bool = False,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    api_key: str | None = None,
) -> None:
    """Initialize a new specification project at PATH."""
    project_path = Path(path).resolve()

    # Create directory if it doesn't exist.
    project_path.mkdir(parents=True, exist_ok=True)

    method_path = _BUILTIN_METHODOLOGIES / f"{methodology}.yaml"
    if not method_path.exists():
        available = [p.stem for p in _BUILTIN_METHODOLOGIES.glob("*.yaml")]
        console.print(
            f"[red]Error:[/red] methodology '{methodology}' not found. "
            f"Available: {', '.join(available)}"
        )
        raise SystemExit(1)

    try:
        method = load_methodology(method_path)
    except Exception as exc:
        console.print(f"[red]Error loading methodology:[/red] {exc}")
        raise SystemExit(1)

    aspects_dir = project_path / "aspects"
    aspects_dir.mkdir(exist_ok=True)

    source_dir = project_path / "source"
    source_dir.mkdir(exist_ok=True)

    sources_raw_dir = project_path / "sources_raw"
    sources_raw_dir.mkdir(exist_ok=True)

    readme_path = source_dir / "readme.md"
    readme_path.write_text(
        _EXAMPLE_TEMPLATE if with_example else _README_TEMPLATE, encoding="utf-8"
    )

    shutil.copy(method_path, project_path / "methodology.yaml")

    # Copy skills.yaml if present (from project root, not data/)
    skills_path = _BUILTIN_METHODOLOGIES.parent.parent / "skills.yaml"
    if skills_path.exists():
        shutil.copy(skills_path, project_path / "skills.yaml")

    # Copy skills/ directory if present
    skills_dir = _BUILTIN_METHODOLOGIES.parent.parent / "skills"
    if skills_dir.is_dir():
        dest = project_path / "skills"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skills_dir, dest)

    # Copy workflows/ directory if present
    workflows_dir = _BUILTIN_METHODOLOGIES.parent.parent / "workflows"
    if workflows_dir.is_dir():
        dest = project_path / "workflows"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(workflows_dir, dest)

    agents_config = _create_default_agents_config()
    if agents:
        try:
            agents_config = AgentsConfig.from_yaml(Path(agents))
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] failed to load {agents}: {exc}")
            console.print("Using default configuration.")

    # Override with CLI options if provided
    overrides: dict[str, Any] = {}
    if provider is not None:
        overrides["provider"] = provider
    if model is not None:
        overrides["model"] = model
    if temperature is not None:
        overrides["temperature"] = temperature
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if overrides:
        for agent_name in ("agent_1", "agent_2", "orchestrator"):
            agent = getattr(agents_config, agent_name)
            for k, v in overrides.items():
                setattr(agent, k, v)

    # API key: write to .env file if provided
    if api_key:
        env_path = project_path / ".env"
        env_path.write_text(f"LLM_API_KEY={api_key}\n", encoding="utf-8")
        console.print(f"[green]API key saved to {env_path}[/green]")

    agents_yaml = {
        "agents": {
            "agent_1": agents_config.agent_1.model_dump(),
            "agent_2": agents_config.agent_2.model_dump(),
            "orchestrator": agents_config.orchestrator.model_dump(),
        },
        "max_rounds": agents_config.max_rounds,
        "max_time_minutes": agents_config.max_time_minutes,
    }
    with open(project_path / "agents.yaml", "w", encoding="utf-8") as f:
        yaml.dump(
            agents_yaml,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    # Create or update local.yaml — preserve existing fields on re-init
    local_yaml_path = project_path / "local.yaml"
    existing_local: dict[str, Any] = {}
    if local_yaml_path.exists():
        try:
            existing_local = yaml.safe_load(local_yaml_path.read_text()) or {}
        except Exception:
            pass
    existing_local["project_path"] = str(project_path)
    existing_local.setdefault("queue_url", "redis://localhost:6379")
    with open(local_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(existing_local, f, allow_unicode=True, default_flow_style=False)

    # ── Implementation Framework: scaffold directories + architecture tests ──
    try:
        from src.implementation.engine import ImplementationEngine

        impl_engine = ImplementationEngine(str(project_path))
        scaffold_result = impl_engine.initialize_project()
        dirs = scaffold_result.get("dirs_created", [])
        files = scaffold_result.get("files_written", [])
        if dirs:
            console.print(f"  [dim]Scaffolded {len(dirs)} directories from pattern[/dim]")
        if files:
            console.print(f"  [dim]Generated {len(files)} architecture file(s)[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] implementation scaffolding skipped: {exc}")

    # ── Config validation ──
    _validate_project_config(project_path, existing_local)

    console.print(f"[green]Project created:[/green] {project_path}")
    console.print(f"  Methodology: {method.name} v{method.version}")
    console.print(f"  Aspects: {len(method.aspects)}")
    console.print(
        f"  Agents: {agents_config.agent_1.model} / {agents_config.agent_2.model}"
    )
    console.print(f"  Orchestrator: {agents_config.orchestrator.model}")
    if with_example:
        console.print(f"\n  [green]Example source document ready![/green]")
        console.print(f"  Next: cd {project_path} && spec-editor run")
    else:
        console.print(f"\n  Next: cd {project_path} && spec-editor run")


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
