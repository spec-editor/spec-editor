"""Dialogue logger — JSONL logging of all messages and traces."""

import json
from datetime import datetime, timezone
from pathlib import Path


def _compact_args(tool_name: str, args: dict) -> dict:
    """Keep only meaningful arguments of a tool call for the log."""
    if tool_name in ("write_element",):
        return {
            k: args.get(k, "")
            for k in ("id", "title", "aspect", "element_type")
            if k in args
        }
    if tool_name in ("add_relationship", "remove_relationship"):
        return {
            k: args.get(k, "")
            for k in ("source_id", "target_id", "rel_type")
            if k in args
        }
    if tool_name == "search_elements":
        return {"query": args.get("query", "")}
    if tool_name in ("run_metrics", "run_validate", "report_complete"):
        return {}
    keys = list(args.keys())[:2]
    return {k: str(args[k])[:60] for k in keys}


class DialogueLogger:
    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(log_path, "a", encoding="utf-8")

    def log_message(
        self,
        agent_name: str,
        content: str,
        tool_calls: list | None = None,
        received_message: str = "",
    ) -> None:
        compact_tools = []
        for tc in tool_calls or []:
            if isinstance(tc, dict):
                name = tc.get("name", "?")
                args = tc.get("arguments", {})
            else:
                name = getattr(tc, "name", "?")
                args = getattr(tc, "arguments", {})
            short_args = _compact_args(name, args)
            compact_tools.append({"name": name, "args": short_args})

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent_name,
            "content": content[:3000] if content else "",
            "tool_calls": compact_tools,
            "received": received_message[:500] if received_message else "",
        }
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def log_orchestrator(
        self, decision: str, reason: str, agent_count: int = 0
    ) -> None:
        self._file.write(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "agent": f"Orchestrator ({agent_count} agents)"
                    if agent_count
                    else "Orchestrator",
                    "decision": decision,
                    "reason": reason,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self._file.flush()

    def log_trace(self, message: str) -> None:
        self._file.write(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace": message,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self._file.flush()

    def close(self) -> None:
        self._file.close()
