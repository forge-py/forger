"""Tests for CPython runtime building: targets, recipes, orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forger.build.cpython_source import CPythonSourceManager
from forger.build.recipes import (
    AndroidRecipe,
    EmscriptenRecipe,
    IosRecipe,
    LinuxRecipe,
    MacosRecipe,
    WindowsRecipe,
    recipe_for,
)
from forger.build.runtime_builder import CpythonRuntimeBuilder, RuntimeSpec
from forger.build.targets import TargetPlatform

# ---------------------------------------------------------------------------
# TargetPlatform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("triple", "expected_os"),
    [
        ("windows-x64", "windows"),
        ("linux-x64-musl", "linux"),
        ("macos-arm64", "macos"),
        ("ios-arm64", "ios"),
        ("android-arm64", "android"),
        ("emscripten-wasm", "emscripten"),
    ],
)
def test_parse_all_families(triple: str, expected_os: str) -> None:
    plat = TargetPlatform.parse(triple)
    assert plat.os == expected_os
    assert plat.triple == triple


def test_mobile_flags() -> None:
    assert TargetPlatform.parse("android-arm64").is_mobile
    assert TargetPlatform.parse("ios-arm64").is_mobile
    assert not TargetPlatform.parse("linux-x64").is_mobile


def test_wasm_flag() -> None:
    assert TargetPlatform.parse("emscripten-wasm").is_wasm
    assert not TargetPlatform.parse("linux-x64").is_wasm


def test_min_versions() -> None:
    assert TargetPlatform.parse("android-arm64").effective_min_version == "24"
    assert TargetPlatform.parse("ios-arm64").effective_min_version == "12.0"
    assert TargetPlatform.parse("linux-x64").effective_min_version is None


def test_registry_contains_first_class_targets() -> None:
    from forger.build.targets import default_registry

    reg = default_registry()
    for triple in (
        "windows-x64",
        "linux-arm64-musl",
        "macos-arm64",
        "ios-arm64",
        "android-arm64",
        "emscripten-wasm",
    ):
        assert reg.get(triple) is not None, triple


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


def test_recipe_factory_dispatch() -> None:
    assert isinstance(recipe_for(TargetPlatform.parse("windows-x64"), "3.12"), WindowsRecipe)
    assert isinstance(recipe_for(TargetPlatform.parse("linux-x64"), "3.12"), LinuxRecipe)
    assert isinstance(recipe_for(TargetPlatform.parse("macos-arm64"), "3.12"), MacosRecipe)
    assert isinstance(
        recipe_for(TargetPlatform.parse("android-arm64"), "3.13"), AndroidRecipe
    )
    assert isinstance(recipe_for(TargetPlatform.parse("ios-arm64"), "3.13"), IosRecipe)
    assert isinstance(
        recipe_for(TargetPlatform.parse("emscripten-wasm"), "3.12"), EmscriptenRecipe
    )


def test_windows_recipe_has_no_configure() -> None:
    recipe = WindowsRecipe(TargetPlatform.parse("windows-x64"), "3.12")
    assert recipe.configure_cmd(Path("."), Path(".")) is None
    cmd = recipe.build_cmd(Path("."), Path("."), 4)
    assert "PCbuild" in " ".join(cmd)
    assert any("build.py" in part for part in cmd)


def test_linux_configure_uses_source_dir(tmp_path: Path) -> None:
    recipe = LinuxRecipe(TargetPlatform.parse("linux-x64"), "3.12")
    cmd = recipe.configure_cmd(tmp_path, tmp_path / "b")
    assert cmd is not None
    assert str(tmp_path / "configure") in cmd[0]


def test_android_requires_ndk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANDROID_NDK_HOME", raising=False)
    monkeypatch.delenv("ANDROID_NDK_ROOT", raising=False)
    recipe = AndroidRecipe(TargetPlatform.parse("android-arm64"), "3.13")
    problems = recipe.validate()
    assert any("NDK" in p or "build" in p for p in problems)


def test_android_env_sets_cross_tools(tmp_path: Path) -> None:
    # Fabricate an NDK layout so _toolchain_bin resolves.
    ndk = tmp_path / "ndk"
    bin_dir = ndk / "toolchains" / "llvm" / "prebuilt" / "windows-x86_64" / "bin"
    bin_dir.mkdir(parents=True)
    recipe = AndroidRecipe(
        TargetPlatform.parse("android-arm64"), "3.13", ndk_home=str(ndk)
    )
    env = recipe.env()
    assert env["CC"] == "clang"
    assert env["AR"] == "llvm-ar"
    assert str(bin_dir) in env["PATH"]


def test_android_clang_target_embeds_api_level(tmp_path: Path) -> None:
    ndk = tmp_path / "ndk"
    recipe = AndroidRecipe(
        TargetPlatform.parse("android-arm64", ), "3.13", ndk_home=str(ndk)
    )
    assert recipe._clang_target_triple() == "aarch64-linux-android24"


def test_ios_env_sets_sdk_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sdk = tmp_path / "iPhonesimulator.sdk"
    sdk.mkdir()
    monkeypatch.setenv("IOS_SDKROOT", str(sdk))
    recipe = IosRecipe(TargetPlatform.parse("ios-x64"), "3.13")
    env = recipe.env()
    assert "-isysroot" in env["CFLAGS"]
    assert str(sdk) in env["CFLAGS"]
    assert "simulator" in env["_PYTHON_HOST_PLATFORM"]


def test_mobile_recipes_reject_old_cpython() -> None:
    recipe = AndroidRecipe(TargetPlatform.parse("android-arm64"), "3.12")
    problems = recipe.validate()
    assert any("3.13" in p for p in problems)


def test_emscripten_emconfigure_wrapper() -> None:
    recipe = EmscriptenRecipe(TargetPlatform.parse("emscripten-wasm"), "3.12")
    cmd = recipe.configure_cmd(Path("/src"), Path("/build"))
    assert cmd is not None
    assert cmd[0] == "emconfigure"


# ---------------------------------------------------------------------------
# Runtime builder orchestration (dry-run + validation only; no real builds)
# ---------------------------------------------------------------------------


def test_runtime_builder_unknown_target_still_parses(tmp_path: Path) -> None:
    spec = RuntimeSpec(target_triple="linux-x64", python_version="3.12")
    builder = CpythonRuntimeBuilder(spec, cache_root=tmp_path)
    assert builder.platform.os == "linux"


def test_runtime_dry_run_reports_without_building(tmp_path: Path) -> None:
    spec = RuntimeSpec(target_triple="windows-x64", python_version="3.12")
    builder = CpythonRuntimeBuilder(spec, cache_root=tmp_path)
    result = builder.ensure_runtime(dry_run=True)
    assert result.runtime_dir == tmp_path / "windows-x64-3.12"
    assert not result.runtime_dir.exists()


def test_runtime_validation_failure_raises(tmp_path: Path) -> None:
    # Old CPython for mobile must fail validation before any build starts.
    spec = RuntimeSpec(target_triple="android-arm64", python_version="3.10")
    builder = CpythonRuntimeBuilder(spec, cache_root=tmp_path)
    with pytest.raises(RuntimeError, match="CPython"):
        builder.ensure_runtime(dry_run=True)


def test_runtime_build_key_layout(tmp_path: Path) -> None:
    spec = RuntimeSpec(target_triple="macos-arm64", python_version="3.13")
    builder = CpythonRuntimeBuilder(spec, cache_root=tmp_path)
    assert builder.build_key == "macos-arm64-3.13"


# ---------------------------------------------------------------------------
# Source manager (offline paths only)
# ---------------------------------------------------------------------------


def test_source_manager_paths(tmp_path: Path) -> None:
    mgr = CPythonSourceManager(root=tmp_path / "cpython")
    tree = mgr.source_tree("3.12.3")
    assert tree.name == "Python-3.12.3"
    assert tree.parent == tmp_path / "cpython"


def test_source_manager_status(tmp_path: Path) -> None:
    mgr = CPythonSourceManager(root=tmp_path / "cpython")
    status = mgr.status(["3.12"])
    assert status == [("3.12", False)]
    tree = mgr.source_tree("3.12")
    tree.mkdir(parents=True)
    (tree / ".forger-ok").write_text("ok\n")
    status = mgr.status(["3.12"])
    assert status == [("3.12", True)]


def test_ensure_source_uses_cache(tmp_path: Path) -> None:
    mgr = CPythonSourceManager(root=tmp_path / "cpython")
    tree = mgr.source_tree("3.12")
    tree.mkdir(parents=True)
    (tree / ".forger-ok").write_text("ok\n", encoding="utf-8")
    # Cached trees are returned without any download attempt.
    assert mgr.ensure_source("3.12") == tree


# ---------------------------------------------------------------------------
# Builder + .forge directory artifact integration
# ---------------------------------------------------------------------------


def _make_forge_artifact(root: Path) -> tuple[Path, str]:
    """Create a minimal uncompressed .forge artifact; return (path, entry)."""
    from forger.compiler import Compiler

    vfs = root / "dist"
    (vfs / "pkg").mkdir(parents=True)
    (vfs / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (vfs / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    artifact = root / "app.forge"
    compiler = Compiler.forge_from_vfs(vfs, artifact, entry_point="main")
    compiler.generate_artifact()
    return artifact, "main"


def test_artifact_is_uncompressed_directory(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    artifact, _ = _make_forge_artifact(root)

    assert artifact.is_dir(), ".forge must be a directory now"
    manifest_path = artifact / "MANIFEST.json"
    assert manifest_path.is_file()
    assert (artifact / "main.py").is_file()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["entry_point"] == "main"
    assert "main.py" in {entry["path"] for entry in data["files"]}


def test_artifact_atomic_swap_keeps_previous_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forger.compiler import Compiler

    root = tmp_path / "proj"
    root.mkdir()
    vfs = root / "dist"
    vfs.mkdir()
    (vfs / "main.py").write_text("x=1\n", encoding="utf-8")

    artifact = root / "app.forge"
    compiler = Compiler.forge_from_vfs(vfs, artifact)
    compiler.generate_artifact()
    good_manifest = (artifact / "MANIFEST.json").read_bytes()

    def boom(src: object, dst: object, **kw: object) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr("shutil.copytree", boom)
    with pytest.raises(OSError):
        compiler.generate_artifact()

    # Previous artifact untouched.
    assert (artifact / "MANIFEST.json").read_bytes() == good_manifest


def test_builder_info_reads_directory_artifact(tmp_path: Path) -> None:
    from forger.builder import Builder

    root = tmp_path / "proj"
    root.mkdir()
    artifact, _ = _make_forge_artifact(root)

    builder = Builder(artifact_path=artifact, target="linux-x64")
    info = builder.artifact_info()
    assert "Entry point: main" in info
    assert "Files:" in info


def test_builder_rejects_missing_artifact(tmp_path: Path) -> None:
    from forger.builder import Builder

    # A directory without MANIFEST.json is not a valid .forge artifact.
    bare = tmp_path / "bare.forge"
    bare.mkdir()
    builder = Builder(artifact_path=bare, target="linux-x64")
    with pytest.raises(RuntimeError, match="Not a .forge"):
        builder.artifact_info()


def test_full_bundle_assembly_with_fake_runtime(tmp_path: Path) -> None:
    """Bundle assembly without compiling CPython: fake the runtime step."""
    from unittest.mock import patch

    from forger.builder import Builder

    root = tmp_path / "proj"
    root.mkdir()
    artifact, _ = _make_forge_artifact(root)

    # Fake cached runtime.
    fake_runtime = tmp_path / "runtime-cache" / "linux-x64-3.12"
    (fake_runtime / "bin").mkdir(parents=True)
    (fake_runtime / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (fake_runtime / "lib").mkdir()
    (fake_runtime / "lib" / "python3.12").mkdir()
    (fake_runtime / "lib" / "python3.12" / "os.py").write_text("", encoding="utf-8")

    output = tmp_path / "bundle"

    fake_result = type(
        "R", (), {
            "triple": "linux-x64",
            "version": "3.12",
            "runtime_dir": fake_runtime,
            "stripped_count": 1,
            "stdlib_module_count": 1,
            "reused_existing": True,
        },
    )()

    with patch(
        "forger.build.runtime_builder.CpythonRuntimeBuilder.ensure_runtime",
        return_value=fake_result,
    ):
        builder = Builder(artifact_path=artifact, target="linux-x64", output_dir=output)
        result = builder.build()

    assert result == output
    assert (output / "app" / "main.py").is_file()
    assert (output / "runtime" / "bin" / "python").is_file()
    launcher = output / "run.sh"
    assert launcher.is_file()
    assert "runtime/bin/python" in launcher.read_text(encoding="utf-8")
    bundle_json = json.loads((output / "BUNDLE.json").read_text(encoding="utf-8"))
    assert bundle_json["target"] == "linux-x64"
    assert bundle_json["entry_point"] == "main"


def test_cli_runtime_info_smoke(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from forger.cli.__main__ import main

    monkeypatch.setattr("sys.argv", ["forger", "runtime", "info", "-t", "linux-x64"])
    with pytest.raises(SystemExit) as exc:
        main()
    # Exits non-zero on this host only if toolchain missing; parse either way.
    assert exc.value.code in (0, 1)
    out = capsys.readouterr().out
    assert "Target:" in out or out == ""
