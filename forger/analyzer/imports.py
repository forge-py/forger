"""Python import analysis using AST.

Analyzes import, from...import, and relative import statements
to build the dependency graph.
"""

from __future__ import annotations

import ast
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forger.core import DependencyGraph  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


@dataclass
class ImportInfo:
    """Information about a discovered import."""

    module: str
    names: list[str] = field(default_factory=list)
    level: int = 0
    source_file: str = ""
    line: int = 0
    is_from_import: bool = False


class ImportAnalyzer:
    """Analyze Python source files for import statements."""

    def __init__(self) -> None:
        self._imports: list[ImportInfo] = []

    @property
    def imports(self) -> list[ImportInfo]:
        return list(self._imports)

    def analyze_file(self, filepath: Path) -> list[ImportInfo]:
        """Analyze a single Python file for imports."""
        self._imports.clear()

        try:
            source = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return []

        try:
            tree = ast.parse(source, filename=str(filepath))
        except SyntaxError as e:
            logger.warning("Syntax error in %s: %s", filepath, e)
            return []

        self._visit_tree(tree, str(filepath))
        return list(self._imports)

    def analyze_source(
        self, source: str, filename: str = "<string>"
    ) -> list[ImportInfo]:
        """Analyze Python source code string for imports."""
        self._imports.clear()

        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as e:
            logger.warning("Syntax error in %s: %s", filename, e)
            return []

        self._visit_tree(tree, filename)
        return list(self._imports)

    def _visit_tree(self, tree: ast.AST, filename: str) -> None:
        """Walk the AST and collect import nodes."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._imports.append(
                        ImportInfo(
                            module=alias.name,
                            source_file=filename,
                            line=node.lineno,
                            is_from_import=False,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                self._imports.append(
                    ImportInfo(
                        module=module,
                        names=names,
                        level=node.level or 0,
                        source_file=filename,
                        line=node.lineno,
                        is_from_import=True,
                    )
                )

    def contribute_to_graph(
        self,
        graph: DependencyGraph,
        source_module: str,
    ) -> None:
        """Add discovered imports to the dependency graph.

        This method bridges the Python analyzer with the Rust dependency graph.
        """
        from forger.core import (  # noqa: E402
            DependencyEdge,
            DependencyNode,
            EdgeProvenance,
            EdgeType,
            NodeType,
        )

        for imp in self._imports:
            # Resolve the target module name
            target = self._resolve_target(imp, source_module)
            if not target:
                continue

            # Ensure target node exists
            if not graph.get_node(target):
                graph.add_node(
                    DependencyNode.new(target, NodeType.PythonModule).with_metadata(
                        "discovered_by", "import_analyzer"
                    )
                )

            # Add edge
            edge_type = (
                EdgeType.FromImport
                if imp.is_from_import
                else EdgeType.Import
            )
            if imp.level > 0:
                edge_type = EdgeType.RelativeImport

            graph.add_edge(
                DependencyEdge.new(
                    source_module,
                    target,
                    edge_type,
                    EdgeProvenance(
                        source=(imp.source_file, imp.line),
                        discovered_by="static_import_analyzer",
                        description=f"{'from ... import' if imp.is_from_import else 'import'} {imp.module}",
                    ),
                )
            )

    def _resolve_target(self, imp: ImportInfo, source_module: str) -> str | None:
        """Resolve an import to a fully qualified module name."""
        if imp.level == 0:
            # Absolute import
            if imp.is_from_import and imp.names:
                return f"{imp.module}.{imp.names[0]}" if imp.module else imp.names[0]
            return imp.module

        # Relative import
        if not source_module:
            return None

        parts = source_module.split(".")
        if imp.level > len(parts):
            return None

        base = ".".join(parts[: len(parts) - imp.level + 1])
        if imp.module:
            return f"{base}.{imp.module}"
        return base
