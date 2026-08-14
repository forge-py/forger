"""Optimizer / plugin framework for the Forger compiler.

Provides:
- Optimizer base class (legacy compatibility)
- Plugin system (Vite-style lifecycle hooks)
- Plugin context for controlled graph access
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
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
# OptimizerContext (legacy)
# ---------------------------------------------------------------------------


@dataclass
class OptimizerContext:
    """Context passed to optimizers."""

    project_root: Path
    source_files: list[Path] = field(default_factory=list)
    installed_packages: dict[str, str] = field(default_factory=dict)
    graph: DependencyGraph | None = None
    config: dict = field(default_factory=dict)

    def has_package(self, name: str) -> bool:
        """Check if a package is installed in the project environment."""
        if name in self.installed_packages:
            return True
        import importlib.metadata

        try:
            importlib.metadata.distribution(name)
            return True
        except importlib.metadata.PackageNotFoundError:
            return False


# ---------------------------------------------------------------------------
# Optimizer (legacy base class)
# ---------------------------------------------------------------------------


class Optimizer(ABC):
    """Base class for framework-specific optimizers.

    Legacy interface — new code should use the Plugin protocol instead.
    """

    name: str = "optimizer"

    @abstractmethod
    def detect(self, context: OptimizerContext) -> float:
        """Return confidence score (0.0–1.0) that this optimizer applies."""

    def analyze(self, context: OptimizerContext, graph: DependencyGraph) -> None:
        """Analyze and contribute to the dependency graph."""


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
# Optimizer discovery
# ---------------------------------------------------------------------------


def discover_optimizers() -> list[Optimizer]:
    """Discover all available optimizers.

    Scans the ``forger.optimizers`` package for subclasses of ``Optimizer``
    and returns them.

    Returns:
        List of optimizer instances.
    """
    import importlib

    optimizers: list[Optimizer] = []
    try:
        optimizers_module = importlib.import_module("forger.optimizers")
    except ImportError:
        return optimizers

    # Discover optimizer modules via entry points or by scanning submodules
    for submodule_name in dir(optimizers_module):
        if submodule_name.startswith("_"):
            continue
        submodule = getattr(optimizers_module, submodule_name, None)
        if submodule is None:
            continue

        # Try to import the submodule to find Optimizer subclasses
        try:
            submodule_obj = importlib.import_module(f"forger.optimizers.{submodule_name}")
        except ImportError:
            continue

        for attr_name in dir(submodule_obj):
            attr = getattr(submodule_obj, attr_name, None)
            if (
                attr is not None
                and isinstance(attr, type)
                and issubclass(attr, Optimizer)
                and attr is not Optimizer
            ):
                optimizers.append(attr())

    return optimizers


def run_optimizers(
    context: OptimizerContext,
    graph: DependencyGraph,
) -> list[str]:
    """Run all detected optimizers on the dependency graph.

    Discovers optimizers, runs detection, and executes those with
    a confidence score >= 0.5.

    Args:
        context: The optimizer context.
        graph: The dependency graph to contribute to.

    Returns:
        List of active optimizer names.
    """
    all_optimizers = discover_optimizers()
    active: list[str] = []

    for optimizer in all_optimizers:
        try:
            confidence = optimizer.detect(context)
            if confidence >= 0.5:
                logger.info(
                    "Optimizer %s activated (confidence: %.2f)",
                    optimizer.name,
                    confidence,
                )
                optimizer.analyze(context, graph)
                active.append(optimizer.name)
            else:
                logger.debug(
                    "Optimizer %s not activated (confidence: %.2f)",
                    optimizer.name,
                    confidence,
                )
        except Exception as e:
            logger.warning(
                "Optimizer %s failed: %s",
                optimizer.name,
                e,
            )

    return active
