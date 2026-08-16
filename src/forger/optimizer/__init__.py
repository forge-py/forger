"""Plugin framework for the Forger compiler.

Provides:
- Plugin system with lifecycle hooks (ABC-based)
- Plugin context for controlled graph access
- PluginRunner for executing lifecycle hooks
- Plugin registration with inheritance validation
- Hook filters (include/exclude patterns for id-based hooks)

This module mirrors the Rolldown plugin API:
https://rolldown.rs/apis/plugin-api
"""

from __future__ import annotations

import abc
import ast as ast_module
import enum
import inspect
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence
    from forger.core import (  # type: ignore[attr-defined]
        DependencyGraph,
        DependencyNode,
        DependencyEdge,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin lifecycle ordering (enforce)
# ---------------------------------------------------------------------------


class PluginOrder(str, enum.Enum):
    """Plugin ordering categories (``enforce``).

    Controls invocation order: pre plugins run first, then normal,
    then post.  Being a ``str`` enum means values serialize cleanly
    to JSON/TOML without calling ``.value``.
    """

    PRE = "pre"
    NORMAL = "normal"
    POST = "post"


# ---------------------------------------------------------------------------
# Hook kind classification (Rolldown convention)
# ---------------------------------------------------------------------------


class HookKind(str, enum.Enum):
    """Describes how a hook is invoked across multiple plugins.

    Rolldown defines five hook kinds:
    - ``async``: The hook may return a Promise (or awaitable).
    - ``sync``: The hook is synchronous.
    - ``first``: Hooks run sequentially until one returns a non-null value.
    - ``sequential``: All plugins run, in order, regardless of return value.
    - ``parallel``: All plugins run, and async hooks don't block each other.
    """

    ASYNC = "async"
    SYNC = "sync"
    FIRST = "first"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


# ---------------------------------------------------------------------------
# Hook filter support (Rolldown convention)
# ---------------------------------------------------------------------------


class HookFilter(TypedDict, total=False):
    """Filter criteria for id-based hooks (resolve_id, load, transform).

    Only hooks that operate on module IDs support filters. The filter
    is a regex pattern; the hook is only invoked when the module ID
    matches.

    Example
    -------
    .. code-block:: python

        class MyPlugin(BasePlugin):
            name = "my-plugin"
            transform_filter = re.compile(r"[.]py$")

            def transform(self, module_id, code, *, context):
                ...
    """

    id: str | re.Pattern[str]


def _matches_filter(pattern: re.Pattern[str] | str | None, value: str) -> bool:
    """Check if a value matches a hook filter pattern."""
    if pattern is None:
        return True
    if isinstance(pattern, re.Pattern):
        return bool(pattern.search(value))
    return pattern in value


# ---------------------------------------------------------------------------
# Plugin context
# ---------------------------------------------------------------------------

@dataclass
class PluginContext:
    """Controlled access to compiler state for plugins.

    Plugins interact with the compiler exclusively through this context.
    Internal compiler state is not exposed directly.
    """

    project_root: Path
    config: dict = field(default_factory=dict)
    _graph: DependencyGraph | None = None
    _logger: logging.Logger = field(default_factory=lambda: logger)
    _retain_symbols: set[str] = field(default_factory=set)
    _retain_modules: set[str] = field(default_factory=set)
    _retain_resources: set[str] = field(default_factory=set)
    _dynamic_symbols: set[str] = field(default_factory=set)
    _unoptimized_dependencies: set[str] = field(default_factory=set)
    _source_files: list[Path] = field(default_factory=list)
    _current_plugin_name: str | None = None

    @property
    def graph(self) -> DependencyGraph:
        if self._graph is None:
            from forger.core import DependencyGraph
            self._graph = DependencyGraph()
        return self._graph

    def set_graph(self, graph: DependencyGraph) -> None:
        self._graph = graph

    def log(self, level: int, message: str, *args) -> None:
        self._logger.log(level, message, *args)

    def debug(self, message: str, *args) -> None:
        self._logger.debug(message, *args)

    def info(self, message: str, *args) -> None:
        self._logger.info(message, *args)

    def warning(self, message: str, *args) -> None:
        self._logger.warning(message, *args)

    # -- Context methods --

    def warn(self, message: str) -> None:
        """Emit a warning message.

        Warnings are logged at WARNING level and appear in the build output.
        Unlike ``error()``, warnings do not halt the build.
        """
        self._logger.warning("[plugin:%s] %s", self._current_plugin_name or "", message)

    def error(self, message: str) -> None:
        """Emit a build error.

        Raises a RuntimeError to halt the build immediately.
        """
        raise RuntimeError(
            f"[plugin:{self._current_plugin_name or ''}] {message}"
        )

    def emit_file(self, file: dict[str, Any]) -> str:
        """Emit a file to the output bundle.

        This mirrors the Rolldown ``this.emitFile()`` API.
        Files are written during the ``generate_bundle`` phase.

        Args:
            file: A dict describing the file. Supported keys:
                - ``type``: ``"asset"`` for static assets.
                - ``name``: Output filename (e.g., ``"style.css"``).
                - ``source``: File content as a string or bytes.

        Returns:
            A generated filename reference that can be used in code.

        Example
        -------
        .. code-block:: python

            ref = context.emit_file({
                "type": "asset",
                "name": "data.json",
                "source": '{"key": "value"}',
            })
        """
        if not hasattr(self, "_emitted_files"):
            self._emitted_files: list[dict[str, Any]] = []

        name = file.get("name", "unnamed")
        # Generate a stable reference name
        ref = f"__forger_asset__{len(self._emitted_files)}_{name}"
        self._emitted_files.append(file)
        return ref

    @property
    def emitted_files(self) -> list[dict[str, Any]]:
        """Return all files emitted via ``emit_file()``."""
        return getattr(self, "_emitted_files", [])

    # -- Module info API --

    def _get_module_info_store(self) -> dict[str, dict[str, Any]]:
        """Lazy-initialize the module info storage."""
        if not hasattr(self, "_module_info"):
            self._module_info: dict[str, dict[str, Any]] = {}
        return self._module_info

    def get_module_info(self, module_id: str) -> dict[str, Any] | None:
        """Get cached metadata for a module.

        This mirrors the Rolldown ``this.getModuleInfo()`` API.
        Returns a dict with module metadata or None if not found.

        Args:
            module_id: The module identifier to look up.

        Returns:
            Dict with keys: ``id``, ``code``, ``ast``, ``is_entry_point``,
            ``is_external``, or None if the module has no cached info.
        """
        store = self._get_module_info_store()
        if module_id in store:
            return store[module_id]

        # Auto-populate from graph node if available
        node = self.graph.get_node(module_id)
        if node is None:
            return None

        info = {
            "id": module_id,
            "code": node.get_content(),
            "is_entry_point": node.entry_point,
            "is_external": node.external,
            "meta": dict(node.metadata),
        }
        store[module_id] = info
        return info

    def set_module_info(self, module_id: str, info: dict[str, Any]) -> None:
        """Set or update metadata for a module.

        This mirrors the Rolldown ``this.setModuleInfo()`` API.
        Allows plugins to attach arbitrary metadata to modules.

        Args:
            module_id: The module identifier.
            info: Metadata dict to merge into the existing info.
        """
        store = self._get_module_info_store()
        if module_id not in store:
            # Initialize from graph node if possible
            existing = self.get_module_info(module_id)
            if existing is None:
                existing = {"id": module_id}
                store[module_id] = existing

        store[module_id].update(info)

    # -- Parse API --

    def parse(self, source: str, options: dict[str, Any] | None = None) -> ast_module.AST:
        """Parse source code into an AST.

        This mirrors the Rolldown ``this.parse()`` API.

        Args:
            source: The source code to parse.
            options: Optional parse options. Supported keys:
                - ``ecma_version``: Ignored (Python always uses latest).

        Returns:
            The root AST node.

        Raises:
            SyntaxError: If the source code is invalid.
        """
        options = options or {}
        filename = options.get("filename", "<unknown>")
        try:
            return ast_module.parse(source, filename=filename)
        except SyntaxError:
            raise

    # -- Resolve API --

    def resolve(
        self,
        specifier: str,
        importer: str | None = None,
        *,
        skip_self: bool = False,
    ) -> str | None:
        """Resolve a module specifier to a module ID.

        This mirrors the Rolldown ``this.resolve()`` API.
        Delegates to the plugin chain's ``resolve_id`` hooks.

        Args:
            specifier: The module specifier to resolve.
            importer: Optional importer ID for relative resolution.
            skip_self: If True, skip the calling plugin to avoid recursion.

        Returns:
            The resolved module ID or None if unresolvable.
        """
        # Resolution is handled by the runner; this is a convenience
        # that delegates to the graph's node lookup
        if specifier in self.graph.nodes:
            return specifier

        # Try to resolve as a path relative to the project root
        try:
            p = Path(specifier)
            if p.is_absolute():
                rel = p.relative_to(self.project_root)
                return str(rel)
        except ValueError:
            pass

        return None

    # -- Graph mutation API --

    def add_node(self, node: DependencyNode) -> bool:
        """Add a node to the dependency graph."""
        return self.graph.add_node(node)

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add an edge to the dependency graph."""
        self.graph.add_edge(edge)

    def add_resource_dependency(
        self, module_id: str, resource_path: str
    ) -> None:
        """Add a resource dependency from a module to a resource path."""
        from forger.core import (
            DependencyEdge,
            DependencyNode,
            EdgeType,
            NodeType,
        )

        resource_node = DependencyNode.new(
            resource_path, NodeType.Resource
        ).with_metadata("discovered_by", "plugin")
        self.add_node(resource_node)

        edge = DependencyEdge.new(
            module_id, resource_path, EdgeType.ResourceDependency
        )
        self.add_edge(edge)

    def mark_root(self, node_id: str) -> None:
        """Mark a node as a root (always reachable)."""
        self.graph.add_entry_point(node_id)

    # -- Retention API --

    def retain_symbol(self, symbol_id: str) -> None:
        """Mark a symbol as always reachable."""
        self._retain_symbols.add(symbol_id)

    def retain_module(self, module_id: str) -> None:
        """Mark a module as always reachable."""
        self._retain_modules.add(module_id)

    def retain_resource(self, resource_path: str) -> None:
        """Mark a resource as always reachable."""
        self._retain_resources.add(resource_path)

    def mark_dynamic(self, symbol_id: str) -> None:
        """Mark a symbol as dynamically accessed (conservative retention)."""
        self._dynamic_symbols.add(symbol_id)

    def is_reachable(self, symbol_id: str) -> bool:
        """Check if a symbol is in the retain set."""
        return symbol_id in self._retain_symbols

    # -- Node content API --

    def get_node_content(self, node_id: str) -> str | None:
        """Read the source content of a graph node.

        Returns None if the node does not exist or has no content.
        """
        node = self.graph.get_node(node_id)
        if node is None:
            return None
        return node.get_content()

    def set_node_content(self, node_id: str, content: str) -> None:
        """Modify the source content of a graph node."""
        node = self.graph.get_node_mut(node_id)
        if node is not None:
            node.set_content(content)

    # -- Dead code API --

    def is_dead(self, node_id: str) -> bool:
        """Check if a node is dead (unreachable and not unoptimized)."""
        return self.graph.is_dead(node_id)

    def find_dead_nodes(self) -> list[str]:
        """Return all dead (unreachable) node IDs."""
        return self.graph.find_dead_nodes()

    def delete_node(self, node_id: str) -> bool:
        """Remove a node from the graph. Returns True if it existed."""
        return self.graph.delete_node(node_id)

    # -- Unoptimized dependency API --

    def add_unoptimized_dependency(self, node_id: str, node_type: str | None = None) -> None:
        """Declare an unoptimized dependency that bypasses tree-shaking.

        Plugins use this to add modules or resources that must be retained
        regardless of reachability analysis. The node will be marked as
        ``unoptimized=True`` so it survives pruning passes.

        Args:
            node_id: The module or resource identifier.
            node_type: Optional node type (defaults to Resource for paths,
                       PythonModule for dotted identifiers).
        """
        from forger.core import DependencyEdge, DependencyNode, EdgeType, NodeType  # noqa: F401

        self._unoptimized_dependencies.add(node_id)

        # Ensure the node exists and is marked unoptimized
        existing = self.graph.get_node(node_id)
        if existing is not None:
            existing.unoptimized = True
            return

        # Infer node type from the id format
        if node_type is not None:
            inferred_type = node_type
        elif "/" in node_id or "\\" in node_id or node_id.endswith((".html", ".css", ".js", ".json", ".png", ".jpg", ".svg")):
            inferred_type = NodeType.Resource
        else:
            inferred_type = NodeType.PythonModule

        self.add_node(DependencyNode.new(node_id, inferred_type).mark_unoptimized())

    def add_unoptimized_resource(self, resource_path: str, source_module: str | None = None) -> None:
        """Add a resource file as unoptimized dependency.

        Shorthand for ``add_unoptimized_dependency`` with Resource type.
        Also creates a ResourceDependency edge from the source module if given.
        """
        from forger.core import DependencyEdge, DependencyNode, EdgeType, NodeType

        self._unoptimized_dependencies.add(resource_path)

        existing = self.graph.get_node(resource_path)
        if existing is None:
            self.add_node(DependencyNode.new(resource_path, NodeType.Resource)
                          .mark_unoptimized()
                          .with_metadata("source", "plugin"))
        else:
            existing.unoptimized = True

        if source_module:
            edge = DependencyEdge.new(source_module, resource_path, EdgeType.UnoptimizedDependency)
            self.add_edge(edge)

    # -- Package detection --

    def has_package(self, name: str) -> bool:
        """Check if a package is installed."""
        import importlib.metadata

        try:
            importlib.metadata.distribution(name)
            return True
        except importlib.metadata.PackageNotFoundError:
            return False

    @property
    def retain_symbols(self) -> set[str]:
        return set(self._retain_symbols)

    @property
    def retain_modules(self) -> set[str]:
        return set(self._retain_modules)

    @property
    def retain_resources(self) -> set[str]:
        return set(self._retain_resources)

    @property
    def dynamic_symbols(self) -> set[str]:
        return set(self._dynamic_symbols)

    @property
    def unoptimized_dependencies(self) -> set[str]:
        return set(self._unoptimized_dependencies)


# ---------------------------------------------------------------------------
# BasePlugin — ABC
# ---------------------------------------------------------------------------

class BasePlugin(abc.ABC):
    """Abstract base class for all Forger plugins.

    Plugins are objects with a ``name`` and optional lifecycle hooks.
    Hooks are called at various stages of the build pipeline. A plugin only
    implements the hooks it needs — all hooks default to no-op.

    The ``enforce`` attribute controls invocation order:

    - ``"pre"`` — runs before normal plugins
    - ``"normal"`` (default) — standard order
    - ``"post"`` — runs after normal plugins (for optimizers, codegen)

    Example
    -------
    .. code-block:: python

        class MyPlugin(BasePlugin):
            name = "my-plugin"
            enforce = "pre"

            def build_start(self, *, context: PluginContext) -> None:
                context.info("hello from my-plugin")

            def transform(self, module_id: str, code: str, *, context: PluginContext) -> str:
                return code.replace("old", "new")

    Hooks
    -----
    config: Modify compiler config before resolution.
    config_resolved: Read and store the final resolved config.
    build_start: Called at the start of each build.
    resolve_id: Resolve a module specifier to an ID.
    load: Load module source by ID.
    transform: Transform module source code.
    module_parsed: Called when AST parsing for a module completes.
    build_end: Called at the end of each build (cleanup).
    render_start: First hook of the output generation phase.
    render_chunk: Transform a chunk before it's written.
    generate_bundle: Modify the final output bundle.
    write_bundle: Called after the output bundle is written.
    close_bundle: Final cleanup after all output is written.
    """

    #: Plugin name — appears in logs and error messages (required).
    name: str = "<unnamed>"

    #: Invocation tier — ``"pre"``, ``"normal"``, or ``"post"``.
    enforce: PluginOrder | str = PluginOrder.NORMAL

    #: When to apply this plugin — ``"build"``, ``"serve"``, or ``None`` (always).
    apply: str | None = None

    #: Optional version string for inter-plugin communication.
    version: str | None = None

    #: Optional public API object shared with other plugins.
    api: Any = None

    #: Optional filter for ``transform`` hook — only match matching module IDs.
    transform_filter: re.Pattern[str] | str | None = None

    #: Optional filter for ``resolve_id`` hook — only match matching specifiers.
    resolve_id_filter: re.Pattern[str] | str | None = None

    #: Optional filter for ``load`` hook — only match matching module IDs.
    load_filter: re.Pattern[str] | str | None = None

    def __init_subclass__(cls, /, **kwargs) -> None:
        """Validate that subclasses are proper BasePlugin derivatives."""
        super().__init_subclass__(**kwargs)
        if cls is BasePlugin:
            return
        # Verify the class is a proper subclass (not a fake mixin)
        if not issubclass(cls, BasePlugin):
            raise TypeError(f"{cls.__name__} must inherit from BasePlugin")

    # ------------------------------------------------------------------
    # Lifecycle hooks (all optional, default to no-op)
    # ------------------------------------------------------------------

    def config(self, *, context: PluginContext) -> None:
        """Modify compiler config before resolution."""

    def config_resolved(self, *, context: PluginContext) -> None:
        """Read and store the final resolved config."""

    def build_start(self, *, context: PluginContext) -> None:
        """Called at the start of each build."""

    def resolve_id(
        self, specifier: str, importer: str | None, *, context: PluginContext
    ) -> str | None:
        """Resolve a module specifier to an ID.

        Returns None to defer to the next plugin or default resolution.
        """
        return None

    def load(self, module_id: str, *, context: PluginContext) -> str | None:
        """Load module source by ID.

        Returns source code string or None to defer.
        """
        return None

    def transform(
        self, module_id: str, code: str, *, context: PluginContext
    ) -> str:
        """Transform module source code.

        Returns transformed source. Default returns unchanged code.
        """
        return code

    def module_parsed(
        self, module_id: str, *, context: PluginContext
    ) -> None:
        """Called when AST parsing completes for a module.

        This hook is invoked after the compiler has successfully parsed
        a module's source code into an AST. Use this to perform analysis
        that depends on the AST being available.

        Args:
            module_id: The module identifier that was parsed.
            context: The plugin context.
        """

    def build_end(self, *, context: PluginContext) -> None:
        """Called at the end of each build (cleanup)."""

    # -- Output generation hooks --

    def render_start(self, *, context: PluginContext) -> None:
        """First hook of the output generation phase.

        Called before any chunks are rendered. Use this to initialize
        output-generation state.
        """

    def render_chunk(
        self, chunk: dict[str, Any], *, context: PluginContext
    ) -> dict[str, Any] | None:
        """Transform a chunk before it's written to the bundle.

        Args:
            chunk: A dict describing the chunk with keys:
                - ``code``: Source code string.
                - ``module_id``: The module this chunk belongs to.
                - ``is_entry``: Whether this is an entry point.
            context: The plugin context.

        Returns:
            Modified chunk dict or None to skip modification.
        """
        return None

    def generate_bundle(
        self, options: dict[str, Any], bundle: dict[str, dict[str, Any]],
        *, context: PluginContext
    ) -> None:
        """Modify the final output bundle before writing.

        Args:
            options: Build options dict.
            bundle: Dict mapping output filenames to chunk dicts.
            context: The plugin context.
        """

    def write_bundle(self, dist_path: Path, *, context: PluginContext) -> None:
        """Called after the output bundle is written."""

    def close_bundle(self, *, context: PluginContext) -> None:
        """Final cleanup hook after all output is written.

        Called once when the compiler is shutting down, after all
        bundles have been written. Use this to clean up temporary
        files, close connections, etc.
        """


# ---------------------------------------------------------------------------
# Plugin registry — validates inheritance
# ---------------------------------------------------------------------------

class PluginRegistry:
    """Registry that validates and manages plugins.

    Every registered plugin must be a subclass of ``BasePlugin``.
    Registration checks the inheritance chain and rejects non-conforming
    classes.
    """

    def __init__(self) -> None:
        self._plugins: list[BasePlugin] = []

    def register(self, plugin: BasePlugin) -> None:
        """Register a plugin instance.

        Validates that the plugin is an instance of ``BasePlugin`` and
        that its class is a proper subclass (not the base class itself).

        Raises
        ------
        TypeError
            If the plugin does not inherit from ``BasePlugin``.
        ValueError
            If the plugin is the unmodified ``BasePlugin`` class.
        """
        if not isinstance(plugin, BasePlugin):
            raise TypeError(
                f"Plugin {type(plugin).__name__!r} is not a valid Forger plugin. "
                f"It must inherit from BasePlugin."
            )
        if type(plugin) is BasePlugin:
            raise ValueError(
                "Cannot register the base BasePlugin class directly. "
                "Subclass it and register your implementation."
            )
        # Verify the MRO contains BasePlugin at the expected position
        mro = inspect.getmro(type(plugin))
        if BasePlugin not in mro:
            raise TypeError(
                f"Plugin {type(plugin).__name__!r} does not have BasePlugin "
                f"in its inheritance chain."
            )
        self._plugins.append(plugin)
        logger.debug("Registered plugin: %s (enforce=%s)", plugin.name, plugin.enforce)

    @property
    def plugins(self) -> list[BasePlugin]:
        """Return sorted plugins: pre → normal → post."""
        order_map = {PluginOrder.PRE: 0, PluginOrder.NORMAL: 1, PluginOrder.POST: 2}
        return sorted(
            self._plugins,
            key=lambda p: order_map.get(p.enforce, 1),
        )

    def clear(self) -> None:
        """Remove all registered plugins."""
        self._plugins.clear()


# ---------------------------------------------------------------------------
# Plugin runner — executes lifecycle hooks
# ---------------------------------------------------------------------------

class PluginRunner:
    """Execute plugin lifecycle hooks in enforce order.

    Hooks are invoked in the order: pre → normal → post.
    Within each tier, plugins run in registration order.
    """

    def __init__(
        self,
        plugins: Sequence[BasePlugin] | None = None,
        *,
        apply: str | None = "build",
    ) -> None:
        """Create a PluginRunner.

        Args:
            plugins: Initial list of plugins to register.
            apply: The build mode. ``"build"`` for compilation, ``"serve"`` for
                dev server. Plugins with an ``apply`` property that doesn't match
                will be skipped. Defaults to ``"build"``.
        """
        self._registry = PluginRegistry()
        self._apply_mode = apply
        if plugins:
            for p in plugins:
                self._registry.register(p)

    @property
    def apply_mode(self) -> str | None:
        """Return the current apply mode."""
        return self._apply_mode

    @property
    def plugins(self) -> list[BasePlugin]:
        return self._registry.plugins

    def register(self, plugin: BasePlugin) -> None:
        """Register a plugin with validation."""
        self._registry.register(plugin)

    def _should_apply(self, plugin: BasePlugin) -> bool:
        """Check if a plugin should run in the current apply mode."""
        if plugin.apply is None:
            return True
        return plugin.apply == self._apply_mode

    def _invoke(
        self,
        hook: str,
        context: PluginContext | None = None,
        *args,
    ) -> None:
        """Invoke a hook on all plugins that implement it."""
        for plugin in self.plugins:
            if not self._should_apply(plugin):
                continue
            method = getattr(plugin, hook, None)
            if method is None:
                continue
            try:
                if context is not None:
                    method(*args, context=context)
                else:
                    method(*args)
                logger.debug(
                    "Plugin %s: %s hook completed", plugin.name, hook
                )
            except Exception as e:
                logger.error(
                    "Plugin %s: %s hook failed: %s",
                    plugin.name,
                    hook,
                    e,
                )

    def run_config(self, context: PluginContext) -> None:
        self._invoke("config", context)

    def run_config_resolved(self, context: PluginContext) -> None:
        self._invoke("config_resolved", context)

    def run_build_start(self, context: PluginContext) -> None:
        self._invoke("build_start", context)

    def run_resolve_id(
        self, specifier: str, importer: str | None, context: PluginContext
    ) -> str | None:
        for plugin in self.plugins:
            if not self._should_apply(plugin):
                continue
            method = getattr(plugin, "resolve_id", None)
            if method is None:
                continue
            # Respect resolve_id_filter
            if plugin.resolve_id_filter and not _matches_filter(
                plugin.resolve_id_filter, specifier
            ):
                continue
            try:
                result = method(specifier, importer, context=context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(
                    "Plugin %s: resolve_id failed: %s",
                    plugin.name,
                    e,
                )
        return None

    def run_load(self, module_id: str, context: PluginContext) -> str | None:
        for plugin in self.plugins:
            if not self._should_apply(plugin):
                continue
            method = getattr(plugin, "load", None)
            if method is None:
                continue
            # Respect load_filter
            if plugin.load_filter and not _matches_filter(
                plugin.load_filter, module_id
            ):
                continue
            try:
                result = method(module_id, context=context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(
                    "Plugin %s: load failed: %s",
                    plugin.name,
                    e,
                )
        return None

    def run_transform(
        self, module_id: str, code: str, context: PluginContext
    ) -> str:
        for plugin in self.plugins:
            method = getattr(plugin, "transform", None)
            if method is None:
                continue
            # Respect transform_filter
            if plugin.transform_filter and not _matches_filter(
                plugin.transform_filter, module_id
            ):
                continue
            try:
                code = method(module_id, code, context=context)
            except Exception as e:
                logger.error(
                    "Plugin %s: transform failed: %s",
                    plugin.name,
                    e,
                )
        return code

    def run_module_parsed(self, module_id: str, context: PluginContext) -> None:
        """Invoke the ``module_parsed`` hook on all plugins."""
        self._invoke("module_parsed", context, module_id)

    def run_build_end(self, context: PluginContext) -> None:
        self._invoke("build_end", context)

    # -- Output generation hooks --

    def run_render_start(self, context: PluginContext) -> None:
        """Invoke the ``render_start`` hook on all plugins."""
        self._invoke("render_start", context)

    def run_render_chunk(
        self, chunk: dict[str, Any], context: PluginContext
    ) -> dict[str, Any] | None:
        """Invoke the ``render_chunk`` hook on all plugins.

        Returns the modified chunk or the original if no plugin
        changed it.
        """
        result = None
        for plugin in self.plugins:
            if not self._should_apply(plugin):
                continue
            method = getattr(plugin, "render_chunk", None)
            if method is None:
                continue
            try:
                new_chunk = method(chunk, context=context)
                if new_chunk is not None:
                    result = new_chunk
            except Exception as e:
                logger.error(
                    "Plugin %s: render_chunk failed: %s",
                    plugin.name,
                    e,
                )
        return result or chunk

    def run_generate_bundle(
        self,
        options: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        context: PluginContext,
    ) -> None:
        """Invoke the ``generate_bundle`` hook on all plugins."""
        for plugin in self.plugins:
            if not self._should_apply(plugin):
                continue
            method = getattr(plugin, "generate_bundle", None)
            if method is None:
                continue
            try:
                method(options, bundle, context=context)
                logger.debug(
                    "Plugin %s: generate_bundle hook completed", plugin.name
                )
            except Exception as e:
                logger.error(
                    "Plugin %s: generate_bundle hook failed: %s",
                    plugin.name,
                    e,
                )

    def run_write_bundle(self, dist_path: Path, context: PluginContext) -> None:
        self._invoke("write_bundle", context, dist_path)

    def run_close_bundle(self, context: PluginContext) -> None:
        """Invoke the ``close_bundle`` hook on all plugins."""
        self._invoke("close_bundle", context)

    # -- Backward-compatible hook names (legacy plugin compat) --

    def run_build_graph(self, context: PluginContext) -> None:
        """Alias for backward compat — maps to build_start."""
        self._invoke("build_graph", context)

    def run_before_shake(self, context: PluginContext) -> None:
        """Alias for backward compat."""
        self._invoke("before_shake", context)

    def run_after_shake(self, context: PluginContext) -> None:
        """Alias for backward compat."""
        self._invoke("after_shake", context)

    def run_transform_ast(
        self, module_id: str, tree, context: PluginContext
    ) -> None:
        """Alias for backward compat."""
        self._invoke("transform_ast", context, module_id, tree)

    def run_analyze(self, module_id: str, context: PluginContext) -> None:
        """Alias for backward compat."""
        self._invoke("analyze", context, module_id)

    def run_discover_resources(
        self, module_id: str, context: PluginContext
    ) -> None:
        """Alias for backward compat."""
        self._invoke("discover_resources", context, module_id)

    def run_generate(self, context: PluginContext) -> None:
        """Alias for backward compat."""
        self._invoke("generate", context)
