"""Telegram Hook tests — message handling and file saving."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestMessageProcessor:
    """MessageProcessor: handles text and attachments."""

    def test_saves_text_message(self, tmp_path):
        from src.ingestion.telegram_hook import MessageProcessor

        project_dir = tmp_path / "project"
        processor = MessageProcessor(project_dir)

        result = processor.save_text_message(
            chat_title="Main Chat",
            sender="John",
            text="Need to add PDF export",
            message_id=123,
        )
        assert result is not None
        assert "msg_" in result.name
        assert result.exists()
        # Check path — should be in sources_raw/
        assert "sources_raw" in str(result)
        content = result.read_text(encoding="utf-8")
        assert "Main Chat" in content
        assert "John" in content
        assert "PDF export" in content

    def test_saves_attachment(self, tmp_path):
        from src.ingestion.telegram_hook import MessageProcessor

        project_dir = tmp_path / "project"
        processor = MessageProcessor(project_dir)

        # Create a temporary source file
        fake_file = tmp_path / "downloaded.pdf"
        fake_file.write_text("test content")

        result = processor.save_attachment(
            original_name="Requirements_v2.pdf",
            file_path=fake_file,
            message_id=456,
        )
        assert result is not None
        assert "attachment" in result.name
        assert "sources_raw" in str(result)


class TestHookConfig:
    """HookConfig: loading hooks.yaml."""

    def test_loads_valid_config(self, tmp_path):
        from src.ingestion.telegram_hook import HookConfig

        config_path = tmp_path / "hooks.yaml"
        config_path.write_text("""
api_id: 12345
api_hash: "abc123"
phone: "+1234567890"
projects:
  - name: gen-panel
    spec_path: /tmp/gen-panel
    chats:
      - id: -100111
        title: "Main Chat"
      - id: -100222
        title: "Feedback Chat"
""")
        config = HookConfig.from_file(config_path)
        assert config.api_id == 12345
        assert config.phone == "+1234567890"
        assert len(config.projects) == 1
        assert len(config.projects[0].chats) == 2

    def test_finds_project_for_chat(self, tmp_path):
        from src.ingestion.telegram_hook import HookConfig

        config = HookConfig(
            api_id=123,
            api_hash="x",
            phone="+1",
            projects=[
                HookConfig.Project(
                    name="p1",
                    spec_path="/tmp/p1",
                    chats=[
                        HookConfig.Chat(id=-100, title="main"),
                    ],
                ),
            ],
        )
        project = config.find_project(-100)
        assert project is not None
        assert project.name == "p1"

    def test_returns_none_for_unknown_chat(self, tmp_path):
        from src.ingestion.telegram_hook import HookConfig

        config = HookConfig(
            api_id=123,
            api_hash="x",
            phone="+1",
            projects=[],
        )
        assert config.find_project(-999) is None

    def test_default_path(self):
        from src.ingestion.telegram_hook import HookConfig

        config = HookConfig.default()
        assert config.api_id == 0
        assert config.projects == []
