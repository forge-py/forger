"""Build target tests."""

from __future__ import annotations

from forger.build.targets import TargetPlatform
from forger.builder import Builder


def test_all_supported_targets() -> None:
    for triple in [
        "windows-x64",
        "windows-arm64",
        "linux-x64",
        "linux-arm64",
        "macos-x64",
        "macos-arm64",
        "android-arm64",
        "emscripten-wasm",
    ]:
        target = TargetPlatform.parse(triple)
        assert target.os
        assert target.arch


def test_windows_executable_ext() -> None:
    target = TargetPlatform.parse("windows-x64")
    assert target.executable_ext == ".exe"


def test_linux_executable_ext() -> None:
    target = TargetPlatform.parse("linux-x64")
    assert target.executable_ext == ""


def test_macos_lib_ext() -> None:
    target = TargetPlatform.parse("macos-arm64")
    assert target.lib_ext == ".dylib"


def test_windows_python_ext() -> None:
    target = TargetPlatform.parse("windows-x64")
    assert target.python_ext == ".pyd"


def test_linux_python_ext() -> None:
    target = TargetPlatform.parse("linux-x64")
    assert target.python_ext == ".so"


def test_builder_initialization() -> None:
    builder = Builder(
        artifact_path="/tmp/app.forge",
        target="windows-x64",
    )
    assert builder.target == "windows-x64"


def test_builder_output_dir_default() -> None:
    builder = Builder(
        artifact_path="/tmp/app.forge",
        target="linux-x64",
    )
    assert "linux-x64" in str(builder.output_dir)


def test_builder_output_dir_custom() -> None:
    builder = Builder(
        artifact_path="/tmp/app.forge",
        target="linux-x64",
        output_dir="/custom/output",
    )
    assert builder.output_dir.name == "output"
