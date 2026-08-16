"""StripCommentsDocstrings optimizer pass.

LLVM/GCC-style optimizer that removes comments and docstrings from
Python source code in the dependency graph nodes. This runs as a
post-pass after framework plugins have completed their analysis.
"""

from __future__ import annotations

import ast
import logging
import tokenize
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from forger.core import DependencyNode
    from forger.optimizer import BasePlugin, PluginContext

logger = logging.getLogger(__name__)

from forger.optimizer import BasePlugin


# ---------------------------------------------------------------------------
# AST-based docstring removal
# ---------------------------------------------------------------------------


def _strip_docstrings(source: str) -> str:
    """Remove docstrings from Python source using AST manipulation.

    Uses ast.parse to find docstring nodes (ast.Expr with Constant(str)
    as the first statement in a module/class/function), removes them,
    and rebuilds the source with ast.unparse().
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    # Remove docstrings from the AST in-place
    _remove_body_docstrings(tree)

    try:
        return ast.unparse(tree)
    except Exception:
        return source


def _remove_body_docstrings(node: ast.AST) -> None:
    """Recursively remove the leading docstring from every body in the AST."""
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        # Strip leading docstring from this body
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body[0:1] = []
    # Recurse into all child nodes
    for child in ast.iter_child_nodes(node):
        _remove_body_docstrings(child)


# ---------------------------------------------------------------------------
# Tokenizer-based comment removal
# ---------------------------------------------------------------------------


def _strip_comments(source: str) -> str:
    """Remove comments from Python source using the tokenize module.

    Reads tokens, keeps everything except COMMENT tokens, and rebuilds
    the source. This preserves string literals, code structure, and
    whitespace.
    """
    try:
        lines = source.splitlines(keepends=True)
        result: list[str] = []
        prev_line = 1
        prev_col = 0

        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                # Skip the comment but update position past it
                prev_line = tok.end[0]
                prev_col = tok.end[1]
                continue

            start_line = tok.start[0]
            start_col = tok.start[1]

            # Append characters from (prev_line, prev_col) to (start_line, start_col)
            result.append(_slice_between(lines, prev_line, prev_col, start_line, start_col))

            # Append the token text itself
            result.append(tok.string)

            prev_line = tok.end[0]
            prev_col = tok.end[1]

        # Append remaining content after the last token
        result.append(_slice_from(lines, prev_line, prev_col))

        return "".join(result)

    except (tokenize.TokenError, SyntaxError, IndexError, ValueError):
        return source


def _slice_between(lines: list[str], line1: int, col1: int, line2: int, col2: int) -> str:
    """Extract a substring between two (line, col) points.

    Lines are 1-based, cols are 0-based (tokenizer convention).
    """
    if line1 == line2:
        idx = line1 - 1
        if idx < len(lines):
            return lines[idx][col1:col2]
        return ""
    parts: list[str] = []
    for i in range(line1 - 1, line2 - 1):
        if i >= len(lines):
            break
        line_text = lines[i]
        start = col1 if i == line1 - 1 else 0
        end = col2 if i == line2 - 1 else len(line_text)
        parts.append(line_text[start:end])
    return "".join(parts)


def _slice_from(lines: list[str], line: int, col: int) -> str:
    """Extract substring from (line, col) to end of source."""
    parts: list[str] = []
    for i in range(line - 1, len(lines)):
        lt = lines[i]
        start = col if i == line - 1 else 0
        parts.append(lt[start:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Combined strip pass
# ---------------------------------------------------------------------------


def strip_comments_and_docstrings(source: str) -> str:
    """Remove both comments and docstrings from Python source code.

    Order matters: strip docstrings first (AST-based), then comments
    (tokenizer-based). This ensures docstring removal doesn't leave
    behind comment-like artifacts.
    """
    # Step 1: Remove docstrings via AST
    no_docstrings = _strip_docstrings(source)

    # Step 2: Remove comments via tokenizer
    stripped = _strip_comments(no_docstrings)

    return stripped


# ---------------------------------------------------------------------------
# Plugin integration
# ---------------------------------------------------------------------------


class StripCommentsDocstrings(BasePlugin):
    """LLVM/GCC-style optimizer pass that strips comments and docstrings.

    This runs as a POST-order plugin, after framework plugins have
    completed their analysis. It transforms node.content in-place
    for all required Python module nodes.

    Usage:
        ```python
        from forger.optimizer.strip import StripCommentsDocstrings

        plugins = [DjangoPlugin(), StripCommentsDocstrings()]
        ```
    """

    name = "strip_comments_docstrings"
    enforce = "post"

    def __init__(
        self,
        *,
        strip_comments: bool = True,
        strip_docstrings: bool = True,
    ) -> None:
        """Initialize the strip optimizer.

        Args:
            strip_comments: Whether to remove comments.
            strip_docstrings: Whether to remove docstrings.
        """
        self.strip_comments = strip_comments
        self.strip_docstrings = strip_docstrings
        self._stripped_count: int = 0
        self._bytes_saved: int = 0

    def transform_ast(
        self,
        module_id: str,
        tree: ast.AST,
        *,
        context: PluginContext,
    ) -> None:
        """Transform a single module's AST to remove docstrings.

        This hook is called by PluginRunner for each Python node.
        """
        node = context.graph.get_node(module_id)
        if node is None:
            return

        source = node.get_content()
        if source is None:
            return

        original_len = len(source)

        if self.strip_docstrings:
            source = _strip_docstrings(source)

        if self.strip_comments:
            source = _strip_comments(source)

        node.set_content(source)
        self._stripped_count += 1
        self._bytes_saved += original_len - len(source)

    def after_shake(self, *, context: PluginContext) -> None:
        """Apply stripping pass to all required Python nodes.

        This runs after tree-shaking, so only reachable nodes are
        processed. The graph is mutated in-place.
        """
        from forger.core import NodeType

        python_nodes = context.graph.nodes_by_type(NodeType.PythonModule)
        processed = 0
        bytes_saved = 0

        for node in python_nodes:
            if not node.required or node.get_content() is None:
                continue

            stripped = node.get_content()
            if stripped is None:
                continue
            original_len = len(stripped)

            if self.strip_docstrings:
                stripped = _strip_docstrings(stripped)

            if self.strip_comments:
                stripped = _strip_comments(stripped)

            node.set_content(stripped)
            processed += 1
            bytes_saved += original_len - len(stripped)

        logger.info(
            "[strip] Processed %d nodes, saved %d bytes",
            processed,
            bytes_saved,
        )
        self._stripped_count = processed
        self._bytes_saved = bytes_saved

    def diagnostic_summary(self) -> str:
        """Return a summary of the strip pass results."""
        return (
            f"StripCommentsDocstrings: "
            f"{self._stripped_count} nodes processed, "
            f"{self._bytes_saved} bytes saved"
        )


# ---------------------------------------------------------------------------
# Standalone functions for direct use
# ---------------------------------------------------------------------------


def optimize_node_content(node: DependencyNode) -> None:
    """Strip comments and docstrings from a single node's content in-place."""
    source = node.get_content()
    if source is None:
        return
    node.set_content(strip_comments_and_docstrings(source))


def optimize_graph_nodes(
    nodes: Iterable[DependencyNode],
    *,
    required_only: bool = True,
) -> tuple[int, int]:
    """Strip comments and docstrings from all nodes.

    Args:
        nodes: Iterable of DependencyNode to process.
        required_only: If True, only process nodes marked as required.

    Returns:
        Tuple of (nodes_processed, bytes_saved).
    """
    processed = 0
    bytes_saved = 0

    for node in nodes:
        if required_only and not node.required:
            continue
        source = node.get_content()
        if source is None:
            continue

        original_len = len(source)
        node.set_content(strip_comments_and_docstrings(source))
        processed += 1
        bytes_saved += original_len - len(node.get_content() or "")

    return processed, bytes_saved
