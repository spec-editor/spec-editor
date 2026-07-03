"""File-based (offline) license validation.

Validates a .license file signed with Ed25519 public-key cryptography.
Used for air-gapped enterprise deployments where the GumRoad API is
not reachable.

License file format (JSON):
{
    "license_key": "XXXX-XXXX-XXXX-XXXX",
    "product": "pro",
    "tier": "pro",
    "issued_to": "acme-corp",
    "issued_at": "2026-07-01T00:00:00Z",
    "expires_at": "2027-07-01T00:00:00Z",
    "seat_limit": 10,
    "machine_fingerprint": "optional-host-id",
    "signature": "base64-ed25519-signature"
}

The signature covers all fields except "signature" itself, serialized
as canonical JSON (sorted keys, no whitespace).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path

from src.licensing.models import LicenseStatus, ProductTier

logger = logging.getLogger(__name__)


class FileLicenseProvider:
    """Validates licenses from a signed local .license file."""

    def __init__(
        self,
        license_file: Path,
        public_key: str = "",
        require_fingerprint_match: bool = False,
    ) -> None:
        self._path = license_file
        self._public_key = public_key
        self._require_fingerprint = require_fingerprint_match

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def validate_key(
        self,
        license_key: str,
        product: str = "pro",
    ) -> LicenseStatus:
        """Read and validate the .license file."""
        if not self._path.exists():
            return LicenseStatus(
                valid=False,
                tier=ProductTier.FREE,
                message=f"License file not found: {self._path}",
            )

        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            return LicenseStatus(
                valid=False,
                tier=ProductTier.FREE,
                message=f"Cannot read license file: {exc}",
            )

        # 1. Check product match
        file_product = data.get("product", "")
        if file_product != product:
            return LicenseStatus(
                valid=False,
                tier=ProductTier.FREE,
                message=(
                    f"License is for product '{file_product}', "
                    f"but '{product}' was requested."
                ),
            )

        # 2. Check expiry
        expires_at = data.get("expires_at", "")
        if expires_at:
            try:
                # Handle 'Z' suffix (Python <3.11 doesn't support it)
                normalized = expires_at.replace("Z", "+00:00")
                expiry = datetime.fromisoformat(normalized)
                if datetime.now(timezone.utc) > expiry:
                    return LicenseStatus(
                        valid=False,
                        tier=ProductTier.FREE,
                        message=f"License expired at {expires_at}.",
                    )
            except ValueError:
                pass  # Invalid date format — treat as warning, not failure

        # 3. Check machine fingerprint (optional)
        if self._require_fingerprint:
            expected_fp = data.get("machine_fingerprint", "")
            if expected_fp and expected_fp != self._get_machine_fingerprint():
                return LicenseStatus(
                    valid=False,
                    tier=ProductTier.FREE,
                    message="License is bound to a different machine.",
                )

        # 4. Verify signature (if public key configured)
        if self._public_key:
            signature = data.get("signature", "")
            if not signature:
                return LicenseStatus(
                    valid=False,
                    tier=ProductTier.FREE,
                    message="License file is not signed.",
                )
            if not self._verify_signature(data, signature):
                return LicenseStatus(
                    valid=False,
                    tier=ProductTier.FREE,
                    message="License signature is invalid — file may be tampered.",
                )

        # 5. Build status
        tier_str = data.get("tier", "pro")
        try:
            tier = ProductTier(tier_str)
        except ValueError:
            tier = ProductTier.PRO

        return LicenseStatus(
            valid=True,
            tier=tier,
            product_name=data.get("product", product),
            email=data.get("issued_to", ""),
            purchase_date=data.get("issued_at", ""),
            seat_limit=data.get("seat_limit", 1),
            message="Offline license validated.",
        )

    async def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _verify_signature(self, data: dict, signature_b64: str) -> bool:
        """Verify Ed25519 signature of the license data.

        The signature covers the canonical JSON of all fields except
        'signature' itself.
        """
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except ImportError:
            logger.warning(
                "cryptography package not installed — skipping signature verification"
            )
            return True  # Degrade gracefully if crypto lib not available

        try:
            public_key_bytes = base64.b64decode(self._public_key)
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
            signature_bytes = base64.b64decode(signature_b64)

            # Canonical JSON of all fields except signature
            payload = {k: v for k, v in data.items() if k != "signature"}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))

            public_key.verify(signature_bytes, canonical.encode("utf-8"))
            return True
        except Exception as exc:
            logger.warning("Signature verification failed: %s", exc)
            return False

    @staticmethod
    def _get_machine_fingerprint() -> str:
        """Generate a stable machine fingerprint.

        Uses hostname + machine hardware hash. Not cryptographically
        secure — just enough to prevent casual license sharing.
        """
        components = [
            platform.node() or "unknown-host",
            platform.machine() or "unknown-arch",
            platform.processor() or "unknown-cpu",
        ]
        return hashlib.sha256(":".join(components).encode()).hexdigest()[:16]
