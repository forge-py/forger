"""CLI entry point for Forger."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

try:
    import click
except ImportError:
    click = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@click.group()  # type: ignore[name-defined]
@click.version_option(version="0.1.0")  # type: ignore[name-defined]
def main() -> None:
    """Forger — Python application compiler and bundler."""
    pass


@main.command()  # type: ignore[name-defined]
@click.argument("source", type=click.Path(exists=True), default=".")  # type: ignore[name-defined]
@click.option(  # type: ignore[name-defined]
    "--output", "-o", default="app.forge", help="Output .forge file path."
)
@click.option(  # type: ignore[name-defined]
    "--entry-point", "-e", default="main", help="Entry point module."
)
@click.option(  # type: ignore[name-defined]
    "--venv", type=click.Path(exists=True),  # type: ignore[name-defined]
    help="Path to virtual environment."
)
@click.option(  # type: ignore[name-defined]
    "--forger-py", type=click.Path(exists=True),  # type: ignore[name-defined]
    help="Path to forger.py project extension."
)
@click.option(  # type: ignore[name-defined]
    "--verbose", "-v", is_flag=True, help="Enable verbose output."
)
def compile(  # type: ignore[name-defined]
    source: str,
    output: str,
    entry_point: str,
    venv: str | None,
    forger_py: str | None,
    verbose: bool,
) -> None:
    """Analyze and compile a Python project into a .forge artifact."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, force=True)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

    logger.info("Forger compile: %s -> %s", source, output)
    logger.info("Entry point: %s", entry_point)

    project_root = Path(source).resolve()

    # Run the compilation pipeline
    try:
        from forger.compiler import Compiler

        compiler = Compiler(
            project_root=project_root,
            entry_point=entry_point,
            output_path=Path(output).resolve(),
        )

        # Process forger.py if present
        if forger_py:
            compiler.process_forger_py(Path(forger_py).resolve())
        else:
            # Auto-detect forger.py
            forger_py_path = project_root / "forger.py"
            if forger_py_path.exists():
                compiler.process_forger_py(forger_py_path)

        # Run analysis
        compiler.analyze()

        # Run optimizers
        compiler.run_optimizers()

        # Generate artifact
        compiler.generate_artifact()

        logger.info("Compilation complete: %s", output)
        logger.info(compiler.diagnostic_summary())

    except Exception as e:
        logger.error("Compilation failed: %s", e, exc_info=True)
        sys.exit(1)


@main.command()  # type: ignore[name-defined]
@click.argument("artifact", type=click.Path(exists=True))  # type: ignore[name-defined]
@click.option(  # type: ignore[name-defined]
    "--target", "-t", default="windows-x64",
    help="Target platform (e.g., windows-x64, linux-x64, android-arm64).",
)
@click.option(  # type: ignore[name-defined]
    "--output", "-o", default=None, help="Output directory."
)
@click.option(  # type: ignore[name-defined]
    "--verbose", "-v", is_flag=True, help="Enable verbose output."
)
def build(  # type: ignore[name-defined]
    artifact: str,
    target: str,
    output: str | None,
    verbose: bool,
) -> None:
    """Build a platform-specific executable from a .forge artifact."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, force=True)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

    logger.info("Forger build: %s -> %s", artifact, target)

    try:
        from forger.builder import Builder

        builder = Builder(
            artifact_path=Path(artifact).resolve(),
            target=target,
            output_dir=Path(output).resolve() if output else None,
        )

        builder.build()

        logger.info("Build complete.")

    except Exception as e:
        logger.error("Build failed: %s", e, exc_info=True)
        sys.exit(1)


@main.command()  # type: ignore[name-defined]
@click.argument("artifact", type=click.Path(exists=True))  # type: ignore[name-defined]
def info(  # type: ignore[name-defined]
    artifact: str,
) -> None:
    """Show information about a .forge artifact."""
    try:
        from forger.builder import Builder

        builder = Builder(
            artifact_path=Path(artifact).resolve(),
            target="unknown",
        )

        print(builder.artifact_info())

    except Exception as e:
        logger.error("Failed to read artifact: %s", e)
        sys.exit(1)


def run() -> None:
    """Entry point for the forger command."""
    if click is None:
        print("Error: click is required. Install with: uv sync", file=sys.stderr)
        sys.exit(1)
    main()


if __name__ == "__main__":
    run()
