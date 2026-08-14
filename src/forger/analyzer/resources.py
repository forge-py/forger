"""Resource discovery analysis.

Analyzes Python source for file access patterns:
open(), Path.open(), Path.read_text(), Path.read_bytes(),
glob.glob(), os.listdir(), os.scandir(), importlib.resources, etc.
"""

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ResourceAccess:
    """Information about a discovered resource access."""

    path_expr: str
    access_type: str  # "open", "read_text", "glob", "listdir", etc.
    source_file: str = ""
    line: int = 0
    is_dynamic: bool = False  # True if the path involves variables


class ResourceAnalyzer:
    """Analyze Python source files for resource access patterns."""

    # Functions that indicate file/resource access
    FILE_ACCESS_FUNCTIONS = {
        "open",
        "Path.open",
        "Path.read_text",
        "Path.read_bytes",
        "Path.write_text",
        "Path.write_bytes",
        "Path.glob",
        "Path.rglob",
        "glob.glob",
        "glob.iglob",
        "os.listdir",
        "os.scandir",
        "pkgutil.get_data",
        "importlib.resources.read_text",
        "importlib.resources.read_bytes",
        "importlib.resources.path",
        "read_text",
        "read_bytes",
    }

    # Names whose calls are file access even when qualified differently
    ACCESS_METHODS = {
        "open", "read_text", "read_bytes", "write_text", "write_bytes",
        "glob", "rglob", "listdir", "scandir", "get_data",
    }

    def __init__(self) -> None:
        self._accesses: list[ResourceAccess] = []

    @property
    def accesses(self) -> list[ResourceAccess]:
        return list(self._accesses)

    def analyze_source(self, source: str, filename: str = "<string>") -> list[ResourceAccess]:
        """Analyze Python source code string for resource access."""
        self._accesses.clear()

        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as e:
            logger.warning("Syntax error in %s: %s", filename, e)
            return []

        self._visit_tree(tree, filename)
        return list(self._accesses)

    def analyze_file(self, filepath: Path) -> list[ResourceAccess]:
        """Analyze a single Python file for resource access."""
        self._accesses.clear()

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
        return list(self._accesses)

    def _visit_tree(self, tree: ast.AST, filename: str) -> None:
        """Walk the AST and collect resource access nodes."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func_name = self._get_call_name(node.func)
            if not func_name:
                continue

            # Check if this is a known file access function
            is_known = func_name in self.FILE_ACCESS_FUNCTIONS
            # Also check if the last component is an access method
            if not is_known:
                last_part = func_name.rsplit(".", 1)[-1]
                is_known = last_part in self.ACCESS_METHODS

            if is_known:
                path_expr, is_dynamic = self._extract_path_arg(node)
                self._accesses.append(
                    ResourceAccess(
                        path_expr=path_expr if path_expr else "",
                        access_type=func_name,
                        source_file=filename,
                        line=node.lineno,
                        is_dynamic=is_dynamic,
                    )
                )

    def _get_call_name(self, node: ast.expr) -> str | None:
        """Extract the function name from a call node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value_name = self._get_call_name(node.value)
            if value_name:
                return f"{value_name}.{node.attr}"
            # Handle chained calls like Path("x").read_text()
            if isinstance(node.value, ast.Call):
                func_name = self._get_call_name(node.value.func)
                if func_name:
                    return f"{func_name}.{node.attr}"
        return None

    def _extract_path_arg(self, node: ast.Call) -> tuple[str | None, bool]:
        """Extract the path argument from a function call.

        Returns (path_string, is_dynamic).
        """
        # Direct argument: open("path"), glob.glob("path"), etc.
        if node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                return first_arg.value, False
            if isinstance(first_arg, (ast.FormattedValue, ast.JoinedStr, ast.Name, ast.BinOp)):
                return None, True
            if isinstance(first_arg, ast.Call):
                if first_arg.args and isinstance(first_arg.args[0], ast.Constant):
                    return first_arg.args[0].value, False
                return None, True

        # Constructor argument: Path("path").read_text()
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Call):
                ctor = node.func.value
                if ctor.args and isinstance(ctor.args[0], ast.Constant):
                    return ctor.args[0].value, False
                return None, True

        return None, False
