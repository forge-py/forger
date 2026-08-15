"""CLI tests — verify the CLI command functions work correctly.

The CLI argument parsing is handled by the Rust binary (clap).
These tests exercise the Python command implementations directly
and use subprocess for end-to-end CLI validation.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from forger.cli.__main__ import run_compile, run_build, run_forge, run_info


def test_run_compile_simple_project(capsys) -> None:
    """Test run_compile on a simple project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("import app\nprint('hello')\n")
        (root / "app.py").write_text("import json\n\ndef run(): pass\n")
        output = root / "dist"

        run_compile(str(root), str(output), "main", verbose=False)
        captured = capsys.readouterr()
        assert "Compilation complete" in captured.out or "Forger compile" in captured.out


def test_run_compile_with_forger_py(capsys) -> None:
    """Test run_compile with forger.py."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("import app\n")
        (root / "app.py").write_text("pass\n")
        (root / "forger.py").write_text(
            "from forger import defineConfig\ndefineConfig({'entry': 'main.py'})\n"
        )
        output = root / "dist"

        run_compile(str(root), str(output), "main", verbose=False)
        # Should complete without error


def test_run_compile_nonexistent_source() -> None:
    """Test run_compile with nonexistent source directory."""
    with pytest.raises(SystemExit) as exc_info:
        run_compile("/nonexistent/forger/path/that/does/not/exist", "dist", "main")
    assert exc_info.value.code != 0


def test_run_compile_custom_entry_point(capsys) -> None:
    """Test run_compile with custom entry point."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "server.py").write_text("import app\n")
        (root / "app.py").write_text("pass\n")
        output = root / "dist"

        run_compile(str(root), str(output), "server", verbose=False)
        captured = capsys.readouterr()
        assert "Forger compile" in captured.out or "Compilation complete" in captured.out


def test_run_build_nonexistent_artifact() -> None:
    """Test run_build with nonexistent artifact."""
    with pytest.raises(SystemExit) as exc_info:
        run_build("/nonexistent/app.forge", "windows-x64")
    assert exc_info.value.code != 0


def test_cli_version(capfd) -> None:
    """Test the --version flag via subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "forger.cli.__main__", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "forger" in result.stdout.lower() or "0.1.0" in result.stdout


def test_cli_help() -> None:
    """Test the --help flag via subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "forger.cli.__main__", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "compile" in result.stdout
    assert "build" in result.stdout


def test_cli_compile_help() -> None:
    """Test compile --help via subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "forger.cli.__main__", "compile", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--entry-point" in result.stdout
    assert "--output" in result.stdout


def test_cli_build_help() -> None:
    """Test build --help via subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "forger.cli.__main__", "build", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--target" in result.stdout


def test_cli_compile_verbose(capsys) -> None:
    """Test run_compile with verbose flag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("pass\n")
        output = root / "dist"

        run_compile(str(root), str(output), "main", verbose=True)
        # Should complete without error


def test_cli_compile_simple_project_via_subprocess() -> None:
    """Test compile command end-to-end via subprocess."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("import app\nprint('hello')\n")
        (root / "app.py").write_text("import json\n\ndef run(): pass\n")
        output = root / "dist"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "forger.cli.__main__",
                "compile",
                str(root),
                "--output",
                str(output),
                "--entry-point",
                "main",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
