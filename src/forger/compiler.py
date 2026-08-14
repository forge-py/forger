"""Compiler module — orchestrates the analysis and artifact generation pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forger.core import DependencyGraph  # type: ignore[attr-defined]
    from forger.optimizer import OptimizerContext  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


class Compiler:
    """Orchestrates the compilation pipeline.

    Pipeline:
    1. File discovery (Rust core)
    2. Static analysis (Python AST)
    3. forger.py processing
    4. Framework optimizer execution
    5. Dependency graph resolution
    6. Artifact generation
    """

    def __init__(
        self,
        project_root: Path,
        entry_point: str,
        output_path: Path,
    ) -> None:
        self.project_root = project_root.resolve()
        self.entry_point = entry_point
        self.output_path = output_path.resolve()
        self._vfs_path: Path = output_path.resolve()
        self.graph: DependencyGraph | None = None  # type: ignore[name-defined]
        self.source_files: list[Path] = []
        self._optimizer_context: OptimizerContext | None = None

    @classmethod
    def forge_from_vfs(
        cls,
        vfs_path: Path,
        artifact_path: Path,
    ) -> Compiler:
        """Create a Compiler to package an existing VFS directory into .forge.

        Args:
            vfs_path: Path to the VFS directory (output of ``forger compile``).
            artifact_path: Desired path for the .forge artifact.

        Returns:
            A ``Compiler`` instance configured to produce the artifact.
        """
        compiler = cls(
            project_root=vfs_path,
            entry_point="",
            output_path=artifact_path,
        )
        compiler._vfs_path = vfs_path
        return compiler

    def analyze(self) -> None:
        """Run static analysis on the project."""
        from forger.core import DependencyGraph, DependencyNode, NodeType

        logger.info("Starting analysis of %s", self.project_root)

        # Create dependency graph
        self.graph = DependencyGraph()
        self.graph.add_entry_point(self.entry_point)
        self.graph.add_node(DependencyNode.new(self.entry_point, NodeType.EntryPoint))

        # Discover Python source files
        self.source_files = self._discover_source_files()
        logger.info("Discovered %d Python source files", len(self.source_files))

        # Analyze imports
        self._analyze_imports()

        # Analyze dynamic imports
        self._analyze_dynamic_imports()

        # Analyze resources
        self._analyze_resources()

    def _analyze_imports(self) -> None:
        """Analyze static imports across all source files."""
        from forger.analyzer import ImportAnalyzer
        from forger.core import DependencyNode, NodeType

        assert self.graph is not None
        import_analyzer = ImportAnalyzer()
        for source_file in self.source_files:
            module_name = self._path_to_module(source_file)
            if not module_name:
                continue

            if not self.graph.get_node(module_name):
                self.graph.add_node(DependencyNode.new(module_name, NodeType.PythonModule))

            imports = import_analyzer.analyze_file(source_file)
            import_analyzer.contribute_to_graph(self.graph, module_name)

            if imports:
                logger.debug("Found %d imports in %s", len(imports), source_file)

    def _analyze_dynamic_imports(self) -> None:
        """Analyze dynamic imports across all source files."""
        from forger.analyzer import DynamicImportAnalyzer

        dynamic_analyzer = DynamicImportAnalyzer()
        for source_file in self.source_files:
            hints = dynamic_analyzer.analyze_file(source_file)
            for hint in hints:
                if hint.pattern != "*":
                    logger.info(
                        "Dynamic import hint: %s (from %s:%d)",
                        hint.pattern,
                        hint.source_file,
                        hint.line,
                    )

    def _analyze_resources(self) -> None:
        """Analyze resource accesses across all source files."""
        from forger.analyzer import ResourceAnalyzer

        resource_analyzer = ResourceAnalyzer()
        for source_file in self.source_files:
            accesses = resource_analyzer.analyze_file(source_file)
            for access in accesses:
                if not access.is_dynamic:
                    logger.debug(
                        "Resource access: %s (from %s:%d)",
                        access.path_expr,
                        access.source_file,
                        access.line,
                    )

    def process_forger_py(self, forger_py_path: Path) -> None:
        """Process the project's forger.py extension file."""
        import runpy

        logger.info("Processing forger.py: %s", forger_py_path)

        # Clear any previous forger context
        import forger.api as api_module

        api_module._context = None  # noqa: SLF001

        # Execute forger.py in isolated context
        try:
            runpy.run_path(
                str(forger_py_path),
                init_globals={
                    "__file__": str(forger_py_path),
                    "__name__": "__forger__",
                },
                run_name="__forger__",
            )
        except Exception as e:
            logger.error("Error executing forger.py: %s", e, exc_info=True)
            raise

        # Get the config from the forger context
        context = api_module.get_context()
        cfg = context.config
        if cfg:
            logger.info(
                "forger.py contributed: %d includes, %d excludes, %d targets",
                len(cfg.include),
                len(cfg.exclude),
                len(cfg.targets),
            )

            # Add included glob patterns as resource nodes to the graph
            from forger.core import DependencyNode, NodeType

            for pattern in cfg.include:
                if self.graph:
                    self.graph.add_node(
                        DependencyNode.new(pattern, NodeType.Resource).with_metadata(
                            "source", "forger.py"
                        )
                    )
        else:
            logger.info("forger.py contributed no config")

    def run_optimizers(self) -> None:
        """Run framework optimizers."""
        if not self.graph:
            return

        from forger.optimizer import OptimizerContext, run_optimizers

        # Build optimizer context
        context = OptimizerContext(
            project_root=self.project_root,
            source_files=self.source_files,
            installed_packages=self._discover_installed_packages(),
        )
        self._optimizer_context = context

        active = run_optimizers(context, self.graph)
        if active:
            logger.info("Active optimizers: %s", ", ".join(active))
        else:
            logger.info("No framework optimizers activated")

    def generate_vfs(self) -> None:
        """Generate the VFS directory from analysis results.

        Copies all reachable Python modules and resources into the output
        directory, preserving the module structure as the VFS layout.
        Also copies required third-party packages from .venv into dist/.venv.
        """
        if not self.graph:
            raise RuntimeError("Analysis not yet performed")

        logger.info("Generating VFS: %s", self.output_path)
        logger.info(
            "Graph: %d nodes, %d edges",
            self.graph.node_count(),
            self.graph.edge_count(),
        )

        # Mark reachable nodes
        self.graph.mark_reachable_required()

        # Clean and create output directory
        import shutil

        if self.output_path.exists():
            shutil.rmtree(self.output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)

        # Copy reachable Python source files
        copied = 0
        # Check if any source file nodes are marked required
        any_required = any(
            self.graph.get_node(self._path_to_module(sf)) is not None
            and self.graph.get_node(self._path_to_module(sf)).required
            for sf in self.source_files
            if self._path_to_module(sf) is not None
        )

        for source_file in self.source_files:
            module_name = self._path_to_module(source_file)
            if module_name is None:
                continue

            # If no nodes were marked reachable (e.g. entry point not found),
            # copy everything conservatively
            if not any_required:
                required = True
            else:
                node = self.graph.get_node(module_name)
                required = node is not None and node.required

            if not required:
                continue

            # Compute relative path from project root
            try:
                rel_path = source_file.relative_to(self.project_root)
            except ValueError:
                continue

            dest = self.output_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source_file.read_bytes())
            copied += 1

        # Copy resources from include patterns (templates, locale, static, etc.)
        resource_count = self._copy_include_resources()

        # Determine required third-party packages from import analysis
        required_packages = self._resolve_required_packages()
        if required_packages:
            logger.info(
                "Required third-party packages: %s",
                ", ".join(sorted(required_packages)),
            )
            venv_copied = self._copy_venv_packages(required_packages)
        else:
            venv_copied = 0

        logger.info(
            "VFS generated: %d modules, %d resources, %d venv packages",
            copied,
            resource_count,
            venv_copied,
        )

    def generate_artifact(self) -> None:
        """Generate the .forge artifact from VFS directory.

        Packages the entire VFS directory into a single .forge file.
        """
        import json
        import tarfile

        vfs = self._vfs_path
        artifact = self.output_path

        logger.info("Generating artifact: %s from VFS: %s", artifact, vfs)

        if not vfs.exists():
            raise RuntimeError(f"VFS directory does not exist: {vfs}")

        # Create .forge as a tar.gz with manifest
        with tarfile.open(artifact, "w:gz") as tar:
            # Add all files from VFS
            files_list: list[str] = []
            for file_path in vfs.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to(vfs)
                    tar.add(file_path, arcname=str(rel))
                    files_list.append(str(rel))

            # Write manifest
            manifest = {
                "version": "0.1.0",
                "entry_point": self.entry_point,
                "project_root": str(self.project_root),
                "files": files_list,
            }
            import io

            manifest_data = json.dumps(manifest, indent=2).encode()
            tarinfo = tarfile.TarInfo(name="MANIFEST.json")
            tarinfo.size = len(manifest_data)
            tar.addfile(tarinfo, io.BytesIO(manifest_data))

        logger.info("Artifact generated: %s", artifact)

    def _copy_include_resources(self) -> int:
        """Copy resources matching include patterns from forger.py config."""
        import forger.api as api_module
        import fnmatch
        import shutil

        context = api_module.get_context()
        cfg = context.config
        if not cfg or not cfg.include:
            return 0

        copied = 0
        for pattern in cfg.include:
            for file_path in self.project_root.rglob("*"):
                if file_path.is_file() and fnmatch.fnmatch(str(file_path), pattern):
                    try:
                        rel_path = file_path.relative_to(self.project_root)
                    except ValueError:
                        continue

                    dest = self.output_path / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copy2(file_path, dest)
                        copied += 1

        return copied

    def diagnostic_summary(self) -> str:
        """Generate a diagnostic summary."""
        if not self.graph:
            return "No analysis performed yet."

        lines = [
            "Compilation Summary:",
            f"  Project: {self.project_root}",
            f"  Entry Point: {self.entry_point}",
            f"  Source Files: {len(self.source_files)}",
            f"  Graph Nodes: {self.graph.node_count()}",
            f"  Graph Edges: {self.graph.edge_count()}",
        ]

        # Count by type
        from forger.core import NodeType

        for node_type in [
            NodeType.PythonModule,
            NodeType.Resource,
            NodeType.ExternalPackage,
        ]:
            count = len(self.graph.nodes_by_type(node_type))
            if count:
                lines.append(f"  {node_type}: {count}")

        return "\n".join(lines)

    def _discover_source_files(self) -> list[Path]:
        """Discover Python source files in the project."""
        sources: list[Path] = []

        for py_file in self.project_root.rglob("*.py"):
            # Skip test files, virtual environments, and cache directories
            parts = py_file.parts
            if any(
                skip in parts
                for skip in [
                    "__pycache__",
                    ".venv",
                    "venv",
                    ".git",
                    "node_modules",
                    "target",
                    "dist",
                ]
            ):
                continue
            sources.append(py_file)

        return sources

    def _path_to_module(self, filepath: Path) -> str | None:
        """Convert a file path to a Python module name."""
        try:
            relative = filepath.resolve().relative_to(self.project_root)
        except ValueError:
            return None

        parts = list(relative.parts)

        # Strip .py extension from the last part
        if parts:
            last = parts[-1]
            if last.endswith(".py"):
                parts[-1] = last[:-3]
            elif last.endswith(".pyi"):
                parts[-1] = last[:-4]

        # Skip __init__
        result_parts = [p for p in parts if p != "__init__"]

        if not result_parts:
            return None

        return ".".join(result_parts)

    def _discover_installed_packages(self) -> dict[str, str]:
        """Discover installed packages in the project environment."""
        packages: dict[str, str] = {}

        try:
            import importlib.metadata

            for dist in importlib.metadata.distributions():
                try:
                    name = dist.metadata["Name"]
                    version = dist.metadata["Version"] or "unknown"
                    packages[name] = version
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Failed to discover installed packages: %s", e)

        return packages

    def _resolve_required_packages(self) -> set[str]:
        """Resolve which third-party packages are required by the project.

        Analyzes all import nodes in the graph, then maps imported module names
        to package names by scanning the project's .venv site-packages directly.
        """
        import importlib.metadata
        import importlib.util

        required: set[str] = set()

        # Build a set of all imported module top-level names from the graph
        imported_modules: set[str] = set()
        for node in self.graph.all_nodes().values():
            module_name = node.id.split(":")[0] if ":" in node.id else node.id
            if not module_name:
                continue
            top_level = module_name.split(".")[0]
            imported_modules.add(top_level)

        # Get stdlib module names to filter out
        stdlib_modules = self._get_stdlib_modules()

        # Find the .venv site-packages to resolve module -> package mapping
        venv_source = self.project_root / ".venv"
        site_packages = self._find_site_packages(venv_source)

        if site_packages:
            # Scan .dist-info directories for top_level.txt to map modules to packages
            for dist_info in site_packages.glob("*.dist-info"):
                top_level_file = dist_info / "top_level.txt"
                if top_level_file.exists():
                    try:
                        pkg_modules = [
                            line.strip()
                            for line in top_level_file.read_text().splitlines()
                            if line.strip()
                        ]
                        # If any of this package's modules are imported
                        if any(m in imported_modules for m in pkg_modules):
                            # Get package name from METADATA
                            meta_file = dist_info / "METADATA"
                            if meta_file.exists():
                                for line in meta_file.read_text().splitlines():
                                    if line.startswith("Name:"):
                                        pkg_name = line.split(":", 1)[1].strip()
                                        required.add(pkg_name)
                                        break
                    except Exception:
                        pass
        else:
            # Fallback: use importlib.metadata from current environment
            for top_level in imported_modules:
                if top_level in stdlib_modules:
                    continue
                try:
                    for dist in importlib.metadata.distributions():
                        if dist.metadata.get("Name") is None:
                            continue
                        try:
                            top_level_txt = dist.read_text("top_level.txt")
                            if top_level_txt and top_level in top_level_txt.splitlines():
                                required.add(dist.metadata["Name"])
                        except Exception:
                            pass
                except Exception:
                    pass

        return required

    def _get_stdlib_modules(self) -> set[str]:
        """Get a set of standard library top-level module names."""
        import sys

        stdlib: set[str] = set()
        stdlib_path = getattr(sys, "stdlib_dir", None)
        if stdlib_path:
            try:
                for entry in Path(stdlib_path).iterdir():
                    if entry.is_dir():
                        stdlib.add(entry.name)
                    elif entry.suffix == ".py":
                        stdlib.add(entry.stem)
            except Exception:
                pass
        # Always include common stdlib modules
        stdlib.update({
            "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
            "asyncore", "atexit", "base64", "bdb", "binascii", "binhex",
            "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk",
            "cmath", "cmd", "code", "codecs", "codeop", "collections",
            "colorsys", "compileall", "concurrent", "configparser", "contextlib",
            "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
            "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
            "difflib", "dis", "distutils", "doctest", "email", "encodings",
            "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
            "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
            "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
            "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
            "importlib", "inspect", "io", "ipaddress", "itertools", "json",
            "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
            "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
            "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
            "numbers", "operator", "optparse", "os", "ossaudiodev", "parser",
            "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
            "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
            "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
            "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
            "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
            "selectors", "shelve", "shlex", "shutil", "signal", "site",
            "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
            "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
            "struct", "subprocess", "sunau", "symtable", "sys", "sysconfig",
            "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios",
            "test", "textwrap", "threading", "timeit", "tkinter", "token",
            "tokenize", "tomllib", "trace", "traceback", "tracemalloc", "tty",
            "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest",
            "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
            "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml",
            "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib",
            "_thread", "_io", "_collections_abc", "_frozen_importlib",
        })
        return stdlib

    def _find_site_packages(self, venv_path: Path) -> Path | None:
        """Find the site-packages directory inside a virtual environment."""
        if not venv_path.exists():
            return None
        for sp_name in ("lib", "Lib"):
            sp_candidate = venv_path / sp_name / "site-packages"
            if sp_candidate.exists():
                return sp_candidate
        return None

    def _copy_venv_packages(self, packages: set[str]) -> int:
        """Copy required packages from .venv into dist/.venv.

        Analyzes which .py files are actually needed and copies only those,
        preserving directory structure. Skips __pycache__, .pyc, .dist-info,
        tests, docs, and other non-runtime files.
        """
        venv_source = self.project_root / ".venv"
        if not venv_source.exists():
            logger.warning("No .venv found at %s, skipping package copy", venv_source)
            return 0

        venv_dest = self.output_path / ".venv"
        venv_dest.mkdir(exist_ok=True)

        # Find the site-packages directory in the source venv
        site_packages = None
        for sp_name in ("lib", "Lib"):
            sp_candidate = venv_source / sp_name / "site-packages"
            if sp_candidate.exists():
                site_packages = sp_candidate
                break

        if site_packages is None:
            logger.warning("Could not find site-packages in .venv")
            return 0

        copied = 0
        # Copy each required package - only .py and native extension files
        for pkg_name in packages:
            pkg_dirs_found: list[Path] = []

            # Exact match
            if (site_packages / pkg_name).is_dir():
                pkg_dirs_found.append(site_packages / pkg_name)

            # Normalized name (PEP 503)
            normalized = pkg_name.replace("-", "_").lower()
            if (site_packages / normalized).is_dir():
                pkg_dirs_found.append(site_packages / normalized)

            # Check for .dist-info sibling
            dist_info = site_packages / f"{normalized}.dist-info"
            if dist_info.exists() and not pkg_dirs_found:
                try:
                    top_level_path = dist_info.parent / "top_level.txt"
                    if top_level_path.exists():
                        for line in top_level_path.read_text().splitlines():
                            line = line.strip()
                            if line and (site_packages / line).is_dir():
                                pkg_dirs_found.append(site_packages / line)
                except Exception:
                    pass

            for pkg_dir in pkg_dirs_found:
                dest_pkg = venv_dest / pkg_dir.name
                copied += self._copy_package_files(pkg_dir, dest_pkg)
                logger.debug("Copied package: %s -> dist/.venv/%s", pkg_name, pkg_dir.name)

        return copied

    def _copy_package_files(self, src_pkg: Path, dst_pkg: Path) -> int:
        """Copy only needed files from a package directory, preserving structure.

        Copies .py files and native extensions (.so, .pyd, .dylib).
        Skips __pycache__, .pyc, .dist-info, tests, docs, and metadata.
        """
        copied = 0
        skip_dirs = {"__pycache__", "tests", "test", "docs", "doc", "examples"}

        for file_path in src_pkg.rglob("*"):
            if not file_path.is_file():
                continue

            parts = file_path.relative_to(src_pkg).parts
            # Skip unwanted directories
            if any(p in skip_dirs for p in parts[:-1]):
                continue

            rel_path = file_path.relative_to(src_pkg)
            dest = dst_pkg / rel_path

            # Only copy .py files and native extensions
            suffix = file_path.suffix
            if suffix == ".py":
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(file_path.read_bytes())
                copied += 1
            elif suffix in (".so", ".pyd", ".dylib"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(file_path.read_bytes())
                copied += 1

        return copied
