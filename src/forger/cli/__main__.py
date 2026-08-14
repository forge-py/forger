"""CLI entry point for Forger.

Workflow:
    forger compile  ->  dist/          (VFS directory)
    forger forge    ->  app.forge      (package dist/ into .forge artifact)
    forger build    ->  native binary  (consume .forge, produce executable)
"""

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
    "--output", "-o", default="dist", help="Output VFS directory."
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
def compile(  # noqa: A001  # type: ignore[name-defined]
    source: str,
    output: str,
    entry_point: str,
    venv: str | None,
    forger_py: str | None,
    verbose: bool,
) -> None:
    """Analyze a Python project and output VFS to dist/ directory."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, force=True)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

    print(f"Forger compile: {source} -> {output}")
    print(f"Entry point: {entry_point}")

    project_root = Path(source).resolve()
    # Resolve output relative to the source directory, not CWD
    output_path = Path(output)
    if not output_path.is_absolute():
        output_dir = project_root / output_path
    else:
        output_dir = output_path.resolve()

    # Run the compilation pipeline
    try:
        from forger.compiler import Compiler

        compiler = Compiler(
            project_root=project_root,
            entry_point=entry_point,
            output_path=output_dir,
        )

        # Run analysis (creates the dependency graph)
        compiler.analyze()

        # Process forger.py if present — graph is now available for plugins
        if forger_py:
            compiler.process_forger_py(Path(forger_py).resolve())
        else:
            # Auto-detect forger.py or forger.config.py
            for forger_cfg_name in ("forger.py", "forger.config.py"):
                forger_py_path = project_root / forger_cfg_name
                if forger_py_path.exists():
                    compiler.process_forger_py(forger_py_path)
                    break

        # Check for dist_dir override from config
        import forger.api as api_module

        ctx = api_module.get_context()
        cfg = ctx.config
        if cfg and cfg.dist_dir:
            cfg_output = project_root / cfg.dist_dir
            if cfg_output != output_dir:
                output_dir = cfg_output
                compiler.output_path = cfg_output
                logger.info("Using dist_dir from config: %s", cfg_output)

        # Clean dist directory before compiling
        import shutil

        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info("Cleaned dist directory: %s", output_dir)

        # Run plugins (framework-specific discovery)
        compiler.run_plugins()

        # Generate VFS directory (includes venv package copying)
        compiler.generate_vfs()

        print(f"Compilation complete: {output_dir}")
        print(compiler.diagnostic_summary())

    except Exception as e:
        logger.error("Compilation failed: %s", e, exc_info=True)
        sys.exit(1)


@main.command()  # type: ignore[name-defined]
@click.argument("vfs_dir", type=click.Path(exists=True), default="dist")  # type: ignore[name-defined]
@click.option(  # type: ignore[name-defined]
    "--output", "-o", default="app.forge", help="Output .forge artifact path."
)
@click.option(  # type: ignore[name-defined]
    "--verbose", "-v", is_flag=True, help="Enable verbose output."
)
def forge(  # type: ignore[name-defined]
    vfs_dir: str,
    output: str,
    verbose: bool,
) -> None:
    """Package the VFS dist/ directory into a .forge artifact."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, force=True)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

    logger.info("Forger forge: %s -> %s", vfs_dir, output)

    vfs_path = Path(vfs_dir).resolve()
    artifact_path = Path(output).resolve()

    try:
        from forger.compiler import Compiler

        compiler = Compiler.forge_from_vfs(vfs_path, artifact_path)
        compiler.generate_artifact()

        logger.info("Artifact generated: %s", artifact_path)

    except Exception as e:
        logger.error("Forge failed: %s", e, exc_info=True)
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

        logger.info("Build complete: %s", builder.output_dir)

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