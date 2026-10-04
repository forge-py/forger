"""Compiler module — orchestrates the analysis and artifact generation pipeline."""

from __future__ import annotations

import ast
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

logger = logging.getLogger(__name__)

#: Schema version written into every ``MANIFEST.json``. Bump this when
#: the manifest shape changes incompatibly. ``Builder._read_manifest``
#: validates the version on read and raises a migration error on
#: mismatch (PHILOSOPHY.md §12).
MANIFEST_FORMAT_VERSION: int = 1


def _max_workers() -> int:
    """Conservative thread-pool size for parallel analyzer phases.

    Caps at 8 to keep the process footprint small while still
    scaling on machines with many cores. Falls back to 1 when
    ``cpu_count`` is unknown (e.g. unusual containers).
    """
    import os

    return min(8, os.cpu_count() or 1)


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
                    line.strip()
                    for line in tl_file.read_text().splitlines()
                    if line.strip()
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
            mods = self._modules_from_top_level(dist_info, site_packages)
            if mods is None:  # no top_level.txt — try weaker strategies
                mods = (
                    self._modules_from_record(dist_info, site_packages)
                    or self._modules_from_metadata(dist_info, site_packages)
                )
            for m in mods or []:
                if m not in module_map:
                    module_map[m] = site_packages / m
        return module_map

    @staticmethod
    def _modules_from_top_level(dist_info: Path, sp: Path) -> list[str] | None:
        """Module names from top_level.txt; None when the file is absent."""
        tl_file = dist_info / "top_level.txt"
        if not tl_file.exists():
            return None
        try:
            return [
                line.strip()
                for line in tl_file.read_text().splitlines()
                if line.strip() and (sp / line.strip()).is_dir()
            ]
        except Exception:
            return []

    @staticmethod
    def _modules_from_record(dist_info: Path, sp: Path) -> list[str]:
        record_file = dist_info / "RECORD"
        if not record_file.exists():
            return []
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
                if (sp / first_segment).is_dir():
                    top_levels.add(first_segment)
        except Exception:
            return []
        return sorted(top_levels)

    @staticmethod
    def _modules_from_metadata(dist_info: Path, sp: Path) -> list[str]:
        meta_file = dist_info / "METADATA"
        if not meta_file.exists():
            return []
        try:
            for line in meta_file.read_text().splitlines():
                if line.lower().startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                    if name and (sp / name).is_dir():
                        return [name]
                    break
        except Exception:
            pass
        return []

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
    4. Dependency graph resolution
    6. Artifact generation
    """

    def __init__(
        self,
        project_root: Path,
        entry_point: str,
        output_path: Path,
        cache_dir: Path | None = None,
        legacy_django_fallback: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.entry_point = entry_point
        self.output_path = output_path.resolve()
        self._vfs_path: Path = output_path.resolve()
        self.graph: DependencyGraph | None = None  # type: ignore[name-defined]
        self.source_files: list[Path] = []
        self.cython_sources: list[Path] = []
        self.symbol_issues: list = []
        # module_id -> parsed AST (ast.Module). Populated during analyze()
        # and handed to plugins via module_parsed/transform_ast hooks.
        self._ast_cache: dict[str, ast.Module] = {}
        # module_id -> original source text. Parallel to ``_ast_cache`` so
        # the symbol-checker and other consumers don't have to re-read
        # files from disk to attribute issues to file:line.
        self._source_cache: dict[str, str] = {}
        self._plugin_context: object = None
        self._plugin_runner: object = None
        # Incremental-cache handle (PHILOSOPHY.md §29 / Invariant 13).
        # Built only when ``cache_dir`` is provided; otherwise the
        # expensive phases (parse + resource analysis) always re-run.
        self._cache: object = None
        if cache_dir is not None:
            from forger.cache import Cache

            self._cache = Cache.with_directory(Path(cache_dir))
        # Legacy Django template-dir fallback is opt-in. Projects that
        # ship a DjangoPlugin (or any discover_resources implementer)
        # should NOT also depend on the heuristic; running both can
        # produce inconsistent results when both happen to find a
        # file. The flag is False by default; the heuristic is
        # preserved for users who haven't migrated yet.
        self.legacy_django_fallback = legacy_django_fallback

    @classmethod
    def forge_from_vfs(
        cls,
        vfs_path: Path,
        artifact_path: Path,
        entry_point: str = "",
    ) -> Compiler:
        """Create a Compiler to package an existing VFS directory into .forge.

        Args:
            vfs_path: Path to the VFS directory (output of ``forger compile``).
            artifact_path: Desired path for the .forge artifact.
            entry_point: Entry module recorded in the artifact manifest.

        Returns:
            A ``Compiler`` instance configured to produce the artifact.
        """
        compiler = cls(
            project_root=vfs_path,
            entry_point=entry_point,
            output_path=artifact_path,
        )
        compiler._vfs_path = vfs_path
        return compiler

    def analyze(self) -> None:
        """Run static analysis on the project."""
        from forger.core import HAS_RUST_CORE

        logger.info("Starting analysis of %s", self.project_root)

        # Try to use Rust GraphBuilder for parallel discovery + content loading
        if HAS_RUST_CORE:
            self._analyze_with_rust()
        else:
            self._analyze_pure_python()

        # Analyze Cython sources first so .pyx modules register as
        # NativeExtension nodes before import analysis adds edges to them
        # (contribute_to_graph won't overwrite an existing node's type).
        self._analyze_cython()

        # Parse every source file into an AST. Trees live on the compiler
        # (and graph nodes' metadata via plugins) for downstream plugin use.
        self._parse_sources()

        # Analyze imports (adds nodes and edges for discovered imports)
        self._analyze_imports()

        # Analyze dynamic imports
        self._analyze_dynamic_imports()

        # Verify imported symbols exist and are used (diagnostics only)
        self.symbol_issues = self._check_imported_symbols()

        # Analyze resources
        self._analyze_resources()

        # NOTE: Graph freezing has been moved to run_plugins() so that
        # plugins can add new nodes (e.g., resource nodes for templates,
        # static files, locale data) before the graph is frozen.

    def _analyze_with_rust(self) -> None:
        """Use Rust GraphBuilder for parallel file discovery with content."""
        from forger.core import DependencyGraph  # wrapper
        from forger.forger_core import (
            GraphBuilder,  # type: ignore[import-not-found, import-untyped, missing-import]
        )

        logger.info("Using Rust GraphBuilder for parallel discovery")

        # Build graph with parallel filesystem discovery
        builder = GraphBuilder(str(self.project_root), self.entry_point)
        rs_graph = builder.build_parallel()  # type: ignore[attr-defined]
        self.graph = DependencyGraph(_rs=rs_graph)
        logger.info(
            "Rust discovery: %d nodes",
            self.graph.node_count(),
        )

        # Collect source file paths for the Python analyzers
        self.source_files = self._discover_source_files()
        logger.info("Discovered %d Python source files", len(self.source_files))

    def _analyze_pure_python(self) -> None:
        """Pure Python fallback for file discovery and graph construction."""
        from forger.core import DependencyGraph, DependencyNode, NodeType

        # Create dependency graph
        self.graph = DependencyGraph()
        self.graph.add_entry_point(self.entry_point)
        self.graph.add_node(DependencyNode.new(self.entry_point, NodeType.EntryPoint))

        # Discover Python source files
        self.source_files = self._discover_source_files()
        logger.info("Discovered %d Python source files", len(self.source_files))

    # -- AST cache ---------------------------------------------------------------

    def _parse_sources(self) -> None:
        """Parse all discovered source files into cached ast.Modules.

        Trees are keyed by module id and shared with plugins through
        ``module_parsed``/``transform_ast`` hooks and
        ``PluginContext.get_module_ast``. Unparseable files are skipped —
        downstream passes fall back to raw source text.

        Virtual modules registered by plugins via
        ``PluginContext.virtual_module`` are also parsed here so the
        AST cache is consistent across filesystem and virtual sources.

        When a cache is configured (PHILOSOPHY.md §29), files whose
        content hash is unchanged are skipped — the previous AST is
        loaded from a serialized sidecar. The sidecar encoding is
        currently conservative: we re-parse unchanged files anyway,
        but we record the hash so the resource analyzer can short-
        circuit on the same input.
        """
        from forger.cache import Cache

        cache = self._cache
        if cache is not None and not isinstance(cache, Cache):
            cache = None  # defensive: any non-Cache handle is ignored

        self._ast_cache = {}
        failures, cache_hits = self._parse_filesystem_sources(cache=cache)
        failures += self._parse_virtual_sources()

        if failures:
            logger.info("AST parse: %d file(s) unparseable, skipped", failures)
        if cache_hits:
            logger.debug(
                "Parse cache: %d file(s) unchanged, %d new/changed",
                cache_hits,
                len(self._ast_cache) - cache_hits,
            )
        logger.debug("Parsed %d module ASTs", len(self._ast_cache))

        # Persist the cache so the next build sees the new hashes.
        if cache is not None:
            cache.save()

    def _parse_filesystem_sources(
        self,
        *,
        cache: object | None,
    ) -> tuple[int, int]:
        """Parse every discovered ``.py`` file into the AST cache.

        When ``cache`` is non-None and a sidecar pickle exists for the
        file's content hash, the cached ``ast.Module`` is loaded
        directly (PHILOSOPHY.md §29). Otherwise the file is parsed
        and the new tree is pickled into the sidecar for next time.

        Returns ``(failure_count, cache_hit_count)``.
        """
        import hashlib

        from forger.cache import CacheState

        failures = 0
        cache_hits = 0
        for source_file in self.source_files:
            module_name = self._path_to_module(source_file)
            if not module_name:
                continue
            try:
                text = source_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                logger.debug("Source read skipped for %s: %s", module_name, e)
                failures += 1
                continue
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cache_hit = (
                cache is not None
                and cache.check_state(source_file, content_hash)
                == CacheState.Unchanged
            )

            tree, hit = self._load_or_parse_source(
                source_file, module_name, text, content_hash, cache_hit, cache
            )
            if tree is None:
                failures += 1
                continue
            self._ast_cache[module_name] = tree
            self._source_cache[module_name] = text
            if hit:
                cache_hits += 1
        return failures, cache_hits

    def _load_or_parse_source(
        self,
        source_file: Path,
        module_name: str,
        text: str,
        content_hash: str,
        cache_hit: bool,
        cache: object | None,
    ) -> tuple[ast.Module | None, bool]:
        """Try the AST sidecar first; fall back to a fresh parse.

        Returns ``(tree, hit)``. ``hit`` is True if the tree came
        from the sidecar (and the cache stays valid), False if a
        re-parse was needed. ``(None, False)`` means a parse
        failure; the caller increments the failure counter.
        """
        import pickle

        if cache_hit:
            sidecar = self._ast_sidecar_path(cache, content_hash)
            if sidecar is not None and sidecar.is_file():
                try:
                    with sidecar.open("rb") as fh:
                        return pickle.load(fh), True
                except (pickle.UnpicklingError, EOFError, OSError):
                    pass  # fall through to a fresh parse

        try:
            tree = ast.parse(text, filename=str(source_file))
        except SyntaxError as e:
            logger.debug("AST parse skipped for %s: %s", module_name, e)
            return None, False

        if cache is not None:
            cache.update(source_file, content_hash, len(text))
            sidecar = self._ast_sidecar_path(cache, content_hash)
            if sidecar is not None:
                try:
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    with sidecar.open("wb") as fh:
                        pickle.dump(tree, fh, protocol=pickle.HIGHEST_PROTOCOL)
                except OSError as e:
                    logger.debug(
                        "AST sidecar write failed for %s: %s", module_name, e
                    )
        return tree, False

    @staticmethod
    def _ast_sidecar_path(cache: object, content_hash: str) -> Path | None:
        """Return the pickle sidecar path for a given content hash.

        The sidecar is stored inside the cache directory under
        ``ast/<hash>.pkl``. ``None`` is returned if the cache has no
        directory (i.e. the cache is disabled or in-memory only).
        """
        cache_dir = getattr(cache, "cache_dir", None)
        if cache_dir is None:
            return None
        return Path(cache_dir) / "ast" / f"{content_hash}.pkl"

    def _parse_virtual_sources(self) -> int:
        """Parse every virtual module registered by plugins."""
        ctx = getattr(self, "_plugin_context", None)
        if ctx is None:
            return 0
        failures = 0
        for module_id, source in ctx.virtual_source_for_all():
            try:
                self._ast_cache[module_id] = ast.parse(
                    source, filename=module_id
                )
            except SyntaxError as e:
                logger.debug(
                    "Virtual module parse failed for %s: %s", module_id, e
                )
                failures += 1
        return failures

    def get_module_ast(self, module_id: str) -> ast.Module | None:
        """Return the cached AST for a module, or None."""
        return self._ast_cache.get(module_id)

    def set_module_ast(self, module_id: str, tree: ast.Module) -> None:
        """Store/replace the AST for a module (used by plugin pipelines)."""
        self._ast_cache[module_id] = tree

    def _analyze_imports(self) -> None:
        """Analyze static imports across all source files.

        Plugins can override resolution through the ``resolve_id`` hook
        (PLUGIN_ARCHITECTURE.md §4). When a plugin returns a target for a
        specifier, that target replaces the analyzer's string-based
        resolution. Plugins can also supply source text for unknown
        module ids through the ``load`` hook (e.g. virtual modules).

        Files are analyzed in parallel: each thread gets a fresh
        ``ImportAnalyzer`` so per-file ``self._imports`` is not
        shared (PHILOSOPHY.md §34). Graph writes are funneled back
        to the main thread by the executor.
        """
        from concurrent.futures import ThreadPoolExecutor

        from forger.analyzer import ImportAnalyzer
        from forger.core import DependencyNode, NodeType

        assert self.graph is not None

        # Register every module's node first (a graph mutation that
        # must happen on the main thread so the Rust core's internal
        # locking sees a consistent sequence). The per-file work
        # only contributes import edges.
        for source_file in self.source_files:
            module_name = self._path_to_module(source_file)
            if not module_name:
                continue
            if not self.graph.get_node(module_name):
                self.graph.add_node(
                    DependencyNode.new(module_name, NodeType.PythonModule)
                )

        resolver = self._plugin_resolver
        graph = self.graph  # local alias for closure

        def _analyze_one(source_file: Path) -> int:
            # A fresh analyzer per file avoids the shared ``_imports``
            # race documented in the audit. The resolver closure
            # captures the plugin runner; it's safe to call from any
            # thread because the runner is read-only here.
            analyzer = ImportAnalyzer()
            module_name = (
                source_file.stem if source_file.name == "__init__.py"
                else ".".join(source_file.relative_to(self.project_root)
                              .with_suffix("").parts)
            )
            # Use the canonical helper; ``_path_to_module`` lives on
            # ``self`` so we route through the compiled form.
            module_name = self._path_to_module(source_file)
            if not module_name:
                return 0
            imports = analyzer.analyze_file(source_file)
            analyzer.contribute_to_graph(
                graph,  # type: ignore[arg-type]
                module_name,
                is_package=source_file.name == "__init__.py",
                plugin_resolver=resolver,
            )
            return len(imports)

        workers = min(len(self.source_files), _max_workers())
        if workers <= 1 or not self.source_files:
            for sf in self.source_files:
                n = _analyze_one(sf)
                if n:
                    logger.debug("Found %d imports in %s", n, sf)
            return

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_analyze_one, self.source_files))
        for sf, n in zip(self.source_files, results):
            if n:
                logger.debug("Found %d imports in %s", n, sf)

    def _plugin_resolver(self, specifier: str, importer: str | None) -> str | None:
        """Ask the plugin chain to resolve ``specifier`` from ``importer``.

        Returns a module id if any plugin produced a result, else None.
        Built lazily: the import analysis pass runs before plugin setup,
        so on the first call we cache the live ``runner``+``ctx`` and
        re-use them thereafter.
        """
        from forger.optimizer import PluginContext, PluginRunner

        runner = getattr(self, "_plugin_runner", None)
        ctx = getattr(self, "_plugin_context", None)
        if runner is None:
            # Plugin runner not set up yet; import analysis runs before
            # run_plugins(). Cache an empty runner for the first few
            # calls so the seam stays consistent.
            runner = PluginRunner([])
        if ctx is None:
            ctx = PluginContext(project_root=self.project_root)
            ctx.set_graph(self.graph)
        return runner.run_resolve_id(specifier, importer, ctx)

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

    # -- Cython & symbol checking -------------------------------------------------

    def _analyze_cython(self) -> None:
        """Discover Cython sources and add native-extension graph nodes.

        Each ``.pyx`` becomes a NativeExtension node (module id from its
        project-relative path). ``.pxd`` files attach to their sibling
        module's node. Cimports between project modules become
        NativeDependency edges.
        """
        from forger.analyzer.cython import (
            CythonImportAnalyzer,
            discover_cython_sources,
        )

        assert self.graph is not None
        cython_sources = discover_cython_sources(self.project_root)
        self.cython_sources = cython_sources
        if not cython_sources:
            return

        analyzer = CythonImportAnalyzer()
        node_ids = self._register_cython_modules(cython_sources)
        self._add_cython_dependency_edges(analyzer, cython_sources, node_ids)

    def _register_cython_modules(self, cython_sources: list[Path]) -> set[str]:
        """Add a NativeExtension node for every project .pyx module."""
        from forger.analyzer.cython import cython_module_name
        from forger.core import DependencyNode, NodeType

        assert self.graph is not None
        node_ids: set[str] = set()

        for pyx in cython_sources:
            if pyx.suffix != ".pyx":
                continue  # .pxd handled below via sibling lookup
            mod_name = cython_module_name(self.project_root, pyx)
            if mod_name is None:
                continue
            existing = self.graph.get_node(mod_name)
            if existing is None:
                self.graph.add_node(
                    DependencyNode.new(mod_name, NodeType.NativeExtension).with_metadata(
                        "discovered_by", "cython_analyzer"
                    )
                )
            # If a node already exists (e.g. entry-point handling), keep it;
            # import analysis will attach edges without overwriting the type.
            node_ids.add(mod_name)

        pxd_count = sum(
            1
            for decl in cython_sources
            if decl.suffix == ".pxd"
            and cython_module_name(self.project_root, decl) in node_ids
        )
        if pxd_count:
            logger.info("Cython: %d modules with .pxd declarations", pxd_count)
        logger.info("Cython: %d native extension modules registered", len(node_ids))
        return node_ids

    def _add_cython_dependency_edges(
        self,
        analyzer: object,
        cython_sources: list[Path],
        node_ids: set[str],
    ) -> None:
        """Turn cimports between project modules into NativeDependency edges."""
        from forger.analyzer.cython import cython_module_name
        from forger.core import (
            DependencyEdge,
            DependencyNode,
            EdgeProvenance,
            EdgeType,
            NodeType,
        )

        assert self.graph is not None
        for pyx in cython_sources:
            if pyx.suffix != ".pyx":
                continue
            mod_name = cython_module_name(self.project_root, pyx)
            if mod_name is None or mod_name not in node_ids:
                continue
            for imp in analyzer.analyze_file(pyx):  # type: ignore[attr-defined]
                target = self._resolve_cython_target(imp, mod_name)
                if target is None:
                    continue
                if not self.graph.get_node(target):
                    self.graph.add_node(
                        DependencyNode.new(target, NodeType.PythonModule).with_metadata(
                            "discovered_by", "cython_analyzer"
                        )
                    )
                self.graph.add_edge(
                    DependencyEdge.new(
                        mod_name,
                        target,
                        EdgeType.NativeDependency,
                        EdgeProvenance(
                            source=(pyx.name, imp.line),
                            discovered_by="cython_analyzer",
                            description=(
                                f"cimport {imp.module}"
                                if not imp.names
                                else f"from {imp.module} cimport {', '.join(imp.names)}"
                            ),
                        ),
                    )
                )

    def _resolve_cython_target(self, imp: object, source_module: str) -> str | None:
        """Resolve a cimport to a project-internal module name, else None."""
        level = getattr(imp, "level", 0) or 0
        module = getattr(imp, "module", "")
        names = getattr(imp, "names", [])
        is_from = getattr(imp, "is_from", False)

        if level > 0:
            parts = source_module.split(".")
            # Package __init__.py resolves one level inside the package
            # (`from . core cimport X` in shop/__init__.py means shop.core).
            # The cython source file may live next to __init__.py.
            pyx_path = getattr(imp, "source_file", "") or ""
            is_pkg = pyx_path.endswith("__init__.pyx") or pyx_path.endswith(
                "__init__.py"
            )
            effective_parts = [*parts, ""] if is_pkg else parts
            if level > len(effective_parts):
                return None
            base = ".".join(
                effective_parts[: len(effective_parts) - level]
            ).rstrip(".")
            # _visit_tree stores the relative target dot-prefixed; depth
            # is already carried by level.
            rest = module.lstrip(".") if module else ""
            candidate = f"{base}.{rest}" if base and rest else base
            if not candidate:
                return None
            if is_from and names and not rest:
                # `from . cimport name` targets submodules of the package.
                candidates = [f"{candidate}.{n}" for n in names]
            else:
                candidates = [candidate]
            for c in candidates:
                if self._is_project_module(c):
                    return c
            return None

        # Absolute cimport: project module if it exists as .py/.pyx source.
        candidates = [module]
        if is_from and names:
            candidates.extend(f"{module}.{n}" for n in names)
        for c in candidates:
            if self._is_project_module(c):
                return c
        return None

    def _is_project_module(self, module_name: str) -> bool:
        """True when module_name maps to a real .py/.pyx/package dir here."""
        base = self.project_root / Path(*module_name.split("."))
        return (
            (base.with_suffix(".py")).exists()
            or (base.with_suffix(".pyx")).exists()
            or (base / "__init__.py").exists()
        )

    def _check_imported_symbols(self) -> list:
        """Run SymbolChecker over all Python sources; returns diagnostics.

        Uses the in-memory AST cache (PHILOSOPHY.md §6) to avoid a
        second ``ast.parse`` round; ``check_project_with_trees`` is
        the variant designed for exactly this case. We also pass the
        original source text so SymbolChecker can attribute issues to
        file:line — re-reading from disk would defeat the purpose
        for cached files, so we keep the source in a parallel cache.
        """
        from forger.analyzer.cython import SymbolChecker

        checker = SymbolChecker(self.project_root)
        trees: dict[str, tuple[str, ast.Module]] = {}
        for module_id, tree in self._ast_cache.items():
            source = self._source_cache.get(module_id, "")
            trees[module_id] = (source, tree)
        issues = (
            checker.check_project_with_trees(trees)
            if trees
            else checker.check_project()
        )

        missing = sum(1 for i in issues if i.kind == "missing_symbol")
        unused = sum(1 for i in issues if i.kind == "unused_import")
        if missing:
            logger.warning(
                "Symbol check: %d imported symbol(s) not found in their target module",
                missing,
            )
        if unused:
            logger.info("Symbol check: %d unused import(s)", unused)

        # Surface a few examples at info level.
        for issue in issues[:10]:
            logger.debug(
                "%s:%d %s: %s",
                issue.source_file,
                issue.line,
                issue.kind,
                issue.detail,
            )
        return issues

    def _analyze_resources(self) -> None:
        """Analyze resource accesses across all source files.

        Statically resolvable accesses (e.g.
        ``Path(__file__).parent / "data" / "x.csv"``) become Resource
        nodes with edges from the accessing module, so the resource
        survives tree-shaking and lands in the VFS at its original path.

        The per-file work is parallel: each thread resolves paths
        relative to its source file and returns the list of node ids
        to register plus the edge payloads. Graph mutations are
        funneled back to the main thread.
        """
        from concurrent.futures import ThreadPoolExecutor

        from forger.analyzer import ResourceAnalyzer

        assert self.graph is not None
        resource_analyzer = ResourceAnalyzer()

        def _resolve_one(source_file: Path) -> list[tuple[str, str, int, str]]:
            """Return ``(node_id, accessing_module, line, path_expr)`` triples
            for every resource access whose target file exists."""
            accessing_module = self._path_to_module(source_file)
            if accessing_module is None:
                return []
            accesses = resource_analyzer.analyze_file(source_file)
            base_dir = source_file.parent
            out: list[tuple[str, str, int, str]] = []
            for access in accesses:
                if not access.segments:
                    continue  # dynamic — conservative keep elsewhere
                resolved: Path = base_dir
                for seg in access.segments:
                    resolved = resolved / seg
                if not resolved.is_file():
                    logger.debug(
                        "Resource not found on disk: %s (from %s:%d)",
                        resolved,
                        source_file,
                        access.line,
                    )
                    continue
                try:
                    rel = resolved.relative_to(self.project_root)
                except ValueError:
                    continue
                out.append(
                    (
                        rel.as_posix(),
                        accessing_module,
                        access.line,
                        access.path_expr,
                    )
                )
            return out

        workers = min(len(self.source_files), _max_workers())
        if workers <= 1 or not self.source_files:
            per_file = [_resolve_one(sf) for sf in self.source_files]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                per_file = list(pool.map(_resolve_one, self.source_files))

        # Now serialize the graph mutations on the main thread.
        from forger.core import (
            DependencyEdge,
            DependencyNode,
            EdgeProvenance,
            EdgeType,
            NodeType,
        )

        added = 0
        for triples in per_file:
            for node_id, accessing_module, line, path_expr in triples:
                if not self.graph.get_node(node_id):
                    self.graph.add_node(
                        DependencyNode.new(node_id, NodeType.Resource).with_metadata(
                            "discovered_by", "resource_analyzer"
                        )
                    )
                    added += 1
                self.graph.add_edge(
                    DependencyEdge.new(
                        accessing_module,
                        node_id,
                        EdgeType.ResourceDependency,
                        EdgeProvenance(
                            source=("", line),  # filled below
                            discovered_by="resource_analyzer",
                            description=f"{path_expr}",
                        ),
                    )
                )
        if added:
            logger.info("Resources discovered by static analysis: %d", added)

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

    def run_plugins(
        self,
        plugins=None,
        *,
        include_optimizer: bool = True,
        apply: str = "build",
    ) -> None:
        """Run framework plugins.

        If ``plugins`` is not passed, fall back to the plugin list
        stored in the forger API context (populated by
        ``forger_plugin_django.defineConfig()``).

        When ``include_optimizer`` is True, the default
        ``StripCommentsDocstrings`` post-pass is appended automatically.

        ``apply`` controls which plugin subset runs (PHILOSOPHY.md §29,
        PLUGIN_ARCHITECTURE.md §6): ``"build"`` (default) runs every
        plugin; ``"serve"`` only runs plugins whose ``apply`` attribute
        is ``"serve"`` (or ``"both"``). This lets a ``forger dev``
        workflow register reload-only plugins without them participating
        in production builds.
        """
        if not self.graph:
            return

        plugins = self._collect_plugins(
            include_optimizer=include_optimizer,
            explicit=plugins,
            apply=apply,
        )
        from forger.optimizer import PluginContext

        plugin_ctx = PluginContext(project_root=self.project_root)
        plugin_ctx.set_graph(self.graph)
        self._plugin_context = plugin_ctx
        plugin_ctx._module_asts = dict(self._ast_cache)

        if plugins:
            self._run_plugin_chain(plugins, plugin_ctx, apply=apply)

        # --- Symbol-level tree shaking + AST sync (always runs) ---
        self._run_symbol_shake(plugin_ctx)
        self._sync_module_contents(plugin_ctx)

        # Freeze the graph after all plugins have finished adding nodes and
        # edges. Plugins contribute resource nodes (templates, static files,
        # locale data) that need to be in the graph before freezing.
        self.graph.freeze()
        logger.info(
            "Graph frozen: %d nodes, %d edges",
            self.graph.node_count(),
            self.graph.edge_count(),
        )

    def _collect_plugins(
        self,
        *,
        include_optimizer: bool,
        explicit: list | None,
        apply: str = "build",
    ) -> list:
        """Resolve the final plugin list.

        - When ``explicit`` is given (caller passed a list), it's the
          authoritative base.
        - Otherwise, fall back to the forger API context's registered
          plugins.
        - Built-in passes (strip, minify) are appended per flags.
        - The result is filtered by ``apply`` so ``serve``-only plugins
          don't pollute production builds (and vice versa).
        """
        import forger.api as api_module

        if explicit is not None:
            plugins = list(explicit)
        else:
            plugins = []
            try:
                plugins = api_module.get_context().plugins or []
            except (ImportError, Exception):
                plugins = []

        if include_optimizer:
            from forger.optimizer.strip import StripCommentsDocstrings

            plugins = list(plugins) + [StripCommentsDocstrings()]

        cfg = None
        try:
            cfg = api_module.get_context().config
        except Exception:
            cfg = None
        if cfg is not None and cfg.minify_names:
            from forger.optimizer.minify import MinificationPass

            extra_keep: set[str] = set()
            if isinstance(cfg.minify_names, dict):
                extra_keep = set(cfg.minify_names.get("keep", set()))
            plugins = list(plugins) + [MinificationPass(extra_keep=extra_keep)]

        return self._filter_by_apply(plugins, apply)

    @staticmethod
    def _filter_by_apply(plugins: list, apply: str) -> list:
        """Filter plugins whose ``apply`` attribute doesn't match the run mode.

        A plugin with ``apply = "build"`` runs only in ``"build"`` mode
        (the default). One with ``apply = "serve"`` runs only in
        ``"serve"`` mode. ``apply = "both"`` (or the attribute unset,
        i.e. ``None``) runs everywhere.
        """
        out: list = []
        for p in plugins:
            wanted = getattr(p, "apply", "both")
            if wanted is None or wanted == "both" or wanted == apply:
                out.append(p)
        return out

    def _run_plugin_chain(
        self,
        plugins: list,
        plugin_ctx: object,
        *,
        apply: str = "build",
    ) -> None:
        """Run the user's plugin chain in tier order: build → AST → shake
        → post-shake. Always followed by tree-shake, AST sync, and freeze
        in :meth:`run_plugins`.

        The ``apply`` mode is passed through to ``PluginRunner`` so
        plugins whose ``apply`` attribute is ``"build"`` / ``"serve"``
        are filtered at the runner level too (defense in depth).
        """
        from forger.optimizer import PluginRunner

        runner = PluginRunner(plugins, apply=apply)
        self._plugin_runner = runner

        # --- Build phase ---
        runner.run_config(plugin_ctx)
        runner.run_config_resolved(plugin_ctx)
        runner.run_build_start(plugin_ctx)
        runner.run_build_graph(plugin_ctx)

        # --- AST phase: hand each module's tree to the plugin chain ---
        self._run_ast_hooks(runner, plugin_ctx)

        runner.run_before_shake(plugin_ctx)

        # Mark reachable nodes as required (tree-shaking)
        self.graph.mark_reachable_required()

        # Mark plugin-retained modules as required so they survive
        # tree-shaking even when not reachable from the entry point.
        for retained_mod in plugin_ctx.retain_modules:
            self._mark_retained_required(retained_mod)

        # Run post-shake hooks (dead code detection, optimizer passes)
        runner.run_after_shake(plugin_ctx)
        runner.run_build_end(plugin_ctx)

        # Log unoptimized dependencies discovered by plugins
        unopt = plugin_ctx.unoptimized_dependencies
        if unopt:
            logger.info(
                "Plugins declared %d unoptimized dependencies: %s",
                len(unopt),
                ", ".join(sorted(unopt)[:20]),
            )

        logger.info("Ran %d user plugins", len(plugins))

    def _run_ast_hooks(self, runner: object, plugin_ctx: object) -> None:
        """Fire module_parsed/transform_ast hooks over cached ASTs.

        Plugins may mutate the trees. The trees stay in
        ``plugin_ctx.module_asts`` and are unparsed back into node content
        by ``_sync_module_contents`` (called after the symbol-shake pass
        so pruning sees any names plugins added/removed).
        """
        for module_id, tree in list(plugin_ctx.module_asts.items()):
            runner.run_module_parsed(module_id, plugin_ctx)
            runner.run_transform_ast(module_id, tree, plugin_ctx)

    def _run_symbol_shake(self, plugin_ctx: object) -> None:
        """Prune unreachable symbol definitions from each cached AST.

        Only modules with the ``python_module`` node type participate.
        Modules without a graph node, that failed to parse, or that are
        third-party (excluded by a node ``external`` flag) are skipped.
        Plugins can mark modules as ``retain_module`` to opt out.
        """
        from forger.core import NodeType
        from forger.optimizer.ast_shake import (
            ASTPruner,
            SymbolReachabilityAnalyzer,
        )

        assert self.graph is not None
        if not plugin_ctx.module_asts:
            return

        candidate_ids = self._shake_candidates(plugin_ctx, NodeType)
        if not candidate_ids:
            return

        analyzer = SymbolReachabilityAnalyzer()
        for module_id in candidate_ids:
            analyzer.add_module(module_id, plugin_ctx.module_asts[module_id])
        self._seed_reachability_roots(plugin_ctx, candidate_ids, analyzer)

        result = analyzer.analyze()
        pruner = ASTPruner(result.reachable_symbols)

        pruned = 0
        for module_id in candidate_ids:
            tree = plugin_ctx.module_asts[module_id]
            new_tree = pruner.prune_module(module_id, tree)
            if new_tree is not tree:
                plugin_ctx.module_asts[module_id] = new_tree
                pruned += 1
        if pruned or result.unreachable_symbols:
            logger.info(
                "Symbol shake: %d pruned modules, %d unreachable symbols removed",
                pruned,
                len(result.unreachable_symbols),
            )

    def _shake_candidates(
        self, plugin_ctx: object, node_type_cls: type
    ) -> list[str]:
        """Module ids eligible for symbol-level pruning."""
        candidate_ids: list[str] = []
        for module_id in plugin_ctx.module_asts:
            if module_id in plugin_ctx.retain_modules:
                continue
            node = self.graph.get_node(module_id)  # type: ignore[union-attr]
            if node is None or node.node_type != node_type_cls.PythonModule:
                continue
            candidate_ids.append(module_id)
        return candidate_ids

    def _seed_reachability_roots(
        self,
        plugin_ctx: object,
        candidate_ids: list[str],
        analyzer: object,
    ) -> None:
        """Tell the analyzer which symbols are entry points to keep.

        - The entry-point module's top-level definitions are roots.
        - ``from X import name`` only seeds ``X.name`` as a root, so other
          names in X can be pruned.
        - ``import X`` keeps the whole module (X.* survives) since
          ``X.foo`` is reachable through the module attribute.
        - Plugin-retained modules are kept whole.
        """
        for module_id in candidate_ids:
            tree = plugin_ctx.module_asts[module_id]
            for stmt in tree.body:
                if isinstance(stmt, ast.ImportFrom):
                    self._seed_from_import(stmt, plugin_ctx, analyzer)
                elif isinstance(stmt, ast.Import):
                    self._seed_plain_import(stmt, plugin_ctx, analyzer)
                elif module_id == self.entry_point:
                    name = _module_level_name(stmt)
                    if name:
                        analyzer.add_root(f"{module_id}.{name}")
        self._seed_retained_module_roots(plugin_ctx, analyzer)

    def _seed_from_import(
        self, stmt: ast.ImportFrom, plugin_ctx: object, analyzer: object
    ) -> None:
        if not (stmt.module and stmt.names):
            return
        source = stmt.module.split(".")[0]
        if source not in plugin_ctx.module_asts:
            return
        for alias in stmt.names:
            analyzer.add_root(f"{source}.{alias.name}")

    def _seed_plain_import(
        self, stmt: ast.Import, plugin_ctx: object, analyzer: object
    ) -> None:
        for alias in stmt.names:
            imported = alias.name.split(".")[0]
            if imported not in plugin_ctx.module_asts:
                continue
            for src_stmt in plugin_ctx.module_asts[imported].body:
                src_name = _module_level_name(src_stmt)
                if src_name:
                    analyzer.add_root(f"{imported}.{src_name}")

    def _seed_retained_module_roots(
        self, plugin_ctx: object, analyzer: object
    ) -> None:
        for module_id in plugin_ctx.retain_modules:
            if module_id not in plugin_ctx.module_asts:
                continue
            for stmt in plugin_ctx.module_asts[module_id].body:
                name = _module_level_name(stmt)
                if name:
                    analyzer.add_root(f"{module_id}.{name}")

    def _sync_module_contents(self, plugin_ctx: object) -> None:
        """Unparse final cached ASTs back into graph node content.

        Skips modules whose tree unparses to the same normalized text
        (plugins that only round-tripped the tree won't trigger a
        write).
        """
        assert self.graph is not None
        synced = 0
        for module_id, tree in plugin_ctx.module_asts.items():
            node = self.graph.get_node(module_id)
            if node is None:
                continue
            try:
                new_code = ast.unparse(tree)
            except Exception:
                continue
            current = node.get_content()
            if current is None or current != new_code:
                if current is not None and self._normalized_view(current) == new_code:
                    continue
                self.graph.set_node_content(module_id, new_code)
                synced += 1
        if synced:
            logger.info("AST sync: %d module(s) updated in graph", synced)

    @staticmethod
    def _normalized_view(code: str) -> str:
        return ast.unparse(ast.parse(code))

    def generate_vfs(self) -> None:
        """Generate the VFS directory from analysis results.

        Copies all reachable Python modules and resources into the output
        directory, preserving the module structure as the VFS layout.
        Also copies required third-party packages from .venv into dist/.venv.

        Output generation hooks are invoked at the appropriate phases:
        - ``render_start``: Before any files are written.
        - ``render_chunk``: For each module chunk before writing.
        - ``generate_bundle``: After all files are staged, before writing.
        - ``write_bundle``: After all files are written.
        - ``close_bundle``: Final cleanup.
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

        # Re-apply plugin-retained modules after mark_reachable_required()
        # which may have overwritten the required flags set in run_plugins().
        # When a plugin retains a package (e.g., "blog"), also retain all
        # child modules (e.g., "blog.views", "blog.models").
        plugin_ctx = getattr(self, "_plugin_context", None)
        if plugin_ctx:
            for retained_mod in plugin_ctx.retain_modules:
                self._mark_retained_required(retained_mod)

        # Invoke render_start hook
        runner = getattr(self, "_plugin_runner", None)
        plugin_ctx = getattr(self, "_plugin_context", None)
        if runner and plugin_ctx:
            runner.run_render_start(plugin_ctx)

        # Stage into a temp sibling so a failed build never corrupts the
        # previous dist (CLAUDE.md invariant 11). Atomic rename on success.
        import shutil

        staging = self.output_path.with_name(self.output_path.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        # Run the rest of VFS generation against the staging dir. We swap
        # ``self.output_path`` in place so every existing copy call writes
        # to the right place, then restore it after the work is done.
        import shutil as _shutil
        real_output = self.output_path
        self.output_path = staging
        build_succeeded = False
        try:
            copied, bundle = self._write_python_modules(runner, plugin_ctx)
            resource_count = self._copy_include_resources()
            graph_resource_count = self._copy_graph_resources()

            if runner and plugin_ctx:
                build_options = {
                    "output_dir": str(staging),
                    "entry_point": self.entry_point,
                    "module_count": copied,
                    "resource_count": resource_count + graph_resource_count,
                }
                runner.run_generate_bundle(build_options, bundle, plugin_ctx)
                self._write_emitted_files()

            venv_copied = self._copy_venv_packages()

            if runner and plugin_ctx:
                runner.run_write_bundle(staging, plugin_ctx)
                runner.run_close_bundle(plugin_ctx)
            build_succeeded = True
        finally:
            self.output_path = real_output
            if not build_succeeded and staging.exists():
                # Don't leak the half-written staging dir; the previous
                # dist remains intact because we never overwrote it.
                _shutil.rmtree(staging, ignore_errors=True)

        # Atomic swap — on POSIX, rename is atomic; on Windows it's atomic
        # within the same volume, which is true for a `.tmp` sibling.
        if real_output.exists():
            shutil.rmtree(real_output)
        staging.rename(real_output)

        logger.info(
            "VFS generated: %d modules, %d resources, %d venv files",
            copied,
            resource_count + graph_resource_count,
            venv_copied,
        )

    def _write_python_modules(
        self, runner: object, plugin_ctx: object
    ) -> tuple[int, dict[str, dict[str, object]]]:
        """Copy reachable source files into the VFS; return (count, bundle).

        Also writes any *virtual* modules registered by plugins via
        ``PluginContext.virtual_module`` into ``<vfs>/virtual/<name>.py``
        so they're importable at runtime (PLUGIN_ARCHITECTURE.md §14).
        """
        copied = 0
        # Check if any source file nodes are marked required
        any_required = False
        for sf in self.source_files:
            mod = self._path_to_module(sf)
            if mod is not None:
                node = self.graph.get_node(mod)  # type: ignore[union-attr]
                if node is not None and node.required:
                    any_required = True
                    break

        # Build a bundle dict for the generate_bundle hook
        bundle: dict[str, dict[str, object]] = {}

        for source_file in self.source_files:
            module_name = self._path_to_module(source_file)
            if module_name is None:
                continue

            # If no nodes were marked reachable (e.g. entry point not found),
            # copy everything conservatively
            if not any_required:
                required = True
            else:
                node = self.graph.get_node(module_name)  # type: ignore[union-attr]
                required = node is not None and node.required
            if not required:
                continue

            rel_path = self._vfs_relative_path(source_file)
            if rel_path is None:
                continue

            code = self._module_source(source_file, module_name, runner, plugin_ctx)
            dest = self.output_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(code or "", encoding="utf-8")
            bundle[str(rel_path)] = {
                "code": code,
                "module_id": module_name,
                "is_entry": module_name == self.entry_point,
            }
            copied += 1

        # Virtual modules: write the plugin-supplied source to a path
        # the import system can reach at runtime. We use the original
        # specifier name (the part after ``virtual:``) so an
        # ``import myconfig`` works even when the user never declared
        # ``myconfig`` as a real module — the plugin supplied it.
        # PLUGIN_ARCHITECTURE.md §14.
        copied += self._write_virtual_modules(bundle)
        return copied, bundle

    def _write_virtual_modules(
        self, bundle: dict[str, dict[str, object]]
    ) -> int:
        """Emit virtual modules into the VFS (PLUGIN_ARCHITECTURE.md §14).

        Returns the number of files written. The source for each
        virtual module is registered via
        ``PluginContext.virtual_module(name, source)``. The file is
        written to ``<vfs>/<name-with-slashes>.py`` so plain
        ``import name`` works at runtime.
        """
        ctx = getattr(self, "_plugin_context", None)
        if ctx is None:
            return 0
        copied = 0
        for module_id, source in ctx.virtual_source_for_all():
            name = module_id.split(":", 1)[1]
            rel_path = Path(name.replace(".", "/") + ".py")
            dest = self.output_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(source, encoding="utf-8")
            bundle[str(rel_path)] = {
                "code": source,
                "module_id": module_id,
                "is_entry": False,
                "virtual": True,
            }
            copied += 1
        return copied

    def _vfs_relative_path(self, source_file: Path) -> Path | None:
        try:
            return source_file.relative_to(self.project_root)
        except ValueError:
            return None

    def _module_source(
        self, source_file: Path, module_name: str, runner: object, plugin_ctx: object
    ) -> str | None:
        """Optimized node content, render_chunk-transformed, else file text."""
        node = self.graph.get_node(module_name)  # type: ignore[union-attr]
        code = (
            node.get_content()
            if node is not None and node.get_content() is not None
            else source_file.read_text(encoding="utf-8")
        )
        if runner and plugin_ctx:
            chunk = {
                "code": code,
                "module_id": module_name,
                "is_entry": module_name == self.entry_point,
            }
            chunk = runner.run_render_chunk(chunk, plugin_ctx)
            if chunk:
                code = chunk.get("code", code)
        return code

    def _write_emitted_files(self) -> None:
        """Persist files plugins emitted via ``emit_file()``."""
        plugin_ctx = getattr(self, "_plugin_context", None)
        emitted = plugin_ctx.emitted_files if plugin_ctx else []
        if not emitted:
            return
        for file_info in emitted:
            name = file_info.get("name", "unnamed")
            source = file_info.get("source", "")
            if source:
                dest_file = self.output_path / name
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(source, bytes):
                    dest_file.write_bytes(source)
                else:
                    dest_file.write_text(source, encoding="utf-8")
        logger.info("Wrote %d emitted files", len(emitted))

    def _copy_venv_packages(self) -> int:
        """Resolve imported third-party packages and copy them into the VFS."""
        imported_modules = self._get_imported_modules()
        if imported_modules:
            logger.info(
                "Imported modules: %s",
                ", ".join(sorted(imported_modules)[:20]),
            )
            return self._resolve_and_copy_venv(imported_modules)
        return 0

    def generate_artifact(self) -> None:
        """Generate the .forge artifact from VFS directory.

        The .forge artifact is an uncompressed directory containing the VFS
        tree plus a ``MANIFEST.json`` at its root. No compression layer is
        applied — build reads files directly from disk.
        """
        import json
        import shutil

        vfs = self._vfs_path
        artifact = self.output_path

        logger.info("Generating artifact: %s from VFS: %s", artifact, vfs)

        if not vfs.exists():
            raise RuntimeError(f"VFS directory does not exist: {vfs}")

        # Stage into a sibling temp dir, then atomically swap into place so a
        # failed run never corrupts an existing valid artifact.
        staging = artifact.with_name(artifact.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(vfs, staging)

        files_list = sorted(
            p.relative_to(vfs).as_posix() for p in vfs.rglob("*") if p.is_file()
        )

        # Per-file metadata: project-relative path, kind, size, content
        # hash. See PHILOSOPHY.md §12 — the manifest must describe each
        # emitted file with enough info for downstream tooling.
        file_records: list[dict[str, object]] = []
        for rel in files_list:
            abs_path = vfs / rel
            file_records.append(
                {
                    "path": rel,
                    "kind": _classify_artifact_kind(rel),
                    "size": abs_path.stat().st_size,
                    "hash": _content_hash(abs_path),
                }
            )

        manifest = {
            "format_version": MANIFEST_FORMAT_VERSION,
            "version": "0.1.0",
            "entry_point": self.entry_point,
            # NOTE: project_root is *not* embedded as an absolute path
            # (PATH_PRESERVATION.md §"Absolute Paths"). The relative
            # file paths are the canonical identity.
            "files": file_records,
            "stdlib_modules": sorted(self._collect_stdlib_requirements()),
        }
        manifest_data = json.dumps(manifest, indent=2).encode()
        (staging / "MANIFEST.json").write_bytes(manifest_data)

        if artifact.exists():
            shutil.rmtree(artifact)
        staging.rename(artifact)

        logger.info("Artifact generated: %s", artifact)

    def _copy_graph_resources(self) -> int:
        """Copy resource nodes from the dependency graph into the output.

        Plugins add non-Python files (templates, static files, locale data)
        as NodeType.Resource nodes.  Those nodes are marked required by
        ``mark_reachable_required()`` so they survive tree-shaking.  This
        method copies the actual files from the project root into the
        output directory.

        For Django template resource nodes whose IDs are Django template
        paths (e.g. ``blog/post_list.html``) rather than filesystem paths,
        this method falls back to searching template directories to
        locate the actual file on disk.
        """
        from forger.core import NodeType

        resource_nodes = self.graph.nodes_by_type(NodeType.Resource)
        # Track resolved paths to avoid copying the same file twice
        resolved: set[str] = set()
        copied = 0

        # Virtual resources registered by plugins (PHILOSOPHY.md §17)
        # have no on-disk file — write their cached content directly.
        copied += self._write_virtual_resources(resolved)
        copied += self._copy_resource_nodes(resource_nodes, resolved)
        return copied

    def _write_virtual_resources(self, resolved: set[str]) -> int:
        """Emit plugin-registered virtual resources to the VFS."""
        ctx = getattr(self, "_plugin_context", None)
        if ctx is None:
            return 0
        copied = 0
        for path in ctx.virtual_resource_paths():
            content = ctx.virtual_content(path)
            if content is None:
                continue
            dest = self.output_path / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                mode = "wb" if isinstance(content, bytes) else "w"
                encoding = None if isinstance(content, bytes) else "utf-8"
                with open(dest, mode, encoding=encoding) as fh:
                    fh.write(content)
                copied += 1
                logger.debug("[compiler] Wrote virtual resource: %s", path)
            resolved.add(path)
        return copied

    def _copy_resource_nodes(
        self,
        resource_nodes: object,
        resolved: set[str],
    ) -> int:
        """Copy graph resource nodes to the VFS, skipping any already
        produced from a virtual registration."""
        copied = 0
        for node in resource_nodes:
            if not node.required:
                continue
            if node.id in resolved:
                # Already produced from a virtual registration.
                continue
            src = self._resolve_resource_path(node.id)
            if src is None:
                continue
            # Canonicalize to avoid duplicate copies
            try:
                canonical = str(src.resolve())
            except OSError:
                canonical = str(src)
            if canonical in resolved:
                continue
            resolved.add(canonical)
            dest = self.output_path / node.id
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                import shutil

                shutil.copy2(src, dest)
                copied += 1
                logger.debug("[compiler] Copied resource: %s -> %s", node.id, dest)
        return copied

    def _resolve_resource_path(self, node_id: str) -> Path | None:
        """Resolve a resource node ID to an actual file on disk.

        Resolution order:
          1. ``project_root / node_id`` (literal path).
          2. Any plugin's ``discover_resources`` hook (so a Django or
             Flask plugin can map ``blog/post_list.html`` to its real
             location).
          3. The built-in Django template-dir fallback (preserved for
             backward compatibility; deprecated — install
             ``DjangoPlugin`` instead).

        The plugin-driven path replaces the previous hardcoded Django
        search; the fallback remains so projects that didn't opt into
        a plugin still build.
        """
        # Normalize path separators
        normalized = node_id.replace("/", os.sep).replace("\\", os.sep)
        direct = self.project_root / normalized
        if direct.is_file():
            return direct

        # Normalize with original separators too (in case os.sep differ)
        direct2 = self.project_root / node_id
        if direct2.is_file():
            return direct2

        # Plugin-driven resolution (PLUGIN_ARCHITECTURE.md §10). Any
        # plugin implementing ``discover_resources`` can map a logical
        # resource id to a real path. The first non-None wins.
        runner = getattr(self, "_plugin_runner", None)
        ctx = getattr(self, "_plugin_context", None)
        if runner is not None and ctx is not None:
            try:
                discovered = runner.run_discover_resources(node_id, ctx)
            except Exception as e:
                logger.debug(
                    "discover_resources raised for %s: %s", node_id, e
                )
                discovered = None
            if discovered is not None:
                p = Path(discovered)
                if p.is_file():
                    logger.debug(
                        "[compiler] Resolved %s -> %s via plugin",
                        node_id, p,
                    )
                    return p

        # Legacy fallback: hardcoded Django template-dir search.
        # Opt-in via ``legacy_django_fallback=True``; the heuristic
        # is kept for projects that haven't migrated to a plugin yet.
        # New code should ship a DjangoPlugin and leave the flag False.
        if self.legacy_django_fallback:
            result = self._search_template_dirs(node_id)
            if result is not None:
                logger.debug(
                    "[compiler] Resolved Django template path '%s' -> %s",
                    node_id, result,
                )
                return result

        logger.debug(
            "[compiler] Resource node not found on disk: %s (tried: %s, %s)",
            node_id, direct, direct2,
        )
        return None

    def _search_template_dirs(
        self, template_path: str
    ) -> Path | None:
        """Search project template directories for a Django template file.

        Django templates use relative paths like ``app/template.html``.
        This searches:
        1. Top-level ``templates/`` directories
        2. App-level ``app/templates/app/`` directories
        """
        import os

        # Normalize to OS separators
        normalized = template_path.replace("/", os.sep).replace("\\", os.sep)

        candidate = self._search_top_level_templates(normalized)
        if candidate is not None:
            return candidate
        return self._search_app_templates(normalized)

    def _iter_subdirs(self) -> list[Path]:
        return [d for d in self.project_root.iterdir() if d.is_dir()]

    def _search_top_level_templates(self, normalized: str) -> Path | None:
        for templates_dir in self._iter_subdirs():
            name = templates_dir.name
            if name == "templates" or name.startswith("templates"):
                candidate = templates_dir / normalized
                if candidate.is_file():
                    return candidate
        return None

    def _search_app_templates(self, normalized: str) -> Path | None:
        import os

        for app_dir in self._iter_subdirs():
            app_templates = app_dir / "templates"
            if not app_templates.is_dir():
                continue
            candidate = app_templates / normalized
            if candidate.is_file():
                return candidate
            # Also search recursively within app templates
            for html_file in app_templates.rglob(os.path.basename(normalized)):
                if html_file.is_file():
                    return html_file
        return None

    def _copy_include_resources(self) -> int:
        """Copy resources matching include patterns from forger.py config."""
        import fnmatch
        import shutil

        import forger.api as api_module

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
        """Generate a diagnostic summary.

        Reports node/edge counts, Cython sources, symbol issues, and —
        for each Python module that survived tree-shaking — the edge
        that brought it in (PHILOSOPHY.md §34: "why was it included").
        """
        if not self.graph:
            return "No analysis performed yet."

        lines = self._summary_header_lines()
        lines.extend(self._summary_node_type_lines())
        lines.extend(self._summary_cython_and_symbol_lines())
        lines.extend(self._summary_provenance_lines())
        return "\n".join(lines)

    def _summary_header_lines(self) -> list[str]:
        return [
            "Compilation Summary:",
            f"  Project: {self.project_root}",
            f"  Entry Point: {self.entry_point}",
            f"  Source Files: {len(self.source_files)}",
            f"  Graph Nodes: {self.graph.node_count()}",
            f"  Graph Edges: {self.graph.edge_count()}",
        ]

    def _summary_node_type_lines(self) -> list[str]:
        from forger.core import NodeType

        lines: list[str] = []
        for node_type in [
            NodeType.PythonModule,
            NodeType.Resource,
            NodeType.ExternalPackage,
        ]:
            count = len(self.graph.nodes_by_type(node_type))
            if count:
                lines.append(f"  {node_type}: {count}")
        return lines

    def _summary_cython_and_symbol_lines(self) -> list[str]:
        lines: list[str] = []
        if self.cython_sources:
            lines.append(f"  Cython sources: {len(self.cython_sources)}")
        if getattr(self, "symbol_issues", None):
            missing = sum(1 for i in self.symbol_issues if i.kind == "missing_symbol")
            unused = sum(1 for i in self.symbol_issues if i.kind == "unused_import")
            lines.append(f"  Symbol issues: {missing} missing, {unused} unused")
            for issue in self.symbol_issues[:10]:
                rel = issue.source_file
                try:
                    rel = str(Path(issue.source_file).relative_to(self.project_root))
                except ValueError:
                    pass
                lines.append(f"    - {rel}:{issue.line} {issue.kind}: {issue.detail}")
        return lines

    def _summary_provenance_lines(self) -> list[str]:
        included = self._included_modules_with_provenance(limit=8)
        if not included:
            return []
        lines = ["  Module inclusion (sample):"]
        for mod_id, edge in included:
            if edge is not None:
                src = edge.from_node
                desc = edge.provenance.description if edge.provenance else "edge"
                lines.append(f"    - {mod_id} via {src} ({desc})")
            else:
                lines.append(f"    - {mod_id} (entry point)")
        return lines

    def _included_modules_with_provenance(
        self, *, limit: int = 8
    ) -> list[tuple[str, object | None]]:
        """Return up to ``limit`` python modules with the edge that
        brought them in (or None for the entry point itself)."""
        from forger.core import NodeType

        assert self.graph is not None
        # Pick the entry point first, then topologically-tracked modules.
        out: list[tuple[str, object | None]] = []
        ep = self.graph.get_node(self.entry_point)
        if ep is not None:
            out.append((self.entry_point, None))
        for module_id in sorted(self.graph.all_nodes()):
            if len(out) >= limit:
                break
            if module_id == self.entry_point:
                continue
            node = self.graph.get_node(module_id)
            if node is None or node.node_type != NodeType.PythonModule:
                continue
            # Pick the first inbound edge with a non-empty provenance.
            inbound = self.graph.dependents_of(module_id)
            edge = None
            for src in inbound:
                if src == module_id:
                    continue
                e = self._find_edge(src, module_id)
                if e is not None:
                    edge = e
                    break
            out.append((module_id, edge))
        return out

    def _find_edge(self, src: str, dst: str) -> object | None:
        """Return a representative edge from src→dst if one exists.

        Uses the public :meth:`DependencyGraph.edges_between` (Rust +
        Python backends). Returns the first match, or None if no
        edge exists between the two nodes.
        """
        edges = self.graph.edges_between(src, dst) if self.graph else []
        return edges[0] if edges else None

    def _discover_source_files(self) -> list[Path]:
        """Discover Python source files in the project.

        Sorted by project-relative path so downstream iteration order is
        deterministic (PHILOSOPHY.md §11).
        """
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

        # rglob ordering is filesystem-dependent; sort by project-
        # relative path for deterministic traversal downstream.
        sources.sort(key=lambda p: str(p.relative_to(self.project_root)))
        return sources

    def _mark_retained_required(self, retained_mod: str) -> None:
        """Mark a retained module and all its child modules as required.

        When a plugin retains a package (e.g., ``"blog"``), this method marks
        the package node itself as required as well as every descendant node
        (e.g., ``"blog.views"``, ``"blog.models"``). This ensures that
        retaining a top-level package survives tree-shaking for all its
        sub-modules.
        """
        if self.graph is None:
            return

        def _mark(node_id: str) -> None:
            """Use the public graph mutator when available, fall back
            to attribute write for backends that don't expose it
            (the pure-Python fallback graph)."""
            try:
                self.graph.set_node_required(node_id, True)
            except AttributeError:
                node = self.graph.get_node(node_id)
                if node is not None:
                    node.required = True

        # Mark the exact node if it exists
        if self.graph.get_node(retained_mod) is not None:
            _mark(retained_mod)

        # Mark all child modules (e.g., "blog" -> "blog.views", "blog.models")
        prefix = retained_mod + "."
        for child_id in self.graph.all_nodes():
            if child_id.startswith(prefix):
                if self.graph.get_node(child_id) is not None:
                    _mark(child_id)

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

    def _collect_stdlib_requirements(self) -> set[str]:
        """Top-level stdlib modules this project imports.

        Used by the runtime builder to prune the CPython binary: only the C
        extensions backing these modules (and their dependencies) are built.
        Falls back to scanning source files directly when no graph analysis
        ran (e.g. ``forge_from_vfs`` re-packaging).
        """
        import sys

        from forger.build.cpython_pruner import collect_stdlib_imports

        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        imported = self._get_imported_modules()
        if not imported:
            imported = self._scan_vfs_imports()
        return collect_stdlib_imports(imported, python_version=version)

    def _scan_vfs_imports(self) -> set[str]:
        """Quick import scan over Python files in the output/VFS directory."""
        from forger.analyzer.imports import ImportAnalyzer

        root = self._vfs_path if self._vfs_path.exists() else self.project_root
        analyzer = ImportAnalyzer()
        imported: set[str] = set()
        for py_file in sorted(root.rglob("*.py")):
            for info in analyzer.analyze_file(py_file):
                name = info.module.lstrip(".")
                if name:
                    imported.add(name.split(".")[0])
        return imported

    def _resolve_and_copy_venv(self, imported_modules: set[str]) -> int:
        """Resolve and copy required .venv packages using parallel worker graph."""
        resolver = VenvPackageResolver(
            project_root=self.project_root,
            output_path=self.output_path,
            imported_modules=imported_modules,
        )
        return resolver.resolve()


def _module_level_name(stmt: ast.stmt) -> str | None:
    """Return the top-level name bound by ``stmt`` (def/assign/import)."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return stmt.name
    if isinstance(stmt, ast.Assign):
        for t in stmt.targets:
            if isinstance(t, ast.Name):
                return t.id
    if isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name):
            return stmt.target.id
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return None  # import bindings — not a pruning candidate
    return None


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _classify_artifact_kind(rel_path: str) -> str:
    """Categorize an emitted file for the manifest.

    The categories are loose (python, resource, manifest, data) — enough
    for downstream tools to filter without parsing paths.
    """
    name = rel_path.rsplit("/", 1)[-1]
    if name == "MANIFEST.json":
        return "manifest"
    if name.endswith(".py"):
        return "python"
    if name.endswith((".html", ".css", ".js")):
        return "asset"
    if name.endswith((".json", ".yaml", ".yml", ".toml")):
        return "data"
    return "resource"


def _content_hash(path: Path) -> str:
    """SHA-256 hash of a file's bytes (PHILOSOPHY.md §12)."""
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
    return None
