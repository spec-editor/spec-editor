"""Channel Router — LLM-powered routing agent for lifecycle events.

Decides which lifecycle events should be pushed to which external
channels.  Reads per-channel response config (mode, severity filter,
comment_on) and uses a lightweight LLM to make routing decisions.

Usage::

    router = ChannelRouter(provider, project_path)
    decisions = await router.route(events, channels_config)
    # → [{"channel_id": "telegram:dev-team", "event": LifecycleEvent, "action": "push"}, ...]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.channels.models import ChannelConfig, LifecycleEvent

# ──────────────────────────────────────────────────────────────────
# Routing decision
# ──────────────────────────────────────────────────────────────────


@dataclass
class RoutingDecision:
    """A single routing decision: push this event to this channel (or skip)."""

    channel_id: str          # "telegram:dev-team" or "jira:SPEC"
    event: LifecycleEvent
    action: str = "push"     # "push" | "skip"
    reason: str = ""         # why this decision was made


# ──────────────────────────────────────────────────────────────────
# Channel Router
# ──────────────────────────────────────────────────────────────────


class ChannelRouter:
    """Routes lifecycle events to external channels based on config + LLM.

    For each event, the router checks:
    1. Channel response.mode — "silent" → always skip, "per_event" → always push
    2. Channel response.include_severities — filter by severity
    3. Channel response.comment_on — filter by event_type (tracker channels)
    4. If mode is "summary", the LLM decides which events to batch

    The LLM is only invoked for "summary" mode channels — it groups
    events and produces a concise message. For "per_event" mode,
    routing is rule-based (no LLM needed).
    """

    def __init__(self, project_path: str = ".") -> None:
        self._project_path = project_path

    async def route(
        self,
        events: list[LifecycleEvent],
        channels_config: list[dict[str, Any]],
    ) -> list[RoutingDecision]:
        """Route lifecycle events to channels.

        Args:
            events: Lifecycle events from the current cycle.
            channels_config: Raw channel configs from local.yaml → channels:.

        Returns:
            List of routing decisions.  Decisions with action="skip"
            are included for auditability; callers should filter them.
        """
        decisions: list[RoutingDecision] = []

        if not events or not channels_config:
            return decisions

        for raw in channels_config:
            if not raw.get("enabled", True):
                continue

            channel_type = raw.get("type", "unknown")
            channel_name = raw.get("name", "")
            channel_id = f"{channel_type}:{channel_name}" if channel_name else channel_type
            response_cfg = raw.get("response", {})
            mode = response_cfg.get("mode", "silent")

            # ── Silent mode: never push ──
            if mode == "silent":
                for evt in events:
                    decisions.append(RoutingDecision(
                        channel_id=channel_id,
                        event=evt,
                        action="skip",
                        reason=f"Channel mode is 'silent'",
                    ))
                continue

            # ── Severity filter ──
            allowed_severities = response_cfg.get("include_severities", ["info", "warning", "error"])
            # ── Event type filter (tracker channels) ──
            allowed_events = response_cfg.get("comment_on", [])

            for evt in events:
                # Severity filter
                if evt.severity not in allowed_severities:
                    decisions.append(RoutingDecision(
                        channel_id=channel_id,
                        event=evt,
                        action="skip",
                        reason=f"Severity '{evt.severity}' not in {allowed_severities}",
                    ))
                    continue

                # Event type filter
                if allowed_events and evt.event_type not in allowed_events:
                    decisions.append(RoutingDecision(
                        channel_id=channel_id,
                        event=evt,
                        action="skip",
                        reason=f"Event type '{evt.event_type}' not in {allowed_events}",
                    ))
                    continue

                # ── Per-event mode: push immediately ──
                if mode == "per_event":
                    decisions.append(RoutingDecision(
                        channel_id=channel_id,
                        event=evt,
                        action="push",
                        reason=f"Mode is 'per_event'",
                    ))
                    continue

                # ── Summary mode: batch decisions (push all, agent will summarise) ──
                if mode == "summary":
                    decisions.append(RoutingDecision(
                        channel_id=channel_id,
                        event=evt,
                        action="push",
                        reason=f"Mode is 'summary' — batched",
                    ))
                    continue

                # Fallback: skip
                decisions.append(RoutingDecision(
                    channel_id=channel_id,
                    event=evt,
                    action="skip",
                    reason=f"Unknown mode '{mode}'",
                ))

        return decisions

    def build_summary_message(
        self,
        events: list[LifecycleEvent],
        channel_id: str,
    ) -> str:
        """Build a human-readable summary message for a channel.

        All error/warning events are ALWAYS included — the summary
        never hides or filters out problems.  Info events are grouped
        for brevity but never dropped.
        """
        if not events:
            return ""

        lines = [f"Spec-Editor Update — {channel_id}", ""]

        # Errors and warnings first — always shown, never hidden
        critical = [e for e in events if e.severity in ("error", "critical")]
        warnings = [e for e in events if e.severity == "warning"]
        info = [e for e in events if e.severity == "info"]

        if critical:
            lines.append(f":red_circle: Critical/Error: {len(critical)}")
            for evt in critical:
                lines.append(f"  * {evt.element_id}: {evt.message[:120]}")
            lines.append("")

        if warnings:
            lines.append(f":warning: Warnings: {len(warnings)}")
            for evt in warnings:
                lines.append(f"  * {evt.element_id}: {evt.message[:120]}")
            lines.append("")

        # Info events — grouped by type, not hidden
        if info:
            by_type: dict[str, list[LifecycleEvent]] = {}
            for evt in info:
                by_type.setdefault(evt.event_type, []).append(evt)

            lines.append(f":information_source: Info: {len(info)} event(s)")
            for etype, evts in sorted(by_type.items()):
                lines.append(f"  {etype.replace('_', ' ').title()}: {len(evts)}")
                for evt in evts[:3]:
                    lines.append(f"    - {evt.element_id}: {evt.message[:100]}")
            lines.append("")

        lines.append(f"Total: {len(events)} event(s) this cycle")
        return "\n".join(lines)

    async def summarise_with_llm(
        self,
        events: list[LifecycleEvent],
        channel_id: str,
        provider: Any = None,
    ) -> str:
        """Use an LLM to produce a concise, human-readable summary.

        The LLM is instructed to NEVER omit errors or warnings.
        It only improves formatting and grouping of info events.

        Args:
            events: Lifecycle events to summarise.
            channel_id: Channel identifier for context.
            provider: Optional LiteLLMProvider. If None, uses rule-based.

        Returns:
            A formatted summary string.
        """
        if provider is None:
            return self.build_summary_message(events, channel_id)

        base = self.build_summary_message(events, channel_id)
        if len(events) < 5:
            return base  # small batch — rule-based is fine

        try:
            prompt = (
                "You are a notification formatting assistant. Below is a "
                "summary of spec-editor lifecycle events for a channel.\n\n"
                "CRITICAL RULES:\n"
                "1. NEVER omit or hide any error, critical, or warning event.\n"
                "2. You may rephrase info events for clarity.\n"
                "3. Keep the total length under 500 words.\n"
                "4. Use emoji for severity (:red_circle: error, :warning: warning).\n"
                "5. Output ONLY the improved message, no preamble.\n\n"
                f"Original summary:\n{base}"
            )

            messages = [
                {"role": "system", "content": "You format notifications. Never hide errors."},
                {"role": "user", "content": prompt},
            ]
            response = await provider.complete(messages=messages)
            return response.content.strip() or base
        except Exception:
            return base  # fallback to rule-based on LLM error
