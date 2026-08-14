"""VFS tests — virtual filesystem operations."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.core import DependencyGraph, DependencyNode, NodeType


def test_compiler_source_discovery() -> None:
    """Test that the compiler discovers all Python source files."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a realistic project
        (root / "main.py").write_text("pass\n")
        (root / "app.py").write_text("pass\n")

        pkg = root / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("pass\n")
        (pkg / "utils.py").write_text("pass\n")

        sub = pkg / "sub"
        sub.mkdir()
        (sub / "__init__.py").write_text("")
        (sub / "nested.py").write_text("pass\n")

        # Should skip these
        pycache = root / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-310.pyc").write_text("bytecode")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should discover 7 .py files (main, app, __init__, core, utils, sub/__init__, nested)
        assert len(compiler.source_files) >= 7


def test_compiler_skips_venv() -> None:
    """Test that the compiler skips .venv directories."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")

        venv = root / ".venv"
        venv_site = venv / "lib" / "site-packages"
        venv_site.mkdir(parents=True)
        (venv_site / "django.py").write_text("pass\n")
        (venv_site / "flask.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should only find main.py
        assert len(compiler.source_files) == 1


def test_compiler_handles_binary_files() -> None:
    """Test that the compiler handles non-Python files gracefully."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")
        (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (root / "data.csv").write_text("a,b,c\n1,2,3\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should only find .py files
        assert len(compiler.source_files) == 1


def test_compiler_handles_empty_project() -> None:
    """Test that the compiler handles an empty project."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        output = root / "app.forge"

        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert len(compiler.source_files) == 0


def test_compiler_handles_syntax_errors() -> None:
    """Test that the compiler handles files with syntax errors."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")
        (root / "broken.py").write_text("def foo( invalid\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should not crash
        assert compiler.graph is not None


def test_compiler_handles_unicode_files() -> None:
    """Test that the compiler handles files with Unicode content."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text(
            "# -*- coding: utf-8 -*-\n"
            "message = 'Hello, World! 🌍'\n"
            "import app\n",
            encoding="utf-8",
        )
        (root / "app.py").write_text(
            "name = '日本語'\n",
            encoding="utf-8",
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert len(compiler.source_files) == 2


def test_compiler_handles_large_files() -> None:
    """Test that the compiler handles large source files."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a large file with many imports
        imports = "\n".join(f"import os\n" * 1000)
        (root / "main.py").write_text(imports)

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None


def test_compiler_handles_deep_nesting() -> None:
    """Test that the compiler handles deeply nested packages."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create deep nesting: a/b/c/d/e/f/g.py
        deep = root / "a" / "b" / "c" / "d" / "e" / "f"
        deep.mkdir(parents=True)
        (deep / "g.py").write_text("pass\n")

        for d in [root / "a", root / "a" / "b", root / "a" / "b" / "c",
                  root / "a" / "b" / "c" / "d", root / "a" / "b" / "c" / "d" / "e",
                  root / "a" / "b" / "c" / "d" / "e" / "f"]:
            (d / "__init__.py").write_text("")

        (root / "main.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 8


def test_compiler_entry_point_resolution() -> None:
    """Test that the compiler correctly sets up entry points."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert "main" in compiler.graph.entry_points()


def test_compiler_path_to_module_edge_cases() -> None:
    """Test path-to-module conversion edge cases."""
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        output = root / "app.forge"

        compiler = Compiler(root, "main", output)

        # .pyi stub file
        stub_file = root / "pkg" / "module.pyi"
        result = compiler._path_to_module(stub_file)
        assert result == "pkg.module"

        # File not under project root
        outside = Path("/outside/project/file.py")
        result = compiler._path_to_module(outside)
        assert result is None
