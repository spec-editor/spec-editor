"""Trello + Plankanban tracker channels.

Configuration (local.yaml):

    channels:
      - type: trello
        name: dev-board
        kind: tracker
        config:
          api_key: "${TRELLO_API_KEY}"
          token: "${TRELLO_TOKEN}"
          board_id: "abc123"
        mapping:
          status:
            "Backlog": draft
            "In Progress": dispatched
            "Done": confirmed
          labels:
            - "spec-editor"
        response:
          comment_on: ["code_generated", "deployed"]

      - type: plankanban
        name: agile-board
        kind: tracker
        config:
          url: "https://plankanban.example.com"
          token: "${PLANKANBAN_TOKEN}"
          board_id: "board-1"
        mapping:
          status:
            "Backlog": draft
            "Ready": reviewed
            "In Progress": dispatched
            "Done": confirmed
"""

from __future__ import annotations
from typing import Any

from src.channels.models import ChannelConfig, LifecycleEvent, TrackerItem
from src.channels.tracker_channel import TrackerChannel
from src.channels.http_helpers import http_get, http_post


class TrelloTrackerChannel(TrackerChannel):
    """Trello board — cards ↔ spec-editor elements."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._api_key = config.config.get("api_key", "")
        self._token = config.config.get("token", "")
        self._board_id = config.config.get("board_id", "")

    async def pull(self) -> list[TrackerItem]:
        if not self._api_key or not self._token or not self._board_id:
            return []
        url = f"https://api.trello.com/1/boards/{self._board_id}/cards"
        params = {"key": self._api_key, "token": self._token}
        status, cards = await http_get(url, params=params)
        if status != 200 or not cards:
            return []
        items = []
        sync_label = (self._config.mapping.get("labels") or ["spec-editor"])[0]
        for card in cards:
            labels = [lb.get("name", "") for lb in card.get("labels", [])]
            if sync_label and sync_label not in labels:
                continue
            items.append(TrackerItem(id=card.get("id", ""), title=card.get("name", ""),
                                      description=card.get("desc", ""),
                                      status=card.get("list", {}).get("name", card.get("idList", "")),
                                      labels=labels, assignee=card.get("idMembers", [None])[0] if card.get("idMembers") else None,
                                      due_date=card.get("due", ""), url=card.get("url", ""), raw=card))
        return items

    async def push(self, event: LifecycleEvent) -> bool:
        if not self._api_key or not self._token:
            return False
        reverse_map = {v: k for k, v in self._config.mapping.get("status", {}).items()}
        target_list = reverse_map.get(event.new_status, "")
        if not target_list or not event.element_id:
            return True

        try:
            import aiohttp
            url = f"https://api.trello.com/1/cards/{event.element_id}"
            params = {"key": self._api_key, "token": self._token}
            payload = {"idList": target_list}
            comment_on = self._config.response.get("comment_on", [])
            if event.event_type in comment_on and event.message:
                # Trello doesn't support simultaneous move+comment — do move first
                async with aiohttp.ClientSession() as s:
                    await s.put(url, params=params, json=payload)
                    # Add comment
                    comment_url = f"https://api.trello.com/1/cards/{event.element_id}/actions/comments"
                    await s.post(comment_url, params=params, json={"text": event.message})
                    return True
            async with aiohttp.ClientSession() as s:
                async with s.put(url, params=params, json=payload) as r:
                    return r.status == 200
        except Exception:
            return False

    async def validate_connection(self) -> dict[str, Any]:
        if not self._api_key or not self._token:
            return {"ok": False, "error": "Missing api_key or token"}
        try:
            import aiohttp
            url = "https://api.trello.com/1/members/me"
            params = {"key": self._api_key, "token": self._token}
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params=params) as r:
                    if r.status == 200:
                        data = await r.json()
                        return {"ok": True, "message": f"Trello OK — user: {data.get('username', '?')}"}
                    return {"ok": False, "error": f"HTTP {r.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class PlankanbanTrackerChannel(TrackerChannel):
    """Plankanban board — cards ↔ spec-editor elements."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._url = config.config.get("url", "")
        self._token = config.config.get("token", "")
        self._board_id = config.config.get("board_id", "")

    async def pull(self) -> list[TrackerItem]:
        """Fetch cards from Planka v2 API.

        Planka v2 nests cards under ``included.cards[]`` in the board response.
        Use ``?with=lists,cards,cardLabels`` to include related data.
        """
        if not self._url or not self._token or not self._board_id:
            return []
        headers = {"Authorization": f"Bearer {self._token}"}
        url = (
            f"{self._url.rstrip('/')}/api/boards/{self._board_id}"
            f"?with=lists,cards,cardLabels"
        )
        status, data = await http_get(url, headers=headers)
        if status != 200 or not data:
            return []

        # Planka v2: cards are nested in included, not at top level
        cards = []
        if isinstance(data, dict):
            cards = data.get("included", {}).get("cards", [])
        elif isinstance(data, list):
            cards = data  # fallback for older API versions

        # Build lane name lookup
        lists = []
        if isinstance(data, dict):
            lists = data.get("included", {}).get("lists", [])
        lane_by_id = {l["id"]: l.get("name", l["id"]) for l in lists}

        items = []
        for card in cards:
            lid = card.get("listId", "")
            items.append(TrackerItem(
                id=card.get("id", ""),
                title=card.get("name", card.get("title", "")),
                description=card.get("description") or "",
                status=lane_by_id.get(lid, lid),
                labels=[lb.get("name", "") for lb in card.get("labels", [])],
                assignee=card.get("assignee", ""),
                raw=card,
            ))
        return items

    async def push(self, event: LifecycleEvent) -> bool:
        """Move a card to the matching lane in Planka v2.

        Uses ``PATCH /api/cards/{id}`` with ``listId`` to change lanes.
        """
        if not self._url or not self._token:
            return False

        # Resolve the target lane ID from the status mapping
        reverse_map = {v: k for k, v in self._config.mapping.get("status", {}).items()}
        target_lane_name = reverse_map.get(event.new_status, "")
        if not target_lane_name or not event.element_id:
            return True

        # Look up the lane ID from the board's lists
        headers = {"Authorization": f"Bearer {self._token}"}
        url = (
            f"{self._url.rstrip('/')}/api/boards/{self._board_id}"
            f"?with=lists"
        )
        status, data = await http_get(url, headers=headers)
        lists = data.get("included", {}).get("lists", []) if isinstance(data, dict) else []
        lane_id = None
        for lst in lists:
            if lst.get("name", "").lower() == target_lane_name.lower():
                lane_id = lst["id"]
                break
        if not lane_id:
            return False  # target lane not found

        try:
            import aiohttp
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self._url.rstrip('/')}/api/cards/{event.element_id}"
            payload = {"listId": lane_id}
            async with aiohttp.ClientSession() as s:
                async with s.patch(url, headers=headers, json=payload) as r:
                    return r.status in (200, 204)
        except Exception:
            return False

    async def validate_connection(self) -> dict[str, Any]:
        if not self._url or not self._token:
            return {"ok": False, "error": "Missing url or token"}
        try:
            import aiohttp
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self._url.rstrip('/')}/api/boards/{self._board_id}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers) as r:
                    if r.status == 200:
                        return {"ok": True, "message": f"Plankanban OK — board: {self._board_id}"}
                    return {"ok": False, "error": f"HTTP {r.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
