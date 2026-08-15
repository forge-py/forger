"""Tests for the StripCommentsDocstrings optimizer pass."""

from __future__ import annotations

import pytest
from forger.optimizer.strip import (
    _strip_comments,
    _strip_docstrings,
    strip_comments_and_docstrings,
    StripCommentsDocstrings,
    optimize_node_content,
    optimize_graph_nodes,
)
from forger.core import DependencyNode, NodeType


# ---------------------------------------------------------------------------
# Docstring stripping tests
# ---------------------------------------------------------------------------


class TestStripDocstrings:
    def test_module_docstring_removed(self) -> None:
        source = '''"""Module docstring."""
x = 1
'''
        result = _strip_docstrings(source)
        assert '"""Module docstring."""' not in result
        assert "x = 1" in result

    def test_class_docstring_removed(self) -> None:
        source = '''class Foo:
    """Class docstring."""
    def bar(self):
        pass
'''
        result = _strip_docstrings(source)
        assert '"""Class docstring."""' not in result
        assert "class Foo" in result
        assert "def bar" in result

    def test_function_docstring_removed(self) -> None:
        source = '''def foo():
    """Function docstring."""
    return 42
'''
        result = _strip_docstrings(source)
        assert '"""Function docstring."""' not in result
        assert "return 42" in result

    def test_nested_docstrings_removed(self) -> None:
        source = '''"""Module doc."""
def outer():
    """Outer doc."""
    class Inner:
        """Inner doc."""
        pass
    return Inner
'''
        result = _strip_docstrings(source)
        assert '"""Module doc."""' not in result
        assert '"""Outer doc."""' not in result
        assert '"""Inner doc."""' not in result
        assert "return Inner" in result

    def test_regular_strings_preserved(self) -> None:
        source = '''x = "hello"
y = 'world'
print("test")
'''
        result = _strip_docstrings(source)
        # ast.unparse normalizes quotes — check that string values survive
        assert "'hello'" in result or '"hello"' in result
        assert "'world'" in result or '"world"' in result
        assert "'test'" in result or '"test"' in result

    def test_syntax_error_returns_original(self) -> None:
        source = "def foo(:"
        result = _strip_docstrings(source)
        assert result == source

    def test_no_docstrings_no_change(self) -> None:
        source = "x = 1\ny = 2\n"
        result = _strip_docstrings(source)
        assert "x = 1" in result
        assert "y = 2" in result


# ---------------------------------------------------------------------------
# Comment stripping tests
# ---------------------------------------------------------------------------


class TestStripComments:
    def test_inline_comment_removed(self) -> None:
        source = "x = 1  # comment\n"
        result = _strip_comments(source)
        assert "# comment" not in result
        assert "x" in result and "=" in result and "1" in result

    def test_standalone_comment_removed(self) -> None:
        source = "# This is a comment\nx = 1\n"
        result = _strip_comments(source)
        assert "# This is a comment" not in result
        assert "x" in result and "=" in result and "1" in result

    def test_hash_in_string_preserved(self) -> None:
        source = 'x = "has # hash"\n'
        result = _strip_comments(source)
        assert '"has # hash"' in result

    def test_syntax_error_returns_original(self) -> None:
        source = "def foo(:"
        result = _strip_comments(source)
        assert result == source


# ---------------------------------------------------------------------------
# Combined strip tests
# ---------------------------------------------------------------------------


class TestCombinedStrip:
    def test_both_removed(self) -> None:
        source = '''"""Module doc."""
# A comment
x = 1  # inline comment
def foo():
    """Docstring."""
    return 42
'''
        result = strip_comments_and_docstrings(source)
        assert '"""Module doc."""' not in result
        assert "# A comment" not in result
        assert "# inline comment" not in result
        assert '"""Docstring."""' not in result
        assert "x" in result and "=" in result and "1" in result
        assert "return" in result and "42" in result

    def test_empty_string(self) -> None:
        result = strip_comments_and_docstrings("")
        assert result == ""

    def test_nothing_to_strip(self) -> None:
        source = "x = 1\ny = 2\n"
        result = strip_comments_and_docstrings(source)
        assert "x" in result and "=" in result and "1" in result
        assert "y" in result and "=" in result and "2" in result


# ---------------------------------------------------------------------------
# Plugin integration tests
# ---------------------------------------------------------------------------


class TestStripCommentsDocstringsPlugin:
    def test_diagnostic_summary(self) -> None:
        plugin = StripCommentsDocstrings()
        summary = plugin.diagnostic_summary()
        assert "StripCommentsDocstrings" in summary
        assert "0 nodes processed" in summary

    def test_init_defaults(self) -> None:
        plugin = StripCommentsDocstrings()
        assert plugin.strip_comments is True
        assert plugin.strip_docstrings is True

    def test_init_custom(self) -> None:
        plugin = StripCommentsDocstrings(strip_comments=False, strip_docstrings=True)
        assert plugin.strip_comments is False
        assert plugin.strip_docstrings is True


# ---------------------------------------------------------------------------
# Standalone function tests
# ---------------------------------------------------------------------------


class TestOptimizeNodeContent:
    def test_node_content_stripped(self) -> None:
        node = DependencyNode.new("test.py", NodeType.PythonModule)
        node.set_content('"""doc"""\n# comment\nx = 1\n')
        optimize_node_content(node)
        content = node.get_content()
        assert '"""doc"""' not in content
        assert "# comment" not in content
        assert "x" in content and "=" in content and "1" in content

    def test_node_no_content(self) -> None:
        node = DependencyNode.new("test.py", NodeType.PythonModule)
        optimize_node_content(node)  # should not raise


class TestOptimizeGraphNodes:
    def test_batch_optimize(self) -> None:
        nodes = [
            DependencyNode.new("a.py", NodeType.PythonModule),
            DependencyNode.new("b.py", NodeType.PythonModule),
        ]
        nodes[0].set_content('"""a""" # c\nx = 1\n')
        nodes[0].required = True
        nodes[1].set_content('"""b""" # c\ny = 2\n')
        nodes[1].required = True

        processed, saved = optimize_graph_nodes(iter(nodes), required_only=True)
        assert processed == 2
        assert saved > 0

    def test_required_only(self) -> None:
        nodes = [
            DependencyNode.new("a.py", NodeType.PythonModule),
            DependencyNode.new("b.py", NodeType.PythonModule),
        ]
        nodes[0].set_content('x = 1\n')
        nodes[0].required = True
        nodes[1].set_content('y = 2\n')
        nodes[1].required = False

        processed, _ = optimize_graph_nodes(iter(nodes), required_only=True)
        assert processed == 1
