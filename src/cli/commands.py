"""CLI commands for spec-editor."""

import shutil
from importlib import resources
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from src.config import get_logger
from src.config._data_path import data_path
from src.config.methodology import load_methodology
from src.config.settings import (
    AgentConfig,
    AgentsConfig,
    create_provider,
)
from src.mcp.metrics import MetricsReport, compute_metrics
from src.mcp.validator import ValidationReport, validate
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements

logger = get_logger(__name__)
console = Console()

_BUILTIN_METHODOLOGIES = data_path("methodologies")

_README_TEMPLATE = """\
# Project Description

Describe the target system for which requirements are being developed.

## Purpose

## Key Features

## Users and Roles

## Constraints
"""

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

## Users and Roles
- **Customer** — browses books, places orders, manages account
- **Admin** — manages inventory, processes orders, creates discount codes
- **Guest** — can browse and add to cart, must register at checkout

## Constraints
- Page load time < 2 seconds under normal load
- Support 1000 concurrent users during peak hours
- PCI-DSS compliance for credit card payments
- GDPR compliance for user data (export, deletion)
- Inventory must not oversell (concurrent checkout safety)
"""


@click.group()
@click.version_option(version="0.1.9", prog_name="spec-editor")
@click.pass_context
@implements("SRC-008")
@implements("MOD-005")
def cli(ctx):
    """Spec Editor — AI agents for requirements development."""
    from pathlib import Path

    from rich.console import Console

    cwd = Path.cwd()
    project = None
    # Auto-detect: check current dir, then parent, then grandparent
    for candidate in [cwd, cwd.parent, cwd.parent.parent]:
        if (candidate / "methodology.yaml").exists() or (
            candidate / "local.yaml"
        ).exists():
            project = candidate
            break

    ctx.ensure_object(dict)
    ctx.obj["project_path"] = str(project) if project else None

    c = Console()
    if project:
        c.print(f"[dim]Project: {project}[/dim]")
    else:
        c.print("[dim]No project detected (cd to project dir or use -p)[/dim]")


# Import submodules to register all @cli.command() decorators
# (core commands only — plugins register via hooks)
from src.cli import (
    commands_core,  # noqa: E402, F401
    commands_coverage,  # noqa: E402, F401
    commands_edit,  # noqa: E402, F401
    commands_export,  # noqa: E402, F401
    commands_export_helpers,  # noqa: E402, F401
    commands_ingest,  # noqa: E402, F401
    commands_init,  # noqa: E402, F401
    commands_license,  # noqa: E402, F401
    commands_shutdown,  # noqa: E402, F401
    commands_view,  # noqa: E402, F401
)

# Register the license command group
from src.cli.commands_license import license_group

cli.add_command(license_group)

# Plugin CLI commands (cycle, agent, etc.) — discovered via hooks
try:
    from src.hooks import get_plugins

    for _plugin in get_plugins():
        try:
            _plugin.register_cli_commands(cli)
        except Exception:
            pass
except ImportError:
    pass
