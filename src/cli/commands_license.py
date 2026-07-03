"""CLI commands for license management — spec-editor license.

Commands:
    spec-editor license status          — Show current license status
    spec-editor license activate <key>  — Activate a license key
    spec-editor license deactivate      — Deactivate license on this machine
    spec-editor license cloud-balance   — Show cloud token balance
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.licensing.models import ProductTier

console = Console()


@click.group(name="license")
def license_group():
    """Manage Spec Editor license (Pro, Cloud tokens)."""
    pass


@license_group.command(name="status")
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
def license_status(path: str):
    """Show current license status, tier, and expiry info."""
    project_path = Path(path).resolve()

    # Load settings to get license config
    import yaml

    local_yaml = project_path / "local.yaml"
    if not local_yaml.exists():
        console.print("[dim]No local.yaml found — running in Free tier.[/dim]")
        _print_free_status()
        return

    with open(local_yaml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    license_cfg = config.get("license", {})
    backend = license_cfg.get("backend", "noop")
    license_key = license_cfg.get("key", "")

    table = Table(title="License Status")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Backend", backend)
    table.add_row("License Key", _mask_key(license_key) if license_key else "(not set)")
    table.add_row("Product ID", license_cfg.get("product_id", "(not set)"))

    if license_cfg.get("cloud_proxy_url"):
        table.add_row("Cloud Proxy", license_cfg["cloud_proxy_url"])
    if license_cfg.get("cloud_token_key"):
        table.add_row("Cloud Token", _mask_key(license_cfg["cloud_token_key"]))

    console.print(table)

    # If GumRoad backend, do a live validation
    if backend == "gumroad" and license_key:
        console.print()
        console.print("[bold]Validating license...[/bold]")
        try:
            from src.licensing.gumroad import GumRoadLicenseProvider
        except ImportError:
            console.print(
                "[yellow]GumRoad validation requires spec-editor-pro.[/yellow]\n"
                "  Install: [cyan]pip install spec-editor-pro[/cyan]"
            )
            return

        try:
            provider = GumRoadLicenseProvider(
                product_id=license_cfg.get("product_id", ""),
            )
            status = asyncio.run(
                provider.validate_key(license_key, increment_uses=False)
            )

            result_table = Table(title="Validation Result")
            result_table.add_column("Field", style="cyan")
            result_table.add_column("Value", style="white")

            color = "green" if status.valid else "red"
            result_table.add_row("Status", f"[{color}]{'Valid' if status.valid else 'Invalid'}[/{color}]")
            result_table.add_row("Tier", status.tier.value)
            result_table.add_row("Product", status.product_name or "-")
            result_table.add_row("Email", status.email or "-")
            result_table.add_row("Purchased", status.purchase_date or "-")
            result_table.add_row("Refunded", str(status.refunded))
            result_table.add_row("Chargebacked", str(status.chargebacked))
            if status.message:
                result_table.add_row("Message", status.message)

            console.print(result_table)

            asyncio.run(provider.close())

        except Exception as exc:
            console.print(f"[red]Validation failed:[/red] {exc}")

    elif backend == "noop" or not license_key:
        _print_free_status()


@license_group.command(name="activate")
@click.argument("license_key", required=True)
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
@click.option(
    "--product",
    default="pro",
    type=click.Choice(["pro", "cloud"]),
    help="Product to activate",
)
def license_activate(license_key: str, path: str, product: str):
    """Activate a license key.

    LICENSE_KEY: Your GumRoad license key (format: XXXX-XXXX-XXXX-XXXX)

    Validates the key against GumRoad, then saves it to local.yaml.
    For cloud tokens, use --product cloud.
    """
    project_path = Path(path).resolve()
    local_yaml = project_path / "local.yaml"

    if not local_yaml.exists():
        console.print(
            "[red]Error:[/red] local.yaml not found. Run 'spec-editor init' first."
        )
        raise SystemExit(1)

    # Basic format validation
    key = license_key.strip()
    if not key or len(key) < 10:
        console.print("[red]Error:[/red] Invalid license key format.")
        raise SystemExit(1)

    console.print(f"[bold]Activating {product} license...[/bold]")

    # Validate against GumRoad
    import yaml

    with open(local_yaml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    license_cfg = config.get("license", {})
    product_id = license_cfg.get("product_id", "")

    try:
        from src.licensing.gumroad import GumRoadLicenseProvider
    except ImportError:
        console.print(
            "[red]GumRoad validation requires spec-editor-pro.[/red]\n"
            "  Install: [cyan]pip install spec-editor-pro[/cyan]"
        )
        raise SystemExit(1)

    try:
        provider = GumRoadLicenseProvider(product_id=product_id)
        status = asyncio.run(
            provider.validate_key(key, product=product, increment_uses=True)
        )

        if not status.valid:
            console.print(f"[red]✗ License invalid:[/red] {status.message}")
            console.print(
                "[dim]Get a valid license at: "
                "https://gumroad.com/l/spec-editor-pro[/dim]"
            )
            raise SystemExit(1)

        console.print(
            f"[green]✓ License valid![/green] "
            f"Tier: {status.tier.value}, "
            f"Product: {status.product_name}"
        )

        # Save to local.yaml
        config.setdefault("license", {})
        if product == "cloud":
            config["license"]["cloud_token_key"] = key
        else:
            config["license"]["key"] = key
            config["license"]["backend"] = "gumroad"

        with open(local_yaml, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        console.print(f"[green]✓[/green] License key saved to local.yaml")

        # Invalidate any stale cache
        from src.licensing.cache import LicenseCache

        cache_path = license_cfg.get("cache_path", "~/.spec-editor/license.cache")
        cache = LicenseCache(cache_path)
        cache.invalidate(key)

        asyncio.run(provider.close())

    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]Activation failed:[/red] {exc}")
        console.print(
            "[dim]Tip: Check your internet connection and license key.[/dim]"
        )
        raise SystemExit(1)


@license_group.command(name="deactivate")
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
def license_deactivate(path: str):
    """Deactivate and remove the license key from this project."""
    project_path = Path(path).resolve()
    local_yaml = project_path / "local.yaml"

    if not local_yaml.exists():
        console.print("[dim]No local.yaml found — nothing to deactivate.[/dim]")
        return

    import yaml

    with open(local_yaml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    license_cfg = config.get("license", {})
    old_key = license_cfg.get("key", "")

    if not old_key:
        console.print("[dim]No license key configured.[/dim]")
        return

    # Clear key but keep backend config
    config.setdefault("license", {})
    config["license"]["key"] = ""

    with open(local_yaml, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    # Invalidate cache
    from src.licensing.cache import LicenseCache

    cache_path = license_cfg.get("cache_path", "~/.spec-editor/license.cache")
    cache = LicenseCache(cache_path)
    cache.invalidate(old_key)

    console.print(
        f"[green]✓[/green] License deactivated. Key {_mask_key(old_key)} removed."
    )


@license_group.command(name="cloud-balance")
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
def license_cloud_balance(path: str):
    """Show cloud token balance from the proxy."""
    project_path = Path(path).resolve()

    import yaml

    local_yaml = project_path / "local.yaml"
    if not local_yaml.exists():
        console.print("[red]Error:[/red] local.yaml not found.")
        raise SystemExit(1)

    with open(local_yaml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    license_cfg = config.get("license", {})
    cloud_token = license_cfg.get("cloud_token_key", "")
    proxy_url = license_cfg.get("cloud_proxy_url", "")

    if not cloud_token:
        console.print(
            "[yellow]No cloud token configured.[/yellow]\n"
            "  Activate with: [cyan]spec-editor license activate <key> --product cloud[/cyan]"
        )
        return

    if not proxy_url:
        console.print(
            "[yellow]No cloud proxy URL configured.[/yellow]\n"
            "  Add 'cloud_proxy_url' to local.yaml → license: section."
        )
        return

    # Query the proxy
    import httpx

    try:
        response = httpx.get(
            f"{proxy_url.rstrip('/')}/v1/balance",
            headers={"X-Cloud-Token": cloud_token},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        table = Table(title="Cloud Token Balance")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("License Key", _mask_key(data.get("license_key", cloud_token)))
        table.add_row("Balance", f"{data.get('balance', 0):,} tokens")
        table.add_row("Total Purchased", f"{data.get('total_purchased', 0):,} tokens")
        table.add_row("Total Used", f"{data.get('total_used', 0):,} tokens")
        table.add_row("Last Top-Up", data.get("last_updated", "-"))

        console.print(table)

    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to query cloud proxy:[/red] {exc}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")


# ── Helpers ─────────────────────────────────────────────────────────


def _print_free_status() -> None:
    """Print information about the Free tier."""
    console.print()
    console.print("[bold]Spec Editor — Free Tier[/bold]")
    console.print()
    console.print("  [green]✓[/green] Requirements engineering (spec mode)")
    console.print("  [green]✓[/green] Methodology-driven development")
    console.print("  [green]✓[/green] MCP server for external editors")
    console.print("  [green]✓[/green] Ingestion from source documents")
    console.print()
    console.print("  [dim]Upgrade to Pro:[/dim]")
    console.print("  [cyan]https://gumroad.com/l/spec-editor-pro[/cyan]")
    console.print()
    console.print("  [dim]Pro features:[/dim]")
    console.print("  • Multi-agent code generation (cycle mode)")
    console.print("  • Automated bug detection from production logs")
    console.print("  • Persistent agent workers (coding, testing, devops)")
    console.print("  • Architecture pattern enforcement")
    console.print("  • Cloud token proxy for managed LLM access")
    console.print()
    console.print("  [dim]Cloud Tokens:[/dim]")
    console.print("  [cyan]https://gumroad.com/l/spec-editor-cloud[/cyan]")


def _mask_key(key: str) -> str:
    """Mask a license key for display: XXXX-XXXX-...-XXXX."""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "-" + key[5:9] + "-****-****"


# Register the license group on the main CLI
# This is imported in commands.py
