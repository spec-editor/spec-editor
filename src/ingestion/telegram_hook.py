"""Telegram Hook — reads messages from chats, saves to source/.

Uses Telethon to connect to Telegram (read-only user account).
Multi-project: one user → many projects → many chats.

Configuration: hooks.yaml
"""

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ======================================================================
# Configuration
# ======================================================================


@dataclass
class HookConfig:
    """Telegram hook configuration from hooks.yaml."""

    @dataclass
    class Chat:
        id: int
        title: str = ""

    @dataclass
    class Project:
        name: str
        spec_path: str
        chats: list["HookConfig.Chat"] = field(default_factory=list)

    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    projects: list[Project] = field(default_factory=list)

    def find_project(self, chat_id: int) -> Project | None:
        """Find project by chat ID."""
        for project in self.projects:
            for chat in project.chats:
                if chat.id == chat_id:
                    return project
        return None

    @classmethod
    def from_file(cls, path: Path) -> "HookConfig":
        """Load configuration from hooks.yaml."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        projects = []
        for pdata in data.get("projects", []):
            chats = [
                cls.Chat(id=c["id"], title=c.get("title", ""))
                for c in pdata.get("chats", [])
            ]
            projects.append(
                cls.Project(
                    name=pdata["name"],
                    spec_path=pdata["spec_path"],
                    chats=chats,
                )
            )

        return cls(
            api_id=data.get("api_id", 0),
            api_hash=data.get("api_hash", ""),
            phone=data.get("phone", ""),
            projects=projects,
        )

    @classmethod
    def default(cls) -> "HookConfig":
        """Create an empty configuration."""
        return cls(api_id=0, api_hash="", phone="")


# ======================================================================
# Message Processor
# ======================================================================


class MessageProcessor:
    """Saves text messages and attachments to the project's source/."""

    def __init__(self, source_dir: Path):
        self._source_dir = Path(source_dir) / "source_raw"
        self._source_dir.mkdir(parents=True, exist_ok=True)

    def save_text_message(
        self,
        chat_title: str,
        sender: str,
        text: str,
        message_id: int,
    ) -> Path:
        """Save a text message as msg_<ts>.txt."""
        ts = int(time.time())
        filename = f"msg_{ts}_{message_id}.md"
        filepath = self._source_dir / filename

        content = (
            f"# Processing Telegram\n\n"
            f"**chat:** {chat_title}\n"
            f"**Saved:** {sender}\n"
            f"**message_id:** {message_id}\n"
            f"**timestamp:** {ts}\n\n"
            f"{text}\n"
        )
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def save_attachment(
        self,
        original_name: str,
        file_path: Path,
        message_id: int,
    ) -> Path:
        """Save an attached file to source/."""
        ts = int(time.time())
        ext = Path(original_name).suffix
        filename = f"attachment_{ts}_{message_id}{ext}"
        dest = self._source_dir / filename
        shutil.copy2(file_path, dest)
        return dest


# ======================================================================
# Telegram Watcher
# ======================================================================


class TelegramWatcher:
    """Listens for messages in Telegram chats, saves to projects' source/.

    Uses Telethon for read-only connection to the user's account.
    Multi-project: one watcher → many projects by chat_id.
    """

    def __init__(self, config: HookConfig):
        self._config = config
        self._processors: dict[int, MessageProcessor] = {}

    async def start(self):
        """Start listening for chats."""
        from telethon import TelegramClient, events

        session_file = str(Path.cwd() / "spec-editor-session")
        client = TelegramClient(
            session_file, self._config.api_id, self._config.api_hash
        )

        @client.on(events.NewMessage)
        async def handler(event):
            await self._handle_message(event, client)

        print(f"Connecting to Telegram...", flush=True)
        try:
            await client.start(phone=self._config.phone)
        except Exception as e:
            if "password" in str(e).lower() or "2fa" in str(e).lower():
                print("[Hook] Enter the verification code from Telegram (2FA)")
            raise
        print(f"[Hook] Connected to Telegram (user: {self._config.phone})")

        # Print the list of tracked chats
        for project in self._config.projects:
            for chat in project.chats:
                print(
                    f"[Hook]  Saved: {chat.title} (id={chat.id}) → {project.name}"
                )

        await client.run_until_disconnected()

    async def fetch_history(self, since, limit: int = 200):
        """Fetch message history from a given date.

        Args:
            since: datetime — date to fetch from
            limit: int — max messages (per chat)
        """
        from datetime import timezone

        from telethon import TelegramClient

        session_file = str(Path.cwd() / "spec-editor-session")
        client = TelegramClient(
            session_file, self._config.api_id, self._config.api_hash
        )
        await client.start(phone=self._config.phone)

        since_utc = since.replace(tzinfo=timezone.utc)
        total = 0

        for project in self._config.projects:
            processor = MessageProcessor(Path(project.spec_path))

            for chat in project.chats:
                print(f"  Saved: {chat.title} (id={chat.id})")
                count = 0

                async for message in client.iter_messages(
                    chat.id, limit=limit, offset_date=since_utc, reverse=True
                ):
                    if message.text:
                        sender = await self._get_sender_for_message(client, message)
                        path = processor.save_text_message(
                            chat_title=chat.title,
                            sender=sender,
                            text=message.text,
                            message_id=message.id,
                        )
                        count += 1

                    if message.media:
                        try:
                            # Determine file name
                            file_name = None
                            if hasattr(message, "file") and message.file:
                                file_name = getattr(message.file, "name", None)
                            if not file_name and hasattr(message, "document"):
                                for attr in getattr(message.document, "attributes", []):
                                    if hasattr(attr, "file_name"):
                                        file_name = attr.file_name
                                        break
                            if not file_name:
                                ext = ".jpg" if hasattr(message, "photo") else ".file"
                                file_name = f"file_{message.id}{ext}"

                            dest_dir = Path(tempfile.gettempdir())
                            downloaded = await client.download_media(
                                message, file=str(dest_dir)
                            )
                            if downloaded:
                                processor.save_attachment(
                                    original_name=file_name,
                                    file_path=Path(downloaded),
                                    message_id=message.id,
                                )
                                count += 1
                        except Exception as exc:
                            print(f"    Error saving: {exc}")

                print(f"    Saved: {count}  messages")
                total += count

        print(f"\nCompleted Saved: {total}  messages")
        await client.disconnect()

    @staticmethod
    async def _get_sender_for_message(client, message) -> str:
        """Get the sender name for a specific message."""
        try:
            sender = await client.get_entity(message.sender_id)
            if hasattr(sender, "first_name"):
                name = sender.first_name or ""
                if hasattr(sender, "last_name") and sender.last_name:
                    name += f" {sender.last_name}"
                return name or str(message.sender_id)
            return str(message.sender_id)
        except Exception:
            return str(message.sender_id)

    async def _handle_message(self, event, client):
        """Handle a new message."""
        chat_id = event.chat_id
        project = self._config.find_project(chat_id)
        if project is None:
            return  # chat is not linked to a project

        # Get or create processor for the project
        if chat_id not in self._processors:
            self._processors[chat_id] = MessageProcessor(Path(project.spec_path))

        processor = self._processors[chat_id]
        message = event.message
        sender = await self._get_sender_name(event)

        # Text message
        if message.text:
            path = processor.save_text_message(
                chat_title=chat_id,  # TODO: get chat title
                sender=sender,
                text=message.text,
                message_id=message.id,
            )
            print(f"[Hook] {project.name}: message received from {sender} → {path.name}")

        # Attachments (photos, documents)
        if message.media:
            try:
                file_name = None
                if hasattr(message, "file") and message.file:
                    file_name = getattr(message.file, "name", None)
                if not file_name and hasattr(message, "document"):
                    for attr in getattr(message.document, "attributes", []):
                        if hasattr(attr, "file_name"):
                            file_name = attr.file_name
                            break
                if not file_name:
                    ext = ".jpg" if hasattr(message, "photo") else ".file"
                    file_name = f"file_{message.id}{ext}"

                dest_dir = Path(tempfile.gettempdir())
                downloaded = await client.download_media(message, file=str(dest_dir))
                if downloaded:
                    path = processor.save_attachment(
                        original_name=file_name,
                        file_path=Path(downloaded),
                        message_id=message.id,
                    )
                    print(f"[Hook] {project.name}: message received from {sender} → {path.name}")
            except Exception as exc:
                print(f"[Hook] Error saving: {exc}")

    @staticmethod
    async def _get_sender_name(event) -> str:
        """Get the sender's name."""
        sender = await event.get_sender()
        if hasattr(sender, "first_name"):
            name = sender.first_name or ""
            if hasattr(sender, "last_name") and sender.last_name:
                name += f" {sender.last_name}"
            return name or str(sender.id)
        return str(sender.id)
