"""CLI commands for spec-editor."""

import shutil
from importlib import resources
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from src.config import get_logger
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

_BUILTIN_METHODOLOGIES = resources.files("data") / "methodologies"

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
@implements("SRC-008")
@implements("MOD-005")
def cli() -> None:
    """Spec Editor — AI agents for requirements development."""
    pass


# Import submodules to register all @cli.command() decorators
from src.cli import (
    commands_core,  # noqa: E402, F401
    commands_edit,  # noqa: E402, F401
    commands_export,  # noqa: E402, F401
    commands_export_helpers,  # noqa: E402, F401
    commands_ingest,  # noqa: E402, F401
    commands_init,  # noqa: E402, F401
    commands_view,  # noqa: E402, F401
)
