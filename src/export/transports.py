"""Transports: result delivery."""

from pathlib import Path

from src.export.pipeline import Transport


class FileTransport(Transport):
    """Saves the result to a file."""

    def send(self, content: str, config: dict) -> str:
        path = Path(config.get("output", "export.md"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path.resolve())


class StdoutTransport(Transport):
    """Outputs the result to stdout."""

    def send(self, content: str, config: dict) -> str:
        print(content)
        return "stdout"


class HttpTransport(Transport):
    """Sends the result via HTTP POST."""

    def send(self, content: str, config: dict) -> str:
        import urllib.request

        url = config.get("url", "")
        if not url:
            return "error: no url in config"

        data = content.encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method=config.get("method", "POST")
        )
        req.add_header("Content-Type", config.get("content_type", "text/plain"))
        if "auth" in config:
            req.add_header("Authorization", config["auth"])

        with urllib.request.urlopen(req) as resp:
            return f"{url} → {resp.status}"
