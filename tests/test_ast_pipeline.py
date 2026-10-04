"""Tests for the AST pipeline: parse phase, plugin access, and transforms.

The compiler parses every source file during ``analyze()``; trees are
cached on the compiler, shared with plugins via ``PluginContext``, and
handed to ``module_parsed``/``transform_ast`` hooks. Mutated trees are
unparsed back into node content.
"""

from __future__ import annotations

import ast as ast_module
from pathlib import Path

import pytest

from forger.compiler import Compiler
from forger.optimizer import BasePlugin, PluginContext


def _make_project(root: Path) -> None:
    (root / "main.py").write_text(
        "import helpers\n\n\ndef run():\n    return helpers.value\n\n\nrun()\n",
        encoding="utf-8",
    )
    (root / "helpers.py").write_text(
        '"""Docstring here."""\nvalue = 42\n',
        encoding="utf-8",
    )


@pytest.fixture()
def compiled(tmp_path: Path) -> Compiler:
    root = tmp_path / "proj"
    root.mkdir()
    _make_project(root)
    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()
    return compiler


# ---------------------------------------------------------------------------
# Parse phase / cache
# ---------------------------------------------------------------------------


def test_ast_cache_populated_for_all_modules(compiled: Compiler) -> None:
    assert set(compiled._ast_cache) == {"main", "helpers"}
    assert isinstance(compiled._ast_cache["main"], ast_module.Module)


def test_get_module_ast_roundtrip(compiled: Compiler) -> None:
    tree = compiled.get_module_ast("helpers")
    assert tree is not None
    assert any(isinstance(n, ast_module.Assign) for n in tree.body)
    assert compiled.get_module_ast("nope") is None


def test_set_module_ast_replaces(compiled: Compiler) -> None:
    new_tree = ast_module.parse("x = 1\n")
    compiled.set_module_ast("helpers", new_tree)
    assert compiled.get_module_ast("helpers") is new_tree


def test_unparseable_file_skipped(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "broken.py").write_text("def f(:\n", encoding="utf-8")

    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()

    assert "main" in compiler._ast_cache
    assert "broken" not in compiler._ast_cache


# ---------------------------------------------------------------------------
# PluginContext exposure
# ---------------------------------------------------------------------------


def test_plugin_context_exposes_asts(compiled: Compiler) -> None:
    ctx = PluginContext(project_root=compiled.project_root)
    ctx.set_graph(compiled.graph)
    ctx._module_asts = dict(compiled._ast_cache)

    tree = ctx.get_module_ast("helpers")
    assert tree is not None
    assert set(ctx.module_asts) == {"main", "helpers"}


def test_get_module_info_includes_ast(compiled: Compiler) -> None:
    ctx = PluginContext(project_root=compiled.project_root)
    ctx.set_graph(compiled.graph)
    ctx._module_asts = dict(compiled._ast_cache)

    info = ctx.get_module_info("helpers")
    assert info is not None
    assert info["ast"] is compiled.get_module_ast("helpers")


# ---------------------------------------------------------------------------
# Hook firing + transform sync
# ---------------------------------------------------------------------------


class _AstRecorder(BasePlugin):
    """Records hook calls; optionally mutates the tree."""

    name = "ast-recorder"

    def __init__(self, mutate: bool = False) -> None:
        self.parsed: list[str] = []
        self.transformed: list[tuple[str, int]] = []  # (module_id, stmt_count)
        self.mutate = mutate

    def module_parsed(self, module_id: str, *, context: PluginContext) -> None:
        self.parsed.append(module_id)

    def transform_ast(
        self, module_id: str, tree: ast_module.AST, *, context: PluginContext
    ) -> None:
        self.transformed.append((module_id, len(tree.body)))  # type: ignore[attr-defined]
        if self.mutate and module_id == "helpers":
            # Append a statement to prove the mutation propagates.
            tree.body.append(ast_module.parse("injected_by_plugin = True\n").body[0])  # type: ignore[attr-defined]


def test_hooks_fire_with_cached_trees(compiled: Compiler) -> None:
    recorder = _AstRecorder()
    compiled.run_plugins(plugins=[recorder], include_optimizer=False)

    assert sorted(recorder.parsed) == ["helpers", "main"]
    assert dict(recorder.transformed)["helpers"] >= 2  # docstring + assign


def test_transformed_tree_syncs_to_node_content(compiled: Compiler) -> None:
    recorder = _AstRecorder(mutate=True)
    compiled.run_plugins(plugins=[recorder], include_optimizer=False)

    node = compiled.graph.get_node("helpers")  # type: ignore[union-attr]
    content = node.get_content()  # type: ignore[union-attr]
    assert content is not None
    assert "injected_by_plugin" in content


def test_unmutated_trees_leave_content_alone(compiled: Compiler) -> None:
    before = compiled.graph.get_node("helpers").get_content()  # type: ignore[union-attr]
    recorder = _AstRecorder(mutate=False)
    compiled.run_plugins(plugins=[recorder], include_optimizer=False)

    after = compiled.graph.get_node("helpers").get_content()  # type: ignore[union-attr]
    assert after == before


# ---------------------------------------------------------------------------
# Strip pass integration through the AST path
# ---------------------------------------------------------------------------


def test_strip_pass_consumes_shared_tree_and_strips_docstrings(
    tmp_path: Path,
) -> None:
    from forger.optimizer.strip import StripCommentsDocstrings

    root = tmp_path / "proj"
    root.mkdir()
    _make_project(root)
    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()
    compiler.run_plugins(
        plugins=[StripCommentsDocstrings()], include_optimizer=False
    )

    helpers_node = compiler.graph.get_node("helpers")  # type: ignore[union-attr]
    content = helpers_node.get_content()  # type: ignore[union-attr]
    assert content is not None
    assert '"""Docstring here."""' not in content
    assert "value = 42" in content


# ---------------------------------------------------------------------------
# Symbol-level tree shaking
# ---------------------------------------------------------------------------


def test_symbol_shake_removes_unreferenced_definitions(
    tmp_path: Path,
) -> None:
    """Unreachable functions/classes get pruned from the emitted content."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text(
        "from helpers import used\nprint(used())\n", encoding="utf-8"
    )
    (root / "helpers.py").write_text(
        "def used():\n    return 1\n\n\n"
        "def unused():\n    return 999\n\n\n"
        "class UnusedClass:\n    pass\n\n\n"
        "USED_CONST = 42\n\n\n"
        "UNUSED_CONST = 'never seen'\n",
        encoding="utf-8",
    )

    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)

    content = compiler.graph.get_node("helpers").get_content()  # type: ignore[union-attr]
    assert content is not None
    # Bound by `from helpers import used` in main.py.
    assert "def used" in content
    # Unbound in main.py — pruned.
    assert "def unused" not in content
    assert "UnusedClass" not in content
    assert "USED_CONST" not in content
    assert "UNUSED_CONST" not in content


def test_symbol_shake_keeps_impure_module_statements(tmp_path: Path) -> None:
    """Unknown / impure statements survive even when their name is unused.

    ``from boot import run`` only seeds ``boot.run`` as a root, so the
    pruner should drop the unused pure constants while keeping the
    impure ``logging.basicConfig`` call (potential side effects on
    module import) and the explicitly-imported ``run`` function.
    """
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("from boot import run\nrun()\n", encoding="utf-8")
    (root / "boot.py").write_text(
        "import logging\n"
        "logging.basicConfig(level=logging.INFO)\n"  # impure: function call
        "FOO = 1\n"  # pure constant — no one references it
        "BAR = {1, 2, 3}\n"  # set literal — statically pure
        "\n\ndef run():\n    return 1\n",
        encoding="utf-8",
    )

    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)

    content = compiler.graph.get_node("boot").get_content()  # type: ignore[union-attr]
    assert content is not None
    # Impure call: side effects on import — keep.
    assert "logging.basicConfig" in content
    # Explicitly imported — keep.
    assert "def run" in content
    # Pure literals: droppable.
    assert "FOO = 1" not in content
    assert "BAR = {1, 2, 3}" not in content


def test_symbol_shake_skips_modules_with_no_graph_node(tmp_path: Path) -> None:
    """Files that aren't graph-tracked (e.g. resource templates) are untouched."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "extra.py").write_text(
        "def lonely():\n    return 1\n", encoding="utf-8"
    )

    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()
    # Strip the graph node for extra.py to simulate a non-tracked file.
    compiler.graph.delete_node("extra")
    compiler.run_plugins(plugins=[], include_optimizer=False)

    # The orphan module's tree is in the cache; it must NOT be touched
    # (the pruner has no reachable info for it).
    content = compiler.get_module_ast("extra")
    assert content is not None
    assert "def lonely" in ast_module.unparse(content)
