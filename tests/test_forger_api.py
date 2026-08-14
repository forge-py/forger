"""Tests for the forger.py API — include, include_module, include_resource."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.api import (
    get_context,
    include,
    include_module,
    include_resource,
    metadata,
)


def _reset_context() -> None:
    """Clear the forger context for a clean test."""
    import forger.api as api_module
    api_module._context = None


def test_include_glob_pattern() -> None:
    _reset_context()
    include("templates/**/*")
    ctx = get_context()
    assert len(ctx.included_globs) >= 1
    assert "templates/**/*" in ctx.included_globs


def test_include_single_file() -> None:
    _reset_context()
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "config.json"
        f.write_text("{}")
        include(str(f))
        ctx = get_context()
        assert len(ctx.included_paths) >= 1


def test_include_directory() -> None:
    _reset_context()
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir) / "data"
        d.mkdir()
        (d / "file1.txt").write_text("a")
        (d / "file2.txt").write_text("b")
        include(str(d))
        ctx = get_context()
        # Should add a glob for the directory
        assert len(ctx.included_globs) >= 1 or len(ctx.included_paths) >= 1


def test_include_recursive_flag() -> None:
    _reset_context()
    include("src/**/*.py", recursive=True)
    ctx = get_context()
    assert len(ctx.included_globs) >= 1


def test_include_module() -> None:
    _reset_context()
    include_module("myapp.plugins.auth")
    include_module("myapp.plugins.cache")
    ctx = get_context()
    assert "myapp.plugins.auth" in ctx.included_modules
    assert "myapp.plugins.cache" in ctx.included_modules


def test_include_resource() -> None:
    _reset_context()
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "schema.json"
        f.write_text("{}")
        include_resource(str(f))
        ctx = get_context()
        assert len(ctx.included_resources) >= 1


def test_metadata() -> None:
    _reset_context()
    metadata("author", "test")
    metadata("version", "1.0")
    ctx = get_context()
    assert ctx.metadata["author"] == "test"
    assert ctx.metadata["version"] == "1.0"


def test_multiple_includes() -> None:
    _reset_context()
    include("templates/**/*")
    include("static/**/*")
    include("data/*.csv")
    include_module("pkg.a")
    include_module("pkg.b")
    include_module("pkg.c")

    ctx = get_context()
    assert len(ctx.included_globs) == 3
    assert len(ctx.included_modules) == 3


def test_context_isolation() -> None:
    """Test that contexts are properly reset between calls."""
    _reset_context()
    include("a/**/*")
    ctx1 = get_context()
    assert len(ctx1.included_globs) == 1

    _reset_context()
    include("b/**/*")
    ctx2 = get_context()
    assert len(ctx2.included_globs) == 1
    assert "a/**/*" not in ctx2.included_globs


def test_include_with_path_object() -> None:
    _reset_context()
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "file.txt"
        f.write_text("data")
        include(f)
        ctx = get_context()
        assert len(ctx.included_paths) >= 1


def test_include_nonexistent_path_as_glob() -> None:
    _reset_context()
    include("nonexistent_pattern/**/*.py")
    ctx = get_context()
    # Should be treated as a glob pattern
    assert len(ctx.included_globs) >= 1


def test_forger_py_execution() -> None:
    """Test executing a forger.py file."""
    import runpy

    with tempfile.TemporaryDirectory() as tmpdir:
        forger_py = Path(tmpdir) / "forger.py"
        forger_py.write_text(
            "from forger import include, include_module\n"
            'include("templates/**/*")\n'
            'include_module("myapp.plugins.auth")\n'
            'include_module("myapp.plugins.db")\n'
        )

        _reset_context()
        runpy.run_path(
            str(forger_py),
            init_globals={"__file__": str(forger_py)},
            run_name="__forger__",
        )

        ctx = get_context()
        assert len(ctx.included_globs) >= 1
        assert "myapp.plugins.auth" in ctx.included_modules
        assert "myapp.plugins.db" in ctx.included_modules
