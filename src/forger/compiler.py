"""Compiler module — orchestrates the analysis and artifact generation pipeline."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forger.core import DependencyGraph  # type: ignore[attr-defined]
    from forger.optimizer import OptimizerContext  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parallel venv exploration
# ---------------------------------------------------------------------------


@dataclass
class VenvFile:
    """A file discovered during venv exploration."""

    path: Path
    size: int
    is_python: bool = False
    is_native: bool = False


@dataclass
class VenvDirResult:
    """Result of exploring a single directory."""

    dir_path: Path
    files: list[VenvFile] = field(default_factory=list)
    subdirs: list[Path] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)


class VenvWorkerGraph:
    """Parallel worker graph for exploring .venv site-packages.

    Each directory is added as a task. A worker picks up a task, scans
    the immediate contents (files + subdirectories), emits results, and
    feeds discovered subdirectories back into the queue. Workers scale
    across all CPU cores.
    """

    def __init__(self, site_packages: Path) -> None:
        self.site_packages = site_packages
        self.task_queue: Queue[Path] = Queue()
        self.results: list[VenvDirResult] = []
        self._lock = Lock()
        self._copied_dirs: set[str] = set()

    def add_root(self, directory: Path) -> None:
        """Add a directory to the exploration graph."""
        self.task_queue.put(directory)

    def add_roots(self, directories: list[Path]) -> None:
        """Add multiple directories to the exploration graph."""
        for d in directories:
            self.task_queue.put(d)

    @staticmethod
    def _explore_directory(dir_path: Path) -> VenvDirResult:
        """Worker function: explore a single directory.

        Scans immediate files and subdirectories only. Does NOT recurse.
        Returns files, subdirs, and any imports discovered in .py files.
        """
        result = VenvDirResult(dir_path=dir_path)
        skip_dirs = {"__pycache__", "tests", "test", "docs", "doc", "examples"}

        try:
            for entry in dir_path.iterdir():
                if entry.is_file():
                    vf = VenvFile(path=entry, size=entry.stat().st_size)
                    if entry.suffix == ".py":
                        vf.is_python = True
                    elif entry.suffix in (".so", ".pyd", ".dylib"):
                        vf.is_native = True
                    result.files.append(vf)
                elif entry.is_dir():
                    if entry.name not in skip_dirs:
                        result.subdirs.append(entry)
        except OSError:
            pass

        return result

    def run(self) -> list[VenvDirResult]:
        """Run the worker graph across all CPU cores."""
        import os

        max_workers = os.cpu_count() or 4
        all_results: list[VenvDirResult] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: dict = {}

            while True:
                # Submit all queued tasks
                while not self.task_queue.empty():
                    dir_task = self.task_queue.get()
                    fut = executor.submit(self._explore_directory, dir_task)
                    futures[fut] = dir_task

                if not futures:
                    break  # no more work

                # Wait for at least one to complete
                for fut in as_completed(futures):
                    dir_task = futures.pop(fut)
                    try:
                        result = fut.result()
                        with self._lock:
                            all_results.append(result)
                            # Feed subdirectories back into the queue
                            for subdir in result.subdirs:
                                self.task_queue.put(subdir)
                    except Exception:
                        pass

        return all_results


class VenvPackageResolver:
    """Resolve and copy required .venv packages using parallel exploration."""

    def __init__(
        self,
        project_root: Path,
        output_path: Path,
        imported_modules: set[str],
    ) -> None:
        self.project_root = project_root
        self.output_path = output_path
        self.imported_modules = imported_modules

    def resolve(self) -> int:
        """Resolve, explore, and copy required venv packages.

        Returns the number of files copied.
        """
        venv_source = self.project_root / ".venv"
        if not venv_source.exists():
            logger.warning("No .venv found at %s", venv_source)
            return 0

        site_packages = self._find_site_packages(venv_source)
        if site_packages is None:
            logger.warning("Could not find site-packages in .venv")
            return 0

        # Step 1: map imported modules -> package directories
        pkg_dirs = self._map_modules_to_dirs(site_packages)
        if not pkg_dirs:
            logger.info("No third-party packages matched to imports")
            return 0

        # Step 2: build a module->dir map for transitive discovery
        module_to_dir = self._build_module_map(site_packages)

        # Step 3: explore each package dir in parallel
        logger.info(
            "Exploring %d package directories across %d cores",
            len(pkg_dirs),
            os.cpu_count() or 1,
        )

        graph = VenvWorkerGraph(site_packages)
        for pkg_dir in pkg_dirs:
            graph.add_root(pkg_dir)

        results = graph.run()

        # Step 4: during exploration, discover transitive imports
        # and add new package directories to explore
        explored = True
        while explored:
            explored = False
            for result in results:
                new_dirs = self._find_transitive_dirs(
                    result, module_to_dir, pkg_dirs
                )
                for nd in new_dirs:
                    if nd not in pkg_dirs:
                        pkg_dirs.append(nd)
                        graph.add_root(nd)
                        explored = True

            if explored:
                results.extend(graph.run())

        # Step 5: copy all discovered files
        venv_dest = self.output_path / ".venv"
        venv_dest.mkdir(exist_ok=True)
        copied = self._copy_all(results, site_packages, venv_dest)

        logger.info("Copied %d files from .venv into dist/.venv", copied)
        return copied

    def _find_site_packages(self, venv_path: Path) -> Path | None:
        if not venv_path.exists():
            return None
        for sp_name in ("lib", "Lib"):
            candidate = venv_path / sp_name / "site-packages"
            if candidate.exists():
                return candidate
        return None

    def _map_modules_to_dirs(
        self, site_packages: Path
    ) -> list[Path]:
        """Map imported module names to package directories in site-packages."""
        pkg_dirs: list[Path] = []
        seen_dirs: set[str] = set()

        for module_name in self.imported_modules:
            # Skip stdlib
            if module_name in self._stdlib_modules():
                continue

            # Look for the module directory
            if (site_packages / module_name).is_dir():
                dir_key = str(site_packages / module_name)
                if dir_key not in seen_dirs:
                    pkg_dirs.append(site_packages / module_name)
                    seen_dirs.add(dir_key)
            else:
                # Try via .dist-info top_level.txt
                resolved = self._resolve_via_dist_info(
                    site_packages, module_name
                )
                for rd in resolved:
                    dir_key = str(rd)
                    if dir_key not in seen_dirs:
                        pkg_dirs.append(rd)
                        seen_dirs.add(dir_key)

        return pkg_dirs

    def _resolve_via_dist_info(
        self, site_packages: Path, module_name: str
    ) -> list[Path]:
        """Find package directories by scanning .dist-info metadata."""
        found: list[Path] = []
        for dist_info in site_packages.glob("*.dist-info"):
            tl_file = dist_info / "top_level.txt"
            if not tl_file.exists():
                continue
            try:
                modules = [
                    l.strip()
                    for l in tl_file.read_text().splitlines()
                    if l.strip()
                ]
            except Exception:
                continue
            if module_name in modules:
                for m in modules:
                    mdir = site_packages / m
                    if mdir.is_dir():
                        found.append(mdir)
        return found

    def _build_module_map(
        self, site_packages: Path
    ) -> dict[str, Path]:
        """Build a mapping of top-level module name -> package directory.

        Reads top_level.txt where available, falling back to RECORD and
        METADATA when top_level.txt is absent (common with uv-installed
        packages that omit it).
        """
        module_map: dict[str, Path] = {}
        for dist_info in site_packages.glob("*.dist-info"):
            tl_file = dist_info / "top_level.txt"
            if tl_file.exists():
                try:
                    for line in tl_file.read_text().splitlines():
                        m = line.strip()
                        if m and (site_packages / m).is_dir():
                            module_map[m] = site_packages / m
                    continue  # top_level.txt present — no fallback needed
                except Exception:
                    pass  # fall through to RECORD fallback

            # Fallback 1: parse RECORD for installed file paths
            record_file = dist_info / "RECORD"
            if record_file.exists():
                try:
                    top_levels: set[str] = set()
                    for line in record_file.read_text().splitlines():
                        path_part = line.split(",")[0]
                        # Skip dist-info entries, scripts, and non-package files
                        if ".dist-info/" in path_part:
                            continue
                        if path_part.startswith("../../"):
                            continue  # scripts/symlinks
                        first_segment = path_part.split("/")[0]
                        if (site_packages / first_segment).is_dir():
                            top_levels.add(first_segment)
                    for m in top_levels:
                        if m not in module_map:
                            module_map[m] = site_packages / m
                    continue
                except Exception:
                    pass  # fall through to METADATA fallback

            # Fallback 2: parse METADATA Name field and assume the module
            # name matches the distribution name (true for most packages)
            meta_file = dist_info / "METADATA"
            if meta_file.exists():
                try:
                    for line in meta_file.read_text().splitlines():
                        if line.lower().startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                            if name and (site_packages / name).is_dir():
                                if name not in module_map:
                                    module_map[name] = site_packages / name
                            break
                except Exception:
                    pass

        return module_map

    def _find_transitive_dirs(
        self,
        result: VenvDirResult,
        module_map: dict[str, Path],
        known_dirs: list[Path],
    ) -> list[Path]:
        """Check explored .py files for imports of other packages."""
        new_dirs: list[Path] = []
        known_set = {str(d) for d in known_dirs}

        for vf in result.files:
            if vf.is_python:
                imported = self._quick_import_scan(vf.path)
                for imp in imported:
                    top = imp.split(".")[0]
                    if top in module_map:
                        dir_path = module_map[top]
                        if str(dir_path) not in known_set:
                            new_dirs.append(dir_path)
                            known_set.add(str(dir_path))
        return new_dirs

    @staticmethod
    def _quick_import_scan(py_file: Path) -> set[str]:
        """Quick scan for import statements in a .py file."""
        imports: set[str] = set()
        try:
            text = py_file.read_text(errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith(
                    "from "
                ):
                    parts = stripped.split()
                    if parts[0] == "import":
                        for mod in parts[1:]:
                            imports.add(mod.split(",")[0].strip())
                    elif parts[0] == "from" and len(parts) > 1:
                        imports.add(parts[1].strip())
        except Exception:
            pass
        return imports

    def _copy_all(
        self,
        results: list[VenvDirResult],
        site_packages: Path,
        venv_dest: Path,
    ) -> int:
        """Copy all discovered Python and native files, preserving structure."""
        copied = 0
        for result in results:
            for vf in result.files:
                if not vf.is_python and not vf.is_native:
                    continue
                try:
                    rel = vf.path.relative_to(site_packages)
                except ValueError:
                    continue
                dest = venv_dest / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.write_bytes(vf.path.read_bytes())
                    copied += 1
        return copied

    @staticmethod
    def _stdlib_modules() -> set[str]:
        """Dynamically discover stdlib module names from the running interpreter."""
        import sys

        stdlib: set[str] = set()
        # Python 3.10+ exposes the canonical list
        if hasattr(sys, "stdlib_module_names"):
            stdlib.update(sys.stdlib_module_names)
        # Also scan stdlib_dir for anything not in the above
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
        return stdlib


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
        ctx = api_module._get_context()

        # Expose the dependency graph to forger.py so plugins can
        # inspect and manipulate it directly
        if self.graph is not None:
            ctx.set_graph(self.graph)

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
        any_required = False
        for sf in self.source_files:
            mod = self._path_to_module(sf)
            if mod is not None:
                node = self.graph.get_node(mod)
                if node is not None and node.required:
                    any_required = True
                    break

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
        imported_modules = self._get_imported_modules()
        if imported_modules:
            logger.info(
                "Imported modules: %s",
                ", ".join(sorted(imported_modules)[:20]),
            )
            venv_copied = self._resolve_and_copy_venv(imported_modules)
        else:
            venv_copied = 0

        logger.info(
            "VFS generated: %d modules, %d resources, %d venv files",
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
                if not file_path.is_file():
                    continue
                try:
                    rel_path = file_path.relative_to(self.project_root)
                except ValueError:
                    continue

                if fnmatch.fnmatch(str(rel_path), pattern):
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

    def _get_imported_modules(self) -> set[str]:
        """Get top-level module names from all graph nodes."""
        if not self.graph:
            return set()
        imported: set[str] = set()
        for node in self.graph.all_nodes().values():
            module_name = node.id.split(":")[0] if ":" in node.id else node.id
            if not module_name:
                continue
            top_level = module_name.split(".")[0]
            imported.add(top_level)
        return imported

    def _resolve_and_copy_venv(self, imported_modules: set[str]) -> int:
        """Resolve and copy required .venv packages using parallel worker graph."""
        resolver = VenvPackageResolver(
            project_root=self.project_root,
            output_path=self.output_path,
            imported_modules=imported_modules,
        )
        return resolver.resolve()
