"""Cloud Token Proxy — FastAPI service for LLM API proxying with metering.

This service sits between spec-editor clients and LLM providers,
metering token usage against per-user cloud token balances.

Endpoints:
    POST /v1/chat/completions    — OpenAI-compatible LLM proxy (metered)
    GET  /v1/balance             — Get current cloud token balance
    POST /webhooks/gumroad       — GumRoad sale webhook (auto top-up)
    GET  /health                 — Health check
    GET  /                       — Service info

Architecture:
    - Hot path (LLM proxy):   Redis for atomic balance operations
    - Cold path (ledger):     SQLite for durable audit trail
    - Webhooks:               GumRoad → auto top-up balance

Concurrency:
    - FastAPI async handlers → non-blocking during LLM calls
    - Redis Lua scripts       → atomic check-and-deduct, no race conditions
    - Uvicorn multi-worker    → horizontal scaling per CPU core
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# Configuration (environment variables)
# ------------------------------------------------------------------

REDIS_URL = os.environ.get("CLOUD_PROXY_REDIS_URL", "redis://localhost:6379")
LLM_PROVIDER_URL = os.environ.get(
    "CLOUD_PROXY_LLM_URL",
    "https://api.deepseek.com/v1/chat/completions",
)
LLM_API_KEY = os.environ.get("CLOUD_PROXY_LLM_API_KEY", "")
LLM_TIMEOUT = int(os.environ.get("CLOUD_PROXY_LLM_TIMEOUT", "90"))
GUMROAD_WEBHOOK_SECRET = os.environ.get("CLOUD_PROXY_GUMROAD_SECRET", "")
LEDGER_DB_PATH = os.environ.get(
    "CLOUD_PROXY_LEDGER_DB",
    "/data/ledger.db",
)
# Token packs: product_id → token amount
# These must match GumRoad product configurations.
# Format: "gumroad_product_id:token_amount" (comma-separated)
_TOKEN_PACKS_RAW = os.environ.get(
    "CLOUD_PROXY_TOKEN_PACKS",
    "cloud-tokens-1m:1000000,cloud-tokens-10m:10000000,cloud-tokens-50m:50000000",
)
# Default estimated tokens for pre-deduction (if max_tokens not in request)
DEFAULT_ESTIMATE_TOKENS = int(os.environ.get("CLOUD_PROXY_DEFAULT_ESTIMATE", "4096"))
# Safety multiplier for pre-deduction (over-reserve, refund difference)
SAFETY_MULTIPLIER = float(os.environ.get("CLOUD_PROXY_SAFETY_MULTIPLIER", "1.2"))
# Low balance threshold for warnings
LOW_BALANCE_THRESHOLD = int(os.environ.get("CLOUD_PROXY_LOW_BALANCE", "100000"))
# Pending request TTL (seconds) — for orphan cleanup
PENDING_TTL = int(os.environ.get("CLOUD_PROXY_PENDING_TTL", "300"))

# ------------------------------------------------------------------
# Logger
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cloud-proxy")

# ------------------------------------------------------------------
# Redis Lua Scripts
# ------------------------------------------------------------------

# Atomic pre-deduct: check balance >= amount, then deduct
# Returns: [success (0|1), new_balance]
PRE_DEDUCT_SCRIPT = """
local balance_key = KEYS[1]
local current = tonumber(redis.call('GET', balance_key) or '0')
local amount = tonumber(ARGV[1])
local request_id = ARGV[2]
local pending_key = 'pending:' .. request_id

if current >= amount then
    redis.call('DECRBY', balance_key, amount)
    redis.call('HSET', pending_key,
        'amount', amount,
        'ts', ARGV[3],
        'user', ARGV[4]
    )
    redis.call('EXPIRE', pending_key, ARGV[5])
    return {1, current - amount}
else
    return {0, current}
end
"""

# Atomic reconcile: refund over-reserve, record in ledger
# Returns: final_balance
RECONCILE_SCRIPT = """
local balance_key = KEYS[1]
local actual = tonumber(ARGV[1])
local estimated = tonumber(ARGV[2])
local request_id = ARGV[3]
local pending_key = 'pending:' .. request_id

local refund = estimated - actual
if refund > 0 then
    redis.call('INCRBY', balance_key, refund)
end
redis.call('DEL', pending_key)

-- Append to ledger sorted set (scored by timestamp)
redis.call('ZADD', 'ledger:' .. balance_key, ARGV[4], request_id .. ':' .. ARGV[1])

return tonumber(redis.call('GET', balance_key) or '0')
end
"""

# Atomic refund (on LLM error): full refund of pre-deducted amount
REFUND_SCRIPT = """
local balance_key = KEYS[1]
local amount = tonumber(ARGV[1])
local request_id = ARGV[2]
local pending_key = 'pending:' .. request_id

redis.call('INCRBY', balance_key, amount)
redis.call('DEL', pending_key)
return tonumber(redis.call('GET', balance_key) or '0')
end
"""

# Credit tokens (top-up from GumRoad webhook)
CREDIT_SCRIPT = """
local balance_key = KEYS[1]
local amount = tonumber(ARGV[1])
local total_purchased_key = KEYS[2]

redis.call('INCRBY', balance_key, amount)
redis.call('INCRBY', total_purchased_key, amount)
redis.call('HSET', balance_key .. ':meta', 'last_topup_ts', ARGV[2])
return tonumber(redis.call('GET', balance_key) or '0')
end
"""


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str
    name: str | None = None


class ChatRequest(BaseModel):
    model: str = "deepseek/deepseek-reasoner"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: list[dict[str, Any]] | None = None
    # Cloud proxy header (stripped before forwarding to LLM)
    cloud_token: str = Field(default="", alias="X-Cloud-Token")


class BalanceResponse(BaseModel):
    license_key: str
    balance: int
    total_purchased: int
    total_used: int
    last_updated: str


class TopUpRequest(BaseModel):
    """Manual top-up via webhook data (for testing/reconciliation)."""
    license_key: str
    amount: int
    product_id: str = ""
    sale_id: str = ""


# ------------------------------------------------------------------
# App lifecycle
# ------------------------------------------------------------------

_redis: redis.Redis | None = None
_http_client: httpx.AsyncClient | None = None
_ledger_queue: asyncio.Queue | None = None

# Parse token packs
_token_packs: dict[str, int] = {}
for _item in _TOKEN_PACKS_RAW.split(","):
    _item = _item.strip()
    if ":" in _item:
        _pid, _amt = _item.split(":", 1)
        _token_packs[_pid.strip()] = int(_amt.strip())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: Redis connection pool, HTTP client, ledger worker."""
    global _redis, _http_client, _ledger_queue

    # Startup
    _redis = redis.from_url(REDIS_URL, decode_responses=True)
    await _redis.ping()
    logger.info("Connected to Redis at %s", REDIS_URL)

    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(LLM_TIMEOUT),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
    )

    _ledger_queue = asyncio.Queue(maxsize=10000)
    asyncio.create_task(_ledger_worker())

    logger.info(
        "Cloud Proxy started. LLM backend: %s, token packs: %s",
        LLM_PROVIDER_URL,
        list(_token_packs.keys()),
    )

    yield

    # Shutdown
    if _http_client:
        await _http_client.aclose()
    if _redis:
        await _redis.aclose()
    logger.info("Cloud Proxy shut down.")


app = FastAPI(
    title="Spec Editor Cloud Proxy",
    version="0.1.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------
# Health / Info
# ------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "service": "spec-editor-cloud-proxy",
        "version": "0.1.0",
        "status": "ok",
    }


@app.get("/health")
async def health():
    """Health check: Redis connectivity."""
    if _redis is None:
        raise HTTPException(503, "Not ready")
    try:
        await _redis.ping()
        return {"status": "healthy", "redis": "connected"}
    except Exception as exc:
        raise HTTPException(503, f"Redis disconnected: {exc}")


# ------------------------------------------------------------------
# Balance endpoint
# ------------------------------------------------------------------

@app.get("/v1/balance")
async def get_balance(x_cloud_token: str = ""):
    """Get cloud token balance for a license key.

    Header: X-Cloud-Token: <license_key>
    """
    license_key = x_cloud_token
    if not license_key:
        raise HTTPException(400, "Missing X-Cloud-Token header")

    balance_key = _balance_key(license_key)
    if _redis is None:
        raise HTTPException(503, "Not ready")

    balance = int(await _redis.get(balance_key) or 0)
    total_purchased = int(await _redis.get(f"{balance_key}:purchased") or 0)
    total_used = int(await _redis.get(f"{balance_key}:used") or 0)
    last_updated = await _redis.hget(f"{balance_key}:meta", "last_topup_ts") or ""

    return BalanceResponse(
        license_key=license_key,
        balance=balance,
        total_purchased=total_purchased,
        total_used=total_used,
        last_updated=last_updated,
    )


# ------------------------------------------------------------------
# LLM Proxy endpoint (the hot path)
# ------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest, raw_request: Request):
    """OpenAI-compatible chat completions with token metering.

    Flow:
        1. Extract cloud token from header
        2. Pre-deduct estimated tokens (atomic Lua script)
        3. Forward request to LLM provider (async, non-blocking)
        4. Reconcile actual tokens (refund over-reserve)
        5. On error → full refund
    """
    if _redis is None or _http_client is None:
        raise HTTPException(503, "Service not ready")

    # Extract cloud token from header (stripped by pydantic alias)
    cloud_token = raw_request.headers.get("X-Cloud-Token", "")
    if not cloud_token:
        raise HTTPException(401, "Missing X-Cloud-Token header. Purchase cloud tokens at https://gumroad.com/l/spec-editor-cloud")

    balance_key = _balance_key(cloud_token)
    request_id = _generate_request_id(cloud_token)
    estimated = _estimate_tokens(request.max_tokens)

    # 1. Pre-deduct
    ok, new_balance = await _redis.eval(
        PRE_DEDUCT_SCRIPT,
        1,  # num keys
        balance_key,
        estimated,
        request_id,
        str(int(time.time())),
        cloud_token,
        str(PENDING_TTL),
    )
    ok = int(ok)
    if not ok:
        current_balance = int(await _redis.get(balance_key) or 0)
        raise HTTPException(
            402,
            detail={
                "error": "Insufficient cloud tokens.",
                "balance": current_balance,
                "required": estimated,
                "shortfall": estimated - current_balance,
                "purchase_url": "https://gumroad.com/l/spec-editor-cloud",
            },
        )

    logger.info(
        "Pre-deducted %d tokens for %s (balance: %d → %d)",
        estimated, cloud_token[:12] + "...", int(new_balance) + estimated, int(new_balance),
    )

    if int(new_balance) < LOW_BALANCE_THRESHOLD:
        logger.warning(
            "Low balance for %s: %d tokens remaining",
            cloud_token[:12] + "...", int(new_balance),
        )

    # 2. Forward to LLM provider
    try:
        llm_request = _build_llm_request(request)
        llm_response = await _http_client.post(
            LLM_PROVIDER_URL,
            json=llm_request,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        )

        if llm_response.status_code != 200:
            # LLM error → full refund
            await _redis.eval(
                REFUND_SCRIPT,
                1,
                balance_key,
                estimated,
                request_id,
            )
            logger.error(
                "LLM provider returned %d for %s — refunded %d tokens",
                llm_response.status_code, request_id, estimated,
            )
            # Forward the error to the client
            return JSONResponse(
                content=llm_response.json(),
                status_code=llm_response.status_code,
            )

        response_data = llm_response.json()

    except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
        # Network error → full refund
        await _redis.eval(
            REFUND_SCRIPT,
            1,
            balance_key,
            estimated,
            request_id,
        )
        logger.error(
            "LLM call failed for %s: %s — refunded %d tokens",
            request_id, exc, estimated,
        )
        raise HTTPException(502, f"LLM provider unreachable: {exc}")

    # 3. Reconcile
    actual_tokens = _extract_usage(response_data)
    ts = int(time.time())

    final_balance = await _redis.eval(
        RECONCILE_SCRIPT,
        1,
        balance_key,
        actual_tokens,
        estimated,
        request_id,
        str(ts),
    )

    # Track total used
    await _redis.incrby(f"{balance_key}:used", actual_tokens)

    logger.info(
        "Reconciled %s: actual=%d, estimated=%d, refund=%d, balance=%d",
        request_id, actual_tokens, estimated, estimated - actual_tokens, int(final_balance),
    )

    # 4. Return LLM response to client
    return JSONResponse(content=response_data)


# ------------------------------------------------------------------
# GumRoad Webhook — Auto Top-Up
# ------------------------------------------------------------------

@app.post("/webhooks/gumroad")
async def gumroad_webhook(request: Request):
    """Handle GumRoad sale webhook for automatic top-up.

    GumRoad sends a POST with application/x-www-form-urlencoded body
    when a product is purchased. We verify the signature, extract the
    license key, and credit the user's cloud token balance.

    The GumRoad product must have a custom field configured so users
    can enter their license key during checkout. The field key should
    be 'cloud_token_key' (or whatever is in CUSTOM_FIELD_NAME).

    Security:
        - HMAC-SHA256 signature verification
        - Idempotency via sale_id tracking in Redis
        - Refund/chargeback reversal handled
    """
    if _redis is None:
        raise HTTPException(503, "Not ready")

    # 1. Verify webhook signature
    body = await request.body()
    signature = request.headers.get("X-GumRoad-Signature", "")

    if GUMROAD_WEBHOOK_SECRET:
        expected = hmac.new(
            GUMROAD_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning("Invalid GumRoad webhook signature")
            raise HTTPException(403, "Invalid signature")

    # 2. Parse form data
    form_data = await request.form()
    data = dict(form_data)

    sale_id = str(data.get("sale_id", ""))
    product_id = str(data.get("product_id", ""))
    email = str(data.get("email", ""))
    price = int(data.get("price", 0))
    refunded = str(data.get("refunded", "")).lower() == "true"
    chargebacked = str(data.get("chargebacked", "")).lower() == "true"
    disputed = str(data.get("disputed", "")).lower() == "true"

    # 3. Extract license key from custom field
    # Users must enter their license key during GumRoad checkout.
    # The custom field key configured in GumRoad determines the form key.
    license_key = ""
    for key, value in data.items():
        if "custom_fields" in key and key.endswith("]"):
            # e.g., custom_fields[cloud_token_key]
            license_key = str(value)
            break

    # Fallback: use license_key field directly (for Pro purchases)
    if not license_key:
        license_key = str(data.get("license_key", ""))

    if not license_key:
        logger.warning("Webhook missing license_key for sale %s", sale_id)
        return {"status": "ignored", "reason": "No license_key in webhook"}

    # 4. Idempotency check
    idempotency_key = f"webhook:{sale_id}"
    if await _redis.exists(idempotency_key):
        logger.info("Duplicate webhook for sale %s — skipped", sale_id)
        return {"status": "duplicate", "sale_id": sale_id}

    # 5. Handle refund/chargeback/dispute → REVERSE tokens
    if refunded or chargebacked or disputed:
        return await _handle_refund_webhook(
            sale_id, license_key, product_id, refunded, chargebacked, disputed,
        )

    # 6. Resolve token amount from product
    token_amount = _token_packs.get(product_id, 0)
    if token_amount == 0:
        # Try partial match (product ID might have variant suffix)
        for pid, amt in _token_packs.items():
            if pid in product_id or product_id in pid:
                token_amount = amt
                break

    if token_amount == 0:
        logger.warning(
            "Unknown product_id '%s' in webhook sale %s — no tokens credited",
            product_id, sale_id,
        )
        return {"status": "unknown_product", "product_id": product_id}

    # 7. Credit balance
    balance_key = _balance_key(license_key)
    purchased_key = f"{balance_key}:purchased"

    new_balance = await _redis.eval(
        CREDIT_SCRIPT,
        2,
        balance_key,
        purchased_key,
        token_amount,
        datetime.now(timezone.utc).isoformat(),
    )

    # 8. Mark as processed
    await _redis.setex(idempotency_key, 86400 * 30, "processed")  # 30 days

    logger.info(
        "Top-up: sale=%s, user=%s, product=%s, amount=%d, new_balance=%d, email=%s",
        sale_id, license_key[:12] + "...", product_id, token_amount, int(new_balance), email,
    )

    return {
        "status": "credited",
        "sale_id": sale_id,
        "license_key": license_key[:12] + "...",
        "amount": token_amount,
        "new_balance": int(new_balance),
    }


async def _handle_refund_webhook(
    sale_id: str,
    license_key: str,
    product_id: str,
    refunded: bool,
    chargebacked: bool,
    disputed: bool,
) -> dict:
    """Reverse a top-up when a purchase is refunded/chargebacked.

    Note: This is a simplified reversal. In production, you'd look up
    the original sale in the ledger to determine the exact amount to
    reverse. Here we use the product_id → token_pack mapping.
    """
    token_amount = _token_packs.get(product_id, 0)
    reason = "refunded" if refunded else ("chargebacked" if chargebacked else "disputed")

    if token_amount > 0:
        balance_key = _balance_key(license_key)
        current = int(await _redis.get(balance_key) or 0)

        # Don't go negative on reversal
        deduct = min(token_amount, current)
        if deduct > 0:
            await _redis.decrby(balance_key, deduct)
            await _redis.decrby(f"{balance_key}:purchased", token_amount)

        logger.warning(
            "Reversal: sale=%s, user=%s, reason=%s, deducted=%d, balance_was=%d",
            sale_id, license_key[:12] + "...", reason, deduct, current,
        )

    # Idempotency
    idempotency_key = f"webhook:{sale_id}"
    await _redis.setex(idempotency_key, 86400 * 30, f"reversed:{reason}")

    return {
        "status": "reversed",
        "sale_id": sale_id,
        "reason": reason,
    }


# ------------------------------------------------------------------
# Ledger worker (async, off hot path)
# ------------------------------------------------------------------

async def _ledger_worker():
    """Periodically drain Redis ledger into SQLite for durability.

    Runs every 5 seconds. Not on the critical path — if Redis crashes
    before drain, at most 5 seconds of audit data is lost.
    """
    await asyncio.sleep(5)  # Wait for startup

    # Ensure SQLite DB exists
    import aiosqlite

    db_path = Path(LEDGER_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            if _redis is None:
                await asyncio.sleep(5)
                continue

            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_key TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        tokens INTEGER NOT NULL,
                        operation TEXT NOT NULL DEFAULT 'deduct',
                        ts REAL NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                """)
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ledger_user
                    ON ledger(user_key, ts)
                """)
                await db.commit()

                # Drain Redis ledger sorted sets
                cursor = 0
                while True:
                    cursor, keys = await _redis.scan(
                        cursor, match="ledger:*", count=100,
                    )
                    for key in keys:
                        user_key = key.replace("ledger:", "")
                        # Pop oldest entries (up to 500 per user per cycle)
                        entries = await _redis.zpopmin(key, count=500)
                        if entries:
                            rows = []
                            for entry_tuple in entries:
                                # entry_tuple is (member, score) but zpopmin
                                # returns list alternating member, score
                                # Actually redis-py returns list of tuples
                                if isinstance(entry_tuple, (list, tuple)):
                                    member, score = entry_tuple[0], entry_tuple[1]
                                else:
                                    # Handle redis-py return format
                                    member = str(entry_tuple)
                                    score = 0.0

                                # member format: "request_id:tokens"
                                parts = str(member).rsplit(":", 1)
                                req_id = parts[0] if len(parts) > 1 else str(member)
                                tokens = int(parts[1]) if len(parts) > 1 else 0

                                rows.append((user_key, req_id, tokens, "deduct", float(score)))

                            if rows:
                                await db.executemany(
                                    "INSERT INTO ledger (user_key, request_id, tokens, operation, ts) "
                                    "VALUES (?, ?, ?, ?, ?)",
                                    rows,
                                )
                                await db.commit()
                                logger.debug(
                                    "Drained %d ledger entries for %s",
                                    len(rows), user_key[:12] + "...",
                                )

                    if cursor == 0:
                        break

        except Exception as exc:
            logger.error("Ledger worker error: %s", exc, exc_info=True)

        await asyncio.sleep(5)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _balance_key(license_key: str) -> str:
    """Derive Redis balance key from license key (hash for safety)."""
    # Use first 32 chars of SHA256 to keep keys manageable
    hashed = hashlib.sha256(license_key.encode()).hexdigest()[:32]
    return f"balance:{hashed}"


def _estimate_tokens(max_tokens: int) -> int:
    """Pre-deduct estimate: max_tokens × safety multiplier."""
    return int((max_tokens or DEFAULT_ESTIMATE_TOKENS) * SAFETY_MULTIPLIER)


def _generate_request_id(license_key: str) -> str:
    """Generate a unique request ID for tracking."""
    ts = int(time.time() * 1_000_000)
    suffix = hashlib.sha256(f"{license_key}:{ts}".encode()).hexdigest()[:8]
    return f"req_{ts}_{suffix}"


def _build_llm_request(request: ChatRequest) -> dict:
    """Build LLM provider request, stripping cloud-only fields."""
    body: dict[str, Any] = {
        "model": request.model,
        "messages": request.messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    if request.tools:
        body["tools"] = request.tools
    return body


def _extract_usage(response_data: dict) -> int:
    """Extract total tokens used from LLM response."""
    usage = response_data.get("usage", {})
    total = usage.get("total_tokens", 0)
    if total == 0:
        # Some providers don't report usage — use prompt + completion
        total = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    if total == 0:
        # Fallback: estimate
        choices = response_data.get("choices", [{}])
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        # Rough estimate: 1 token ≈ 4 chars
        total = max(len(content) // 4, 100)
        logger.warning("LLM response missing usage info — estimated %d tokens", total)
    return int(total)


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CLOUD_PROXY_PORT", "8089"))
    workers = int(os.environ.get("CLOUD_PROXY_WORKERS", "4"))

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level="info",
        access_log=True,
    )
