"""Resource reference analyzer.

Detects filesystem resource references from reachable Python code.
Identifies resource access patterns like open(), Path.read_text(), etc.

Resource references participate in the same unified dependency graph
as Python symbols — they are not a separate copy mechanism.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------


class Resolution:
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    DYNAMIC = "dynamic"


@dataclass
class ResourceRef:
    """A resolved resource reference."""

    path: str
    resolution: str  # Resolution.RESOLVED | UNKNOWN | DYNAMIC
    source: str      # module_id where the reference was found
    context: str     # human-readable description


# ---------------------------------------------------------------------------
# ResourceResolver (extensible)
# ---------------------------------------------------------------------------


class ResourceResolver:
    """Extensible resource path resolver."""

    def can_resolve(self, node: ast.AST) -> bool:
        raise NotImplementedError

    def resolve(self, node: ast.AST, context: dict) -> ResourceRef | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Built-in: open() call resolver
# ---------------------------------------------------------------------------


class OpenCallResolver(ResourceResolver):
    """Resolve resource references from open() calls."""

    def can_resolve(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "open":
            return True
        return False

    def resolve(self, node: ast.AST, context: dict) -> ResourceRef | None:
        if not isinstance(node, ast.Call):
            return None
        if not node.args:
            return None
        first_arg = node.args[0]
        path = _extract_string_value(first_arg)
        if path is None:
            return ResourceRef(
                path="",
                resolution=Resolution.DYNAMIC,
                source=context.get("module_id", ""),
                context="open() with dynamic path",
            )
        return ResourceRef(
            path=path,
            resolution=Resolution.RESOLVED,
            source=context.get("module_id", ""),
            context=f'open("{path}")',
        )


# ---------------------------------------------------------------------------
# Built-in: pathlib resolver
# ---------------------------------------------------------------------------


class PathlibResolver(ResourceResolver):
    """Resolve resource references from pathlib.Path calls and methods."""

    PATH_METHODS = {"read_text", "read_bytes", "open", "exists", "stat"}

    def can_resolve(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in self.PATH_METHODS:
                    return True
            if isinstance(func, ast.Name) and func.id == "Path":
                return True
        return False

    def resolve(self, node: ast.AST, context: dict) -> ResourceRef | None:
        if not isinstance(node, ast.Call):
            return None
        func = node.func
        if isinstance(func, ast.Attribute):
            path = _extract_string_value(func.value)
            if path is not None:
                return ResourceRef(
                    path=path,
                    resolution=Resolution.RESOLVED,
                    source=context.get("module_id", ""),
                    context=f'Path("{path}").{func.attr}()',
                )
            if isinstance(func.value, ast.BinOp):
                resolved = _resolve_file_relative(func.value, context)
                if resolved is not None:
                    return ResourceRef(
                        path=resolved,
                        resolution=Resolution.RESOLVED,
                        source=context.get("module_id", ""),
                        context=f"Path(__file__) relative .{func.attr}()",
                    )
            return ResourceRef(
                path="",
                resolution=Resolution.DYNAMIC,
                source=context.get("module_id", ""),
                context=f"Path().{func.attr}() with dynamic path",
            )
        if isinstance(func, ast.Name) and func.id == "Path":
            if node.args:
                path = _extract_string_value(node.args[0])
                if path is not None:
                    return ResourceRef(
                        path=path,
                        resolution=Resolution.RESOLVED,
                        source=context.get("module_id", ""),
                        context=f'Path("{path}")',
                    )
        return None


# ---------------------------------------------------------------------------
# Resource reference analyzer
# ---------------------------------------------------------------------------


@dataclass
class ResourceAnalysisResult:
    """Result of resource reference analysis."""

    resources: list[ResourceRef] = field(default_factory=list)
    dynamic_refs: list[ResourceRef] = field(default_factory=list)


class ResourceReferenceAnalyzer:
    """Analyze Python AST for resource references.

    Walks the AST and identifies file/resource access patterns.
    Uses pluggable ResourceResolver instances.

    Resource references participate in the same unified dependency graph
    as Python symbols, not a separate asset system.
    """

    def __init__(self) -> None:
        self.resolvers: list[ResourceResolver] = [
            OpenCallResolver(),
            PathlibResolver(),
        ]

    def add_resolver(self, resolver: ResourceResolver) -> None:
        """Add a custom resource resolver."""
        self.resolvers.append(resolver)

    def analyze(
        self,
        tree: ast.AST,
        module_id: str,
    ) -> ResourceAnalysisResult:
        """Analyze a module AST for resource references."""
        result = ResourceAnalysisResult()
        context = {"module_id": module_id}

        for node in ast.walk(tree):
            for resolver in self.resolvers:
                if resolver.can_resolve(node):
                    ref = resolver.resolve(node, context)
                    if ref is not None:
                        if ref.resolution == Resolution.DYNAMIC:
                            result.dynamic_refs.append(ref)
                        else:
                            result.resources.append(ref)

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_string_value(node: ast.AST) -> str | None:
    """Extract a constant string value from an AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _extract_fstring_value(node)
    return None


def _extract_fstring_value(node: ast.JoinedStr) -> str | None:
    """Try to extract a fully static f-string value."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            return None
    return "".join(parts) if parts else None


def _resolve_file_relative(node: ast.BinOp, context: dict) -> str | None:
    """Resolve Path(__file__) / ... expressions."""
    if not isinstance(node.left, ast.Attribute):
        return None
    if node.left.attr != "parent":
        return None
    if not isinstance(node.left.value, ast.Call):
        return None
    segments = _extract_path_segments(node.right)
    if segments:
        return "/".join(segments)
    return None


def _extract_path_segments(node: ast.AST) -> list[str]:
    """Extract path segments from a chained / operation."""
    segments: list[str] = []
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.FloorDiv):
            segments.extend(_extract_path_segments(node.left))
            segments.extend(_extract_path_segments(node.right))
        else:
            return []
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        segments.append(node.value)
    else:
        return []
    return segments
