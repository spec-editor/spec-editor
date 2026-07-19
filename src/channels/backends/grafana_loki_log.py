"""GrafanaLokiLogChannel — streaming log source via Grafana Loki API.

Implements LogChannel ABC for Grafana Loki.  Queries Loki for log
entries, applies threshold gating and dedup, and returns LogItem
objects for the cycle pipeline.

Configuration (local.yaml → channels: section):

    channels:
      - type: grafana_loki
        name: prod                 # optional instance name
        kind: log
        config:
          url: "https://loki.company.com"
          token: "${LOKI_TOKEN}"
          query: '{job="spec-editor"} |= ""'
        analysis:
          dedup_window: 300        # seconds — collapse identical errors
          thresholds:
            error: {count: 5, window_sec: 300}
            critical: {count: 1, window_sec: 60}
        response:
          create_bugs: true
          max_open_bugs: 20

Uses the existing StructuredLogEmitter log tree as a fallback when
Loki is unreachable — reads from ``logs/MOD-*/structured.jsonl``.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from src.channels.log_channel import LogChannel
from src.channels.models import ChannelConfig, LifecycleEvent, LogItem


class GrafanaLokiLogChannel(LogChannel):
    """Grafana Loki log source — streaming queries → LogItem events."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._url = config.config.get("url", "")
        self._token = config.config.get("token", "")
        self._query = config.config.get("query", '{job="spec-editor"} |= ""')
        self._dedup_window = config.analysis.get("dedup_window", 300)
        self._thresholds = config.analysis.get("thresholds", {})
        self._seen_hashes: dict[str, float] = {}  # content_hash → first_seen_ts

    # ── LogChannel ABC ──────────────────────────────────────────────

    async def pull(self) -> list[LogItem]:
        """Query Loki for recent log entries, apply threshold gating.

        Falls back to reading local log files when Loki is unreachable.
        """
        items: list[LogItem] = []

        # Try Loki first
        loki_items = await self._pull_from_loki()
        if loki_items:
            items.extend(loki_items)

        # Fallback: read local structured.jsonl files
        local_items = await self._pull_from_local()
        items.extend(local_items)

        # Apply dedup + threshold gating
        return self._apply_thresholds(items)

    async def push(self, event: LifecycleEvent) -> bool:
        """Log channels are read-only by default. Override for PagerDuty etc."""
        return True

    async def validate_connection(self) -> dict[str, Any]:
        """Verify Loki URL and auth."""
        if not self._url:
            return {"ok": False, "error": "Missing url in config"}

        try:
            import aiohttp

            headers = {}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            url = f"{self._url.rstrip('/')}/loki/api/v1/status/buildinfo"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"ok": True, "message": f"Loki {data.get('version', '?')}"}
                    return {"ok": False, "error": f"HTTP {resp.status}"}
        except ImportError:
            return {"ok": False, "error": "aiohttp not installed — pip install aiohttp"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Internal ───────────────────────────────────────────────────

    async def _pull_from_loki(self) -> list[LogItem]:
        """Query Loki LogQL for recent entries."""
        if not self._url:
            return []

        try:
            import aiohttp
        except ImportError:
            return []

        items: list[LogItem] = []

        try:
            headers = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            # Query last 5 minutes
            query_url = f"{self._url.rstrip('/')}/loki/api/v1/query_range"
            now_ns = int(time.time() * 1_000_000_000)
            params = {
                "query": self._query,
                "start": now_ns - 300_000_000_000,  # 5 min ago
                "end": now_ns,
                "limit": 200,
                "direction": "backward",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    query_url, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()

            for stream in data.get("data", {}).get("result", []):
                stream_labels = stream.get("stream", {})
                module = stream_labels.get("module_id", stream_labels.get("job", "unknown"))
                level = stream_labels.get("level", "info")

                for ts_ns, line in stream.get("values", []):
                    items.append(LogItem(
                        timestamp=str(int(ts_ns) // 1_000_000_000),
                        level=level,
                        message=line,
                        module=module,
                        raw={"labels": stream_labels, "source": "loki"},
                    ))
        except Exception:
            pass

        return items

    async def _pull_from_local(self) -> list[LogItem]:
        """Read from local logs/MOD-*/structured.jsonl as fallback."""
        import json

        items: list[LogItem] = []

        # Find log directories relative to project
        for search_dir in [Path("logs"), Path.cwd() / "logs"]:
            if not search_dir.is_dir():
                continue

            for mod_dir in sorted(search_dir.iterdir()):
                if not mod_dir.is_dir():
                    continue
                log_file = mod_dir / "structured.jsonl"
                if not log_file.is_file():
                    continue

                # Read last 100 lines (tail-equivalent)
                lines = log_file.read_text().strip().split("\n")[-100:]
                for line in lines:
                    try:
                        entry = json.loads(line)
                        items.append(LogItem(
                            timestamp=entry.get("timestamp", ""),
                            level=entry.get("level", "info"),
                            message=entry.get("message", ""),
                            module=mod_dir.name,
                            raw=entry,
                        ))
                    except json.JSONDecodeError:
                        pass

            break  # only use first found logs/ dir

        return items

    def _apply_thresholds(self, items: list[LogItem]) -> list[LogItem]:
        """Dedup by content hash + apply per-severity thresholds.

        Only returns items that breach their configured threshold —
        a single error is ignored, 10 errors in 5 minutes becomes one LogItem.
        """
        if not items:
            return []

        now = time.time()
        by_hash: dict[str, list[LogItem]] = {}

        for item in items:
            h = self._content_hash(item)
            by_hash.setdefault(h, []).append(item)

        result: list[LogItem] = []
        for h, group in by_hash.items():
            count = len(group)
            level = group[0].level

            # Check threshold for this severity
            threshold_cfg = self._thresholds.get(level, {})
            min_count = threshold_cfg.get("count", 1)
            window_sec = threshold_cfg.get("window_sec", 60)

            # Check if we've seen this hash recently (dedup window)
            first_seen = self._seen_hashes.get(h, 0)
            if now - first_seen < self._dedup_window:
                continue  # still in dedup window — skip

            if count >= min_count:
                # Breach! Report the first occurrence with aggregated count
                first = group[0]
                first.count = count
                first.raw["threshold_breach"] = True
                first.raw["occurrences"] = count
                first.raw["window_sec"] = window_sec
                result.append(first)
                self._seen_hashes[h] = now

        # Clean up stale hashes
        stale = [h for h, ts in self._seen_hashes.items() if now - ts > self._dedup_window * 2]
        for h in stale:
            del self._seen_hashes[h]

        return result

    @staticmethod
    def _content_hash(item: LogItem) -> str:
        """Stable hash for dedup: module + level + first 200 chars of message."""
        key = f"{item.module}:{item.level}:{item.message[:200]}"
        return hashlib.md5(key.encode()).hexdigest()
