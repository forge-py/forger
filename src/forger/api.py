"""Public API for forger.py project extensions.

This module provides the build-time API that projects use in their forger.py
to declare dependencies that cannot be inferred automatically.

```python
from forger import defineConfig

forger_config = defineConfig({
    "entry": "manage.py",
    "include": ["templates/**/*", "static/**/*"],
    "exclude": ["**/__pycache__"],
    "optimizers": {"django": {"settings_module": "myblog.settings"}},
    "targets": ["linux-x64", "windows-x64"],
})
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


class OptimizerOptions(TypedDict, total=False):
    """Typed options passed to a framework optimizer."""

    settings_module: str
    root_urlconf: str
    templates_dir: str
    static_dir: str
    extra: dict[str, Any]


class ForgerConfigDict(TypedDict, total=False):
    """Typed dict for the defineConfig input parameter.

    All keys are optional — missing keys fall back to defaults on
    ``ForgerConfig``.
    """

    entry: str
    project: str
    include: list[str]
    exclude: list[str]
    optimizers: dict[str, OptimizerOptions]
    targets: list[str]
    metadata: dict[str, str | int]
    dist_dir: str
    minify_names: bool | dict[str, Any]


@dataclass
class ForgerConfig:
    """Top-level build configuration.

    Attributes:
        entry: Entry-point module or script (e.g. ``"manage.py"``).
        project: Project name (for diagnostics).
        include: Glob patterns for files/directories to bundle.
        exclude: Glob patterns for files/directories to skip.
        optimizers: Framework optimizer configurations.
        targets: Target platforms to build for.
        metadata: Arbitrary key-value metadata.
        dist_dir: Output directory name (default: ``"dist"``).
    """

    entry: str = ""
    project: str = ""
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    optimizers: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    dist_dir: str = "dist"
    # Name minification toggle (MINIFIER.md §1). Off by default; the
    # compiler must NEVER enable it implicitly. Either a bool (True
    # means "minify with defaults") or a dict (e.g.
    # ``{"enabled": True, "keep": {"myapp.api.create_user"}}``).
    minify_names: bool | dict[str, Any] = False


# ---------------------------------------------------------------------------
# Internal build context (populated by forger.py at compile time)
# ---------------------------------------------------------------------------


class _ForgerContext:
    """Holds build-time state shared between forger.py and the compiler."""

    def __init__(self) -> None:
        self._config: ForgerConfig | None = None
        self._graph: Any = None
        self._plugins: list[Any] | None = None

    @property
    def config(self) -> ForgerConfig | None:
        return self._config

    def set_config(self, cfg: ForgerConfig) -> None:
        self._config = cfg

    @property
    def graph(self) -> Any:
        """The dependency graph being built.

        Exposed so that ``forger.py`` can inspect and manipulate the
        graph directly — adding nodes, edges, or marking modules as
        required.
        """
        return self._graph

    def set_graph(self, graph: Any) -> None:
        self._graph = graph

    @property
    def plugins(self) -> list[Any] | None:
        return self._plugins

    def set_plugins(self, plugins: list[Any]) -> None:
        _validate_plugins(plugins)
        self._plugins = plugins


def _validate_plugins(plugins: list[Any]) -> None:
    """Validate that all plugins inherit from BasePlugin.

    Raises
    ------
    TypeError
        If a plugin does not inherit from ``BasePlugin``.
    """
    from forger.optimizer import BasePlugin

    for idx, plugin in enumerate(plugins):
        if not isinstance(plugin, BasePlugin):
            raise TypeError(
                f"Plugin at index {idx} ({type(plugin).__name__!r}) is not a "
                f"valid Forger plugin. It must inherit from BasePlugin."
            )
        if type(plugin) is BasePlugin:
            raise TypeError(
                f"Plugin at index {idx} is the base BasePlugin class. "
                f"Subclass it and register your implementation."
            )


# Global context instance — populated by forger.py during build.
_context: _ForgerContext | None = None


def _get_context() -> _ForgerContext:
    global _context
    if _context is None:
        _context = _ForgerContext()
    return _context


# ---------------------------------------------------------------------------
# Declarative API
# ---------------------------------------------------------------------------


def defineConfig(  # noqa: N802
    config: ForgerConfigDict | ForgerConfig,
) -> ForgerConfig:
    """Define a Forger build configuration.

    Accepts either a ``ForgerConfig`` instance or a typed dict that will be
    used to construct one.  The returned config is stored in the global
    forger context so the compiler can read it at build time.

    Args:
        config: A ``ForgerConfig`` or ``ForgerConfigDict`` with keys
            matching ``ForgerConfig`` fields.

    Returns:
        The resolved ``ForgerConfig``.

    Example:
        ```python
        from forger import defineConfig

        forger_config = defineConfig({
            "entry": "manage.py",
            "project": "myblog",
            "include": [
                "templates/**/*",
                "static/**/*",
                "locale/**/*",
            ],
            "exclude": [
                "**/__pycache__",
                "*.pyc",
                "*.sqlite3",
            ],
            "optimizers": {
                "django": {
                    "settings_module": "myblog.settings",
                },
            },
            "targets": ["linux-x64", "windows-x64"],
        })
        ```
    """
    if isinstance(config, ForgerConfig):
        cfg = config
    else:
        cfg = ForgerConfig(
            entry=config.get("entry", ""),
            project=config.get("project", ""),
            include=config.get("include", []),
            exclude=config.get("exclude", []),
            optimizers=config.get("optimizers", {}),
            targets=config.get("targets", []),
            metadata={k: str(v) for k, v in config.get("metadata", {}).items()},
            dist_dir=config.get("dist_dir", "dist"),
            minify_names=config.get("minify_names", False),
        )

    _get_context().set_config(cfg)
    return cfg


def get_context() -> _ForgerContext:
    """Get the current forger build context (for advanced usage)."""
    return _get_context()


# ---------------------------------------------------------------------------
# Plugin API — helpers for forger.py to manipulate the dependency graph
# ---------------------------------------------------------------------------


def include(pattern: str) -> None:
    """Declare a glob pattern of files to include in the build.

    The pattern is matched against relative paths from the project root.

    Example:
        ```python
        from forger import include

        include("templates/**/*")
        include("static/**/*")
        ```
    """
    cfg = _get_context().config
    if cfg is not None:
        cfg.include.append(pattern)


def include_module(module_id: str) -> None:
    """Explicitly include a Python module in the dependency graph.

    Adds a node to the graph and marks it as required.

    Example:
        ```python
        from forger import include_module

        include_module("myapp.plugins.foo")
        ```
    """
    graph = _get_context().graph
    if graph is None:
        return
    from forger.core import DependencyNode, NodeType

    node = graph.get_node(module_id)
    if node is None:
        graph.add_node(DependencyNode.new(module_id, NodeType.PythonModule))
        node = graph.get_node(module_id)
    if node is not None:
        node.required = True


def include_resource(path: str) -> None:
    """Explicitly include a resource file in the build.

    Adds a resource node to the dependency graph.

    Example:
        ```python
        from forger import include_resource

        include_resource("data/schema.json")
        ```
    """
    graph = _get_context().graph
    if graph is None:
        return
    from forger.core import DependencyNode, NodeType

    graph.add_node(
        DependencyNode.new(path, NodeType.Resource)
        .with_metadata("source", "forger.py")
        .with_metadata("included_by", "include_resource")
    )
