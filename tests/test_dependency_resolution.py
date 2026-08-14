"""Dependency resolution tests — graph algorithms and edge cases."""

from __future__ import annotations

from forger.core import (
    DependencyEdge,
    DependencyGraph,
    DependencyNode,
    EdgeProvenance,
    EdgeType,
    NodeType,
)


def create_node(node_id: str, node_type: str) -> DependencyNode:
    return DependencyNode.new(node_id, node_type)


def create_edge(
    from_id: str, to_id: str, edge_type: str
) -> DependencyEdge:
    return DependencyEdge.new(
        from_id,
        to_id,
        edge_type,
        EdgeProvenance(source=("test.py", 1), discovered_by="test"),
    )


# --- Graph construction ---

def test_empty_graph() -> None:
    graph = DependencyGraph()
    assert graph.node_count() == 0
    assert graph.edge_count() == 0
    assert len(graph.entry_points()) == 0


def test_single_node_graph() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("main", NodeType.EntryPoint))
    graph.add_entry_point("main")
    assert graph.node_count() == 1
    assert graph.edge_count() == 0


def test_linear_chain() -> None:
    graph = DependencyGraph()
    for name in ["main", "a", "b", "c"]:
        graph.add_node(create_node(name, NodeType.PythonModule))
    graph.add_edge(create_edge("main", "a", EdgeType.Import))
    graph.add_edge(create_edge("a", "b", EdgeType.Import))
    graph.add_edge(create_edge("b", "c", EdgeType.Import))
    graph.add_entry_point("main")

    assert graph.node_count() == 4
    assert graph.edge_count() == 3

    reachable = graph.find_reachable()
    assert reachable == {"main", "a", "b", "c"}


def test_star_graph() -> None:
    """main -> a, b, c, d, e"""
    graph = DependencyGraph()
    graph.add_node(create_node("main", NodeType.EntryPoint))
    for leaf in ["a", "b", "c", "d", "e"]:
        graph.add_node(create_node(leaf, NodeType.PythonModule))
        graph.add_edge(create_edge("main", leaf, EdgeType.Import))
    graph.add_entry_point("main")

    reachable = graph.find_reachable()
    assert len(reachable) == 6


def test_deep_chain() -> None:
    """Test a very deep dependency chain."""
    graph = DependencyGraph()
    for i in range(100):
        name = f"mod_{i}"
        graph.add_node(create_node(name, NodeType.PythonModule))
        if i > 0:
            graph.add_edge(
                create_edge(f"mod_{i - 1}", name, EdgeType.Import)
            )
    graph.add_entry_point("mod_0")

    reachable = graph.find_reachable()
    assert len(reachable) == 100


def test_wide_graph() -> None:
    """Test a wide dependency graph (many parallel imports)."""
    graph = DependencyGraph()
    graph.add_node(create_node("main", NodeType.EntryPoint))
    for i in range(200):
        name = f"mod_{i}"
        graph.add_node(create_node(name, NodeType.PythonModule))
        graph.add_edge(create_edge("main", name, EdgeType.Import))
    graph.add_entry_point("main")

    reachable = graph.find_reachable()
    assert len(reachable) == 201


# --- Reachability ---

def test_multiple_entry_points() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("entry_a", NodeType.EntryPoint))
    graph.add_node(create_node("entry_b", NodeType.EntryPoint))
    graph.add_node(create_node("shared", NodeType.PythonModule))
    graph.add_node(create_node("only_a", NodeType.PythonModule))
    graph.add_node(create_node("only_b", NodeType.PythonModule))

    graph.add_edge(create_edge("entry_a", "shared", EdgeType.Import))
    graph.add_edge(create_edge("entry_a", "only_a", EdgeType.Import))
    graph.add_edge(create_edge("entry_b", "shared", EdgeType.Import))
    graph.add_edge(create_edge("entry_b", "only_b", EdgeType.Import))

    graph.add_entry_point("entry_a")
    graph.add_entry_point("entry_b")

    reachable = graph.find_reachable()
    assert "shared" in reachable
    assert "only_a" in reachable
    assert "only_b" in reachable


def test_disconnected_components() -> None:
    graph = DependencyGraph()
    # Component 1
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_node(create_node("b", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "b", EdgeType.Import))
    graph.add_entry_point("a")

    # Component 2 (disconnected)
    graph.add_node(create_node("c", NodeType.PythonModule))
    graph.add_node(create_node("d", NodeType.PythonModule))
    graph.add_edge(create_edge("c", "d", EdgeType.Import))

    reachable = graph.find_reachable()
    assert "a" in reachable
    assert "b" in reachable
    assert "c" not in reachable
    assert "d" not in reachable


def test_self_loop() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "a", EdgeType.Import))
    graph.add_entry_point("a")

    reachable = graph.find_reachable()
    assert reachable == {"a"}


def test_bidirectional_edge() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_node(create_node("b", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "b", EdgeType.Import))
    graph.add_edge(create_edge("b", "a", EdgeType.Import))
    graph.add_entry_point("a")

    reachable = graph.find_reachable()
    assert reachable == {"a", "b"}


# --- Pruning ---

def test_prune_removes_unreachable() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("main", NodeType.EntryPoint))
    graph.add_node(create_node("used", NodeType.PythonModule))
    graph.add_node(create_node("unused", NodeType.PythonModule))
    graph.add_edge(create_edge("main", "used", EdgeType.Import))
    graph.add_entry_point("main")

    graph.prune_unreachable()

    assert graph.get_node("main") is not None
    assert graph.get_node("used") is not None
    assert graph.get_node("unused") is None


def test_prune_all_unreachable() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_node(create_node("b", NodeType.PythonModule))
    # No entry points, no edges

    graph.prune_unreachable()
    assert graph.node_count() == 0


# --- Marking ---

def test_mark_reachable_required() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("main", NodeType.EntryPoint))
    graph.add_node(create_node("used", NodeType.PythonModule))
    graph.add_node(create_node("unused", NodeType.PythonModule))
    graph.add_edge(create_edge("main", "used", EdgeType.Import))
    graph.add_entry_point("main")

    graph.mark_reachable_required()

    assert graph.get_node("main").required is True
    assert graph.get_node("used").required is True
    assert graph.get_node("unused").required is False


# --- Node types ---

def test_nodes_by_type() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("mod1", NodeType.PythonModule))
    graph.add_node(create_node("mod2", NodeType.PythonModule))
    graph.add_node(create_node("res1", NodeType.Resource))
    graph.add_node(create_node("ext1", NodeType.ExternalPackage))

    assert len(graph.nodes_by_type(NodeType.PythonModule)) == 2
    assert len(graph.nodes_by_type(NodeType.Resource)) == 1
    assert len(graph.nodes_by_type(NodeType.ExternalPackage)) == 1
    assert len(graph.nodes_by_type(NodeType.NativeExtension)) == 0


# --- Edge traversal ---

def test_dependencies_of() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_node(create_node("b", NodeType.PythonModule))
    graph.add_node(create_node("c", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "b", EdgeType.Import))
    graph.add_edge(create_edge("a", "c", EdgeType.Import))

    deps = graph.dependencies_of("a")
    assert set(deps) == {"b", "c"}


def test_dependents_of() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_node(create_node("b", NodeType.PythonModule))
    graph.add_node(create_node("c", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "c", EdgeType.Import))
    graph.add_edge(create_edge("b", "c", EdgeType.Import))

    dependents = graph.dependents_of("c")
    assert set(dependents) == {"a", "b"}


# --- Validation ---

def test_validate_good_graph() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_node(create_node("b", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "b", EdgeType.Import))
    graph.add_entry_point("a")

    assert graph.validate() is True


def test_validate_missing_target() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_edge(create_edge("a", "nonexistent", EdgeType.Import))

    assert graph.validate() is False


def test_validate_missing_source() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("b", NodeType.PythonModule))
    graph.add_edge(create_edge("nonexistent", "b", EdgeType.Import))

    assert graph.validate() is False


def test_validate_entry_point_missing() -> None:
    graph = DependencyGraph()
    graph.add_node(create_node("a", NodeType.PythonModule))
    graph.add_entry_point("nonexistent")

    assert graph.validate() is False


# --- Merge ---

def test_merge_preserves_nodes() -> None:
    g1 = DependencyGraph()
    g1.add_node(create_node("a", NodeType.PythonModule))

    g2 = DependencyGraph()
    g2.add_node(create_node("b", NodeType.PythonModule))
    g2.add_node(create_node("c", NodeType.PythonModule))

    g1.merge(g2)
    assert g1.node_count() == 3


def test_merge_preserves_edges() -> None:
    g1 = DependencyGraph()
    g1.add_node(create_node("a", NodeType.PythonModule))
    g1.add_node(create_node("b", NodeType.PythonModule))
    g1.add_edge(create_edge("a", "b", EdgeType.Import))

    g2 = DependencyGraph()
    g2.add_node(create_node("c", NodeType.PythonModule))
    g2.add_node(create_node("d", NodeType.PythonModule))
    g2.add_edge(create_edge("c", "d", EdgeType.Import))

    g1.merge(g2)
    assert g1.edge_count() == 2


def test_merge_entry_points() -> None:
    g1 = DependencyGraph()
    g1.add_node(create_node("a", NodeType.EntryPoint))
    g1.add_entry_point("a")

    g2 = DependencyGraph()
    g2.add_node(create_node("b", NodeType.EntryPoint))
    g2.add_entry_point("b")

    g1.merge(g2)
    assert set(g1.entry_points()) == {"a", "b"}


# --- Metadata ---

def test_node_metadata() -> None:
    node = create_node("test", NodeType.PythonModule).with_metadata(
        "key1", "value1"
    ).with_metadata("key2", "value2")

    assert node.metadata["key1"] == "value1"
    assert node.metadata["key2"] == "value2"


def test_edge_provenance() -> None:
    prov = EdgeProvenance(
        source=("app.py", 42),
        discovered_by="import_analyzer",
        description="import os",
    )
    assert prov.source == ("app.py", 42)
    assert prov.discovered_by == "import_analyzer"


# --- Stress test ---

def test_large_graph_performance() -> None:
    """Stress test with a large graph."""
    graph = DependencyGraph()

    # 1000 nodes, chain + star pattern
    graph.add_node(create_node("main", NodeType.EntryPoint))
    for i in range(1000):
        graph.add_node(create_node(f"mod_{i}", NodeType.PythonModule))
        graph.add_edge(
            create_edge(
                "main" if i < 100 else f"mod_{i // 10}",
                f"mod_{i}",
                EdgeType.Import,
            )
        )
    graph.add_entry_point("main")

    reachable = graph.find_reachable()
    assert len(reachable) == 1001  # main + 1000 mods
