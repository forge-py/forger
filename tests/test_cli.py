"""CLI tests — verify the Click CLI commands work correctly."""

from __future__ import annotations

import tempfile
from pathlib import Path

from click.testing import CliRunner

from forger.cli.__main__ import main


def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "forger" in result.output.lower() or "0.1.0" in result.output


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "compile" in result.output
    assert "build" in result.output


def test_cli_compile_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["compile", "--help"])
    assert result.exit_code == 0
    assert "--entry-point" in result.output
    assert "--output" in result.output


def test_cli_build_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["build", "--help"])
    assert result.exit_code == 0
    assert "--target" in result.output


def test_cli_compile_simple_project() -> None:
    """Test the compile command on a simple project."""
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text(
            "import app\nprint('hello')\n"
        )
        (root / "app.py").write_text(
            "import json\n\ndef run(): pass\n"
        )

        output = root / "app.forge"
        result = runner.invoke(
            main,
            [
                "compile",
                str(root),
                "--output", str(output),
                "--entry-point", "main",
            ],
        )

        assert result.exit_code == 0
        assert "Compilation complete" in result.output or "Compilation Summary" in result.output


def test_cli_compile_with_forger_py() -> None:
    """Test compile with forger.py."""
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("import app\n")
        (root / "app.py").write_text("pass\n")
        (root / "forger.py").write_text(
            "from forger import include_module\n"
            'include_module("extra")\n'
        )

        output = root / "app.forge"
        result = runner.invoke(
            main,
            [
                "compile",
                str(root),
                "--output", str(output),
            ],
        )

        assert result.exit_code == 0


def test_cli_compile_nonexistent_source() -> None:
    """Test compile with nonexistent source directory."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["compile", "/nonexistent/path"],
    )
    assert result.exit_code != 0


def test_cli_build_nonexistent_artifact() -> None:
    """Test build with nonexistent artifact."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["build", "/nonexistent/app.forge"],
    )
    assert result.exit_code != 0


def test_cli_info_command() -> None:
    """Test the info command."""
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        artifact = root / "app.forge"
        artifact.write_bytes(b"FRG0" + b"\x00" * 100)

        result = runner.invoke(
            main,
            ["info", str(artifact)],
        )

        # Should show artifact info
        assert result.exit_code == 0 or "Artifact" in result.output


def test_cli_compile_verbose() -> None:
    """Test compile with verbose flag."""
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("pass\n")

        output = root / "app.forge"
        result = runner.invoke(
            main,
            [
                "compile",
                str(root),
                "--output", str(output),
                "--verbose",
            ],
        )

        assert result.exit_code == 0


def test_cli_compile_custom_entry_point() -> None:
    """Test compile with custom entry point."""
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "server.py").write_text("import app\n")
        (root / "app.py").write_text("pass\n")

        output = root / "app.forge"
        result = runner.invoke(
            main,
            [
                "compile",
                str(root),
                "--output", str(output),
                "--entry-point", "server",
            ],
        )

        assert result.exit_code == 0
