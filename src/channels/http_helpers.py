"""Shared HTTP helpers for channel backends.

All channel backends use aiohttp for API calls.  This module provides
shared utilities to avoid duplicating import guards, session management,
and connection validation patterns across 10 backend files.
"""

from __future__ import annotations

from typing import Any


def get_aiohttp() -> Any | None:
    """Import aiohttp with graceful fallback.

    Returns the aiohttp module or None if not installed.
    Backends call this instead of duplicating try/except ImportError.
    """
    try:
        import aiohttp
        return aiohttp
    except ImportError:
        import sys
        print("[channels] aiohttp not installed — pip install aiohttp", file=sys.stderr)
        return None


async def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[int, Any]:
    """GET request with aiohttp. Returns (status_code, response_data or None)."""
    aiohttp = get_aiohttp()
    if aiohttp is None:
        return -1, None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers or {}, params=params or {},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    return resp.status, await resp.json()
                return resp.status, None
    except Exception:
        return -1, None


async def http_post(
    url: str,
    headers: dict[str, str] | None = None,
    json_data: dict[str, Any] | None = None,
    timeout: int = 10,
) -> tuple[int, Any]:
    """POST request with aiohttp. Returns (status_code, response_data or None)."""
    aiohttp = get_aiohttp()
    if aiohttp is None:
        return -1, None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers or {}, json=json_data or {},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in (200, 201, 204):
                    return resp.status, await resp.json() if resp.status != 204 else {}
                return resp.status, None
    except Exception:
        return -1, None


async def http_patch(
    url: str,
    headers: dict[str, str] | None = None,
    json_data: dict[str, Any] | None = None,
    timeout: int = 10,
) -> tuple[int, Any]:
    """PATCH request with aiohttp. Returns (status_code, response_data or None)."""
    aiohttp = get_aiohttp()
    if aiohttp is None:
        return -1, None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                url, headers=headers or {}, json=json_data or {},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in (200, 201, 204):
                    return resp.status, await resp.json() if resp.status != 204 else {}
                return resp.status, None
    except Exception:
        return -1, None
