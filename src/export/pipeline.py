"""Export pipeline: abstract base classes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExportData:
    """Gathered data for export."""

    doc_title: str = ""
    sections: list["ExportSection"] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ExportSection:
    """A single export section."""

    title: str = ""
    number: str = ""
    description: str = ""
    diagram: str = ""  # mermaid diagram
    elements: list["ExportElement"] = field(default_factory=list)


@dataclass
class ExportElement:
    """A single element in a section."""

    id: str = ""
    title: str = ""
    content: str = ""
    aspect: str = ""
    element_type: str = ""
    status: str = ""
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    relationships: dict = field(default_factory=dict)
    group_key: str = ""  # for grouping within a section
    back_refs: dict = field(default_factory=dict)  # who references this element
    inline_steps: list = field(default_factory=list)  # scenario steps for UI


class Gatherer(ABC):
    """Gathers data from storage into ExportData."""

    @abstractmethod
    def gather(
        self, storage, template_path: Path, project_path: Path
    ) -> ExportData: ...


class Formatter(ABC):
    """Formats ExportData into a string (md, html, json...)."""

    @abstractmethod
    def format(self, data: ExportData, config: dict | None = None) -> str: ...


class Transport(ABC):
    """Delivers the result to the outside world."""

    @abstractmethod
    def send(self, content: str, config: dict) -> str:
        """Returns a description: where it was sent."""
        ...


class ExportPipeline:
    """Gathers, formats, delivers."""

    def __init__(
        self, gatherer: Gatherer, formatter: Formatter, transport: Transport
    ) -> None:
        self._gatherer = gatherer
        self._formatter = formatter
        self._transport = transport

    def run(
        self,
        storage,
        template_path: Path,
        project_path: Path,
        format_config: dict | None = None,
        transport_config: dict | None = None,
    ) -> tuple[str, ExportData]:
        """Run the pipeline. Returns (delivery result, data)."""
        data = self._gatherer.gather(storage, template_path, project_path)
        content = self._formatter.format(data, format_config or {})
        result = self._transport.send(content, transport_config or {})
        return result, data


def pipeline_from_config(config: dict, storage, project_path: Path) -> ExportPipeline:
    """Create a pipeline from config (gatherer + formatter + transport)."""
    from src.export.formatters import Jinja2Formatter, MarkdownFormatter
    from src.export.gatherers import SRSGatherer
    from src.export.transports import FileTransport, HttpTransport, StdoutTransport

    gatherer_name = config.get("gatherer", "srs")
    formatter_name = config.get("formatter", "markdown")
    transport_name = config.get("transport", "file")

    gatherer = {"srs": SRSGatherer()}.get(gatherer_name, SRSGatherer())
    formatter = {
        "markdown": MarkdownFormatter(),
        "jinja2": Jinja2Formatter(),
    }.get(formatter_name, MarkdownFormatter())
    transport = {
        "file": FileTransport(),
        "stdout": StdoutTransport(),
        "http": HttpTransport(),
    }.get(transport_name, FileTransport())

    return ExportPipeline(
        gatherer=gatherer,
        formatter=formatter,
        transport=transport,
    )
