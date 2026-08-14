"""Tests for the build targets module."""

from __future__ import annotations

from forger.build.targets import TargetPlatform


def test_target_parse() -> None:
    target = TargetPlatform.parse("windows-x64")
    assert target.os == "windows"
    assert target.arch == "x64"
    assert target.triple == "windows-x64"
    assert target.executable_ext == ".exe"


def test_target_linux() -> None:
    target = TargetPlatform.parse("linux-arm64")
    assert target.os == "linux"
    assert target.arch == "arm64"
    assert target.executable_ext == ""


def test_target_with_env() -> None:
    target = TargetPlatform.parse("linux-x64-musl")
    assert target.os == "linux"
    assert target.arch == "x64"
    assert target.env == "musl"
    assert target.triple == "linux-x64-musl"


def test_runtime_info() -> None:
    from forger.runtime import get_runtime_info

    info = get_runtime_info()
    assert "python_version" in info
    assert "platform" in info
