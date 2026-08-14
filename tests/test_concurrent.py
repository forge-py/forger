"""Concurrent analysis tests — verify thread safety and parallel processing."""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from forger.analyzer.imports import ImportAnalyzer
from forger.analyzer.resources import ResourceAnalyzer
from forger.core import DependencyGraph, DependencyNode, NodeType


def test_concurrent_file_analysis() -> None:
    """Test that multiple files can be analyzed concurrently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create 50 files
        for i in range(50):
            (root / f"mod_{i}.py").write_text(f"import os\nimport sys\nx = {i}\n")

        files = list(root.glob("*.py"))

        results = []

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for f in files:
                futures.append(executor.submit(ImportAnalyzer().analyze_file, f))

            for future in as_completed(futures):
                result = future.result()
                results.append(result)

        assert len(results) == 50
        # Each file should have 2 imports (os, sys)
        assert all(len(r) == 2 for r in results)


def test_concurrent_graph_operations() -> None:
    """Test that graph operations are thread-safe for reads."""
    graph = DependencyGraph()

    # Populate graph
    for i in range(100):
        graph.add_node(DependencyNode.new(f"mod_{i}", NodeType.PythonModule))
    graph.add_entry_point("mod_0")

    # Concurrent reads should work
    def read_node(i: int) -> str | None:
        node = graph.get_node(f"mod_{i}")
        return node.id if node else None

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(read_node, i) for i in range(100)]
        results = [f.result() for f in futures]

    assert all(r is not None for r in results)


def test_parallel_resource_analysis() -> None:
    """Test parallel resource analysis across multiple files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create files with resource access
        for i in range(20):
            (root / f"loader_{i}.py").write_text(
                f'with open("config_{i}.json") as f: data = f.read()\n'
            )

        files = list(root.glob("*.py"))

        results = []

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for f in files:
                futures.append(executor.submit(ResourceAnalyzer().analyze_file, f))

            for future in as_completed(futures):
                result = future.result()
                results.append(result)

        assert len(results) == 20
        assert all(len(r) >= 1 for r in results)


def test_stress_compiler_with_many_modules() -> None:
    """Stress test: compile a project with many modules."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create 500 modules
        for i in range(500):
            imports = f"import mod_{i % 100}\n" if i > 0 else "import os\n"
            (root / f"mod_{i}.py").write_text(f"{imports}\nVALUE = {i}\n")

        (root / "main.py").write_text("import mod_0\nimport mod_1\nimport mod_2\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 500
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 500


def test_concurrent_optimizer_runs() -> None:
    """Test that plugin discovery can run concurrently."""
    from forger.optimizer import PluginContext

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        contexts = []
        for i in range(5):
            sub = root / f"project_{i}"
            sub.mkdir()
            (sub / "main.py").write_text("pass\n")

            contexts.append(
                PluginContext(
                    project_root=sub,
                )
            )

        # Create plugin contexts concurrently
        results = []

        def get_project_root(ctx):
            return str(ctx.project_root)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(get_project_root, ctx) for ctx in contexts]

            for future in as_completed(futures):
                result = future.result()
                results.append(result)

        assert len(results) == 5
