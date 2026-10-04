"""Python import analysis using AST.

Analyzes import, from...import, and relative import statements
to build the dependency graph.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
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

    def analyze_source(self, source: str, filename: str = "<string>") -> list[ImportInfo]:
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
                level = node.level or 0
                module = node.module or ""
                # Prefix dots for relative imports that have a module part
                if level > 0 and module:
                    module = "." * level + module
                names = [alias.name for alias in node.names]
                self._imports.append(
                    ImportInfo(
                        module=module,
                        names=names,
                        level=level,
                        source_file=filename,
                        line=node.lineno,
                        is_from_import=True,
                    )
                )

    def contribute_to_graph(
        self,
        graph: DependencyGraph,
        source_module: str,
        *,
        is_package: bool = False,
        plugin_resolver: Callable[[str, str | None], str | None] | None = None,
    ) -> None:
        """Add discovered imports to the dependency graph.

        This method bridges the Python analyzer with the Rust dependency graph.
        ``is_package`` marks the source as a package ``__init__`` module so
        level-1 relative imports resolve inside the package (e.g.
        ``from .core import X`` in ``shop/__init__.py`` -> ``shop.core``).

        ``plugin_resolver`` lets plugins override string-based
        resolution (e.g. for ``virtual:foo`` specifiers). When given, the
        resolver is consulted with the raw specifier; its return value
        (a module id) replaces the target. Returning None falls through
        to the standard string-based resolution.
        """
        from forger.core import (  # noqa: E402
            DependencyEdge,
            DependencyNode,
            EdgeProvenance,
            EdgeType,
            NodeType,
        )

        for imp in self._imports:
            targets = self._resolve_targets(
                imp,
                source_module,
                is_package=is_package,
                plugin_resolver=plugin_resolver,
            )
            if not targets:
                continue

            edge_type = EdgeType.FromImport if imp.is_from_import else EdgeType.Import
            if imp.level > 0:
                edge_type = EdgeType.RelativeImport

            for target in targets:
                # Ensure target node exists
                if not graph.get_node(target):
                    graph.add_node(
                        DependencyNode.new(target, NodeType.PythonModule).with_metadata(
                            "discovered_by", "import_analyzer"
                        )
                    )

                # Add edge
                graph.add_edge(
                    DependencyEdge.new(
                        source_module,
                        target,
                        edge_type,
                        EdgeProvenance(
                            source=(imp.source_file, imp.line),
                            discovered_by="static_import_analyzer",
                            description=(
                                f"{'from ... import' if imp.is_from_import else 'import'} "
                                f"{imp.module}"
                            ),
                        ),
                    )
                )

    def _resolve_targets(
        self,
        imp: ImportInfo,
        source_module: str,
        *,
        is_package: bool = False,
        plugin_resolver: Callable[[str, str | None], str | None] | None = None,
    ) -> list[str]:
        """Resolve an import to fully qualified module name(s).

        Returns a list of target module names. For ``from X import Y`` this
        returns both ``X`` and ``X.Y`` so that the package node is also
        marked reachable.

        ``plugin_resolver`` (when given) is consulted first with the
        absolute specifier (e.g. ``"virtual:config"``); its return value
        replaces the analyzer's string-based result. This is the seam
        for the plugin ``resolve_id`` hook (PLUGIN_ARCHITECTURE.md §4).
        """
        # Plugin hook: let plugins handle unusual specifiers first.
        if plugin_resolver is not None and imp.module and imp.level == 0:
            resolved = self._resolve_via_plugin(plugin_resolver, imp, source_module)
            if resolved:
                return [resolved]

        if imp.level == 0:
            return self._resolve_absolute(imp)
        return self._resolve_relative(imp, source_module, is_package)

    @staticmethod
    def _resolve_via_plugin(
        plugin_resolver: Callable[[str, str | None], str | None],
        imp: ImportInfo,
        source_module: str,
    ) -> str | None:
        """Call the plugin resolver with the specifier; tolerate failures."""
        specifier = imp.module if imp.is_from_import else imp.module.split(".")[0]
        try:
            return plugin_resolver(specifier, source_module or None)
        except Exception:  # noqa: BLE001 - plugins may be missing
            return None

    @staticmethod
    def _resolve_absolute(imp: ImportInfo) -> list[str]:
        """Resolve an absolute ``import X`` / ``from X import Y`` target."""
        if imp.is_from_import and imp.names:
            base = imp.module
            targets: list[str] = [base] if base else []
            if base:
                targets.extend(f"{base}.{n}" for n in imp.names)
            return targets
        return [imp.module] if imp.module else []

    @staticmethod
    def _resolve_relative(
        imp: ImportInfo,
        source_module: str,
        is_package: bool,
    ) -> list[str]:
        """Resolve a level>0 relative import against the importer module."""
        if not source_module:
            return []

        parts = source_module.split(".")
        # A package __init__ module resolves level-1 relative imports one
        # level *inside* the package: `from .core import X` in
        # shop/__init__.py means shop.core. Python semantics: the package
        # itself counts as its own base for `from . import ...`.
        effective_parts = [*parts, ""] if is_package else parts
        if imp.level > len(effective_parts):
            return []

        base = ".".join(
            effective_parts[: len(effective_parts) - imp.level]
        ).rstrip(".")
        # _visit_tree stores relative targets dot-prefixed ("." * level +
        # module); strip them — depth is already carried by imp.level.
        module_part = (imp.module or "").lstrip(".")
        if module_part:
            targets = (
                [base, f"{base}.{module_part}"] if base else [f"{base}.{module_part}"]
            )
        else:
            targets = [base] if base else []
        # `from pkg import name`: also link pkg.name so submodules survive
        # tree-shaking (mirrors the absolute-import handling above).
        if imp.is_from_import and base:
            targets.extend(f"{base}.{n}" for n in imp.names if n != "*")
        return [t for t in targets if t]

    # Backward-compatible alias: return most specific target
    def _resolve_target(self, imp: ImportInfo, source_module: str) -> str | None:
        targets = self._resolve_targets(imp, source_module)
        # Return the last (most specific) target for backward compatibility
        return targets[-1] if targets else None
