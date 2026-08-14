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
        self.graph: DependencyGraph | None = None  # type: ignore[name-defined]
        self.source_files: list[Path] = []
        self._optimizer_context: OptimizerContext | None = None

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

        # Get the context
        context = api_module.get_context()
        logger.info(
            "forger.py contributed: %d paths, %d globs, %d modules, %d resources",
            len(context.included_paths),
            len(context.included_globs),
            len(context.included_modules),
            len(context.included_resources),
        )

        # Add contributed items to the graph
        from forger.core import DependencyNode, NodeType

        for module_name in context.included_modules:
            if self.graph and not self.graph.get_node(module_name):
                self.graph.add_node(
                    DependencyNode.new(module_name, NodeType.PythonModule).with_metadata(
                        "source", "forger.py"
                    )
                )

        for path in context.included_paths:
            if self.graph:
                self.graph.add_node(
                    DependencyNode.new(str(path), NodeType.Resource).with_metadata(
                        "source", "forger.py"
                    )
                )

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

    def generate_artifact(self) -> None:
        """Generate the .forge artifact."""
        if not self.graph:
            raise RuntimeError("Analysis not yet performed")

        logger.info("Generating artifact: %s", self.output_path)
        logger.info(
            "Graph: %d nodes, %d edges",
            self.graph.node_count(),
            self.graph.edge_count(),
        )

        # Mark reachable nodes
        self.graph.mark_reachable_required()

        # TODO: Serialize to .forge format
        # This will use the Rust core forge module when available
        logger.info("Artifact generation placeholder — Rust core integration pending")

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
