"""Plugin framework for the Forger compiler.

Provides:
- Plugin system (Vite-style lifecycle hooks)
- Plugin context for controlled graph access
- PluginRunner for executing lifecycle hooks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from forger.core import (  # type: ignore[attr-defined]
        DependencyGraph,
        DependencyNode,
        DependencyEdge,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin lifecycle ordering
# ---------------------------------------------------------------------------


class PluginOrder:
    """Plugin ordering categories."""

    PRE = "pre"
    NORMAL = "normal"
    POST = "post"


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
# Plugin protocol
# ---------------------------------------------------------------------------


class Plugin(Protocol):
    """Vite-style plugin protocol for the Forger compiler.

    All methods are optional. A plugin only implements the hooks it needs.
    The name attribute is required.
    """

    name: str
    order: str  # PluginOrder.PRE, PluginOrder.NORMAL, PluginOrder.POST

    def config(self, context: PluginContext) -> None: ...
    def build_start(self, context: PluginContext) -> None: ...
    def resolve(
        self, specifier: str, importer: str, context: PluginContext
    ) -> str | None: ...
    def load(self, module_id: str, context: PluginContext) -> str | None: ...
    def transform_ast(
        self, module_id: str, tree, context: PluginContext
    ): ...
    def analyze(self, module_id: str, context: PluginContext) -> None: ...
    def discover_resources(
        self, module_id: str, context: PluginContext
    ) -> None: ...
    def build_graph(self, context: PluginContext) -> None: ...
    def before_shake(self, context: PluginContext) -> None: ...
    def after_shake(self, context: PluginContext) -> None: ...
    def transform_resource(
        self, resource_path: str, data: bytes, context: PluginContext
    ) -> bytes: ...
    def generate(self, context: PluginContext) -> None: ...
    def write_bundle(self, dist_path: Path, context: PluginContext) -> None: ...
    def build_end(self, context: PluginContext) -> None: ...


# ---------------------------------------------------------------------------
# Plugin runner
# ---------------------------------------------------------------------------


class PluginRunner:
    """Execute plugin lifecycle hooks in order."""

    def __init__(self, plugins: Sequence[Plugin]) -> None:
        self.plugins = self._sort_plugins(list(plugins))

    @staticmethod
    def _sort_plugins(plugins: list[Plugin]) -> list[Plugin]:
        """Sort plugins by order: pre → normal → post."""
        order_map = {PluginOrder.PRE: 0, PluginOrder.NORMAL: 1, PluginOrder.POST: 2}
        return sorted(plugins, key=lambda p: order_map.get(getattr(p, "order", PluginOrder.NORMAL), 1))

    def _invoke(
        self,
        hook: str,
        context: PluginContext | None = None,
        *args,
    ) -> None:
        """Invoke a hook on all plugins that implement it."""
        for plugin in self.plugins:
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

    def run_build_start(self, context: PluginContext) -> None:
        self._invoke("build_start", context)

    def run_resolve(
        self, specifier: str, importer: str, context: PluginContext
    ) -> str | None:
        for plugin in self.plugins:
            method = getattr(plugin, "resolve", None)
            if method is None:
                continue
            try:
                result = method(specifier, importer, context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(
                    "Plugin %s: resolve failed: %s",
                    plugin.name,
                    e,
                )
        return None

    def run_load(self, module_id: str, context: PluginContext) -> str | None:
        for plugin in self.plugins:
            method = getattr(plugin, "load", None)
            if method is None:
                continue
            try:
                result = method(module_id, context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(
                    "Plugin %s: load failed: %s",
                    plugin.name,
                    e,
                )
        return None

    def run_transform_ast(
        self, module_id: str, tree, context: PluginContext
    ) -> None:
        self._invoke("transform_ast", context, module_id, tree)

    def run_analyze(self, module_id: str, context: PluginContext) -> None:
        self._invoke("analyze", context, module_id)

    def run_discover_resources(
        self, module_id: str, context: PluginContext
    ) -> None:
        self._invoke("discover_resources", context, module_id)

    def run_build_graph(self, context: PluginContext) -> None:
        self._invoke("build_graph", context)

    def run_before_shake(self, context: PluginContext) -> None:
        self._invoke("before_shake", context)

    def run_after_shake(self, context: PluginContext) -> None:
        self._invoke("after_shake", context)

    def run_transform_resource(
        self, resource_path: str, data: bytes, context: PluginContext
    ) -> bytes:
        for plugin in self.plugins:
            method = getattr(plugin, "transform_resource", None)
            if method is None:
                continue
            try:
                data = method(resource_path, data, context)
            except Exception as e:
                logger.error(
                    "Plugin %s: transform_resource failed: %s",
                    plugin.name,
                    e,
                )
        return data

    def run_generate(self, context: PluginContext) -> None:
        self._invoke("generate", context)

    def run_write_bundle(self, dist_path: Path, context: PluginContext) -> None:
        self._invoke("write_bundle", context, dist_path)

    def run_build_end(self, context: PluginContext) -> None:
        self._invoke("build_end", context)


# ---------------------------------------------------------------------------
# Optimizer discovery (removed)
# ---------------------------------------------------------------------------
#
# Framework-specific optimizers (Django, Flask, etc.) have been moved to
# external plugins:
#
#     forge-plugin-django  — https://github.com/forge-py/forge-django-plugin
#     forge-plugin-flask   — https://github.com/forge-py/forge-plugin-flask
#
# The Plugin protocol and PluginRunner remain for extensibility.
