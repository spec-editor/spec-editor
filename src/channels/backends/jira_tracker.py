"""JiraTrackerChannel — Jira Cloud integration via REST API v3.

Implements TrackerChannel ABC for Jira.  Syncs Jira issues ↔
spec-editor elements bidirectionally.

Configuration (local.yaml → channels: section):

    channels:
      - type: jira
        name: SPEC              # optional — Jira project key
        kind: tracker
        config:
          url: "https://company.atlassian.net"
          token: "${JIRA_TOKEN}"
          project_key: "SPEC"
        mapping:
          status:
            "To Do": draft
            "In Progress": dispatched
            "Done": confirmed
          labels:
            - "spec-editor"
        response:
          comment_on: ["code_generated", "test_failed", "deployed"]
"""

from __future__ import annotations

from typing import Any

from src.channels.models import ChannelConfig, LifecycleEvent, TrackerItem
from src.channels.tracker_channel import TrackerChannel
from src.channels.http_helpers import get_aiohttp, http_get, http_post


class JiraTrackerChannel(TrackerChannel):
    """Jira Cloud tracker channel — issues ↔ spec-editor elements."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._url = config.config.get("url", "")
        self._token = config.config.get("token", "")
        self._project_key = config.config.get("project_key", config.name or "")

    # ── TrackerChannel ABC ─────────────────────────────────────────

    async def pull(self) -> list[TrackerItem]:
        """Fetch issues from Jira project.

        Uses Jira REST API v3.  Only issues with the configured
        sync label (e.g. 'spec-editor') are returned.
        """
        if not self._url or not self._token:
            import sys
            print("[jira] Missing url/token in channel config", file=sys.stderr)
            return []

        issues: list[TrackerItem] = []

        try:
            import aiohttp

            headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            }
            jql = f"project={self._project_key} AND labels=spec-editor"
            url = f"{self._url.rstrip('/')}/rest/api/3/search"
            params = {"jql": jql, "maxResults": 50}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for issue in data.get("issues", []):
                            fields = issue.get("fields", {})
                            status_name = (
                                fields.get("status", {}).get("name", "")
                            )
                            issues.append(TrackerItem(
                                id=issue.get("key", ""),
                                title=fields.get("summary", ""),
                                description=(
                                    fields.get("description", {}) or {}
                                ).get("content", [{}])[0].get("content", [{}])[0].get("text", "")
                                if isinstance(fields.get("description"), dict)
                                else str(fields.get("description", "")),
                                status=status_name,
                                labels=fields.get("labels", []),
                                assignee=(
                                    fields.get("assignee", {}) or {}
                                ).get("displayName"),
                                url=f"{self._url.rstrip('/')}/browse/{issue.get('key', '')}",
                                raw=issue,
                            ))
                    else:
                        import sys
                        print(
                            f"[jira] HTTP {resp.status} from {url}",
                            file=sys.stderr,
                        )
        except ImportError:
            import sys
            print("[jira] aiohttp not installed — install with: pip install aiohttp", file=sys.stderr)
        except Exception as exc:
            import sys
            print(f"[jira] pull failed: {exc}", file=sys.stderr)

        return issues

    async def push(self, event: LifecycleEvent) -> bool:
        """Transition a Jira issue to match the element's new status."""
        if not self._url or not self._token:
            return False

        # Map element status back to Jira status
        reverse_map = {
            v: k for k, v in self._config.mapping.get("status", {}).items()
        }
        jira_status = reverse_map.get(event.new_status, "")
        if not jira_status:
            return True  # no mapping — nothing to push

        # Find the transition ID for the target status
        try:
            import aiohttp

            headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

            # Get available transitions
            transitions_url = (
                f"{self._url.rstrip('/')}/rest/api/3/issue/"
                f"{event.element_id}/transitions"
            )

            async with aiohttp.ClientSession() as session:
                async with session.get(transitions_url, headers=headers) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()

                transition_id = None
                for t in data.get("transitions", []):
                    if t.get("to", {}).get("name") == jira_status:
                        transition_id = t.get("id")
                        break

                if transition_id is None:
                    return False  # transition not available

                # Execute the transition
                payload = {"transition": {"id": transition_id}}

                # Optionally add a comment
                comment_on = self._config.response.get("comment_on", [])
                if event.event_type in comment_on and event.message:
                    from src.channels.models import LifecycleEvent
                    payload["update"] = {
                        "comment": [{"add": {"body": event.message}}]
                    }

                async with session.post(
                    transitions_url, headers=headers, json=payload
                ) as resp:
                    return resp.status in (200, 204)
        except Exception as exc:
            import sys
            print(f"[jira] push failed: {exc}", file=sys.stderr)
            return False

    async def validate_connection(self) -> dict[str, Any]:
        """Verify Jira URL, auth token, and project access."""
        if not self._url or not self._token:
            return {"ok": False, "error": "Missing url or token in config"}

        try:
            import aiohttp

            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self._url.rstrip('/')}/rest/api/3/project/{self._project_key}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return {
                            "ok": True,
                            "message": f"Jira connection OK — project: {self._project_key}",
                        }
                    elif resp.status == 404:
                        return {
                            "ok": False,
                            "error": f"Project '{self._project_key}' not found",
                        }
                    else:
                        return {
                            "ok": False,
                            "error": f"HTTP {resp.status}",
                        }
        except ImportError:
            return {"ok": False, "error": "aiohttp not installed"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
