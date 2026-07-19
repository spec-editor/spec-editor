"""GitHubIssuesTrackerChannel — GitHub Issues REST API v3.

Configuration (local.yaml):

    channels:
      - type: github_issues
        name: spec-editor
        kind: tracker
        config:
          token: "${GITHUB_TOKEN}"
          owner: "spec-editor"
          repo: "spec-editor"
        mapping:
          status:
            "open": draft
            "in progress": dispatched
            "closed": confirmed
          labels:
            - "spec-editor"
        response:
          comment_on: ["code_generated", "test_failed"]
"""

from __future__ import annotations
from typing import Any

from src.channels.models import ChannelConfig, LifecycleEvent, TrackerItem
from src.channels.tracker_channel import TrackerChannel
from src.channels.http_helpers import get_aiohttp, http_get, http_post, http_patch


class GitHubIssuesTrackerChannel(TrackerChannel):
    """GitHub Issues — issues ↔ spec-editor elements."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._token = config.config.get("token", "")
        self._owner = config.config.get("owner", "")
        self._repo = config.config.get("repo", "")

    async def pull(self) -> list[TrackerItem]:
        if not self._token or not self._owner or not self._repo:
            return []
        sync_label = (self._config.mapping.get("labels") or ["spec-editor"])[0]
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github.v3+json"}
        url = f"https://api.github.com/repos/{self._owner}/{self._repo}/issues"
        status, data = await http_get(url, headers=headers, params={"labels": sync_label, "state": "all", "per_page": 50})
        if status != 200 or not data:
            return []
        items = []
        for issue in data:
            if "pull_request" in issue:
                continue
            items.append(TrackerItem(
                id=str(issue.get("number", "")),
                title=issue.get("title", ""),
                description=issue.get("body", "") or "",
                status="closed" if issue.get("state") == "closed" else "open",
                labels=[lb["name"] for lb in issue.get("labels", [])],
                assignee=issue.get("assignee", {}).get("login") if issue.get("assignee") else None,
                url=issue.get("html_url", ""),
                raw=issue,
            ))
        return items

    async def push(self, event: LifecycleEvent) -> bool:
        if not self._token or not self._owner or not self._repo:
            return False
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }
        url = f"https://api.github.com/repos/{self._owner}/{self._repo}/issues/{event.element_id}"
        reverse_map = {v: k for k, v in self._config.mapping.get("status", {}).items()}
        gh_state = reverse_map.get(event.new_status, "")
        payload = {"state": gh_state} if gh_state in ("open", "closed") else {}
        comment_on = self._config.response.get("comment_on", [])
        if event.event_type in comment_on and event.message:
            await http_post(f"{url}/comments", headers=headers, json_data={"body": event.message})
        if payload:
            status, _ = await http_patch(url, headers=headers, json_data=payload)
            return status in (200, 201, 204)
        return True

    async def validate_connection(self) -> dict[str, Any]:
        if not self._token:
            return {"ok": False, "error": "Missing token"}
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github.v3+json"}
        url = f"https://api.github.com/repos/{self._owner}/{self._repo}"
        status, data = await http_get(url, headers=headers)
        if status == 200 and data:
            return {"ok": True, "message": f"GitHub OK — {data.get('full_name', '?')}"}
        return {"ok": False, "error": f"HTTP {status}"}
