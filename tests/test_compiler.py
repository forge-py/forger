"""Tests for the compiler module."""

from __future__ import annotations

import tempfile
from pathlib import Path


def test_compiler_initialization() -> None:
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        output = project_root / "app.forge"

        compiler = Compiler(
            project_root=project_root,
            entry_point="main",
            output_path=output,
        )

        assert compiler.project_root == project_root
        assert compiler.entry_point == "main"
        assert compiler.output_path == output


def test_compiler_with_source_files() -> None:
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)

        # Create some source files
        (project_root / "main.py").write_text("import app\n")
        (project_root / "app.py").write_text("import utils\n")
        (project_root / "utils.py").write_text("x = 1\n")

        output = project_root / "app.forge"
        compiler = Compiler(
            project_root=project_root,
            entry_point="main",
            output_path=output,
        )

        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 3
        assert len(compiler.source_files) >= 3


def test_compiler_diagnostic_summary() -> None:
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        (project_root / "main.py").write_text("pass\n")

        output = project_root / "app.forge"
        compiler = Compiler(
            project_root=project_root,
            entry_point="main",
            output_path=output,
        )

        compiler.analyze()
        summary = compiler.diagnostic_summary()

        assert "Compilation Summary" in summary
        assert "main" in summary


def test_path_to_module() -> None:
    from forger.compiler import Compiler

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        output = project_root / "app.forge"

        compiler = Compiler(
            project_root=project_root,
            entry_point="main",
            output_path=output,
        )

        # Test path conversion
        test_file = project_root / "mypackage" / "module.py"
        module_name = compiler._path_to_module(test_file)
        assert module_name == "mypackage.module"

        # Test __init__ handling
        init_file = project_root / "mypackage" / "__init__.py"
        module_name = compiler._path_to_module(init_file)
        assert module_name == "mypackage"
