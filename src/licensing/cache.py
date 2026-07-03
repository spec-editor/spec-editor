"""Local license validation cache.

Avoids hitting the GumRoad API on every CLI invocation by caching
validation results on disk with a configurable TTL.

Cache file format: JSON with per-key entries:
{
    "XXXX-XXXX-XXXX-XXXX": {
        "status": {...LicenseStatus...},
        "cached_at": "2026-07-03T12:00:00Z",
        "ttl_days": 7
    }
}
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.licensing.models import LicenseStatus


class LicenseCache:
    """File-based cache for license validation results.

    Thread-safe via atomic file writes. Suitable for single-user CLI usage.
    Not suitable for multi-process server usage (use Redis for that).
    """

    def __init__(self, cache_path: str | Path, default_ttl_days: int = 7) -> None:
        self._path = Path(cache_path).expanduser().resolve()
        self._default_ttl = default_ttl_days
        self._lockfile = str(self._path) + ".lock"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, license_key: str) -> LicenseStatus | None:
        """Return cached status if valid and not expired, else None."""
        data = self._read()
        entry = data.get(license_key)
        if entry is None:
            return None

        cached_at = datetime.fromisoformat(entry["cached_at"])
        ttl_days = entry.get("ttl_days", self._default_ttl)
        expires_at = cached_at + timedelta(days=ttl_days)

        if datetime.now(timezone.utc) > expires_at:
            # Expired — remove stale entry
            del data[license_key]
            self._write(data)
            return None

        return LicenseStatus(**entry["status"])

    def put(
        self,
        license_key: str,
        status: LicenseStatus,
        ttl_days: int | None = None,
    ) -> None:
        """Cache a validation result."""
        data = self._read()
        data[license_key] = {
            "status": status.model_dump(),
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "ttl_days": ttl_days or self._default_ttl,
        }
        self._write(data)

    def invalidate(self, license_key: str) -> None:
        """Remove a cached entry."""
        data = self._read()
        data.pop(license_key, None)
        self._write(data)

    def get_age_seconds(self, license_key: str) -> float | None:
        """Return age of cached entry in seconds, or None if not cached."""
        data = self._read()
        entry = data.get(license_key)
        if entry is None:
            return None
        cached_at = datetime.fromisoformat(entry["cached_at"])
        return (datetime.now(timezone.utc) - cached_at).total_seconds()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        """Read cache file. Returns empty dict if file missing or corrupt."""
        if not self._path.exists():
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        """Atomically write cache file via temp + rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, self._path)
