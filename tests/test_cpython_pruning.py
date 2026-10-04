"""Tests for CPython C-level pruning (cpython_pruner + integration).

These tests exercise the pruning pipeline without building CPython:
the Rust registry analysis, plan generation, Setup.local emission,
configure-arg merging, and the manifest -> RuntimeSpec wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forger.build.cpython_pruner import (
    CPythonBuildPlan,
    CPythonPruner,
    collect_stdlib_imports,
    write_setup_local,
)
from forger.build.recipes import LinuxRecipe, WindowsRecipe
from forger.build.targets import TargetPlatform

# ---------------------------------------------------------------------------
# collect_stdlib_imports
# ---------------------------------------------------------------------------


def test_collect_filters_third_party() -> None:
    used = collect_stdlib_imports({"json", "flask", "numpy", "os"})
    assert "json" in used
    assert "os" in used
    assert "flask" not in used
    assert "numpy" not in used


def test_collect_includes_core_baseline() -> None:
    used = collect_stdlib_imports(set())
    # The interpreter needs these to even boot.
    for core in ("builtins", "sys", "os", "io", "importlib"):
        assert core in used, core


def test_collect_dotted_top_level_match() -> None:
    used = collect_stdlib_imports({"collections.abc"})
    assert "collections.abc" in used


# ---------------------------------------------------------------------------
# CPythonPruner.build_plan (requires Rust core)
# ---------------------------------------------------------------------------


def _has_rust_core() -> bool:
    try:
        import forger.forger_core  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


rust_core = pytest.mark.skipif(
    not _has_rust_core(), reason="forger.forger_core extension not built"
)


@pytest.fixture()
def pruner() -> CPythonPruner:
    return CPythonPruner("3.12")


@rust_core
def test_plan_requires_json_stack(pruner: CPythonPruner) -> None:
    plan = pruner.build_plan(collect_stdlib_imports({"json"}))
    assert isinstance(plan, CPythonBuildPlan)
    assert "_json" in plan.required_extensions
    assert "_ssl" in plan.excluded_extensions or "_curses" in plan.excluded_extensions


@rust_core
def test_plan_excludes_unused_heavy_modules(pruner: CPythonPruner) -> None:
    plan = pruner.build_plan(collect_stdlib_imports({"json"}))
    assert "_tkinter" in plan.excluded_extensions


@rust_core
def test_plan_setup_local_mentions_requirements(pruner: CPythonPruner) -> None:
    plan = pruner.build_plan(collect_stdlib_imports({"json"}))
    assert "Forger" in plan.setup_local


@rust_core
def test_plan_configure_args_disable_domains(pruner: CPythonPruner) -> None:
    plan = pruner.build_plan(collect_stdlib_imports({"json"}))
    assert "--without-tk" in plan.configure_args
    assert "--disable-test-modules" in plan.configure_args
    assert "--without-ensurepip" in plan.configure_args


@rust_core
def test_plan_keeps_tk_when_used(pruner: CPythonPruner) -> None:
    plan = pruner.build_plan({"tkinter"})
    assert "--without-tk" not in plan.configure_args


# ---------------------------------------------------------------------------
# write_setup_local
# ---------------------------------------------------------------------------


def test_write_setup_local_creates_file(tmp_path: Path) -> None:
    target = write_setup_local(tmp_path, "# Auto-generated Modules/Setup by Forger\n")
    assert target is not None
    assert target == tmp_path / "Modules" / "Setup.local"
    assert "Forger" in target.read_text(encoding="utf-8")


def test_write_setup_local_skips_empty(tmp_path: Path) -> None:
    assert write_setup_local(tmp_path, "") is None
    assert not (tmp_path / "Modules").exists()


# ---------------------------------------------------------------------------
# Recipe configure-arg merging
# ---------------------------------------------------------------------------


def test_linux_recipe_merges_extra_args(tmp_path: Path) -> None:
    recipe = LinuxRecipe(TargetPlatform.parse("linux-x64"), "3.12")
    base = recipe.configure_cmd(tmp_path, tmp_path / "b") or []
    merged = recipe.with_extra_configure_args(base, ["--without-tk"])
    assert merged[-1] == "--without-tk"  # appended after existing options


def test_linux_recipe_merges_before_env_assignment(tmp_path: Path) -> None:
    recipe = LinuxRecipe(TargetPlatform.parse("linux-x64"), "3.12")
    base = [str(tmp_path / "configure"), "--prefix=/forger"]
    cmd_with_env = [*base, "CC=musl-gcc"]
    merged = recipe.with_extra_configure_args(cmd_with_env, ["--without-tk"])
    expected = [
        str(tmp_path / "configure"),
        "--prefix=/forger",
        "--without-tk",
        "CC=musl-gcc",
    ]
    assert merged == expected


def test_windows_recipe_drops_extra_args(tmp_path: Path) -> None:
    recipe = WindowsRecipe(TargetPlatform.parse("windows-x64"), "3.12")
    assert recipe.with_extra_configure_args(["x"], ["--without-tk"]) == ["x"]


# ---------------------------------------------------------------------------
# Manifest <-> RuntimeSpec wiring
# ---------------------------------------------------------------------------


def _make_artifact(root: Path) -> Path:
    from forger.compiler import Compiler

    vfs = root / "dist"
    vfs.mkdir(parents=True)
    (vfs / "main.py").write_text("print('hi')\n", encoding="utf-8")
    artifact = root / "app.forge"
    compiler = Compiler.forge_from_vfs(vfs, artifact, entry_point="main")
    compiler.generate_artifact()
    return artifact


def test_manifest_contains_stdlib_modules(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    artifact = _make_artifact(root)
    data = json.loads((artifact / "MANIFEST.json").read_text(encoding="utf-8"))
    assert isinstance(data["stdlib_modules"], list)
    # Baseline modules are always present.
    assert "sys" in data["stdlib_modules"]
    assert "os" in data["stdlib_modules"]


def test_builder_passes_stdlib_to_runtime_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import patch

    from forger.build.runtime_builder import RuntimeSpec
    from forger.builder import Builder

    root = tmp_path / "proj"
    root.mkdir()
    vfs = root / "dist"
    vfs.mkdir()
    (vfs / "main.py").write_text("import json\nprint(json.dumps(1))\n", encoding="utf-8")
    artifact = root / "app.forge"
    compiler_mod = pytest.importorskip("forger.compiler")
    compiler_mod.Compiler.forge_from_vfs(
        vfs, artifact, entry_point="main"
    ).generate_artifact()

    captured: dict[str, RuntimeSpec] = {}

    class FakeResult:
        triple = "linux-x64"
        version = "3.12"
        stripped_count = 0
        stdlib_module_count = 0
        pruned_extensions = 0
        reused_existing = True

        def __init__(self) -> None:
            self.runtime_dir = tmp_path / "fake-runtime"

    def fake_ensure(self: object, **kw: object) -> FakeResult:
        captured["spec"] = self.spec
        return FakeResult()

    fake_runtime = tmp_path / "fake-runtime"
    (fake_runtime / "bin").mkdir(parents=True)
    (fake_runtime / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    lib = fake_runtime / "lib" / "python3.12"
    lib.mkdir(parents=True)
    (lib / "os.py").write_text("", encoding="utf-8")

    with patch(
        "forger.build.runtime_builder.CpythonRuntimeBuilder.ensure_runtime",
        autospec=True,
        side_effect=fake_ensure,
    ):
        output = tmp_path / "bundle"
        Builder(
            artifact_path=artifact, target="linux-x64", output_dir=output
        ).build()

    spec = captured["spec"]
    assert isinstance(spec, RuntimeSpec)
    assert "json" in spec.required_modules
    assert "sys" in spec.required_modules
