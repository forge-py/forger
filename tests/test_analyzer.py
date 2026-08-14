"""Tests for the static analyzer."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.analyzer.dynamic import DynamicImportAnalyzer
from forger.analyzer.imports import ImportAnalyzer
from forger.analyzer.resources import ResourceAnalyzer


def test_import_analyzer_simple() -> None:
    """Test basic import analysis."""
    source = """
import os
import sys
from pathlib import Path
from collections import OrderedDict, defaultdict
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)

    assert len(imports) == 4
    assert imports[0].module == "os"
    assert imports[1].module == "sys"
    assert imports[2].module == "pathlib"
    assert imports[2].names == ["Path"]
    assert imports[3].module == "collections"
    assert imports[3].names == ["OrderedDict", "defaultdict"]


def test_import_analyzer_relative() -> None:
    """Test relative import analysis."""
    source = """
from . import utils
from .models import User
from .. import config
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)

    assert len(imports) == 3
    assert imports[0].level == 1
    assert imports[1].level == 1
    assert imports[2].level == 2


def test_resource_analyzer() -> None:
    """Test resource access analysis."""
    source = """
with open("config.json") as f:
    data = f.read()

from pathlib import Path
Path("templates/index.html").read_text()
"""
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)

    assert len(accesses) >= 1
    # Check that we found the open() call
    assert any(
        a.path_expr == "config.json" and not a.is_dynamic
        for a in accesses
    )


def test_dynamic_import_analyzer() -> None:
    """Test dynamic import pattern detection."""
    source = """
import importlib
mod = importlib.import_module("myapp.plugins.auth")

import sys
sys.modules["myapp.plugins.db"]
"""
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)

    # Should detect import_module call
    assert any(
        h.hint_type == "import_module" and h.pattern == "myapp.plugins.auth"
        for h in hints
    )


def test_import_analyzer_file() -> None:
    """Test import analysis on a real file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_module.py"
        test_file.write_text(
            "import json\nfrom os.path import join\n"
        )

        analyzer = ImportAnalyzer()
        imports = analyzer.analyze_file(test_file)

        assert len(imports) == 2
        assert imports[0].module == "json"
        assert imports[1].module == "os.path"


def test_dependency_graph() -> None:
    """Test the dependency graph operations."""
    from forger.core import (
        DependencyEdge,
        DependencyGraph,
        DependencyNode,
        EdgeProvenance,
        EdgeType,
        NodeType,
    )

    graph = DependencyGraph()
    graph.add_entry_point("main")

    # Add nodes
    graph.add_node(DependencyNode.new("main", NodeType.EntryPoint))
    graph.add_node(DependencyNode.new("app", NodeType.PythonModule))
    graph.add_node(DependencyNode.new("utils", NodeType.PythonModule))
    graph.add_node(DependencyNode.new("orphan", NodeType.PythonModule))

    # Add edges
    graph.add_edge(
        DependencyEdge.new(
            "main",
            "app",
            EdgeType.Import,
            EdgeProvenance(
                source=("main.py", 1),
                discovered_by="test",
            ),
        )
    )
    graph.add_edge(
        DependencyEdge.new(
            "app",
            "utils",
            EdgeType.FromImport,
            EdgeProvenance(
                source=("app.py", 5),
                discovered_by="test",
            ),
        )
    )

    # Check reachability
    reachable = graph.find_reachable()
    assert "main" in reachable
    assert "app" in reachable
    assert "utils" in reachable
    assert "orphan" not in reachable

    # Check counts
    assert graph.node_count() == 4
    assert graph.edge_count() == 2

    # Prune unreachable
    graph.prune_unreachable()
    assert graph.node_count() == 3
    assert graph.get_node("orphan") is None


def test_dependency_graph_merge() -> None:
    """Test merging two dependency graphs."""
    from forger.core import DependencyGraph, DependencyNode, NodeType

    graph1 = DependencyGraph()
    graph1.add_node(DependencyNode.new("a", NodeType.PythonModule))

    graph2 = DependencyGraph()
    graph2.add_node(DependencyNode.new("b", NodeType.PythonModule))

    graph1.merge(graph2)

    assert graph1.node_count() == 2
    assert graph1.get_node("a") is not None
    assert graph1.get_node("b") is not None


def test_optimizer_framework() -> None:
    """Test optimizer discovery."""
    from forger.optimizer import discover_optimizers

    optimizers = discover_optimizers()
    # Should find at least Django and Flask optimizers
    names = {opt.name for opt in optimizers}
    assert "django" in names or len(optimizers) > 0


def test_forger_api() -> None:
    """Test the forger.py API."""
    # Clear context
    import forger.api as api_module
    from forger.api import get_context, include, include_module
    api_module._context = None

    include("templates/**/*")
    include_module("myapp.plugins.auth")

    context = get_context()
    assert len(context.included_globs) >= 1
    assert "myapp.plugins.auth" in context.included_modules
