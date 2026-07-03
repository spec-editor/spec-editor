"""Tests for the licensing module.

Covers:
- NoopLicenseProvider (default, always returns FREE)
- LicenseCache (file-based cache with TTL)
- LicenseStatus and ProductTier models
- License key masking
- GumRoad license provider (mocked HTTP)
- File license provider (offline validation)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.licensing import (
    LicenseProvider,
    NoopLicenseProvider,
    create_license_provider,
)
from src.licensing.cache import LicenseCache
from src.licensing.models import (
    LicenseInfo,
    LicenseStatus,
    ProductTier,
)


# ------------------------------------------------------------------
# NoopLicenseProvider
# ------------------------------------------------------------------


class TestNoopLicenseProvider:
    """Default provider — always returns valid FREE tier."""

    @pytest.mark.asyncio
    async def test_validate_always_valid(self):
        provider = NoopLicenseProvider()
        status = await provider.validate_key("any-key")
        assert status.valid is True
        assert status.tier == ProductTier.FREE

    @pytest.mark.asyncio
    async def test_validate_any_product(self):
        provider = NoopLicenseProvider()
        status = await provider.validate_key("key", product="pro")
        assert status.valid is True
        assert status.tier == ProductTier.FREE

    @pytest.mark.asyncio
    async def test_get_license_info(self):
        provider = NoopLicenseProvider()
        info = await provider.get_license_info("key")
        assert info.status.valid is True
        assert info.status.tier == ProductTier.FREE
        assert info.cloud_balance is None


# ------------------------------------------------------------------
# LicenseCache
# ------------------------------------------------------------------


class TestLicenseCache:
    """File-based cache for license validation results."""

    def test_put_and_get(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cache_path = f.name

        try:
            cache = LicenseCache(cache_path, default_ttl_days=7)
            key = "TEST-KEY-1234-5678-ABCD"
            status = LicenseStatus(
                valid=True,
                tier=ProductTier.PRO,
                email="test@example.com",
                product_name="Spec Editor Pro",
            )

            # Initially no cache
            assert cache.get(key) is None

            # Put and retrieve
            cache.put(key, status)
            cached = cache.get(key)
            assert cached is not None
            assert cached.valid is True
            assert cached.tier == ProductTier.PRO
            assert cached.email == "test@example.com"

        finally:
            Path(cache_path).unlink(missing_ok=True)

    def test_expired_cache(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cache_path = f.name

        try:
            # Use TTL of 0 days — immediate expiry
            cache = LicenseCache(cache_path, default_ttl_days=0)
            key = "EXPIRED-KEY"
            status = LicenseStatus(valid=True, tier=ProductTier.PRO)

            cache.put(key, status, ttl_days=0)
            # Should be expired immediately
            cached = cache.get(key)
            assert cached is None

        finally:
            Path(cache_path).unlink(missing_ok=True)

    def test_invalidate(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cache_path = f.name

        try:
            cache = LicenseCache(cache_path, default_ttl_days=30)
            key = "TO-REMOVE"
            status = LicenseStatus(valid=True, tier=ProductTier.PRO)

            cache.put(key, status)
            assert cache.get(key) is not None

            cache.invalidate(key)
            assert cache.get(key) is None

        finally:
            Path(cache_path).unlink(missing_ok=True)

    def test_age_seconds(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cache_path = f.name

        try:
            cache = LicenseCache(cache_path)
            key = "AGE-TEST"
            status = LicenseStatus(valid=True, tier=ProductTier.PRO)

            assert cache.get_age_seconds(key) is None
            cache.put(key, status)
            age = cache.get_age_seconds(key)
            assert age is not None
            assert age >= 0
            assert age < 5  # Should be very recent

        finally:
            Path(cache_path).unlink(missing_ok=True)

    def test_corrupt_cache_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"not valid json {{{")
            cache_path = f.name

        try:
            cache = LicenseCache(cache_path)
            # Should not crash on corrupt file
            assert cache.get("any-key") is None
            # Should be able to write to it
            cache.put("new-key", LicenseStatus(valid=True, tier=ProductTier.PRO))

        finally:
            Path(cache_path).unlink(missing_ok=True)


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


class TestLicenseModels:
    """Pydantic model validation for licensing types."""

    def test_license_status_defaults(self):
        status = LicenseStatus(valid=True)
        assert status.tier == ProductTier.FREE
        assert status.refunded is False
        assert status.chargebacked is False
        assert status.seat_limit == 1

    def test_product_tier_values(self):
        assert ProductTier.FREE.value == "free"
        assert ProductTier.PRO.value == "pro"
        assert ProductTier.CLOUD.value == "cloud"

    def test_license_info_aggregation(self):
        status = LicenseStatus(valid=True, tier=ProductTier.PRO)
        info = LicenseInfo(status=status, cache_age_seconds=42.5)
        assert info.status == status
        assert info.cache_age_seconds == 42.5
        assert info.cloud_balance is None


# ------------------------------------------------------------------
# GumRoad License Provider (mocked)
# ------------------------------------------------------------------


class TestGumRoadProvider:
    """GumRoad API integration with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_validate_valid_key(self):
        from src.licensing.gumroad import GumRoadLicenseProvider

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "uses": 1,
            "purchase": {
                "product_name": "Spec Editor Pro",
                "email": "buyer@example.com",
                "created_at": "2026-07-01T00:00:00Z",
                "refunded": False,
                "chargebacked": False,
                "subscription_cancelled_at": None,
                "subscription_failed_at": None,
                "quantity": 1,
            },
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        provider = GumRoadLicenseProvider(
            product_id="test-product-id",
            http_client=mock_client,
        )

        status = await provider.validate_key("VALID-KEY-1234-5678-ABCD")
        assert status.valid is True
        assert status.tier == ProductTier.PRO
        assert status.email == "buyer@example.com"
        assert status.product_name == "Spec Editor Pro"

        await provider.close()

    @pytest.mark.asyncio
    async def test_validate_invalid_key(self):
        from src.licensing.gumroad import GumRoadLicenseProvider

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": False,
            "message": "License key not found.",
            "purchase": {},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        provider = GumRoadLicenseProvider(
            product_id="test-product-id",
            http_client=mock_client,
        )

        status = await provider.validate_key("INVALID-KEY")
        assert status.valid is False
        assert status.tier == ProductTier.FREE

        await provider.close()

    @pytest.mark.asyncio
    async def test_validate_refunded_purchase(self):
        from src.licensing.gumroad import GumRoadLicenseProvider

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,  # GumRoad returns success=true even if refunded
            "uses": 0,
            "purchase": {
                "product_name": "Spec Editor Pro",
                "email": "refunded@example.com",
                "refunded": True,
                "chargebacked": False,
                "subscription_cancelled_at": None,
                "subscription_failed_at": None,
                "quantity": 1,
            },
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        provider = GumRoadLicenseProvider(
            product_id="test-product-id",
            http_client=mock_client,
        )

        status = await provider.validate_key("REFUNDED-KEY")
        assert status.valid is False
        assert "refunded" in status.message.lower()

        await provider.close()

    @pytest.mark.asyncio
    async def test_webhook_signature_verification(self):
        from src.licensing.gumroad import GumRoadLicenseProvider

        secret = "test-webhook-secret"
        payload = b"sale_id=123&product_id=abc&email=test@example.com"

        # Compute valid signature
        import hashlib
        import hmac

        valid_sig = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()

        assert GumRoadLicenseProvider.verify_webhook_signature(
            payload, valid_sig, secret
        ) is True

        assert GumRoadLicenseProvider.verify_webhook_signature(
            payload, "wrong-signature", secret
        ) is False

        assert GumRoadLicenseProvider.verify_webhook_signature(
            payload, "", secret
        ) is False

    def test_webhook_parse(self):
        from src.licensing.gumroad import GumRoadLicenseProvider

        form_data = {
            "sale_id": "SALE-12345",
            "product_id": "cloud-tokens-10m",
            "product_name": "Cloud Tokens 10M",
            "email": "buyer@example.com",
            "price": "4999",
            "license_key": "CLOUD-KEY-ABCD",
            "custom_fields[cloud_token_key]": "USER-LICENSE-KEY",
            "refunded": "false",
            "chargebacked": "false",
            "disputed": "false",
            "subscription_cancelled": "false",
            "subscription_failed": "false",
        }

        payload = GumRoadLicenseProvider.parse_webhook(form_data)

        assert payload.sale_id == "SALE-12345"
        assert payload.product_id == "cloud-tokens-10m"
        assert payload.price == 4999
        assert payload.email == "buyer@example.com"
        assert payload.license_key == "CLOUD-KEY-ABCD"
        assert payload.custom_fields == {"cloud_token_key": "USER-LICENSE-KEY"}
        assert payload.refunded is False

    @pytest.mark.asyncio
    async def test_cloud_product_tier_detection(self):
        from src.licensing.gumroad import GumRoadLicenseProvider

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "uses": 1,
            "purchase": {
                "product_name": "Cloud Tokens 10M Pack",
                "email": "cloud@example.com",
                "refunded": False,
                "chargebacked": False,
                "subscription_cancelled_at": None,
                "subscription_failed_at": None,
                "quantity": 1,
            },
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        provider = GumRoadLicenseProvider(
            product_id="test-product-id",
            http_client=mock_client,
        )

        status = await provider.validate_key("CLOUD-KEY")
        assert status.valid is True
        assert status.tier == ProductTier.CLOUD  # Detected from product name

        await provider.close()


# ------------------------------------------------------------------
# File License Provider
# ------------------------------------------------------------------


class TestFileLicenseProvider:
    """Offline file-based license validation."""

    def test_valid_license_file(self):
        from src.licensing.file_backend import FileLicenseProvider

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".license", delete=False
        ) as f:
            json.dump(
                {
                    "license_key": "FILE-KEY-1234",
                    "product": "pro",
                    "tier": "pro",
                    "issued_to": "acme-corp",
                    "issued_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2027-01-01T00:00:00Z",
                    "seat_limit": 10,
                },
                f,
            )
            license_path = f.name

        try:
            provider = FileLicenseProvider(Path(license_path))
            status = asyncio.run(provider.validate_key("FILE-KEY-1234", "pro"))

            assert status.valid is True
            assert status.tier == ProductTier.PRO
            assert status.email == "acme-corp"
            assert status.seat_limit == 10

        finally:
            Path(license_path).unlink(missing_ok=True)

    def test_expired_license_file(self):
        from src.licensing.file_backend import FileLicenseProvider

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".license", delete=False
        ) as f:
            json.dump(
                {
                    "license_key": "EXPIRED-KEY",
                    "product": "pro",
                    "tier": "pro",
                    "issued_to": "old-corp",
                    "expires_at": "2020-01-01T00:00:00Z",
                },
                f,
            )
            license_path = f.name

        try:
            provider = FileLicenseProvider(Path(license_path))
            status = asyncio.run(provider.validate_key("EXPIRED-KEY", "pro"))

            assert status.valid is False
            assert "expired" in status.message.lower()

        finally:
            Path(license_path).unlink(missing_ok=True)

    def test_missing_file(self):
        from src.licensing.file_backend import FileLicenseProvider

        provider = FileLicenseProvider(Path("/nonexistent/license.file"))
        status = asyncio.run(provider.validate_key("any-key", "pro"))

        assert status.valid is False
        assert "not found" in status.message.lower()

    def test_wrong_product(self):
        from src.licensing.file_backend import FileLicenseProvider

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".license", delete=False
        ) as f:
            json.dump(
                {
                    "license_key": "PRO-KEY",
                    "product": "cloud",  # Cloud license, but checking for "pro"
                    "tier": "cloud",
                },
                f,
            )
            license_path = f.name

        try:
            provider = FileLicenseProvider(Path(license_path))
            status = asyncio.run(provider.validate_key("PRO-KEY", "pro"))

            assert status.valid is False
            assert "cloud" in status.message.lower()

        finally:
            Path(license_path).unlink(missing_ok=True)


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


class TestCreateLicenseProvider:
    """Factory function tests."""

    def test_default_noop_when_no_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Directory without local.yaml
            provider = create_license_provider(tmpdir)
            assert isinstance(provider, NoopLicenseProvider)

    def test_noop_from_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_yaml = Path(tmpdir) / "local.yaml"
            local_yaml.write_text(
                json.dumps({"license": {"backend": "noop"}})
            )

            provider = create_license_provider(tmpdir)
            assert isinstance(provider, NoopLicenseProvider)

    def test_gumroad_from_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_yaml = Path(tmpdir) / "local.yaml"
            local_yaml.write_text(
                json.dumps(
                    {
                        "license": {
                            "backend": "gumroad",
                            "key": "TEST-KEY",
                            "product_id": "test-pid",
                        }
                    }
                )
            )

            provider = create_license_provider(tmpdir)
            from src.licensing.gumroad import GumRoadLicenseProvider
            assert isinstance(provider, GumRoadLicenseProvider)

    def test_file_from_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_yaml = Path(tmpdir) / "local.yaml"
            local_yaml.write_text(
                json.dumps(
                    {
                        "license": {
                            "backend": "file",
                            "file_path": str(Path(tmpdir) / ".license"),
                        }
                    }
                )
            )

            provider = create_license_provider(tmpdir)
            from src.licensing.file_backend import FileLicenseProvider
            assert isinstance(provider, FileLicenseProvider)


# ------------------------------------------------------------------
# Key masking helper
# ------------------------------------------------------------------


class TestKeyMasking:
    """License key display masking."""

    def test_mask_key_standard(self):
        from src.cli.commands_license import _mask_key

        key = "ABCD-EFGH-IJKL-MNOP"
        masked = _mask_key(key)
        assert masked == "ABCD-EFGH-****-****"
        assert "MNOP" not in masked

    def test_mask_key_short(self):
        from src.cli.commands_license import _mask_key

        key = "short"
        masked = _mask_key(key)
        assert masked == "*****"

    def test_mask_key_empty(self):
        from src.cli.commands_license import _mask_key

        assert _mask_key("") == ""
