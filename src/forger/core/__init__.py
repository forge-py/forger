"""Python bridge to the Rust core.

This module provides Python-accessible wrappers around the Rust core
functionality (filesystem discovery, dependency graph, hashing, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import the compiled Rust extension
# If not available, fall back to pure Python implementations
try:
    import forger.forger_core  # noqa: F401  # type: ignore[import-not-found, import-untyped, missing-import]

    HAS_RUST_CORE = True
except ImportError:
    HAS_RUST_CORE = False
    logger.debug("Rust core not available — using pure Python fallback")


# --- Dependency Graph (pure Python fallback) ---


@dataclass
class EdgeProvenance:
    """Provenance information for a dependency edge."""

    source: tuple[str, int] | None = None
    discovered_by: str = ""
    description: str | None = None


@dataclass
class DependencyEdge:
    """Edge in the dependency graph."""

    from_node: str
    to_node: str
    edge_type: str
    provenance: EdgeProvenance | None = None

    @classmethod
    def new(
        cls,
        from_node: str,
        to_node: str,
        edge_type: str,
        provenance: EdgeProvenance | None = None,
    ) -> DependencyEdge:
        return cls(from_node, to_node, edge_type, provenance)


@dataclass
class DependencyNode:
    """Node in the dependency graph."""

    id: str
    node_type: str
    path: Path | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    required: bool = False
    conservative: bool = False
    target: str | None = None
    unoptimized: bool = False
    content: str | None = None

    @classmethod
    def new(cls, node_id: str, node_type: str) -> DependencyNode:
        return cls(id=node_id, node_type=node_type)

    def with_metadata(self, key: str, value: str) -> DependencyNode:
        self.metadata[key] = value
        return self

    def mark_unoptimized(self) -> DependencyNode:
        """Mark this node as an unoptimized dependency (bypasses tree-shaking)."""
        self.unoptimized = True
        return self

    def set_content(self, content: str) -> None:
        """Set the source content of this node."""
        self.content = content

    def get_content(self) -> str | None:
        """Get the source content of this node."""
        return self.content


class NodeType:
    """Node types for the dependency graph."""

    PythonModule = "python_module"
    PythonPackage = "python_package"
    StdlibModule = "stdlib_module"
    StdlibPackage = "stdlib_package"
    NativeExtension = "native_extension"
    NativeLibrary = "native_library"
    Resource = "resource"
    Configuration = "configuration"
    EntryPoint = "entry_point"
    VfsPath = "vfs_path"
    DynamicImport = "dynamic_import"
    ExternalPackage = "external_package"


class EdgeType:
    """Edge types for the dependency graph."""

    Import = "import"
    FromImport = "from_import"
    RelativeImport = "relative_import"
    DynamicImport = "dynamic_import"
    ResourceDependency = "resource"
    NativeDependency = "native"
    ConfigDependency = "config"
    PluginDependency = "plugin"
    EntryPointDependency = "entry_point"
    StdlibDependency = "stdlib"
    Indirect = "indirect"
    UnoptimizedDependency = "unoptimized"


class DependencyGraph:
    """Dependency graph — pure Python fallback implementation."""

    def __init__(self) -> None:
        self._nodes: dict[str, DependencyNode] = {}
        self._edges_from: dict[str, list[DependencyEdge]] = {}
        self._edges_to: dict[str, list[DependencyEdge]] = {}
        self._entry_points: list[str] = []

    def add_node(self, node: DependencyNode) -> bool:
        if node.id in self._nodes:
            return False
        self._nodes[node.id] = node
        return True

    def get_node(self, node_id: str) -> DependencyNode | None:
        return self._nodes.get(node_id)

    def add_edge(self, edge: DependencyEdge) -> None:
        self._edges_from.setdefault(edge.from_node, []).append(edge)
        self._edges_to.setdefault(edge.to_node, []).append(edge)

    def add_entry_point(self, node_id: str) -> None:
        self._entry_points.append(node_id)

    def entry_points(self) -> list[str]:
        return list(self._entry_points)

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return sum(len(v) for v in self._edges_from.values())

    def all_nodes(self) -> dict[str, DependencyNode]:
        return dict(self._nodes)

    def dependencies_of(self, node_id: str) -> list[str]:
        return [e.to_node for e in self._edges_from.get(node_id, [])]

    def dependents_of(self, node_id: str) -> list[str]:
        return [e.from_node for e in self._edges_to.get(node_id, [])]

    def nodes_by_type(self, node_type: str) -> list[DependencyNode]:
        return [n for n in self._nodes.values() if n.node_type == node_type]

    def find_reachable(self) -> set[str]:
        """BFS from entry points to find all reachable nodes."""
        visited: set[str] = set()
        queue = list(self._entry_points)

        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            for dep in self.dependencies_of(node_id):
                if dep not in visited:
                    queue.append(dep)

        return visited

    def mark_reachable_required(self) -> None:
        reachable = self.find_reachable()
        # Unoptimized nodes are always required — they bypass tree-shaking
        for node in self._nodes.values():
            if node.unoptimized:
                reachable.add(node.id)
            node.required = node.id in reachable

    def prune_unreachable(self) -> None:
        reachable = self.find_reachable()
        self._nodes = {k: v for k, v in self._nodes.items() if k in reachable}
        self._edges_from = {
            k: v for k, v in self._edges_from.items() if k in reachable
        }
        self._edges_to = {
            k: v for k, v in self._edges_to.items() if k in reachable
        }

    def validate(self) -> bool:
        # Entry points must have corresponding nodes
        for ep in self._entry_points:
            if ep not in self._nodes:
                return False
        # Edge endpoints must have corresponding nodes
        for edges in self._edges_from.values():
            for edge in edges:
                if edge.from_node not in self._nodes:
                    return False
                if edge.to_node not in self._nodes:
                    return False
        return True

    def merge(self, other: DependencyGraph) -> None:
        for node in other._nodes.values():
            self.add_node(node)
        for edges in other._edges_from.values():
            for edge in edges:
                self.add_edge(edge)
        for ep in other._entry_points:
            self.add_entry_point(ep)

    def get_unoptimized_nodes(self) -> list[DependencyNode]:
        """Return all nodes marked as unoptimized (bypass tree-shaking)."""
        return [n for n in self._nodes.values() if n.unoptimized]

    def has_unoptimized_nodes(self) -> bool:
        """Check if any nodes are marked unoptimized."""
        return any(n.unoptimized for n in self._nodes.values())

    def is_dead(self, node_id: str) -> bool:
        """Check if a node is dead (not reachable and not unoptimized)."""
        node = self._nodes.get(node_id)
        if node is None:
            return True
        if node.unoptimized:
            return False
        return node_id not in self.find_reachable()

    def find_dead_nodes(self) -> list[str]:
        """Return all dead (unreachable) node IDs."""
        reachable = self.find_reachable()
        # Unoptimized nodes are never considered dead
        dead: list[str] = []
        for node_id, node in self._nodes.items():
            if node.unoptimized:
                continue
            if node_id not in reachable:
                dead.append(node_id)
        return dead

    def get_node_mut(self, node_id: str) -> DependencyNode | None:
        """Get a mutable reference to a node by ID."""
        return self._nodes.get(node_id)

    def delete_node(self, node_id: str) -> bool:
        """Remove a node from the graph. Returns True if the node existed."""
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        self._edges_from.pop(node_id, None)
        self._edges_to.pop(node_id, None)
        self._entry_points = [ep for ep in self._entry_points if ep != node_id]
        return True
