"""CLI entry point for Forger.

Workflow:
    forger compile  ->  dist/          (VFS directory)
    forger forge    ->  app.forge      (package dist/ into .forge artifact)
    forger build    ->  native binary  (consume .forge, produce executable)

The CLI is implemented in Rust (clap) and this Python module delegates
command execution to the Python-level Compiler/Builder pipelines.
Argument parsing is handled by the Rust binary — this module provides
the Python execution layer.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command implementations (called by the Rust CLI via the entry point)
# ---------------------------------------------------------------------------


def run_compile(
    source: str,
    output: str,
    entry_point: str,
    venv: str | None = None,
    forger_py: str | None = None,
    verbose: bool = False,
) -> None:
    """Analyze a Python project and output VFS to dist/ directory."""
    setup_logging(verbose)

    print(f"Forger compile: {source} -> {output}")
    print(f"Entry point: {entry_point}")

    project_root = Path(source).resolve()
    if not project_root.exists() or not project_root.is_dir():
        print(f"Error: source directory does not exist: {project_root}", file=sys.stderr)
        sys.exit(1)
    entry_file = project_root / (entry_point + ".py")
    if not entry_file.exists():
        # Try without .py extension (package mode)
        entry_pkg = project_root / entry_point
        if not entry_pkg.is_dir():
            print(f"Error: entry point not found: {entry_point}", file=sys.stderr)
            sys.exit(1)

    output_path = Path(output)
    if not output_path.is_absolute():
        output_dir = project_root / output_path
    else:
        output_dir = output_path.resolve()

    try:
        from forger.compiler import Compiler

        compiler = Compiler(
            project_root=project_root,
            entry_point=entry_point,
            output_path=output_dir,
        )

        compiler.analyze()

        if forger_py:
            compiler.process_forger_py(Path(forger_py).resolve())
        else:
            for forger_cfg_name in ("forger.py", "forger.config.py"):
                forger_py_path = project_root / forger_cfg_name
                if forger_py_path.exists():
                    compiler.process_forger_py(forger_py_path)
                    break

        import forger.api as api_module

        ctx = api_module.get_context()
        cfg = ctx.config
        if cfg and cfg.dist_dir:
            cfg_output = project_root / cfg.dist_dir
            if cfg_output != output_dir:
                output_dir = cfg_output
                compiler.output_path = cfg_output
                logger.info("Using dist_dir from config: %s", cfg_output)

        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info("Cleaned dist directory: %s", output_dir)

        compiler.run_plugins()
        compiler.generate_vfs()

        print(f"Compilation complete: {output_dir}")
        print(compiler.diagnostic_summary())

    except Exception as e:
        logger.error("Compilation failed: %s", e, exc_info=True)
        sys.exit(1)


def run_forge(
    vfs_dir: str,
    output: str,
    verbose: bool = False,
) -> None:
    """Package the VFS dist/ directory into a .forge artifact."""
    setup_logging(verbose)

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


def run_build(
    artifact: str,
    target: str,
    output: str | None = None,
    verbose: bool = False,
) -> None:
    """Build a platform-specific executable from a .forge artifact."""
    setup_logging(verbose)

    artifact_path = Path(artifact).resolve()
    if not artifact_path.is_file():
        print(f"Error: artifact does not exist: {artifact_path}", file=sys.stderr)
        sys.exit(1)

    logger.info("Forger build: %s -> %s", artifact, target)

    try:
        from forger.builder import Builder

        builder = Builder(
            artifact_path=artifact_path,
            target=target,
            output_dir=Path(output).resolve() if output else None,
        )

        builder.build()

        logger.info("Build complete: %s", builder.output_dir)

    except Exception as e:
        logger.error("Build failed: %s", e, exc_info=True)
        sys.exit(1)


def run_info(artifact: str) -> None:
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


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(verbose: bool) -> None:
    """Configure logging based on verbosity."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, force=True)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)


# ---------------------------------------------------------------------------
# Entry point — delegates to Rust CLI for arg parsing, then runs Python
# command implementations.
# ---------------------------------------------------------------------------


def run() -> None:
    """Entry point for the forger command.

    The Rust binary (clap) handles argument parsing. When invoked via
    the Python entry point, we dispatch to the appropriate command
    implementation based on sys.argv.
    """
    args = sys.argv[1:]
    if not args:
        print_usage()
        sys.exit(1)

    command = args[0]

    if command == "--version" or command == "-V":
        from forger import __version__

        print(f"forger {__version__}")
        return

    if command == "--help" or command == "-h":
        print_usage()
        return

    if command == "compile":
        parsed = _parse_compile_args(args[1:])
        run_compile(**parsed)
    elif command == "forge":
        parsed = _parse_forge_args(args[1:])
        run_forge(**parsed)
    elif command == "build":
        parsed = _parse_build_args(args[1:])
        run_build(**parsed)
    elif command == "info":
        parsed = _parse_info_args(args[1:])
        run_info(**parsed)
    elif command == "build-config":
        _run_build_config(args[1:])
    elif command == "cpython":
        _run_cpython(args[1:])
    else:
        print(f"Error: unknown command '{command}'", file=sys.stderr)
        print_usage()
        sys.exit(1)


def _parse_compile_args(args: list[str]) -> dict[str, object]:
    result: dict[str, object] = {
        "source": ".",
        "output": "dist",
        "entry_point": "main",
        "venv": None,
        "forger_py": None,
        "verbose": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--help" or a == "-h":
            print(
                "Usage: forger compile [SOURCE] [OPTIONS]\n"
                "\n"
                "Options:\n"
                "  -o, --output TEXT        Output path for VFS artifact (default: dist)\n"
                "  -e, --entry-point TEXT   Entry point module (default: main)\n"
                "  --venv TEXT              Virtual environment path\n"
                "  --forger-py TEXT         Path to forger.py config\n"
                "  -v, --verbose            Enable verbose output\n"
                "  -h, --help               Show this message\n"
            )
            sys.exit(0)
        elif a == "--output" or a == "-o":
            i += 1
            result["output"] = args[i] if i < len(args) else "dist"
        elif a == "--entry-point" or a == "-e":
            i += 1
            result["entry_point"] = args[i] if i < len(args) else "main"
        elif a == "--venv":
            i += 1
            result["venv"] = args[i] if i < len(args) else None
        elif a == "--forger-py":
            i += 1
            result["forger_py"] = args[i] if i < len(args) else None
        elif a == "--verbose" or a == "-v":
            result["verbose"] = True
        elif not a.startswith("-"):
            result["source"] = a
        i += 1
    return result


def _parse_forge_args(args: list[str]) -> dict[str, object]:
    result: dict[str, object] = {
        "vfs_dir": "dist",
        "output": "app.forge",
        "verbose": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--output" or a == "-o":
            i += 1
            result["output"] = args[i] if i < len(args) else "app.forge"
        elif a == "--verbose" or a == "-v":
            result["verbose"] = True
        elif not a.startswith("-"):
            result["vfs_dir"] = a
        i += 1
    return result


def _parse_build_args(args: list[str]) -> dict[str, object]:
    result: dict[str, object] = {
        "artifact": "",
        "target": "windows-x64",
        "output": None,
        "verbose": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--help" or a == "-h":
            print(
                "Usage: forger build ARTIFACT [OPTIONS]\n"
                "\n"
                "Options:\n"
                "  -t, --target TEXT   Target platform (default: windows-x64)\n"
                "  -o, --output TEXT   Output directory\n"
                "  -v, --verbose       Enable verbose output\n"
                "  -h, --help          Show this message\n"
            )
            sys.exit(0)
        elif a == "--target" or a == "-t":
            i += 1
            result["target"] = args[i] if i < len(args) else "windows-x64"
        elif a == "--output" or a == "-o":
            i += 1
            result["output"] = args[i] if i < len(args) else None
        elif a == "--verbose" or a == "-v":
            result["verbose"] = True
        elif not a.startswith("-"):
            result["artifact"] = a
        i += 1
    return result


def _parse_info_args(args: list[str]) -> dict[str, object]:
    result: dict[str, object] = {"artifact": ""}
    for a in args:
        if not a.startswith("-"):
            result["artifact"] = a
    return result


def _run_build_config(args: list[str]) -> None:
    if not args or args[0] != "check":
        print("Error: unknown build-config subcommand. Use 'check'.", file=sys.stderr)
        sys.exit(1)

    target = "windows-x64"
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--target" or a == "-t":
            i += 1
            target = args[i] if i < len(args) else "windows-x64"
        i += 1

    try:
        from forger_core import check_build_config  # type: ignore[import-not-found, import-untyped, missing-import]

        result = check_build_config(target)
        print(result.format_report())
        if result.has_issues():
            sys.exit(1)
    except ImportError:
        # Rust core not available — do a basic Python-level check
        _build_config_check_python(target)


def _build_config_check_python(target: str) -> None:
    """Fallback build-config check when Rust core is not available."""
    import shutil
    import subprocess
    import sys

    os = target.split("-")[0]
    tools: list[tuple[str, bool]] = []

    if os == "windows":
        tools.append(("MSVC Compiler (cl.exe)", shutil.which("cl.exe") is not None))
        tools.append(("MSVC Librarian (lib.exe)", shutil.which("lib.exe") is not None))
        tools.append(("MSVC Linker (link.exe)", shutil.which("link.exe") is not None))
    elif os == "linux":
        tools.append(("GCC", shutil.which("gcc") is not None))
        tools.append(("G++", shutil.which("g++") is not None))
        tools.append(("Make", shutil.which("make") is not None))
    elif os == "macos":
        try:
            result = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
            tools.append(("Xcode Command Line Tools", result.returncode == 0))
        except FileNotFoundError:
            tools.append(("Xcode Command Line Tools", False))
        tools.append(("Clang", shutil.which("clang") is not None))

    # Python check (all platforms)
    tools.append(("Python", sys.executable is not None and Path(sys.executable).exists()))

    passed = all(ok for _, ok in tools)

    print(f"Build Configuration Check: {target}")
    print("─" * 50)
    for name, ok in tools:
        status = "✓" if ok else "✗"
        state = "present" if ok else "missing"
        print(f"  {status} {name}: {state}")
    print("─" * 50)
    print(
        "Result: All required tools are available"
        if passed
        else "Result: Some required tools are missing or misconfigured"
    )

    if not passed:
        sys.exit(1)


def _run_cpython(args: list[str]) -> None:
    if not args or args[0] not in ("analyze", "build-config"):
        print("Error: unknown cpython subcommand. Use 'analyze' or 'build-config'.", file=sys.stderr)
        sys.exit(1)

    subcommand = args[0]

    if subcommand == "analyze":
        _cpython_analyze(args[1:])
    elif subcommand == "build-config":
        _cpython_build_config(args[1:])


def _cpython_analyze(args: list[str]) -> None:
    modules = []
    version = "3.12"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--version":
            i += 1
            version = args[i] if i < len(args) else "3.12"
        elif not a.startswith("-"):
            modules.append(a)
        i += 1

    if not modules:
        print("Error: no modules specified. Provide one or more module names.", file=sys.stderr)
        sys.exit(1)

    try:
        from forger_core import CpythonModuleRegistry  # type: ignore[import-not-found, import-untyped, missing-import]

        ver_parts = version.split(".")
        major = int(ver_parts[0]) if len(ver_parts) > 0 else 3
        minor = int(ver_parts[1]) if len(ver_parts) > 1 else 12
        registry = CpythonModuleRegistry.new((major, minor))
        analysis = registry.analyze_required_sources(modules)
        print(analysis.format_report())
    except ImportError:
        print("Error: Rust core not available. Install with 'uv sync' and rebuild.")
        sys.exit(1)


def _cpython_build_config(args: list[str]) -> None:
    modules = []
    target = "windows-x64"
    version = "3.12"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--target" or a == "-t":
            i += 1
            target = args[i] if i < len(args) else "windows-x64"
        elif a == "--version":
            i += 1
            version = args[i] if i < len(args) else "3.12"
        elif not a.startswith("-"):
            modules.append(a)
        i += 1

    if not modules:
        print("Error: no modules specified. Provide one or more module names.", file=sys.stderr)
        sys.exit(1)

    try:
        from forger_core import CpythonModuleRegistry, CpythonBuildConfig  # type: ignore[import-not-found, import-untyped, missing-import]

        ver_parts = version.split(".")
        major = int(ver_parts[0]) if len(ver_parts) > 0 else 3
        minor = int(ver_parts[1]) if len(ver_parts) > 1 else 12
        registry = CpythonModuleRegistry.new((major, minor))
        analysis = registry.analyze_required_sources(modules)
        build_cfg = CpythonBuildConfig.from_analysis(analysis, target, version)
        print(build_cfg.format_report())
    except ImportError:
        print("Error: Rust core not available. Install with 'uv sync' and rebuild.")
        sys.exit(1)


def print_usage() -> None:
    """Print CLI usage information."""
    print(
        """\
Usage: forger <COMMAND>

Forger — Python application compiler and bundler

Commands:
  compile        Analyze and compile a Python project into a VFS directory
  forge          Package the VFS directory into a .forge artifact
  build          Build a platform-specific executable from a .forge artifact
  info           Show information about a .forge artifact
  build-config   Build-config subcommands
    check        Check if required build tools are present on the machine
  cpython        CPython module analysis and minimal build configuration
    analyze      Analyze which CPython modules are required
    build-config Generate a minimal CPython build configuration

Options:
  -h, --help     Print help
  -V, --version  Print version
  -v, --verbose  Enable verbose output"""
    )


if __name__ == "__main__":
    run()


# Alias for test compatibility — tests import `main` but the entry point is `run`
main = run
