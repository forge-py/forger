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
    # Statically resolvable path segments relative to the accessing file's
    # directory, e.g. ["data", "users.csv"] for
    # ``Path(__file__).parent / "data" / "users.csv"``. None when not
    # statically resolvable.
    segments: list[str] | None = None


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
        # One-step constant propagation: name -> path expression it was
        # bound to (e.g. ``p = Path(__file__).parent / "x"``).
        bindings: dict[str, ast.expr] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Name) and isinstance(node.value, (ast.BinOp, ast.Call)):
                    bindings[t.id] = node.value

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
                segments = self._static_segments(node, bindings)
                self._accesses.append(
                    ResourceAccess(
                        path_expr=path_expr if path_expr else "",
                        access_type=func_name,
                        source_file=filename,
                        line=node.lineno,
                        is_dynamic=is_dynamic,
                        segments=segments,
                    )
                )

    def _static_segments(
        self, node: ast.Call, bindings: dict[str, ast.expr]
    ) -> list[str] | None:
        """Extract statically resolvable relative segments from a call.

        Recognizes the ``Path(__file__).parent / "a" / "b"`` family,
        including chains first bound to a local variable. Returns None
        when any segment is dynamic.
        """
        arg: ast.expr | None
        if node.args:
            arg = node.args[0]
        elif isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if isinstance(receiver, ast.Name):
                arg = bindings.get(receiver.id)
            elif isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Call):
                arg = receiver.value  # Path("x").read_text() form
            else:
                arg = receiver
        else:
            arg = None
        if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Call):
            # Path("x").read_text() — value is Path(...) ctor call.
            arg = arg.value
        return _segments_from_expr(arg)

    @staticmethod
    def _string_constant(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

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
                    return str(first_arg.args[0].value), False
                return None, True

        # Constructor argument: Path("path").read_text()
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Call):
                ctor = node.func.value
                if ctor.args and isinstance(ctor.args[0], ast.Constant):
                    return str(ctor.args[0].value), False
                return None, True

        return None, False


def _segments_from_expr(expr: ast.expr | None) -> list[str] | None:
    """Resolve a path expression to static relative segments.

    Handles ``Path(__file__).parent / "a" / "b"`` chains. Returns None
    when the expression is not fully static.
    """
    if expr is None:
        return None

    # Unwrap the BinOp chain into leaves (right-nested or left-nested).
    leaves: list[ast.expr] = []

    def flatten(e: ast.expr) -> None:
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
            flatten(e.left)
            flatten(e.right)
        else:
            leaves.append(e)

    flatten(expr)

    segments: list[str] = []
    for leaf in leaves:
        if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
            segments.append(leaf.value)
            continue
        if _is_file_dir_root(leaf):
            continue  # anchor — contributes no segment
        return None  # anything dynamic makes the whole path unknown
    return segments or None


def _is_file_dir_root(node: ast.expr) -> bool:
    """True for Path(__file__), __file__, .parent/.parents/cwd anchors."""
    if isinstance(node, ast.Name) and node.id == "__file__":
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
        # Path(__file__).parents[N] — N is an integer literal parent hops.
        if node.value.attr == "parents" and isinstance(node.slice, ast.Constant):
            return isinstance(node.slice.value, int)
        return False
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "Path"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "__file__"
        ):
            return True
        if isinstance(func, ast.Attribute) and func.attr in ("parent", "parents", "cwd", "resolve"):
            return _is_file_dir_root(func.value)
    if isinstance(node, ast.Attribute) and node.attr in ("parent", "parents"):
        return _is_file_dir_root(node.value)
    return False
