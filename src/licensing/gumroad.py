"""GumRoad License Verification API client.

Implements the GumRoad v2/licenses/verify endpoint with:
- Online validation against GumRoad API
- Local cache with configurable TTL
- Graceful degradation when GumRoad is unreachable
- Automatic top-up webhook handling (received by cloud-proxy, but
  validation logic is shared)

GumRoad API docs: https://help.gumroad.com/article/76-license-verification

Flow:
    1. Check local cache → return if valid
    2. POST /v2/licenses/verify → GumRoad
    3. Cache result on success, return status
    4. On network error → use stale cache if within grace period
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from src.licensing.cache import LicenseCache
from src.licensing.models import (
    GumRoadWebhookPayload,
    LicenseInfo,
    LicenseStatus,
    ProductTier,
)

logger = logging.getLogger(__name__)

# GumRoad license verification endpoint
_GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"

# GumRoad API timeout (seconds)
_GUMROAD_TIMEOUT = 10.0

# Stale cache grace period: if GumRoad is unreachable, accept cached
# result for this many hours beyond normal TTL
_STALE_GRACE_HOURS = 24

# Known GumRoad product IDs → tier mapping
# These are configured per-product in GumRoad and should be set via
# environment or config, but sensible defaults are provided.
_DEFAULT_PRODUCT_TIER_MAP: dict[str, ProductTier] = {}


class GumRoadLicenseProvider:
    """Validates license keys against the GumRoad API."""

    def __init__(
        self,
        product_id: str = "",
        cache: LicenseCache | None = None,
        cache_ttl_days: int = 7,
        stale_grace_hours: int = _STALE_GRACE_HOURS,
        http_client: httpx.AsyncClient | None = None,
        product_tier_map: dict[str, ProductTier] | None = None,
    ) -> None:
        self._product_id = product_id
        self._cache = cache
        self._cache_ttl = cache_ttl_days
        self._stale_grace = stale_grace_hours
        self._http = http_client
        self._owns_http = http_client is None
        self._tier_map = product_tier_map or _DEFAULT_PRODUCT_TIER_MAP

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def validate_key(
        self,
        license_key: str,
        product_id: str = "",
        increment_uses: bool = True,
    ) -> LicenseStatus:
        """Validate a license key against GumRoad.

        Checks cache first, then hits GumRoad API. Returns a LicenseStatus
        with tier information.

        Args:
            license_key: The GumRoad-issued license key (XXXX-XXXX-XXXX-XXXX)
            product_id: Override the default product ID
            increment_uses: Whether GumRoad should increment usage count
        """
        pid = product_id or self._product_id
        if not pid:
            return LicenseStatus(
                valid=False,
                tier=ProductTier.FREE,
                message="No product ID configured — cannot validate license.",
            )

        # 1. Check cache
        if self._cache is not None:
            cached = self._cache.get(license_key)
            if cached is not None:
                return cached

        # 2. Hit GumRoad API
        try:
            status = await self._verify_online(license_key, pid, increment_uses)
        except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
            logger.warning("GumRoad API unreachable: %s", exc)
            # Try stale cache (beyond normal TTL but within grace period)
            status = self._check_stale_cache(license_key)
            if status is not None:
                return status
            # No stale cache — return a permissive status to avoid
            # locking users out due to transient network issues.
            # Callers should decide policy: fail-open or fail-closed.
            return LicenseStatus(
                valid=False,
                tier=ProductTier.FREE,
                message=(
                    f"Cannot reach license server. "
                    f"Please check your internet connection. "
                    f"({str(exc)[:100]})"
                ),
            )

        # 3. Cache successful result
        if self._cache is not None and status.valid:
            self._cache.put(license_key, status, ttl_days=self._cache_ttl)

        return status

    async def get_license_info(
        self,
        license_key: str,
        product_id: str = "",
    ) -> LicenseInfo:
        """Get full license info including cache metadata."""
        status = await self.validate_key(license_key, product_id)
        cache_age = None
        if self._cache is not None:
            cache_age = self._cache.get_age_seconds(license_key)
        return LicenseInfo(status=status, cache_age_seconds=cache_age)

    def invalidate_cache(self, license_key: str) -> None:
        """Force re-validation on next check by removing cache entry."""
        if self._cache is not None:
            self._cache.invalidate(license_key)

    # ------------------------------------------------------------------
    # Webhook verification (used by cloud-proxy)
    # ------------------------------------------------------------------

    @staticmethod
    def verify_webhook_signature(
        payload: bytes,
        signature: str,
        secret: str,
    ) -> bool:
        """Verify a GumRoad webhook signature (HMAC-SHA256).

        Used by the cloud-proxy's /webhooks/gumroad endpoint to
        authenticate incoming sale notifications.

        Args:
            payload: Raw request body bytes
            signature: Value of X-GumRoad-Signature header
            secret: Your GumRoad webhook secret (set in GumRoad dashboard)
        """
        if not signature or not secret:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def parse_webhook(form_data: dict) -> GumRoadWebhookPayload:
        """Parse a GumRoad webhook form-encoded body into a typed model.

        GumRoad sends `application/x-www-form-urlencoded` POST requests.
        Custom fields are prefixed with a key set in the GumRoad product
        configuration (e.g., `custom_fields[cloud_token_key]`).

        Returns GumRoadWebhookPayload with all recognized fields.
        """
        custom_fields: dict[str, str] = {}
        for key, value in form_data.items():
            if key.startswith("custom_fields[") and key.endswith("]"):
                field_name = key[14:-1]  # strip "custom_fields[" and "]"
                custom_fields[field_name] = str(value)

        return GumRoadWebhookPayload(
            sale_id=str(form_data.get("sale_id", "")),
            product_id=str(form_data.get("product_id", "")),
            product_name=str(form_data.get("product_name", "")),
            seller_id=str(form_data.get("seller_id", "")),
            price=int(form_data.get("price", 0)),
            email=str(form_data.get("email", "")),
            license_key=str(form_data.get("license_key", "")),
            custom_fields=custom_fields,
            refunded=str(form_data.get("refunded", "")).lower() == "true",
            chargebacked=str(form_data.get("chargebacked", "")).lower() == "true",
            disputed=str(form_data.get("disputed", "")).lower() == "true",
            subscription_cancelled=(
                str(form_data.get("subscription_cancelled", "")).lower() == "true"
            ),
            subscription_failed=(
                str(form_data.get("subscription_failed", "")).lower() == "true"
            ),
            timestamp=str(form_data.get("timestamp", "")),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _verify_online(
        self,
        license_key: str,
        product_id: str,
        increment_uses: bool,
    ) -> LicenseStatus:
        """POST to GumRoad's verify endpoint."""
        client = self._http or httpx.AsyncClient()
        try:
            response = await client.post(
                _GUMROAD_VERIFY_URL,
                data={
                    "product_id": product_id,
                    "license_key": license_key,
                    "increment_uses": "true" if increment_uses else "false",
                },
                timeout=_GUMROAD_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        finally:
            if self._owns_http and client is not self._http:
                await client.aclose()

        return self._parse_verify_response(data, license_key)

    def _parse_verify_response(
        self,
        data: dict,
        license_key: str,
    ) -> LicenseStatus:
        """Parse GumRoad's /v2/licenses/verify JSON response."""
        success = data.get("success", False)
        purchase = data.get("purchase", {})
        uses = data.get("uses", 0)
        message = data.get("message", "")

        # Determine if the purchase is void
        refunded = purchase.get("refunded", False)
        chargebacked = purchase.get("chargebacked", False)
        sub_cancelled = purchase.get("subscription_cancelled_at") is not None
        sub_failed = purchase.get("subscription_failed_at") is not None

        # Map product to tier
        product_name = purchase.get("product_name", "")
        tier = self._resolve_tier(product_name)

        is_valid = (
            success
            and not refunded
            and not chargebacked
            and not sub_failed
            and not sub_cancelled
        )

        if not is_valid:
            if not success:
                message = message or "Invalid license key."
            elif refunded:
                message = "Purchase was refunded."
            elif chargebacked:
                message = "Purchase was chargebacked."
            elif sub_cancelled:
                message = "Subscription was cancelled."
            elif sub_failed:
                message = "Subscription payment failed."

        return LicenseStatus(
            valid=is_valid,
            tier=tier if is_valid else ProductTier.FREE,
            product_name=product_name,
            email=purchase.get("email", ""),
            purchase_date=purchase.get("created_at", ""),
            refunded=refunded,
            chargebacked=chargebacked,
            subscription_cancelled=sub_cancelled,
            subscription_failed=sub_failed,
            seat_limit=purchase.get("quantity", 1),
            seats_used=uses,
            message=message,
        )

    def _resolve_tier(self, product_name: str) -> ProductTier:
        """Map a GumRoad product name to a ProductTier.

        Uses the configured tier map, falling back to heuristics.
        """
        # Check explicit mapping
        for pid, tier in self._tier_map.items():
            if pid.lower() in product_name.lower():
                return tier

        # Heuristic: product name contains "pro" → PRO, "cloud" → CLOUD
        name_lower = product_name.lower()
        if "cloud" in name_lower or "token" in name_lower:
            return ProductTier.CLOUD
        if "pro" in name_lower:
            return ProductTier.PRO

        # Default: valid license → PRO
        return ProductTier.PRO

    def _check_stale_cache(self, license_key: str) -> LicenseStatus | None:
        """Check cache beyond normal TTL but within grace period.

        Returns cached status if within grace, None otherwise.
        """
        if self._cache is None:
            return None
        cached = self._cache.get(license_key)
        if cached is not None:
            return cached  # within normal TTL (already handled above)

        # Read raw cache data to check stale entries
        import json
        from datetime import datetime, timedelta, timezone

        try:
            with open(self._cache._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        entry = data.get(license_key)
        if entry is None:
            return None

        cached_at = datetime.fromisoformat(entry["cached_at"])
        grace_expiry = cached_at + timedelta(hours=self._stale_grace)
        if datetime.now(timezone.utc) <= grace_expiry:
            logger.info("Using stale cache for %s (within grace period)", license_key)
            return LicenseStatus(**entry["status"])

        return None

    async def close(self) -> None:
        """Clean up HTTP client if we own it."""
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
