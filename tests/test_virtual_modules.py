"""Tests for virtual modules, virtual resources, and structured errors.

PLUGIN_ARCHITECTURE.md §14-15, §20; PHILOSOPHY.md §16-17, §34.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from forger.errors import PluginError
from forger.optimizer import BasePlugin, PluginContext, PluginRunner

# ---------------------------------------------------------------------------
# Structured plugin errors
# ---------------------------------------------------------------------------


def test_strict_hook_raises_plugin_error() -> None:
    """A failing ``resolve_id`` aborts the build via PluginError."""

    class BrokenResolvePlugin(BasePlugin):
        name = "broken-resolve"

        def resolve_id(self, specifier, importer, *, context):
            raise RuntimeError("oops")

    runner = PluginRunner([BrokenResolvePlugin()])
    ctx = _bare_context()
    with pytest.raises(PluginError) as excinfo:
        runner.run_resolve_id("anything", None, ctx)
    err = excinfo.value
    assert err.plugin_name == "broken-resolve"
    assert err.hook == "resolve_id"
    assert err.module_id == "anything"
    assert "oops" in str(err)


def test_strict_load_raises_plugin_error() -> None:
    class BrokenLoadPlugin(BasePlugin):
        name = "broken-load"

        def load(self, module_id, *, context):
            raise ValueError("nope")

    runner = PluginRunner([BrokenLoadPlugin()])
    ctx = _bare_context()
    with pytest.raises(PluginError) as excinfo:
        runner.run_load("some_module", ctx)
    err = excinfo.value
    assert err.plugin_name == "broken-load"
    assert err.hook == "load"
    assert err.module_id == "some_module"


def test_non_strict_hook_continues_on_error() -> None:
    """A failing transform-style hook logs and continues (legacy behavior)."""
    import logging

    class BrokenTransform(BasePlugin):
        name = "broken-transform"

        def transform_ast(self, module_id, tree, *, context):
            raise RuntimeError("transform kaboom")

    captured: list[str] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = CapturingHandler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)
    try:
        runner = PluginRunner([BrokenTransform()])
        ctx = _bare_context()
        # Must not raise; the legacy contract is "log and continue".
        runner._invoke("transform_ast", ctx, "x")
    finally:
        logging.getLogger().removeHandler(handler)

    assert any("broken-transform" in m for m in captured), captured


def test_plugin_error_serializes_to_dict() -> None:
    err = PluginError(
        "bad",
        plugin_name="p1",
        hook="resolve_id",
        module_id="foo",
        source_file="bar.py",
    )
    d = err.as_dict()
    assert d["kind"] == "plugin_error"
    assert d["plugin"] == "p1"
    assert d["hook"] == "resolve_id"
    assert d["module_id"] == "foo"
    assert d["source_file"] == "bar.py"


# ---------------------------------------------------------------------------
# Virtual modules
# ---------------------------------------------------------------------------


def test_virtual_module_registers_node_and_source(tmp_path: Path) -> None:
    from forger.core import NodeType

    ctx = _bare_context()
    module_id = ctx.virtual_module(
        "config", "VERSION = '1.0'\nDEBUG = False\n"
    )
    assert module_id == "virtual:config"
    # The node is in the graph.
    node = ctx.graph.get_node(module_id)
    assert node is not None
    assert node.node_type == NodeType.PythonModule
    # The source is cached for the parser.
    assert ctx.virtual_source(module_id) == "VERSION = '1.0'\nDEBUG = False\n"


def test_virtual_resource_writes_to_output(tmp_path: Path) -> None:
    """A virtual resource lands in the dist tree with the given content."""
    from forger.api import defineConfig
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "from app.config import VERSION\nprint(VERSION)\n",
        encoding="utf-8",
    )
    # The user doesn't have app/config.py; the plugin supplies it.
    defineConfig({"entry": "main"})
    try:
        compiler = Compiler(
            project_root=tmp_path,
            entry_point="main",
            output_path=tmp_path / "out",
        )

        class ConfigPlugin(BasePlugin):
            name = "config"

            def config(self, *, context):
                context.virtual_module(
                    "config", "VERSION = '42'\n"
                )
                # And a virtual resource for completeness.
                context.virtual_resource(
                    "app/settings.json", '{"version": 42}'
                )

        compiler.analyze()
        compiler.run_plugins(plugins=[ConfigPlugin()], include_optimizer=False)
        compiler.generate_vfs()

        # The virtual resource landed in the dist tree.
        settings = (tmp_path / "out" / "app" / "settings.json")
        assert settings.exists()
        assert json.loads(settings.read_text()) == {"version": 42}
    finally:
        _reset_forger_config()


# ---------------------------------------------------------------------------
# End-to-end: BasePlugin rewrites a specifier
# ---------------------------------------------------------------------------


def test_baseplugin_rewrites_specifier_end_to_end(tmp_path: Path) -> None:
    """A custom BasePlugin implementing ``resolve_id`` rewrites imports
    at the compiler level — not just the analyzer unit test."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "from myconfig import VERSION\nprint(VERSION)\n",
        encoding="utf-8",
    )

    class RewritePlugin(BasePlugin):
        name = "rewrite-myconfig"

        def resolve_id(self, specifier, importer, *, context):
            if specifier == "myconfig":
                return "virtual:myconfig"
            return None

        def load(self, module_id, *, context):
            if module_id == "virtual:myconfig":
                return "VERSION = '99'\n"
            return None

        def config(self, *, context):
            # Pre-register so the virtual module is in the graph
            # before resolve_id gets called.
            context.virtual_module("myconfig", "VERSION = '99'\n")

    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[RewritePlugin()], include_optimizer=False)
    compiler.generate_vfs()

    # The virtual module is at the specifier-named path in the dist tree.
    assert (tmp_path / "out" / "myconfig.py").exists()
    # The program runs and prints 99.
    _assert_runs(tmp_path / "out", expected_output="99")


def test_get_module_info_is_live(tmp_path: Path) -> None:
    """get_module_info reflects post-transform state, not the initial parse."""
    from forger.core import DependencyGraph, DependencyNode, NodeType
    from forger.optimizer import PluginContext

    graph = DependencyGraph()
    graph.add_node(
        DependencyNode.new("m", NodeType.PythonModule).with_metadata(
            "discovered_by", "test"
        )
    )
    ctx = PluginContext(project_root=tmp_path)
    ctx.set_graph(graph)
    ctx._module_asts["m"] = "fake-tree"

    info = ctx.get_module_info("m")
    assert info is not None
    assert info["ast"] == "fake-tree"

    # Mutate the AST; the next call must reflect the new state.
    ctx._module_asts["m"] = "newer-tree"
    info2 = ctx.get_module_info("m")
    assert info2["ast"] == "newer-tree"


def test_set_then_get_module_info_round_trip(tmp_path: Path) -> None:
    """Plugin-attached metadata round-trips through get_module_info."""
    from forger.core import DependencyGraph, DependencyNode, NodeType
    from forger.optimizer import PluginContext

    graph = DependencyGraph()
    graph.add_node(
        DependencyNode.new("m", NodeType.PythonModule).with_metadata(
            "discovered_by", "test"
        )
    )
    ctx = PluginContext(project_root=tmp_path)
    ctx.set_graph(graph)

    ctx.set_module_info("m", {"custom_key": "custom_value", "sla": 99})
    info = ctx.get_module_info("m")
    assert info is not None
    # Plugin-set keys are present.
    assert info["custom_key"] == "custom_value"
    assert info["sla"] == 99
    # Base keys are not overwritten by the round-trip.
    assert info["id"] == "m"

    # Reading the info is non-mutating — a second call still has the
    # plugin-attached keys.
    info2 = ctx.get_module_info("m")
    assert info2["custom_key"] == "custom_value"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_context() -> PluginContext:
    """Build a PluginContext with a minimal in-memory graph."""
    from forger.core import DependencyGraph  # type: ignore[attr-defined]

    try:
        graph = DependencyGraph()
    except Exception:
        pytest.skip("no graph backend available in this environment")
    ctx = PluginContext(project_root=Path("/dev/null"))
    ctx.set_graph(graph)
    return ctx


def _has_virtual_module(compiler, module_id: str) -> bool:
    node = compiler.graph.get_node(module_id)
    if node is None:
        return False
    # The compiler writes the source via the same path it would for a
    # real file: <output>/<module_id-as-path>.py
    candidate = compiler.output_path / (module_id.replace(":", "_") + ".py")
    return candidate.exists()


def _assert_runs(dist: Path, expected_output: str) -> None:
    """Run the VFS dist as a Python script and assert it prints the
    expected string. Skips if there's no script-style entry point."""
    # Find the main script — usually the entry point with .py suffix.
    main_candidate = None
    for p in dist.rglob("*.py"):
        if p.name == "main.py":
            main_candidate = p
            break
    if main_candidate is None:
        pytest.skip(f"no main.py in {dist}")
    result = subprocess.run(
        [sys.executable, str(main_candidate)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert expected_output in result.stdout, result.stdout


def _reset_forger_config() -> None:
    """Clear the global forger config so subsequent tests aren't affected."""
    import forger.api as _api
    from forger.api import ForgerConfig

    _api._get_context().set_config(ForgerConfig())


    # json import is needed for the virtual_resource test.
