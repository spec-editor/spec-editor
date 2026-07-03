"""Plugin API — extension points for spec-editor-core.

Plugins register via setuptools entry_points:

    [project.entry-points."spec_editor.plugins"]
    my_plugin = "my_package.plugin:MyPlugin"

For development (without installing plugins as packages), plugins
are discovered from ``plugins/`` directory automatically.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from pathlib import Path
from typing import Any


class SpecEditorPlugin(ABC):
    """Base class for spec-editor plugins.

    Override any method to extend spec-editor-core functionality.
    All methods return empty results by default.
    """

    def register_mcp_tools(
        self, storage, project_path: str
    ) -> dict[str, Callable[..., Any]]:
        """Return additional MCP tool name → handler mappings.

        These are appended to the agent's tool_handlers dict and
        exposed via MCP server.
        """
        return {}

    def register_mcp_tool_schemas(self) -> list[dict]:
        """Return MCP tool schemas (name, description, inputSchema).

        These are appended to the MCP tools/list response.
        """
        return []

    def register_cli_commands(self, cli_group) -> None:
        """Register additional Click commands on the spec-editor CLI group.

        Called from cli/commands.py after core commands are loaded.
        """
        pass

    def register_agent_roles(self) -> list[dict]:
        """Return additional agent role definitions.

        Each dict: {name, writable, prompt, allowed_tools}
        """
        return []

    def on_run(
        self,
        mode: str,
        project_path: Path,
        storage,
        method,
        agents_config,
        settings,
        initial_task: str,
    ) -> bool:
        """Handle a run request for non-core modes.

        Called when ``spec-editor run`` is invoked with a mode
        other than "spec" (e.g., "cycle-graph", "cycle", "coding").

        Returns:
            True if the plugin handled the run (core should not proceed).
            False if the mode is not recognized by this plugin.
        """
        return False


# ── Plugin discovery ──


def discover_plugins() -> list[SpecEditorPlugin]:
    """Discover all installed plugins.

    Priority:
    1. setuptools entry_points (production)
    2. plugins/ directory with local imports (development)
    """
    plugins: list[SpecEditorPlugin] = []

    # 1. setuptools entry_points
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="spec_editor.plugins"):
            try:
                plugin = ep.load()()
                plugins.append(plugin)
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "Failed to load plugin: %s", ep.name, exc_info=True
                )
    except ImportError:
        pass

    # 2. Development: plugins/ directory (import plugins without installing)
    _discover_dev_plugins(plugins)

    return plugins


def _discover_dev_plugins(plugins: list[SpecEditorPlugin]) -> None:
    """Discover plugins from local plugins/ directories (dev mode + user projects).

    Searches (in order):
    1. Package root ``plugins/`` — monorepo dev (e.g., spec-editor2/plugins/)
    2. Current working directory ``plugins/`` — user project with local plugin
    3. Parent/grandparent of CWD — typical project nesting
    """
    import importlib
    import os
    import sys

    search_roots: list[Path] = []

    # 1. Package root (for monorepo dev: spec-editor2/plugins/)
    pkg_root = Path(__file__).resolve().parent.parent
    search_roots.append(pkg_root / "plugins")

    # 2. Current working directory + ancestors (user project context)
    cwd = Path.cwd()
    for candidate in [cwd, cwd.parent, cwd.parent.parent]:
        pd = candidate / "plugins"
        if pd not in search_roots:
            search_roots.append(pd)

    # 3. SPEC_EDITOR_PLUGINS_PATH env var (explicit override)
    env_path = os.environ.get("SPEC_EDITOR_PLUGINS_PATH", "")
    if env_path:
        search_roots.insert(0, Path(env_path))

    for plugins_dir in search_roots:
        if not plugins_dir.is_dir():
            continue
        _scan_plugins_dir(plugins_dir, plugins)

    for plugins_dir in search_roots:
        if not plugins_dir.is_dir():
            continue
        _scan_plugins_dir(plugins_dir, plugins)


def _scan_plugins_dir(plugins_dir: Path, plugins: list[SpecEditorPlugin]) -> None:
    """Scan a single plugins/ directory and load discovered plugins."""
    import importlib
    import sys

    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue

        plugin_py = plugin_dir / "src" / "spec_editor_cycle" / "plugin.py"
        if not plugin_py.is_file():
            # Also try plugin.py directly
            plugin_py = plugin_dir / "plugin.py"
        if not plugin_py.is_file():
            continue

        # Add plugin package to sys.path
        plugin_src = plugin_dir / "src"
        if plugin_src.is_dir() and str(plugin_src) not in sys.path:
            sys.path.insert(0, str(plugin_src))

        # Determine module path
        if (plugin_dir / "src" / "spec_editor_cycle" / "plugin.py").is_file():
            module_path = "spec_editor_cycle.plugin"
        else:
            module_path = "plugin"

        try:
            mod = importlib.import_module(module_path)
            # Find the first SpecEditorPlugin subclass
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, SpecEditorPlugin)
                    and attr is not SpecEditorPlugin
                ):
                    plugins.append(attr())
                    break
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to load dev plugin from: %s", plugin_dir, exc_info=True
            )


# ── Cached access ──

_plugins_cache: list[SpecEditorPlugin] | None = None


def get_plugins() -> list[SpecEditorPlugin]:
    """Return cached list of installed plugins."""
    global _plugins_cache
    if _plugins_cache is None:
        _plugins_cache = discover_plugins()

        # Register cleanup on exit
        import atexit

        def _clear():
            global _plugins_cache
            _plugins_cache = None

        atexit.register(_clear)
    return _plugins_cache
