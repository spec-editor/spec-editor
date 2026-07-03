"""License Provider — pluggable license key validation.

Mirrors the SecretProvider and AuthProvider patterns. Supports multiple
backends for license validation:

Backends:
    - ``noop`` — always returns valid FREE tier (default, for OSS)
    - ``gumroad`` — validates against GumRoad's /v2/licenses/verify API
    - ``file`` — offline validation via signed .license file

Configured via ``local.yaml`` → ``license:`` section.

Usage::

    from src.licensing import create_license_provider

    provider = create_license_provider(project_path, settings)
    status = await provider.validate_key("XXXX-XXXX-XXXX-XXXX", product="pro")
    if not status.valid:
        raise SystemExit("Pro license required")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.licensing.models import LicenseInfo, LicenseStatus, ProductTier


class LicenseProvider(ABC):
    """Abstract license validation backend.

    Implementations answer: "Is this license key valid, and what tier
    does it grant access to?"
    """

    @abstractmethod
    async def validate_key(
        self,
        license_key: str,
        product: str = "pro",
    ) -> LicenseStatus:
        """Validate a license key for a specific product.

        Args:
            license_key: The license key string (e.g., GumRoad XXXX-XXXX-XXXX-XXXX)
            product: Which product to check (``pro`` or ``cloud``)

        Returns:
            LicenseStatus with validity and tier information.
        """
        ...

    async def get_license_info(self, license_key: str) -> LicenseInfo:
        """Get full license info including cloud token balance.

        Default implementation returns just the status. Override for
        backends that track cloud token balances.
        """
        status = await self.validate_key(license_key)
        return LicenseInfo(status=status)

    async def get_cloud_balance(self, license_key: str) -> int:
        """Get remaining cloud token balance.

        Returns -1 if cloud tokens are not applicable for this backend.
        """
        return -1

    async def close(self) -> None:
        """Clean up resources (connections, caches)."""
        pass


# ── Backend implementations ────────────────────────────────────────


class NoopLicenseProvider(LicenseProvider):
    """Dummy provider — always returns valid FREE tier.

    Used as the default so that the OSS version works without any
    license configuration.
    """

    async def validate_key(
        self,
        license_key: str,
        product: str = "pro",
    ) -> LicenseStatus:
        return LicenseStatus(
            valid=True,
            tier=ProductTier.FREE,
            message="No license required (OSS version). Pro features are not available.",
        )


# ── Factory ─────────────────────────────────────────────────────────


def create_license_provider(
    project_path: str | Path,
    settings: Any | None = None,
) -> LicenseProvider:
    """Create a LicenseProvider from configuration.

    Reads ``local.yaml`` → ``license:`` section. Falls back to noop
    if no license section is configured.

    Args:
        project_path: Path to the project directory (contains local.yaml)
        settings: Optional Settings object (from src.config.settings)
    """
    import yaml

    project_path = Path(project_path)

    # Try to read local.yaml
    local_yaml = project_path / "local.yaml"
    if not local_yaml.exists():
        return NoopLicenseProvider()

    try:
        with open(local_yaml, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return NoopLicenseProvider()

    license_cfg = config.get("license", {}) if config else {}
    backend = license_cfg.get("backend", "noop")

    if backend == "gumroad":
        return _create_gumroad_provider(license_cfg)
    elif backend == "file":
        return _create_file_provider(license_cfg, project_path)
    else:
        return NoopLicenseProvider()


def _create_gumroad_provider(cfg: dict) -> LicenseProvider:
    """Create a GumRoad-backed license provider."""
    from src.licensing.cache import LicenseCache
    from src.licensing.gumroad import GumRoadLicenseProvider

    cache_path = cfg.get("cache_path", "~/.spec-editor/license.cache")
    cache_ttl = int(cfg.get("cache_ttl_days", 7))
    offline_validation = bool(cfg.get("offline_validation", False))

    cache = LicenseCache(cache_path, default_ttl_days=cache_ttl)

    return GumRoadLicenseProvider(
        product_id=cfg.get("product_id", ""),
        cache=cache if not offline_validation else None,
        cache_ttl_days=cache_ttl,
    )


def _create_file_provider(cfg: dict, project_path: Path) -> LicenseProvider:
    """Create a file-based (offline) license provider."""
    from src.licensing.file_backend import FileLicenseProvider

    license_file = cfg.get("file_path", str(project_path / ".license"))
    public_key = cfg.get("public_key", "")

    return FileLicenseProvider(
        license_file=Path(license_file),
        public_key=public_key,
    )
