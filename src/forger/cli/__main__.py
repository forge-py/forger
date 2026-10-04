"""CLI entry point for Forger.

Workflow:
    forger compile  ->  dist/          (VFS directory)
    forger forge    ->  app.forge      (package dist/ into .forge artifact)
    forger build    ->  native binary  (consume .forge, produce executable)

The CLI is a pure Python entry point that calls into the forger_core
Rust library via PyO3 for performance-critical operations. No separate
Rust binary is produced — all argument parsing and dispatch happens
in Python, with heavy lifting delegated to the Rust core.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config pre-read helpers
# ---------------------------------------------------------------------------


def _entry_point_from_config(config_path: Path) -> str | None:
    """Read the entry point from a forger config file without full execution.

    Parses the config file via AST to extract the ``entry`` keyword argument
    from ``defineConfig()`` calls, avoiding a full import that may fail
    when dependencies are not yet available.
    """
    try:
        import ast

        tree = ast.parse(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match defineConfig(...) calls
        func = node.func
        func_name = None
        if isinstance(func, ast.Name) and func.id == "defineConfig":
            func_name = "defineConfig"
        elif isinstance(func, ast.Attribute) and func.attr == "defineConfig":
            func_name = "defineConfig"
        if func_name is None:
            continue
        # Look for entry= keyword argument
        for kw in node.keywords:
            if kw.arg == "entry" and isinstance(kw.value, ast.Constant):
                val = kw.value.value
                # Normalize: strip trailing .py so the entry point is a
                # module name consistent with the rest of the codebase.
                if isinstance(val, str) and val.endswith(".py"):
                    val = val[:-3]
                return val
    return None


# ---------------------------------------------------------------------------
# Command implementations (called by the Rust CLI via the entry point)
# ---------------------------------------------------------------------------


def _find_forger_config(project_root: Path, explicit: str | None) -> Path | None:
    """Locate the project's forger.py / forger.config.py."""
    if explicit:
        return Path(explicit).resolve()
    for name in ("forger.py", "forger.config.py"):
        candidate = project_root / name
        if candidate.exists():
            return candidate
    return None


def _resolve_output_dir(
    project_root: Path, output: str, compiler: object
) -> Path:
    """Resolve dist dir, honoring a ``dist_dir`` override from config."""
    out = Path(output)
    output_dir = out.resolve() if out.is_absolute() else project_root / out
    import forger.api as api_module

    ctx = api_module.get_context()
    cfg = ctx.config
    if cfg and cfg.dist_dir:
        cfg_output = project_root / cfg.dist_dir
        if cfg_output != output_dir:
            logger.info("Using dist_dir from config: %s", cfg_output)
            if hasattr(compiler, "output_path"):
                compiler.output_path = cfg_output
            return cfg_output
    return output_dir


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

    project_root = Path(source).resolve()
    if not project_root.exists() or not project_root.is_dir():
        print(f"Error: source directory does not exist: {project_root}", file=sys.stderr)
        sys.exit(1)

    forger_py_path = _find_forger_config(project_root, forger_py)

    # If a config file exists, try to read the entry point override
    # before we validate the entry file.
    if forger_py_path is not None:
        resolved_entry = _entry_point_from_config(forger_py_path)
        if resolved_entry:
            entry_point = resolved_entry

    print(f"Entry point: {entry_point}")

    entry_file = project_root / (entry_point + ".py")
    if not entry_file.exists():
        # Try without .py extension (package mode)
        entry_pkg = project_root / entry_point
        if not entry_pkg.is_dir():
            print(f"Error: entry point not found: {entry_point}", file=sys.stderr)
            sys.exit(1)

    try:
        from forger.compiler import Compiler

        compiler = Compiler(
            project_root=project_root,
            entry_point=entry_point,
            output_path=(Path(output).resolve()),
        )

        compiler.analyze()

        if forger_py_path is not None:
            compiler.process_forger_py(forger_py_path)

        output_dir = _resolve_output_dir(project_root, output, compiler)
        # No rmtree here: generate_vfs() stages into a .tmp sibling and
        # atomically renames on success (CLAUDE.md invariant 11). Removing
        # the directory here would defeat the atomicity guarantee.

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
    entry_point: str | None = None,
    verbose: bool = False,
) -> None:
    """Package the VFS dist/ directory into a .forge directory artifact."""
    setup_logging(verbose)

    logger.info("Forger forge: %s -> %s", vfs_dir, output)

    vfs_path = Path(vfs_dir).resolve()
    artifact_path = Path(output).resolve()

    try:
        from forger.compiler import Compiler

        compiler = Compiler.forge_from_vfs(vfs_path, artifact_path, entry_point=entry_point or "")
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
    """Build a platform-specific bundle from a .forge directory artifact."""
    setup_logging(verbose)

    artifact_path = Path(artifact).resolve()
    if not artifact_path.is_dir():
        print(
            f"Error: artifact is not a .forge directory: {artifact_path}",
            file=sys.stderr,
        )
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
# Argparse setup
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser with all subcommands."""
    from forger import __version__

    parser = argparse.ArgumentParser(
        prog="forger",
        description="Forger — Python application compiler and bundler",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"forger {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- compile ---
    compile_parser = subparsers.add_parser(
        "compile",
        help="Analyze and compile a Python project into a VFS directory",
    )
    compile_parser.add_argument(
        "source", nargs="?", default=".",
        help="Source directory (default: .)",
    )
    compile_parser.add_argument(
        "-o", "--output", default="dist",
        help="Output path for VFS artifact (default: dist)",
    )
    compile_parser.add_argument(
        "-e", "--entry-point", default="main",
        help="Entry point module (default: main)",
    )
    compile_parser.add_argument(
        "--venv", default=None,
        help="Virtual environment path",
    )
    compile_parser.add_argument(
        "--forger-py", default=None,
        help="Path to forger.py config",
    )
    compile_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output",
    )

    # --- forge ---
    forge_parser = subparsers.add_parser(
        "forge",
        help="Package the VFS directory into a .forge artifact",
    )
    forge_parser.add_argument(
        "vfs_dir", nargs="?", default="dist",
        help="VFS directory to package (default: dist)",
    )
    forge_parser.add_argument(
        "-o", "--output", default="app.forge",
        help="Output .forge artifact path (default: app.forge)",
    )
    forge_parser.add_argument(
        "-e", "--entry-point", default=None,
        help="Entry point module to record in the artifact manifest",
    )
    forge_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output",
    )

    # --- build ---
    build_parser = subparsers.add_parser(
        "build",
        help="Build a platform-specific executable from a .forge artifact",
    )
    build_parser.add_argument(
        "artifact",
        help="Path to .forge artifact",
    )
    build_parser.add_argument(
        "-t", "--target", default="windows-x64",
        help="Target platform (default: windows-x64)",
    )
    build_parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory",
    )
    build_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output",
    )

    # --- info ---
    info_parser = subparsers.add_parser(
        "info",
        help="Show information about a .forge artifact",
    )
    info_parser.add_argument(
        "artifact",
        help="Path to .forge artifact",
    )

    # --- build-config check ---
    build_config_parser = subparsers.add_parser(
        "build-config",
        help="Build configuration subcommands",
    )
    build_config_sub = build_config_parser.add_subparsers(dest="build_config_command")
    check_parser = build_config_sub.add_parser(
        "check", help="Check if required build tools are present"
    )
    check_parser.add_argument(
        "-t", "--target", default="windows-x64",
        help="Target platform (default: windows-x64)",
    )

    # --- runtime ---
    runtime_parser = subparsers.add_parser(
        "runtime",
        help="Build and inspect stripped CPython runtimes for targets",
    )
    runtime_sub = runtime_parser.add_subparsers(dest="runtime_command")

    runtime_build = runtime_sub.add_parser(
        "build",
        help="Fetch, configure, compile and strip a CPython runtime",
    )
    runtime_build.add_argument(
        "-t", "--target", default="windows-x64",
        help="Target platform triple (default: windows-x64)",
    )
    runtime_build.add_argument(
        "--version", default="3.12",
        help="CPython version (default: 3.12)",
    )
    runtime_build.add_argument(
        "--modules", nargs="*", default=[],
        help="Required stdlib modules for conservative pruning (default: full Lib)",
    )
    runtime_build.add_argument(
        "--force", action="store_true",
        help="Rebuild even if a cached runtime exists",
    )
    runtime_build.add_argument(
        "--dry-run", action="store_true",
        help="Validate toolchain and report without building",
    )
    runtime_build.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output",
    )

    runtime_info = runtime_sub.add_parser(
        "info",
        help="Show runtime build status for a target",
    )
    runtime_info.add_argument(
        "-t", "--target", default="windows-x64",
        help="Target platform triple (default: windows-x64)",
    )
    runtime_info.add_argument(
        "--version", default="3.12",
        help="CPython version (default: 3.12)",
    )

    # --- cpython analyze ---
    cpython_parser = subparsers.add_parser(
        "cpython",
        help="CPython module analysis and minimal build configuration",
    )
    cpython_sub = cpython_parser.add_subparsers(dest="cpython_command")

    cpython_analyze = cpython_sub.add_parser(
        "analyze",
        help="Analyze which CPython modules are required",
    )
    cpython_analyze.add_argument(
        "modules", nargs="+",
        help="Module names to analyze",
    )
    cpython_analyze.add_argument(
        "--version", default="3.12",
        help="CPython version (default: 3.12)",
    )

    cpython_build_config = cpython_sub.add_parser(
        "build-config",
        help="Generate a minimal CPython build configuration",
    )
    cpython_build_config.add_argument(
        "modules", nargs="+",
        help="Module names to analyze",
    )
    cpython_build_config.add_argument(
        "-t", "--target", default="windows-x64",
        help="Target platform (default: windows-x64)",
    )
    cpython_build_config.add_argument(
        "--version", default="3.12",
        help="CPython version (default: 3.12)",
    )

    return parser


# ---------------------------------------------------------------------------
# Command callbacks
# ---------------------------------------------------------------------------


def _on_compile(args: argparse.Namespace) -> None:
    run_compile(
        source=args.source,
        output=args.output,
        entry_point=args.entry_point,
        venv=args.venv,
        forger_py=args.forger_py,
        verbose=args.verbose,
    )


def _on_forge(args: argparse.Namespace) -> None:
    run_forge(
        vfs_dir=args.vfs_dir,
        output=args.output,
        entry_point=args.entry_point,
        verbose=args.verbose,
    )


def _on_build(args: argparse.Namespace) -> None:
    run_build(
        artifact=args.artifact,
        target=args.target,
        output=args.output,
        verbose=args.verbose,
    )


def _on_info(args: argparse.Namespace) -> None:
    run_info(artifact=args.artifact)


def _on_build_config(args: argparse.Namespace) -> None:
    if not hasattr(args, "build_config_command") or args.build_config_command != "check":
        print("Error: unknown build-config subcommand. Use 'check'.", file=sys.stderr)
        sys.exit(1)

    target = getattr(args, "target", "windows-x64")
    try:
        from forger_core import (
            check_build_config,  # type: ignore[import-not-found, import-untyped, missing-import]
        )

        result = check_build_config(target)
        print(result.format_report())
        if result.has_issues():
            sys.exit(1)
    except ImportError:
        _build_config_check_python(target)


def _build_config_check_python(target: str) -> None:
    """Fallback build-config check when Rust core is not available."""
    import subprocess

    os_name = target.split("-")[0]
    tools: list[tuple[str, bool]] = []

    if os_name == "windows":
        tools.append(("MSVC Compiler (cl.exe)", shutil.which("cl.exe") is not None))
        tools.append(("MSVC Librarian (lib.exe)", shutil.which("lib.exe") is not None))
        tools.append(("MSVC Linker (link.exe)", shutil.which("link.exe") is not None))
    elif os_name == "linux":
        tools.append(("GCC", shutil.which("gcc") is not None))
        tools.append(("G++", shutil.which("g++") is not None))
        tools.append(("Make", shutil.which("make") is not None))
    elif os_name == "macos":
        try:
            result = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
            tools.append(("Xcode Command Line Tools", result.returncode == 0))
        except FileNotFoundError:
            tools.append(("Xcode Command Line Tools", False))
        tools.append(("Clang", shutil.which("clang") is not None))

    tools.append(("Python", sys.executable is not None and Path(sys.executable).exists()))

    passed = all(ok for _, ok in tools)

    print(f"Build Configuration Check: {target}")
    print("-" * 50)
    for name, ok in tools:
        status = "OK" if ok else "MISSING"
        state = "present" if ok else "missing"
        print(f"  [{status}] {name}: {state}")
    print("-" * 50)
    print(
        "Result: All required tools are available"
        if passed
        else "Result: Some required tools are missing or misconfigured"
    )

    if not passed:
        sys.exit(1)


def _on_runtime(args: argparse.Namespace) -> None:
    command = getattr(args, "runtime_command", None)
    if command not in ("build", "info"):
        print("Error: unknown runtime subcommand. Use 'build' or 'info'.", file=sys.stderr)
        sys.exit(1)

    from forger.build.runtime_builder import CpythonRuntimeBuilder, RuntimeSpec

    spec = RuntimeSpec(
        target_triple=args.target,
        python_version=args.version,
        required_modules=list(getattr(args, "modules", []) or []),
    )
    builder = CpythonRuntimeBuilder(spec)

    if command == "info":
        info = builder.info()
        print(f"Target:      {info['target']}")
        print(f"Version:     {info['version']}")
        print(f"Recipe:      {info['recipe']}")
        print(f"Cross build: {info['cross']}")
        print(f"Cached:      {info['cached']}")
        print(f"Runtime dir: {info['runtime_dir']}")
        problems = info["problems"]
        if isinstance(problems, list) and problems:
            print("Problems:")
            for p in problems:
                if isinstance(p, str):
                    print(f"  - {p}")
            sys.exit(1)
        return

    try:
        result = builder.ensure_runtime(
            force_rebuild=args.force, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if args.dry_run:
        print(f"Dry run OK: {args.target} / CPython {args.version}")
        print(f"Runtime would be placed at: {result.runtime_dir}")
        return

    print(f"Runtime ready: {result.runtime_dir}")
    if result.stripped_count:
        print(f"Stripped binaries: {result.stripped_count}")
    if result.stdlib_module_count:
        print(f"Stdlib modules: {result.stdlib_module_count}")


def _on_cpython(args: argparse.Namespace) -> None:
    valid_commands = ("analyze", "build-config")
    cmd = getattr(args, "cpython_command", None)
    if cmd not in valid_commands:
        print(
            "Error: unknown cpython subcommand. Use 'analyze' or 'build-config'.",
            file=sys.stderr,
        )
        sys.exit(1)

    modules = list(args.modules)
    version = getattr(args, "version", "3.12")

    try:
        from forger_core import (  # type: ignore[import-not-found, import-untyped, missing-import]
            CpythonBuildConfig,
            CpythonModuleRegistry,
        )

        ver_parts = version.split(".")
        major = int(ver_parts[0]) if len(ver_parts) > 0 else 3
        minor = int(ver_parts[1]) if len(ver_parts) > 1 else 12
        registry = CpythonModuleRegistry((major, minor))
        analysis = registry.analyze_required_sources(modules)

        if args.cpython_command == "analyze":
            print(analysis.format_report())
        elif args.cpython_command == "build-config":
            target = getattr(args, "target", "windows-x64")
            build_cfg = CpythonBuildConfig.from_analysis(analysis, target, version)
            print(build_cfg.format_report())
    except ImportError:
        print("Error: Rust core not available. Install with 'uv sync' and rebuild.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point — Python CLI that delegates heavy lifting to forger_core (Rust).
# ---------------------------------------------------------------------------


def run() -> None:
    """Entry point for the forger command.

    All argument parsing and dispatch happens in Python via argparse.
    Heavy-lifting operations (hashing, graph construction, VFS generation)
    are delegated to the forger_core Rust library via PyO3.
    """
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Setup logging early
    verbose = getattr(args, "verbose", False)
    setup_logging(verbose)

    dispatch = {
        "compile": _on_compile,
        "forge": _on_forge,
        "build": _on_build,
        "info": _on_info,
        "build-config": _on_build_config,
        "cpython": _on_cpython,
        "runtime": _on_runtime,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    run()


# Alias for test compatibility — tests import `main` but the entry point is `run`
main = run
