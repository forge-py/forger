"""Python bridge to the Rust core.

This module provides Python-accessible wrappers around the Rust core
functionality (filesystem discovery, dependency graph, hashing, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Try to import the compiled Rust extension
# If not available, fall back to pure Python implementations
try:
    from forger.forger_core import (
        DependencyEdge as _RustDependencyEdge,
    )
    from forger.forger_core import (
        DependencyGraph as _RustDependencyGraph,
    )
    from forger.forger_core import (
        DependencyNode as _RustDependencyNode,
    )
    from forger.forger_core import (  # type: ignore[import-not-found, import-untyped, missing-import]
        EdgeProvenance as _RustEdgeProvenance,
    )
    from forger.forger_core import (
        EdgeType as _RustEdgeType,
    )
    from forger.forger_core import (
        NodeType as _RustNodeType,
    )

    HAS_RUST_CORE = True
    logger.debug("Rust core available — using Rust-backed dependency graph types")
except ImportError:
    HAS_RUST_CORE = False
    logger.debug("Rust core not available — using pure Python fallback")


# ---------------------------------------------------------------------------
# Type definitions for static analysis.
#
# The pure-Python dataclasses are the canonical type definitions.  At runtime
# either the Rust wrappers (when available) or these dataclasses are used,
# but the type checker always sees the dataclasses so there is a single
# source of truth for types.
# ---------------------------------------------------------------------------

if TYPE_CHECKING:

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
        ) -> DependencyEdge: ...

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
        def new(cls, node_id: str, node_type: str) -> DependencyNode: ...

        def with_metadata(self, key: str, value: str) -> DependencyNode: ...

        def mark_unoptimized(self) -> DependencyNode: ...

        def set_content(self, content: str) -> None: ...

        def get_content(self) -> str | None: ...

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
        """Dependency graph."""

        def add_node(self, node: DependencyNode) -> bool: ...
        def get_node(self, node_id: str) -> DependencyNode | None: ...
        def get_node_mut(self, node_id: str) -> DependencyNode | None: ...
        def add_edge(self, edge: DependencyEdge) -> None: ...
        def add_entry_point(self, node_id: str) -> None: ...
        def entry_points(self) -> list[str]: ...
        def node_count(self) -> int: ...
        def edge_count(self) -> int: ...
        def all_nodes(self) -> dict[str, DependencyNode]: ...
        def dependencies_of(self, node_id: str) -> list[str]: ...
        def dependents_of(self, node_id: str) -> list[str]: ...
        def nodes_by_type(self, node_type: str) -> list[DependencyNode]: ...
        def find_reachable(self) -> set[str]: ...
        def mark_reachable_required(self) -> None: ...
        def prune_unreachable(self) -> None: ...
        def validate(self) -> bool: ...
        def merge(self, other: DependencyGraph) -> None: ...
        def get_unoptimized_nodes(self) -> list[DependencyNode]: ...
        def has_unoptimized_nodes(self) -> bool: ...
        def is_dead(self, node_id: str) -> bool: ...
        def find_dead_nodes(self) -> list[str]: ...
        def delete_node(self, node_id: str) -> bool: ...
        def freeze(self) -> None: ...
        def is_frozen(self) -> bool: ...

        @property
        def nodes(self) -> dict[str, DependencyNode]: ...


# ---------------------------------------------------------------------------
# Runtime implementations
# ---------------------------------------------------------------------------

if HAS_RUST_CORE:

    class EdgeProvenance:  # type: ignore[no-redef]
        """Thin wrapper around Rust EdgeProvenance."""

        __slots__ = ("_rs",)

        def __init__(self, _rs=None, **kwargs):
            if _rs is not None:
                self._rs = _rs
                return
            self._rs = _RustEdgeProvenance(
                discovered_by=kwargs.get("discovered_by", ""),
                source=kwargs.get("source"),
                description=kwargs.get("description"),
            )

        @property
        def source(self):
            return getattr(self._rs, "source", None)

        @property
        def discovered_by(self):
            return getattr(self._rs, "discovered_by", "")

        @property
        def description(self):
            return getattr(self._rs, "description", None)

    class DependencyNode:  # type: ignore[no-redef]
        """Thin wrapper around Rust DependencyNode."""

        __slots__ = ("_rs",)

        def __init__(self, _rs=None, **kwargs):
            if _rs is not None:
                self._rs = _rs
                return
            self._rs = _RustDependencyNode(
                id=kwargs.get("id", ""),
                node_type=kwargs.get("node_type", _RustNodeType.PythonModule),
            )
            for k, v in kwargs.items():
                if k not in ("id", "node_type") and v is not None:
                    setattr(self._rs, k, v)

        @classmethod
        def new(cls, node_id: str, node_type) -> DependencyNode:
            if isinstance(node_type, str):
                type_map = {
                    "python_module": _RustNodeType.PythonModule,
                    "python_package": _RustNodeType.PythonPackage,
                    "stdlib_module": _RustNodeType.StdlibModule,
                    "stdlib_package": _RustNodeType.StdlibPackage,
                    "native_extension": _RustNodeType.NativeExtension,
                    "native_library": _RustNodeType.NativeLibrary,
                    "resource": _RustNodeType.Resource,
                    "configuration": _RustNodeType.Configuration,
                    "entry_point": _RustNodeType.EntryPoint,
                    "vfs_path": _RustNodeType.VfsPath,
                    "dynamic_import": _RustNodeType.DynamicImport,
                    "external_package": _RustNodeType.ExternalPackage,
                }
                node_type = type_map.get(node_type, _RustNodeType.PythonModule)
            return cls(id=node_id, node_type=node_type)

        def __getattr__(self, name):
            return getattr(self._rs, name)

        def __setattr__(self, name, value):
            if name == "_rs":
                super().__setattr__(name, value)
            else:
                setattr(self._rs, name, value)

        def with_metadata(self, key: str, value: str) -> DependencyNode:
            meta = getattr(self._rs, "metadata", {})
            meta[key] = value
            self._rs.metadata = meta
            return self

        def mark_unoptimized(self) -> DependencyNode:
            self._rs.unoptimized = True
            return self

        def set_content(self, content: str) -> None:
            self._rs.content = content

        def get_content(self) -> str | None:
            return self._rs.content

        @property
        def node_type(self) -> str:
            """Short string form (e.g. "python_module").

            The Rust binding exposes NodeType as a pyclass enum, whose
            default Python repr is the class name. Normalize to the same
            string the pure-Python fallback stores so string comparisons
            against ``NodeType.PythonModule`` keep working.
            """
            raw = str(getattr(self._rs, "node_type", ""))
            return raw.split("::")[-1] if raw else ""

        @node_type.setter
        def node_type(self, value: str) -> None:
            # Accept either a NodeType enum, the short string, or "NodeType::x".
            short = value.split("::")[-1] if "::" in str(value) else str(value)
            type_map = {
                "python_module": _RustNodeType.PythonModule,
                "python_package": _RustNodeType.PythonPackage,
                "stdlib_module": _RustNodeType.StdlibModule,
                "stdlib_package": _RustNodeType.StdlibPackage,
                "native_extension": _RustNodeType.NativeExtension,
                "native_library": _RustNodeType.NativeLibrary,
                "resource": _RustNodeType.Resource,
                "configuration": _RustNodeType.Configuration,
                "entry_point": _RustNodeType.EntryPoint,
                "vfs_path": _RustNodeType.VfsPath,
                "dynamic_import": _RustNodeType.DynamicImport,
                "external_package": _RustNodeType.ExternalPackage,
            }
            self._rs.node_type = type_map.get(short, _RustNodeType.PythonModule)

    class DependencyEdge:  # type: ignore[no-redef]
        """Thin wrapper around Rust DependencyEdge."""

        __slots__ = ("_rs",)

        def __init__(self, _rs=None, **kwargs):
            if _rs is not None:
                self._rs = _rs
                return
            from_node = kwargs.get("from_node", "")
            to_node = kwargs.get("to_node", "")
            edge_type_raw = kwargs.get("edge_type", "import")
            provenance_raw = kwargs.get("provenance")

            if isinstance(edge_type_raw, str):
                et_map = {
                    "import": _RustEdgeType.Import,
                    "from_import": _RustEdgeType.FromImport,
                    "relative_import": _RustEdgeType.RelativeImport,
                    "dynamic_import": _RustEdgeType.DynamicImport,
                    "resource": _RustEdgeType.ResourceDependency,
                    "native": _RustEdgeType.NativeDependency,
                    "config": _RustEdgeType.ConfigDependency,
                    "plugin": _RustEdgeType.PluginDependency,
                    "entry_point": _RustEdgeType.EntryPointDependency,
                    "stdlib": _RustEdgeType.StdlibDependency,
                    "indirect": _RustEdgeType.Indirect,
                    "unoptimized": _RustEdgeType.UnoptimizedDependency,
                }
                edge_cls = et_map.get(edge_type_raw, _RustEdgeType.Import)
                edge_type = edge_cls()
            elif isinstance(edge_type_raw, type):
                edge_type = edge_type_raw()
            else:
                edge_type = edge_type_raw

            if provenance_raw is not None:
                prov = _RustEdgeProvenance(
                    discovered_by=getattr(provenance_raw, "discovered_by", "static_analyzer"),
                    source=getattr(provenance_raw, "source", None),
                    description=getattr(provenance_raw, "description", None),
                )
            else:
                prov = _RustEdgeProvenance(discovered_by="static_analyzer")

            self._rs = _RustDependencyEdge(
                from_node=from_node,
                to_node=to_node,
                edge_type=edge_type,
                provenance=prov,
            )

        @classmethod
        def new(
            cls,
            from_node: str,
            to_node: str,
            edge_type,
            provenance=None,
        ) -> DependencyEdge:
            return cls(
                from_node=from_node,
                to_node=to_node,
                edge_type=edge_type,
                provenance=provenance,
            )

        def __getattr__(self, name):
            rust_name = {"from_node": "from", "to_node": "to"}.get(name, name)
            return getattr(self._rs, rust_name)

    class NodeType:  # type: ignore[no-redef]
        """Node types — string constants compatible with pure-Python fallback."""

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

    class EdgeType:  # type: ignore[no-redef]
        """Edge types — string constants compatible with pure-Python fallback."""

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

    class DependencyGraph:  # type: ignore[no-redef]
        """Thin wrapper around Rust DependencyGraph."""

        __slots__ = ("_rs",)

        def __init__(self, _rs=None):
            if _rs is not None:
                self._rs = _rs
            else:
                self._rs = _RustDependencyGraph()

        def __getattr__(self, name):
            return getattr(self._rs, name)

        def __setattr__(self, name, value):
            if name == "_rs":
                super().__setattr__(name, value)
            else:
                setattr(self._rs, name, value)

        @property
        def nodes(self):
            return {nid: self.get_node(nid) for nid in self._rs.nodes}

        def get_node(self, node_id: str) -> DependencyNode | None:
            rs_node = self._rs.get_node(node_id)
            if rs_node is None:
                return None
            return DependencyNode(_rs=rs_node)

        def add_node(self, node) -> bool:
            if isinstance(node, DependencyNode):
                return self._rs.add_node(node._rs)
            return self._rs.add_node(node)

        def add_edge(self, edge) -> None:
            if isinstance(edge, DependencyEdge):
                self._rs.add_edge(edge._rs)
            else:
                self._rs.add_edge(edge)

        def nodes_by_type(self, node_type):
            if isinstance(node_type, str):
                type_map = {
                    "python_module": _RustNodeType.PythonModule,
                    "python_package": _RustNodeType.PythonPackage,
                    "stdlib_module": _RustNodeType.StdlibModule,
                    "stdlib_package": _RustNodeType.StdlibPackage,
                    "native_extension": _RustNodeType.NativeExtension,
                    "native_library": _RustNodeType.NativeLibrary,
                    "resource": _RustNodeType.Resource,
                    "configuration": _RustNodeType.Configuration,
                    "entry_point": _RustNodeType.EntryPoint,
                    "vfs_path": _RustNodeType.VfsPath,
                    "dynamic_import": _RustNodeType.DynamicImport,
                    "external_package": _RustNodeType.ExternalPackage,
                }
                node_type = type_map.get(node_type, _RustNodeType.PythonModule)
            rs_nodes = self._rs.nodes_by_type(node_type)
            return [DependencyNode(_rs=n) for n in rs_nodes]

        def find_reachable(self) -> set[str]:
            return set(self._rs.find_reachable())

        def set_node_content(self, node_id: str, content: str) -> bool:
            """Write content through to the stored node (by id)."""
            return self._rs.set_node_content(node_id, content)

        def set_node_required(self, node_id: str, required: bool) -> bool:
            """Set the required flag on the stored node (by id)."""
            return self._rs.set_node_required(node_id, required)

        def all_nodes(self) -> dict[str, DependencyNode]:
            reachable_ids = self._rs.find_reachable()
            result = {}
            for nid in reachable_ids:
                n = self.get_node(nid)
                if n:
                    result[nid] = n
            return result

        def merge(self, other) -> None:
            if isinstance(other, DependencyGraph):
                self._rs.merge(other._rs)
            else:
                self._rs.merge(other)

        def get_unoptimized_nodes(self) -> list[DependencyNode]:
            rs_nodes = self._rs.get_unoptimized_nodes()
            return [DependencyNode(_rs=n) for n in rs_nodes]

        def has_unoptimized_nodes(self) -> bool:
            return self._rs.has_unoptimized_nodes()

        def is_dead(self, node_id: str) -> bool:
            return self._rs.is_dead(node_id)

        def find_dead_nodes(self) -> list[str]:
            return self._rs.find_dead_nodes()

        def get_node_mut(self, node_id: str) -> DependencyNode | None:
            rs_node = self._rs.get_node(node_id)
            if rs_node is None:
                return None
            return DependencyNode(_rs=rs_node)

        def delete_node(self, node_id: str) -> bool:
            return self._rs.delete_node(node_id)

        def validate(self) -> bool:
            try:
                self._rs.validate()
                return True
            except ValueError:
                return False

        def freeze(self) -> None:
            """Freeze the graph: no new nodes/edges can be added."""
            self._rs.freeze()

        def is_frozen(self) -> bool:
            """Check if the graph is frozen."""
            return self._rs.is_frozen()


# ---------------------------------------------------------------------------
# Pure Python fallback (used when Rust core is not available)
# ---------------------------------------------------------------------------

if not HAS_RUST_CORE:

    @dataclass
    class EdgeProvenance:  # type: ignore[no-redef]
        """Provenance information for a dependency edge."""

        source: tuple[str, int] | None = None
        discovered_by: str = ""
        description: str | None = None

    @dataclass
    class DependencyEdge:  # type: ignore[no-redef]
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
    class DependencyNode:  # type: ignore[no-redef]
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

    class NodeType:  # type: ignore[no-redef]
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

    class EdgeType:  # type: ignore[no-redef]
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

    class DependencyGraph:  # type: ignore[no-redef]
        """Dependency graph — pure Python fallback implementation."""

        def __init__(self) -> None:
            self._nodes: dict[str, DependencyNode] = {}
            self._edges_from: dict[str, list[DependencyEdge]] = {}
            self._edges_to: dict[str, list[DependencyEdge]] = {}
            self._entry_points: list[str] = []
            self._frozen: bool = False

        @property
        def nodes(self) -> dict[str, DependencyNode]:
            return dict(self._nodes)

        def add_node(self, node: DependencyNode) -> bool:
            if self._frozen:
                return False
            if node.id in self._nodes:
                return False
            self._nodes[node.id] = node
            return True

        def get_node(self, node_id: str) -> DependencyNode | None:
            return self._nodes.get(node_id)

        def add_edge(self, edge: DependencyEdge) -> None:
            if self._frozen:
                return
            self._edges_from.setdefault(edge.from_node, []).append(edge)
            self._edges_to.setdefault(edge.to_node, []).append(edge)

        def add_entry_point(self, node_id: str) -> None:
            if self._frozen:
                return
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

        def edges_between(self, src: str, dst: str) -> list[DependencyEdge]:
            """Return every edge from ``src`` to ``dst``, in insertion order.

            Returns an empty list if no such edge exists. Public API
            so diagnostic code (and the compiler's edge lookups) can
            avoid reaching into the private ``_edges_from`` dict.
            """
            return [
                edge
                for edge in self._edges_from.get(src, [])
                if edge.to_node == dst
            ]

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

        def set_node_content(self, node_id: str, content: str) -> bool:
            """Write content through to the stored node (by id)."""
            node = self._nodes.get(node_id)
            if node is None:
                return False
            node.set_content(content)
            return True

        def set_node_required(self, node_id: str, required: bool) -> bool:
            """Set the required flag on the stored node (by id)."""
            node = self._nodes.get(node_id)
            if node is None:
                return False
            node.required = required
            return True

        def mark_reachable_required(self) -> None:
            reachable = self.find_reachable()
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
            for ep in self._entry_points:
                if ep not in self._nodes:
                    return False
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
            return [n for n in self._nodes.values() if n.unoptimized]

        def has_unoptimized_nodes(self) -> bool:
            return any(n.unoptimized for n in self._nodes.values())

        def is_dead(self, node_id: str) -> bool:
            node = self._nodes.get(node_id)
            if node is None:
                return True
            if node.unoptimized:
                return False
            return node_id not in self.find_reachable()

        def find_dead_nodes(self) -> list[str]:
            reachable = self.find_reachable()
            dead: list[str] = []
            for node_id, node in self._nodes.items():
                if node.unoptimized:
                    continue
                if node_id not in reachable:
                    dead.append(node_id)
            return dead

        def get_node_mut(self, node_id: str) -> DependencyNode | None:
            return self._nodes.get(node_id)

        def delete_node(self, node_id: str) -> bool:
            if node_id not in self._nodes:
                return False
            del self._nodes[node_id]
            self._edges_from.pop(node_id, None)
            self._edges_to.pop(node_id, None)
            self._entry_points = [ep for ep in self._entry_points if ep != node_id]
            return True

        def freeze(self) -> None:
            self._frozen = True

        def is_frozen(self) -> bool:
            return getattr(self, "_frozen", False)
