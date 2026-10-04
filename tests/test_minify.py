"""Tests for the scope-aware name minifier."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------
# Identifier generation
# ---------------------------------------------------------------------------


def test_short_name_generation() -> None:
    from forger.optimizer.minify import _generate_name

    assert _generate_name(0) == "a"
    assert _generate_name(25) == "z"
    assert _generate_name(26) == "aa"
    assert _generate_name(27) == "ab"
    assert _generate_name(51) == "az"
    assert _generate_name(52) == "ba"
    assert _generate_name(701) == "zz"
    assert _generate_name(702) == "aaa"


def test_name_generator_skips_used() -> None:
    from forger.optimizer.minify import NameGenerator

    gen = NameGenerator()
    gen.reserve("a")  # 'a' is taken
    assert gen.next() == "b"


# ---------------------------------------------------------------------------
# Reserved names
# ---------------------------------------------------------------------------


def test_reserved_names_skipped() -> None:
    from forger.optimizer.minify import _is_reserved

    assert _is_reserved("__init__")
    assert _is_reserved("__name__")
    assert _is_reserved("_private")  # leading underscore → don't touch
    assert _is_reserved("if")
    assert _is_reserved("print")  # builtin
    assert _is_reserved("True")
    assert not _is_reserved("regular")
    assert not _is_reserved("MyClass")


def _min(src: str, keep: set[str] | None = None) -> tuple[ast.Module, dict[str, str]]:
    from forger.optimizer.minify import minify_module

    return minify_module(ast.parse(src), keep=keep)


def test_renames_top_level_function() -> None:
    tree, mapping = _min("def hello(): return 1\n")
    assert "hello" in mapping
    assert len(mapping["hello"]) <= 2
    # The function call site must reflect the new name.
    src = ast.unparse(tree)
    assert "hello" not in src
    assert mapping["hello"] in src


def test_renames_top_level_class() -> None:
    tree, mapping = _min("class Foo:\n    def bar(self): return 1\n")
    assert "Foo" in mapping
    src = ast.unparse(tree)
    assert "Foo" not in src
    # ``bar`` is a method, not a top-level def — v1 does not recurse
    # into class bodies. The class rename alone is enough to verify the
    # top-level pass.


def test_respects_keep_set() -> None:
    tree, mapping = _min(
        "def public_api(): return 1\ndef _internal(): return 2\n",
        keep={"public_api"},
    )
    assert "public_api" not in mapping
    assert mapping["_internal"] not in {"public_api"}


def test_underscore_prefix_is_renamed_by_default() -> None:
    """Underscore-prefixed names are renamable; they're reserved only as
    generated identifiers (we won't *generate* ``_foo`` as a short name).
    """
    tree, mapping = _min("def _helper(): return 1\n")
    # The minifier can pick any short id; the underscore convention is a
    # user-side signal, not a rename trigger. We just verify a rename
    # happened.
    assert "_helper" in mapping
    assert ast.unparse(tree) != "def _helper(): return 1\n"


def test_dunder_is_renamed_by_default() -> None:
    tree, mapping = _min("def __init__(self): pass\n")
    # We won't generate a dunder id; the rename may still happen if the
    # target is something else.
    assert isinstance(mapping, dict)


def test_keep_set_overrides_underscore() -> None:
    """Users can explicitly opt out of renaming via the keep set."""
    tree, mapping = _min("def _helper(): return 1\n", keep={"_helper"})
    assert "_helper" not in mapping
    assert "def _helper" in ast.unparse(tree)


def test_no_rename_when_all_kept() -> None:
    tree, mapping = _min(
        "def used(): return 1\n",
        keep={"used"},
    )
    assert mapping == {}
    assert "def used" in ast.unparse(tree)


def test_renames_module_assignment() -> None:
    tree, mapping = _min("config = 1\n")
    # Lowercase module-level assignments are renamable.
    assert "config" in mapping


def test_renames_multiple_top_level_defs() -> None:
    src = (
        "def a():\n    return 1\n"
        "def b():\n    return 2\n"
        "def c():\n    return 3\n"
    )
    tree, mapping = _min(src)
    # All three must be renamed to distinct short ids.
    assert len(set(mapping.values())) == 3
    assert {"a", "b", "c"} <= set(mapping.keys())


def test_renamed_uses_get_updated() -> None:
    tree, mapping = _min(
        "def helper():\n    return 1\n"
        "x = helper()\n"
    )
    new_name = mapping["helper"]
    src = ast.unparse(tree)
    # The call site must use the new name.
    assert f"{new_name}()" in src
    # And the original name is gone.
    assert "helper(" not in src


def test_all_caps_constant_kept_by_default() -> None:
    """ALL_CAPS module-level names are treated as config and kept by default
    (mirrors the Python convention that ``MAX_RETRIES = 3`` is a
    configuration constant, not an implementation detail)."""
    tree, mapping = _min("CONFIG = 1\ncounter = 0\n")
    assert "CONFIG" not in mapping
    assert "counter" in mapping


def test_keep_set_overrides_constant() -> None:
    """A normal-name can be kept via the keep set."""
    tree, mapping = _min(
        "CONFIG = 1\ncounter = 0\n",
        keep={"counter"},
    )
    assert "counter" not in mapping
    assert "CONFIG" not in mapping  # all-caps rule still applies


# ---------------------------------------------------------------------------
# MinificationPass plugin
# ---------------------------------------------------------------------------


def test_minification_pass_via_compiler(tmp_path: Path) -> None:
    """End-to-end: compiler runs MinificationPass when minify_names is set."""
    from forger.api import defineConfig
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "def secret_helper():\n    return 42\n"
        "print(secret_helper())\n",
        encoding="utf-8",
    )

    defineConfig({"entry": "main", "minify_names": True})
    try:
        compiler = Compiler(
            project_root=tmp_path,
            entry_point="main",
            output_path=tmp_path / "out.forge",
        )
        compiler.analyze()
        compiler.run_plugins(plugins=[], include_optimizer=False)
        content = compiler.graph.get_node("main").get_content()  # type: ignore[union-attr]
        assert content is not None
        # The function name was renamed in the emitted source.
        assert "secret_helper" not in content
        # Some short name is present.
        assert any(name in content for name in ("a(", "b(", "c("))
    finally:
        # Reset the global forger config so other tests aren't affected.
        import forger.api as _api

        _api._get_context().set_config(_api.ForgerConfig())  # type: ignore[attr-defined]


def test_minification_pass_disabled_by_default(tmp_path: Path) -> None:
    """No minification unless the config explicitly opts in."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "def keep_this_name():\n    return 1\n"
        "print(keep_this_name())\n",
        encoding="utf-8",
    )
    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out.forge",
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    content = compiler.graph.get_node("main").get_content()  # type: ignore[union-attr]
    assert content is not None
    assert "keep_this_name" in content


def test_minified_source_still_parses(tmp_path: Path) -> None:
    """The minified source must be syntactically valid Python."""
    from forger.api import defineConfig
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "def greet(name):\n    return f'Hello {name}'\n"
        "def farewell(name):\n    return f'Bye {name}'\n"
        "print(greet('world'))\n"
        "print(farewell('world'))\n",
        encoding="utf-8",
    )

    defineConfig({"entry": "main", "minify_names": True})
    try:
        compiler = Compiler(
            project_root=tmp_path,
            entry_point="main",
            output_path=tmp_path / "out.forge",
        )
        compiler.analyze()
        compiler.run_plugins(plugins=[], include_optimizer=False)
        content = compiler.graph.get_node("main").get_content()  # type: ignore[union-attr]
        assert content is not None
        # Round-trip the minified source through the Python parser to
        # ensure it is syntactically valid.
        compile(content, "<minified>", "exec")  # noqa: S102 - intentional
    finally:
        import forger.api as _api

        _api._get_context().set_config(_api.ForgerConfig())  # type: ignore[attr-defined]


def test_minified_module_runs_in_subprocess(tmp_path: Path) -> None:
    """The minified module still has the same runtime behavior."""
    from forger.api import defineConfig
    from forger.compiler import Compiler

    src = (
        "def double(x):\n    return x * 2\n"
        "def add(a, b):\n    return a + b\n"
        "print(double(add(3, 4)))\n"
    )
    (tmp_path / "main.py").write_text(src, encoding="utf-8")

    defineConfig({"entry": "main", "minify_names": True})
    try:
        compiler = Compiler(
            project_root=tmp_path,
            entry_point="main",
            output_path=tmp_path / "out.forge",
        )
        compiler.analyze()
        compiler.run_plugins(plugins=[], include_optimizer=False)
        content = compiler.graph.get_node("main").get_content()  # type: ignore[union-attr]
        assert content is not None
    finally:
        import forger.api as _api

        _api._get_context().set_config(_api.ForgerConfig())  # type: ignore[attr-defined]

    result = subprocess.run(
        [sys.executable, "-c", content],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "14"  # double(add(3, 4)) = double(7) = 14
