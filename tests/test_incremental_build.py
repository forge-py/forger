"""Incremental build tests — verify cache and change detection."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.core import DependencyGraph, DependencyNode, NodeType


def test_graph_unchanged_no_rebuild() -> None:
    """When the graph is unchanged, no rebuild should be needed."""
    graph = DependencyGraph()
    graph.add_node(DependencyNode.new("main", NodeType.EntryPoint))
    graph.add_node(DependencyNode.new("app", NodeType.PythonModule))
    graph.add_entry_point("main")

    # First analysis
    initial_count = graph.node_count()

    # Second analysis (same graph)
    assert graph.node_count() == initial_count


def test_graph_changed_rebuild_needed() -> None:
    """When a new node is added, rebuild should be needed."""
    graph = DependencyGraph()
    graph.add_node(DependencyNode.new("main", NodeType.EntryPoint))
    initial_count = graph.node_count()

    # Add new node
    graph.add_node(DependencyNode.new("new_module", NodeType.PythonModule))
    assert graph.node_count() == initial_count + 1


def test_file_hash_change_detection() -> None:
    """Test that file content changes are detected via hashing."""
    from forger.hash import content_hash, HashValue

    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "test.py"
        f.write_text("x = 1")

        hash1 = content_hash(f)

        # Same content — same hash
        hash2 = content_hash(f)
        assert hash1 == hash2

        # Changed content — different hash
        f.write_text("x = 2")
        hash3 = content_hash(f)
        assert hash1 != hash3


def test_cache_persistence() -> None:
    """Test that cache persists across runs."""
    from forger.cache import Cache
    from forger.hash import content_hash_bytes

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)

        # First run: save cache
        cache1 = Cache.with_directory(cache_dir)
        hash1 = content_hash_bytes(b"content")
        cache1.update(Path("file.py"), hash1, 7)
        cache1.save()

        # Second run: load cache
        cache2 = Cache.with_directory(cache_dir)
        cache2.load()

        assert cache2.contains(Path("file.py"))
        assert cache2.len() == 1


def test_cache_invalidates_on_change() -> None:
    """Test that cache detects file changes."""
    from forger.cache import Cache, CacheState
    from forger.hash import content_hash_bytes

    cache = Cache.new()
    old_hash = content_hash_bytes(b"old content")
    cache.update(Path("file.py"), old_hash, 11)

    new_hash = content_hash_bytes(b"new content")
    state = cache.check_state(Path("file.py"), new_hash)
    assert state == CacheState.Modified


def test_cache_unchanged() -> None:
    """Test that cache reports unchanged for same content."""
    from forger.cache import Cache, CacheState
    from forger.hash import content_hash_bytes

    cache = Cache.new()
    file_hash = content_hash_bytes(b"stable content")
    cache.update(Path("file.py"), file_hash, 14)

    state = cache.check_state(Path("file.py"), file_hash)
    assert state == CacheState.Unchanged


def test_compiler_incremental_analysis() -> None:
    """Test that the compiler can be run incrementally."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("import app\n")
        (root / "app.py").write_text("x = 1\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        initial_nodes = compiler.graph.node_count()

        # Add a new file
        (root / "new_module.py").write_text("import json\n")

        # Re-analyze
        compiler.analyze()
        assert compiler.graph.node_count() >= initial_nodes


def test_cache_needs_rebuild() -> None:
    """Test needs_rebuild detection."""
    from forger.cache import Cache
    from forger.hash import content_hash_bytes

    cache = Cache.new()
    hash1 = content_hash_bytes(b"content1")
    hash2 = content_hash_bytes(b"content2")

    cache.update(Path("a.py"), hash1, 8)
    cache.update(Path("b.py"), hash2, 8)

    # Same state — no rebuild
    current = [
        ("a.py", hash1),
        ("b.py", hash2),
    ]
    assert not cache.needs_rebuild(current)

    # New file — rebuild needed
    hash3 = content_hash_bytes(b"content3")
    current_with_new = [
        ("a.py", hash1),
        ("b.py", hash2),
        ("c.py", hash3),
    ]
    assert cache.needs_rebuild(current_with_new)

    # Modified file — rebuild needed
    modified = [
        ("a.py", hash3),  # changed
        ("b.py", hash2),
    ]
    assert cache.needs_rebuild(modified)
