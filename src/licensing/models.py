"""License data models — ProductTier, LicenseStatus, LicenseInfo.

These models represent the licensing domain: product tiers, validation
results, and cloud token balances.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProductTier(str, Enum):
    """Product tier determined by license validation."""

    FREE = "free"          # No license key — core spec-editor features only
    PRO = "pro"            # Valid Pro license — cycle plugin, multi-agent codegen
    CLOUD = "cloud"        # Cloud token key — LLM proxy access


class LicenseStatus(BaseModel):
    """Result of a license key validation."""

    valid: bool = Field(description="Whether the license is currently valid")
    tier: ProductTier = Field(default=ProductTier.FREE)
    product_name: str = Field(default="")
    email: str = Field(default="")
    purchase_date: str = Field(default="")
    refunded: bool = Field(default=False)
    chargebacked: bool = Field(default=False)
    subscription_cancelled_at: Optional[str] = Field(default=None)
    subscription_failed_at: Optional[str] = Field(default=None)
    subscription_cancelled: bool = Field(default=False)
    subscription_failed: bool = Field(default=False)
    seat_limit: int = Field(default=1)
    seats_used: int = Field(default=0)
    message: str = Field(
        default="",
        description="Human-readable status message for the user",
    )


class CloudTokenBalance(BaseModel):
    """Current cloud token balance for a user."""

    license_key: str = Field(description="Associated license key")
    balance: int = Field(default=0, description="Remaining token count")
    total_purchased: int = Field(default=0, description="Lifetime tokens purchased")
    total_used: int = Field(default=0, description="Lifetime tokens consumed")
    last_updated: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last balance update timestamp",
    )
    auto_top_up: bool = Field(
        default=False,
        description="Whether auto top-up is enabled",
    )
    low_balance_threshold: int = Field(
        default=100000,
        description="Threshold for low-balance notifications",
    )


class LicenseInfo(BaseModel):
    """Aggregated license information for display."""

    status: LicenseStatus
    cloud_balance: Optional[CloudTokenBalance] = None
    cache_age_seconds: Optional[float] = Field(
        default=None,
        description="Age of the cached validation result",
    )


class GumRoadWebhookPayload(BaseModel):
    """Parsed GumRoad sale webhook (POST to /webhooks/gumroad)."""

    sale_id: str = Field(default="")
    product_id: str = Field(default="")
    product_name: str = Field(default="")
    seller_id: str = Field(default="")
    price: int = Field(default=0, description="Price in cents")
    email: str = Field(default="")
    license_key: str = Field(default="")
    custom_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Custom fields from GumRoad checkout form",
    )
    refunded: bool = Field(default=False)
    chargebacked: bool = Field(default=False)
    disputed: bool = Field(default=False)
    subscription_cancelled: bool = Field(default=False)
    subscription_failed: bool = Field(default=False)
    timestamp: str = Field(default="")
