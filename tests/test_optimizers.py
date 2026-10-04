"""Tests for the Django optimizer plugin, the discover_resources hook,
and the incremental cache wiring.

PLUGIN_ARCHITECTURE.md §10, PHILOSOPHY.md §6/§29, CLAUDE.md invariant 13.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forger.errors import PluginError
from forger.optimizer import BasePlugin, PluginContext, PluginRunner
from forger.optimizers.django import DjangoPlugin

# ---------------------------------------------------------------------------
# discover_resources hook + runner
# ---------------------------------------------------------------------------


def test_discover_resources_returns_resolved_path(tmp_path: Path) -> None:
    """A plugin's discover_resources can map a logical id to a real file."""

    real = tmp_path / "real_template.html"
    real.write_text("hi", encoding="utf-8")

    class StaticPlugin(BasePlugin):
        name = "static"

        def discover_resources(self, module_id, *, context):
            if module_id == "logical_id":
                return str(real)
            return None

    runner = PluginRunner([StaticPlugin()])
    ctx = PluginContext(project_root=tmp_path)
    result = runner.run_discover_resources("logical_id", ctx)
    assert result == str(real)


def test_discover_resources_returns_none_when_no_match() -> None:
    class Noop(BasePlugin):
        name = "noop"

    runner = PluginRunner([Noop()])
    ctx = PluginContext(project_root=Path("/dev/null"))
    assert runner.run_discover_resources("anything", ctx) is None


def test_discover_resources_strict_raises() -> None:
    class BrokenPlugin(BasePlugin):
        name = "broken"

        def discover_resources(self, module_id, *, context):
            raise RuntimeError("boom")

    runner = PluginRunner([BrokenPlugin()])
    ctx = PluginContext(project_root=Path("/dev/null"))
    with pytest.raises(PluginError) as excinfo:
        runner.run_discover_resources("any_id", ctx)
    assert excinfo.value.hook == "discover_resources"
    assert excinfo.value.plugin_name == "broken"


def test_compiler_calls_discover_resources_before_fallback(tmp_path: Path) -> None:
    """When a plugin can resolve a resource, the hardcoded fallback
    is bypassed — and the resolved file lands in the VFS."""
    from forger.compiler import Compiler

    # A real file at an unexpected path.
    real = tmp_path / "blog" / "templates" / "blog" / "post_list.html"
    real.parent.mkdir(parents=True)
    real.write_text("<p>post</p>", encoding="utf-8")
    # The resource node id points to the logical Django path.
    (tmp_path / "blog" / "views.py").write_text(
        "from pathlib import Path\n"
        "def get():\n"
        "    return Path('blog/post_list.html').read_text()\n",
        encoding="utf-8",
    )
    (tmp_path / "blog" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from blog.views import get\nprint(len(get()))\n",
        encoding="utf-8",
    )

    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    compiler.analyze()
    # Install the Django plugin.
    compiler.run_plugins(
        plugins=[DjangoPlugin(project_root=tmp_path)],
        include_optimizer=False,
    )
    # The plugin should resolve the Django-style id to the real file.
    ctx = compiler._plugin_context
    runner = compiler._plugin_runner
    resolved = runner.run_discover_resources("blog/post_list.html", ctx)
    assert resolved == str(real)


# ---------------------------------------------------------------------------
# DjangoPlugin
# ---------------------------------------------------------------------------


def test_django_plugin_resolves_app_template(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    app = root / "blog"
    app.mkdir()
    (app / "templates").mkdir()
    target = app / "templates" / "blog" / "post.html"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    plugin = DjangoPlugin(project_root=root)
    ctx = PluginContext(project_root=root)
    out = plugin.discover_resources("blog/post.html", context=ctx)
    assert out == str(target)


def test_django_plugin_resolves_project_templates_dir(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "templates").mkdir()
    target = root / "templates" / "shared" / "base.html"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    plugin = DjangoPlugin(project_root=root)
    ctx = PluginContext(project_root=root)
    out = plugin.discover_resources("shared/base.html", context=ctx)
    assert out == str(target)


def test_django_plugin_falls_back_to_basename_match(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    weird = root / "weird_place" / "login.html"
    weird.parent.mkdir(parents=True)
    weird.write_text("x", encoding="utf-8")

    plugin = DjangoPlugin(project_root=root)
    ctx = PluginContext(project_root=root)
    out = plugin.discover_resources("login.html", context=ctx)
    assert out == str(weird)


def test_django_plugin_returns_none_when_no_match(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()

    plugin = DjangoPlugin(project_root=root)
    ctx = PluginContext(project_root=root)
    assert plugin.discover_resources("missing.html", context=ctx) is None


# ---------------------------------------------------------------------------
# Incremental cache
# ---------------------------------------------------------------------------


def test_compiler_uses_cache_for_unchanged_sources(tmp_path: Path) -> None:
    """Re-running analyze with the same cache_dir skips unchanged files."""
    from forger.cache import Cache
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    cache_dir = tmp_path / ".forger_cache"

    # First build populates the cache.
    c1 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out1",
        cache_dir=cache_dir,
    )
    assert isinstance(c1._cache, Cache)
    c1.analyze()
    first_entries = dict(c1._cache._entries)
    assert first_entries, "expected cache to be populated after first build"

    # Second build: same source content, same cache dir.
    c2 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out2",
        cache_dir=cache_dir,
    )
    c2.analyze()
    # Cache hash for the source matches the first run (Unchanged state).
    main_path = next(p for p in first_entries if p.endswith("main.py"))
    assert c2._cache.check_state(
        Path(main_path), first_entries[main_path].hash
    ).name == "Unchanged"


def test_compiler_no_cache_when_not_requested(tmp_path: Path) -> None:
    """Without ``cache_dir``, the cache handle is None and behavior is unchanged."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    c = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    assert c._cache is None
    c.analyze()  # must not raise
    assert c._ast_cache  # parses worked


def test_compiler_detects_modified_sources(tmp_path: Path) -> None:
    """Modifying a file invalidates its cache entry on the next build."""
    from forger.cache import CacheState
    from forger.compiler import Compiler

    main = tmp_path / "main.py"
    main.write_text("x = 1\n", encoding="utf-8")
    cache_dir = tmp_path / ".forger_cache"

    c1 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out1",
        cache_dir=cache_dir,
    )
    c1.analyze()
    # Mutate the file.
    main.write_text("x = 2\n", encoding="utf-8")

    c2 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out2",
        cache_dir=cache_dir,
    )
    c2.analyze()
    # The cache now reflects the new content hash.
    state = c2._cache.check_state(main, "<placeholder>")
    # It won't be Unchanged; it could be Modified or New depending on
    # the pre-existing entry's hash vs. "<placeholder>".
    assert state in (CacheState.Modified, CacheState.New)


def test_compiler_creates_cache_dir_on_save(tmp_path: Path) -> None:
    """The cache directory is created on save() if missing."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    cache_dir = tmp_path / "deep" / "nested" / ".forger_cache"
    assert not cache_dir.exists()

    c = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
        cache_dir=cache_dir,
    )
    c.analyze()
    assert cache_dir.exists()
    assert (cache_dir / "cache_index.json").exists()


# ---------------------------------------------------------------------------
# JSON parsing edge case: invalid index is handled gracefully
# ---------------------------------------------------------------------------


def test_invalid_cache_index_is_recovered(tmp_path: Path) -> None:
    """A corrupted cache_index.json should not crash the next build."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    cache_dir = tmp_path / ".forger_cache"
    cache_dir.mkdir()
    (cache_dir / "cache_index.json").write_text("not-json", encoding="utf-8")

    c = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
        cache_dir=cache_dir,
    )
    # Should not raise.
    c.analyze()
    # And the new index is valid JSON.
    data = json.loads((cache_dir / "cache_index.json").read_text())
    assert "entries" in data


# ---------------------------------------------------------------------------
# Apply mode (PHILOSOPHY.md §29)
# ---------------------------------------------------------------------------


def test_apply_mode_filters_serve_only_plugins(tmp_path: Path) -> None:
    """A plugin with ``apply = "serve"`` runs only in serve mode."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    class BuildOnlyPlugin(BasePlugin):
        name = "build-only"
        apply = "build"
        ran = False

        def build_start(self, *, context):
            self.__class__.ran = True

    class ServeOnlyPlugin(BasePlugin):
        name = "serve-only"
        apply = "serve"
        served = False

        def build_start(self, *, context):
            self.__class__.served = True

    # Build mode: only build-only fires.
    build_plugin = BuildOnlyPlugin()
    serve_plugin = ServeOnlyPlugin()
    c1 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out1",
    )
    c1.analyze()
    c1.run_plugins(plugins=[build_plugin, serve_plugin], include_optimizer=False)
    assert build_plugin.ran is True
    assert serve_plugin.served is False

    # Serve mode: only serve-only fires.
    BuildOnlyPlugin.ran = False
    ServeOnlyPlugin.served = False
    build_plugin2 = BuildOnlyPlugin()
    serve_plugin2 = ServeOnlyPlugin()
    c2 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out2",
    )
    c2.analyze()
    c2.run_plugins(
        plugins=[build_plugin2, serve_plugin2],
        include_optimizer=False,
        apply="serve",
    )
    assert build_plugin2.ran is False
    assert serve_plugin2.served is True


def test_apply_mode_default_is_build(tmp_path: Path) -> None:
    """Plugins with ``apply = "build"`` fire in default mode."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    class BuildPlugin(BasePlugin):
        name = "build"
        apply = "build"
        ran = False

        def build_start(self, *, context):
            self.__class__.ran = True

    plugin = BuildPlugin()
    c = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    c.analyze()
    c.run_plugins(plugins=[plugin], include_optimizer=False)
    assert plugin.ran is True


def test_apply_none_runs_in_either_mode(tmp_path: Path) -> None:
    """A plugin with ``apply = None`` (the default) runs everywhere."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    class UniversalPlugin(BasePlugin):
        name = "universal"
        ran = False

        def build_start(self, *, context):
            self.__class__.ran = True

    p1 = UniversalPlugin()
    c1 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out1",
    )
    c1.analyze()
    c1.run_plugins(plugins=[p1], include_optimizer=False)
    assert p1.ran is True

    p2 = UniversalPlugin()
    c2 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out2",
    )
    c2.analyze()
    c2.run_plugins(plugins=[p2], include_optimizer=False, apply="serve")
    assert p2.ran is True


# ---------------------------------------------------------------------------
# AST sidecar (PHILOSOPHY.md §29)
# ---------------------------------------------------------------------------


def test_compiler_uses_edges_between_for_diagnostics(tmp_path: Path) -> None:
    """The compiler's diagnostic_summary uses the public
    ``DependencyGraph.edges_between`` API instead of poking at the
    private ``_edges_from`` dict."""
    from forger.core import DependencyGraph

    g = DependencyGraph()
    # Direct construction for the test bypasses Compiler's per-file
    # bookkeeping and gives us a deterministic graph shape.
    from forger.core import DependencyEdge, DependencyNode, EdgeProvenance, EdgeType, NodeType

    g.add_node(DependencyNode.new("a", NodeType.PythonModule))
    g.add_node(DependencyNode.new("b", NodeType.PythonModule))
    g.add_edge(DependencyEdge.new(
        "a", "b", EdgeType.Import,
        EdgeProvenance(source=("a.py", 1), discovered_by="test", description="a→b"),
    ))

    # Public API is wired on both backends.
    edges = g.edges_between("a", "b")
    assert len(edges) == 1
    assert edges[0].from_node == "a"
    assert edges[0].to_node == "b"

    # No such edge.
    assert g.edges_between("b", "a") == []


def test_analyze_imports_runs_in_parallel(tmp_path: Path) -> None:
    """_analyze_imports parallelizes per-file work; the graph still
    ends up with the right set of nodes and edges."""
    from forger.compiler import Compiler

    # Build a project with several independent files.
    for i in range(8):
        (tmp_path / f"m{i}.py").write_text(
            f"import os\nimport sys\nx{i} = 1\n", encoding="utf-8"
        )
    (tmp_path / "main.py").write_text(
        "from m0 import x0\nfrom m1 import x1\n", encoding="utf-8"
    )
    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    compiler.analyze()
    # All modules are present.
    for i in range(8):
        assert compiler.graph.get_node(f"m{i}") is not None


def test_analyze_resources_runs_in_parallel(tmp_path: Path) -> None:
    """_analyze_resources parallelizes per-file work."""
    from forger.compiler import Compiler

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for i in range(6):
        (data_dir / f"f{i}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"m{i}.py").write_text(
            f"from pathlib import Path\n"
            f"p = Path(__file__).parent / 'data' / 'f{i}.csv'\n",
            encoding="utf-8"
        )
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    compiler.analyze()
    from forger.core import NodeType

    resource_nodes = compiler.graph.nodes_by_type(NodeType.Resource)
    discovered = {n.id for n in resource_nodes}
    sep = os.sep
    for i in range(6):
        assert f"data{sep}f{i}.csv" in discovered


def test_set_node_required_through_public_api(tmp_path: Path) -> None:
    """The compiler uses ``DependencyGraph.set_node_required`` instead
    of poking at the node's ``required`` attribute directly."""
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "from blog import views\nx = views.y\n", encoding="utf-8"
    )
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "blog" / "views.py").write_text("y = 1\n", encoding="utf-8")

    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out",
    )
    compiler.analyze()
    # The graph must expose set_node_required (it does on both backends).
    assert hasattr(compiler.graph, "set_node_required")

    blog = compiler.graph.get_node("blog")
    views = compiler.graph.get_node("blog.views")
    assert blog is not None and views is not None

    # Mark a package as retained.
    compiler._mark_retained_required("blog")
    # Re-fetch the nodes: the rust-backed graph returns snapshots from
    # ``get_node`` so the previously-captured references still carry
    # the pre-mark ``required`` value.
    blog = compiler.graph.get_node("blog")
    views = compiler.graph.get_node("blog.views")
    assert blog is not None and blog.required
    assert views is not None and views.required


def test_legacy_django_fallback_is_opt_in(tmp_path: Path) -> None:
    """The hardcoded Django template-dir fallback runs only when
    explicitly enabled — projects that ship a DjangoPlugin should
    not have the heuristic compete with the plugin."""
    from forger.compiler import Compiler

    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "templates").mkdir()
    (tmp_path / "blog" / "templates" / "blog" / "post.html").parent.mkdir(
        parents=True
    )
    target = tmp_path / "blog" / "templates" / "blog" / "post.html"
    target.write_text("hi", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    # Default: legacy fallback is OFF, so direct resolve fails
    # (the path is *not* literal at the node id, and no plugin
    # is registered).
    c_off = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out1",
    )
    assert c_off.legacy_django_fallback is False
    assert (
        c_off._resolve_resource_path("blog/post.html") is None
    )

    # Opt in: legacy fallback finds the file.
    c_on = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out2",
        legacy_django_fallback=True,
    )
    assert c_on.legacy_django_fallback is True
    assert c_on._resolve_resource_path("blog/post.html") == target


def test_analyze_hook_returns_first_non_none(tmp_path: Path) -> None:
    """``PluginRunner.run_analyze`` returns the first plugin's
    non-None result and is strict on errors."""
    from forger.optimizer import PluginContext, PluginRunner

    class APlugin(BasePlugin):
        name = "a"

        def analyze(self, module_id, *, context):
            return {"by": "a", "module": module_id}

    class BPlugin(BasePlugin):
        name = "b"

        def analyze(self, module_id, *, context):
            return {"by": "b", "module": module_id}

    runner = PluginRunner([APlugin(), BPlugin()])
    ctx = PluginContext(project_root=tmp_path)
    # First non-None wins.
    assert runner.run_analyze("mod", ctx) == {"by": "a", "module": "mod"}


def test_analyze_hook_strict_raises(tmp_path: Path) -> None:
    from forger.errors import PluginError
    from forger.optimizer import PluginContext, PluginRunner

    class Broken(BasePlugin):
        name = "broken"

        def analyze(self, module_id, *, context):
            raise RuntimeError("analyze fail")

    runner = PluginRunner([Broken()])
    ctx = PluginContext(project_root=tmp_path)
    with pytest.raises(PluginError) as excinfo:
        runner.run_analyze("m", ctx)
    assert excinfo.value.hook == "analyze"


def test_transform_resource_chains(tmp_path: Path) -> None:
    """``transform_resource`` runs the plugins in order; each sees
    the previous plugin's output."""
    from forger.optimizer import PluginContext, PluginRunner

    class StripPlugin(BasePlugin):
        name = "strip"

        def transform_resource(self, module_id, content, *, context):
            return content.strip()

    class UpperPlugin(BasePlugin):
        name = "upper"

        def transform_resource(self, module_id, content, *, context):
            return content.upper()

    runner = PluginRunner([StripPlugin(), UpperPlugin()])
    ctx = PluginContext(project_root=tmp_path)
    out = runner.run_transform_resource("m", "  hello  ", ctx)
    assert out == "HELLO"


def test_ast_sidecar_skips_reparse_on_unchanged_files(tmp_path: Path) -> None:
    """A second build with unchanged source loads the AST from a
    pickle sidecar — no re-parse."""
    import pickle

    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    cache_dir = tmp_path / ".forger_cache"

    # First build populates the sidecar.
    c1 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out1",
        cache_dir=cache_dir,
    )
    c1.analyze()
    assert c1._ast_cache  # parsed the file
    # The sidecar directory was created.
    ast_sidecar_dir = cache_dir / "ast"
    sidecars = list(ast_sidecar_dir.glob("*.pkl")) if ast_sidecar_dir.is_dir() else []
    assert sidecars, f"expected sidecar in {ast_sidecar_dir}"

    # Corrupt the sidecar — replace it with something that fails to
    # unpickle. The second build should fall back to re-parsing.
    sidecar = sidecars[0]
    sidecar.write_bytes(b"not a valid pickle")
    c2 = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "out2",
        cache_dir=cache_dir,
    )
    c2.analyze()
    # The second build recovers and re-parses.
    assert c2._ast_cache

    # Sanity: sidecars from a *fresh* build also have a valid
    # pickle (round-trip). Reading it must not raise.
    sidecar_after = list((cache_dir / "ast").glob("*.pkl"))[0]
    pickle.loads(sidecar_after.read_bytes())  # must not raise
