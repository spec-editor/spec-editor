"""Notifier — pluggable notification delivery.

Sends notifications (Slack, Telegram, Email) on key lifecycle events:
spec created, bug found, deploy done, review needed.

Configured via ``local.yaml`` → ``notifications:`` section.

Usage::

    from src.notifiers import create_notifier

    notifier = create_notifier(project_path)
    notifier.send("Bug SRC-042 fixed and deployed", channel="deploys")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Notifier(ABC):
    """Abstract notification delivery backend.

    Implementations send structured messages to external channels.
    All methods are fire-and-forget — they should never raise.
    """

    @abstractmethod
    def send(
        self,
        message: str,
        channel: str = "general",
        *,
        title: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send a notification.

        Args:
            message: Notification body (plain text or Markdown)
            channel: Target channel/room/chat ID
            title: Optional title (used by Slack/Email subject)
            severity: ``info`` | ``warning`` | ``error`` (maps to colors/icons)
            metadata: Extra structured data (element IDs, links, etc.)

        Returns:
            True if sent successfully.
        """
        ...

    def send_batch(
        self,
        messages: list[dict[str, Any]],
        channel: str = "general",
    ) -> int:
        """Send multiple notifications. Returns count of successful sends."""
        count = 0
        for msg in messages:
            if self.send(
                message=msg.get("message", ""),
                channel=msg.get("channel", channel),
                title=msg.get("title", ""),
                severity=msg.get("severity", "info"),
                metadata=msg.get("metadata"),
            ):
                count += 1
        return count


# ── Backend implementations ────────────────────────────────────────


class LogNotifier(Notifier):
    """Logs notifications to stderr — for development and debugging."""

    def send(
        self,
        message: str,
        channel: str = "general",
        *,
        title: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        import sys

        prefix = {"info": "ℹ", "warning": "⚠", "error": "❌"}.get(severity, "ℹ")
        header = f"[{channel}]" if title == "" else f"[{channel}] {title}"
        print(f"{prefix} {header}: {message}", file=sys.stderr)
        if metadata:
            print(f"   metadata: {metadata}", file=sys.stderr)
        return True


class SlackNotifier(Notifier):
    """Slack webhook notification backend.

    Requires ``slack-webhook-url`` in config or ``SLACK_WEBHOOK_URL`` env var.
    """

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(
        self,
        message: str,
        channel: str = "general",
        *,
        title: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        try:
            import urllib.request
            import json as _json

            color = {"info": "#36a64f", "warning": "#ffcc00", "error": "#ff0000"}.get(
                severity, "#36a64f"
            )

            payload: dict[str, Any] = {
                "attachments": [
                    {
                        "fallback": title or message[:80],
                        "color": color,
                        "title": title or "Spec Editor",
                        "text": message,
                        "fields": [],
                    }
                ]
            }
            if metadata:
                for k, v in metadata.items():
                    payload["attachments"][0]["fields"].append(
                        {"title": k, "value": str(v), "short": True}
                    )

            data = _json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            return False


class TelegramNotifier(Notifier):
    """Telegram bot notification backend.

    Requires ``telegram-bot-token`` and ``telegram-chat-id`` in config
    or ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` env vars.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id

    def send(
        self,
        message: str,
        channel: str = "general",
        *,
        title: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        try:
            import urllib.request
            import urllib.parse
            import json as _json

            prefix = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(severity, "ℹ️")
            text = f"{prefix} *{title}*\n{message}" if title else f"{prefix} {message}"
            if metadata:
                meta_str = "\n".join(f"• {k}: {v}" for k, v in metadata.items())
                text += f"\n\n{meta_str}"

            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = _json.dumps(
                {
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            return False


class EmailNotifier(Notifier):
    """SMTP email notification backend.

    Requires ``smtp-*`` config or standard SMTP env vars.
    """

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_addr: str = "spec-editor@localhost",
        to_addrs: list[str] | None = None,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = smtp_password
        self._from = from_addr
        self._to = to_addrs or []

    def send(
        self,
        message: str,
        channel: str = "general",
        *,
        title: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"] = self._from
            msg["To"] = ", ".join(self._to)
            msg["Subject"] = f"[{severity.upper()}] {title or 'Spec Editor Notification'}"
            body = message
            if metadata:
                body += "\n\n---\n"
                body += "\n".join(f"{k}: {v}" for k, v in metadata.items())
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self._host, self._port, timeout=10) as server:
                server.starttls()
                if self._user:
                    server.login(self._user, self._password)
                server.sendmail(self._from, self._to, msg.as_string())
            return True
        except Exception:
            return False


# ── Factory ─────────────────────────────────────────────────────────


def create_notifier(project_path: str | Path) -> Notifier:
    """Create a Notifier from project configuration.

    Reads ``local.yaml`` → ``notifications:`` section:

    .. code-block:: yaml

        notifications:
          backend: slack            # log | slack | telegram | email
          slack:
            webhook_url: ${SLACK_WEBHOOK_URL}
          telegram:
            bot_token: ${TELEGRAM_BOT_TOKEN}
            chat_id: ${TELEGRAM_CHAT_ID}
          email:
            smtp_host: smtp.example.com
            smtp_port: 587
            smtp_user: ${SMTP_USER}
            smtp_password: ${SMTP_PASSWORD}
            from: spec-editor@example.com
            to:
              - team@example.com

    Uses the Secrets Provider for ``${VAR}`` resolution.
    """
    import os

    proj = Path(project_path)
    backend_name = "log"
    backend_config: dict[str, Any] = {}

    local_yaml = proj / "local.yaml"
    if local_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(local_yaml.read_text()) or {}
            notif_cfg = data.get("notifications", {})
            backend_name = notif_cfg.get("backend", "log")
            backend_config = notif_cfg.get(backend_name, {})
        except Exception:
            pass

    backend_name = os.environ.get("SPEC_EDITOR__NOTIFIER_BACKEND", backend_name)

    if backend_name == "slack":
        webhook = backend_config.get("webhook_url", "")
        if not webhook:
            webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            return LogNotifier()  # fallback
        return SlackNotifier(webhook_url=os.path.expandvars(webhook))

    elif backend_name == "telegram":
        token = backend_config.get("bot_token", "")
        if not token:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat = backend_config.get("chat_id", "")
        if not chat:
            chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return LogNotifier()
        return TelegramNotifier(
            bot_token=os.path.expandvars(token),
            chat_id=os.path.expandvars(chat),
        )

    elif backend_name == "email":
        from_addr = backend_config.get("from", "spec-editor@localhost")
        to_addrs = backend_config.get("to", [])
        return EmailNotifier(
            smtp_host=os.path.expandvars(backend_config.get("smtp_host", "localhost")),
            smtp_port=int(backend_config.get("smtp_port", 587)),
            smtp_user=os.path.expandvars(backend_config.get("smtp_user", "")),
            smtp_password=os.path.expandvars(backend_config.get("smtp_password", "")),
            from_addr=os.path.expandvars(from_addr),
            to_addrs=[
                os.path.expandvars(a) for a in to_addrs
            ],
        )

    else:
        return LogNotifier()
