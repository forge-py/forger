"""Dynamic import analysis.

Detects dynamic import patterns that static analysis alone cannot resolve:
importlib.import_module(), __import__(), getattr() on modules,
sys.modules access, and exec/eval that may trigger imports.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DynamicImportHint:
    """Hint about a potentially dynamically imported module."""

    pattern: str
    hint_type: str  # "import_module", "__import__", "sys_modules", "exec", "eval"
    source_file: str = ""
    line: int = 0
    context: str = ""


class DynamicImportAnalyzer:
    """Analyze Python source for dynamic import patterns."""

    def __init__(self) -> None:
        self._hints: list[DynamicImportHint] = []

    @property
    def hints(self) -> list[DynamicImportHint]:
        return list(self._hints)

    def analyze_source(self, source: str, filename: str = "<string>") -> list[DynamicImportHint]:
        """Analyze Python source code string for dynamic import patterns."""
        self._hints.clear()

        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as e:
            logger.warning("Syntax error in %s: %s", filename, e)
            return []

        self._visit_tree(tree, filename, source)
        return list(self._hints)

    def analyze_file(self, filepath: Path) -> list[DynamicImportHint]:
        """Analyze a single file for dynamic import patterns."""
        self._hints.clear()

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

        self._visit_tree(tree, str(filepath), source)
        return list(self._hints)

    def _visit_tree(self, tree: ast.AST, filename: str, source: str) -> None:
        """Walk the AST looking for dynamic import patterns."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self._check_call(node, filename, source)
            elif isinstance(node, ast.Subscript):
                self._check_sys_modules(node, filename, source)

    def _check_call(self, node: ast.Call, filename: str, source: str) -> None:
        """Check a function call for dynamic import patterns."""
        func_name = self._get_func_name(node.func)
        if not func_name:
            return

        # importlib.import_module("...")
        if func_name in ("importlib.import_module", "import_module"):
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self._hints.append(
                        DynamicImportHint(
                            pattern=arg.value,
                            hint_type="import_module",
                            source_file=filename,
                            line=node.lineno,
                        )
                    )
                else:
                    # Dynamic argument
                    self._hints.append(
                        DynamicImportHint(
                            pattern="*",
                            hint_type="import_module",
                            source_file=filename,
                            line=node.lineno,
                            context="dynamic argument",
                        )
                    )

        # __import__("...")
        elif func_name == "__import__":
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self._hints.append(
                        DynamicImportHint(
                            pattern=arg.value,
                            hint_type="__import__",
                            source_file=filename,
                            line=node.lineno,
                        )
                    )

        # exec() or eval() — broad hint
        elif func_name in ("exec", "eval"):
            self._hints.append(
                DynamicImportHint(
                    pattern="*",
                    hint_type=func_name,
                    source_file=filename,
                    line=node.lineno,
                    context="exec/eval may trigger dynamic imports",
                )
            )

    def _check_sys_modules(self, node: ast.Subscript, filename: str, source: str) -> None:
        """Check for sys.modules["..."] access."""
        if isinstance(node.value, ast.Subscript):
            outer_name = self._get_name(node.value.value)
            if outer_name == "sys":
                # Could be sys.modules[key]
                pass
        elif isinstance(node.value, ast.Attribute):
            if (
                isinstance(node.value.value, ast.Name)
                and node.value.value.id == "sys"
                and node.value.attr == "modules"
            ):
                # sys.modules["..."]
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    self._hints.append(
                        DynamicImportHint(
                            pattern=node.slice.value,
                            hint_type="sys_modules",
                            source_file=filename,
                            line=node.lineno,
                        )
                    )

    def _get_func_name(self, node: ast.expr) -> str | None:
        """Extract function name from a call target."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value_name = self._get_func_name(node.value)
            if value_name:
                return f"{value_name}.{node.attr}"
        return None

    def _get_name(self, node: ast.expr) -> str | None:
        """Extract a simple name from an expression."""
        if isinstance(node, ast.Name):
            return node.id
        return None

    def extract_module_patterns(self, source_dir: Path) -> list[str]:
        """Scan a directory for common dynamic import patterns.

        Looks for things like:
        - Plugin discovery via importlib
        - Configuration-driven imports
        - Entry point loading
        """
        patterns: list[str] = []

        # Check for setup.py / pyproject.toml entry points
        for ep_file in [source_dir / "setup.py", source_dir / "pyproject.toml"]:
            if ep_file.exists():
                patterns.extend(self._extract_entry_points(ep_file))

        return patterns

    def _extract_entry_points(self, filepath: Path) -> list[str]:
        """Extract entry point module references from config files."""
        patterns: list[str] = []

        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return patterns

        if filepath.suffix == ".toml":
            patterns.extend(self._extract_entry_points_toml(filepath, content))
        else:
            patterns.extend(self._extract_entry_points_ast(content))

        return patterns

    def _extract_entry_points_ast(self, content: str) -> list[str]:
        """Extract entry points from setup.py using AST."""
        patterns: list[str] = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return patterns

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Look for setup(entry_points={...}) calls
            for kw in node.keywords:
                if kw.arg != "entry_points":
                    continue
                if not isinstance(kw.value, ast.Dict):
                    continue
                # entry_points is a dict: {"console_scripts": [...], ...}
                for value in kw.value.values:
                    if isinstance(value, ast.List):
                        for item in value.elts:
                            patterns.extend(
                                self._extract_module_from_ep(item)
                            )

        return patterns

    def _extract_entry_points_toml(
        self, filepath: Path, content: str
    ) -> list[str]:
        """Extract entry points from pyproject.toml."""
        data = self._load_toml(content)
        if data is None:
            return []

        patterns = self._collect_toml_eps(data)
        return self._parse_module_refs(patterns)

    def _load_toml(self, content: str) -> dict | None:
        """Load and parse TOML content."""
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                return None

        try:
            return tomllib.loads(content)
        except Exception:
            return None

    def _collect_toml_eps(self, data: dict) -> list[str]:
        """Collect entry point strings from parsed TOML data."""
        patterns: list[str] = []
        project = data.get("project", {})
        self._extend_scripts(project, patterns)
        self._extend_entry_points(project, patterns)
        return patterns

    def _extend_scripts(
        self, project: dict, patterns: list[str]
    ) -> None:
        """Extend patterns with script entry points."""
        for key in ("scripts", "gui-scripts", "gui_scripts"):
            scripts = project.get(key, {})
            if isinstance(scripts, dict):
                patterns.extend(scripts.values())

    def _extend_entry_points(
        self, project: dict, patterns: list[str]
    ) -> None:
        """Extend patterns with entry-points section."""
        entry_points = project.get("entry-points", {})
        if isinstance(entry_points, dict):
            for group in entry_points.values():
                if isinstance(group, dict):
                    patterns.extend(group.values())

    @staticmethod
    def _parse_module_refs(patterns: list[str]) -> list[str]:
        """Parse module references: 'module.path:attr' -> 'module.path'."""
        result: list[str] = []
        for ep in patterns:
            if isinstance(ep, str) and ":" in ep:
                result.append(ep.split(":", 1)[0])
        return result

    def _extract_module_from_ep(
        self, node: ast.expr
    ) -> list[str]:
        """Extract module name from an entry point AST node."""
        patterns: list[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # "name = module.path:func"
            if ":" in node.value:
                parts = node.value.split("=", 1)
                if len(parts) == 2:
                    mod_part = parts[1].strip().split(":", 1)[0].strip()
                    if mod_part:
                        patterns.append(mod_part)
        return patterns
