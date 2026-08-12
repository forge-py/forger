"""Tests for the core module."""

from __future__ import annotations

import tempfile
from pathlib import Path


def test_file_discovery() -> None:
    """Test pure Python file discovery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create test structure
        (root / "main.py").write_text("pass")
        (root / "utils.py").write_text("pass")
        (root / "subdir").mkdir()
        (root / "subdir" / "nested.py").write_text("pass")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "main.cpython-310.pyc").write_text("bytecode")

        # Discover Python files
        py_files = list(root.rglob("*.py"))
        assert len(py_files) == 3  # main.py, utils.py, subdir/nested.py


def test_node_types() -> None:
    from forger.core import NodeType

    assert NodeType.PythonModule == "python_module"
    assert NodeType.Resource == "resource"
    assert NodeType.NativeExtension == "native_extension"


def test_edge_types() -> None:
    from forger.core import EdgeType

    assert EdgeType.Import == "import"
    assert EdgeType.FromImport == "from_import"
    assert EdgeType.DynamicImport == "dynamic_import"


def test_node_metadata() -> None:
    from forger.core import DependencyNode, NodeType

    node = DependencyNode.new("test", NodeType.PythonModule).with_metadata(
        "key", "value"
    )

    assert node.id == "test"
    assert node.metadata["key"] == "value"
