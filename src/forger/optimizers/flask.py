"""Flask framework optimizer.

Analyzes Flask project configuration to discover:
- Template folders (Jinja2)
- Static folders
- Blueprint registrations
- Extension configurations
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from forger.optimizer import Optimizer, OptimizerContext

if TYPE_CHECKING:
    from forger.core import (  # type: ignore[attr-defined]
        DependencyGraph,
    )

logger = logging.getLogger(__name__)


class FlaskOptimizer(Optimizer):
    """Optimizer for Flask projects."""

    name = "flask"

    def detect(self, context: OptimizerContext) -> float:
        """Detect Flask by checking for imports and project structure."""
        if not context.has_package("flask"):
            return 0.0

        score = 0.3  # Base score for having flask installed

        # Check for Flask app patterns in source files
        for source_file in context.source_files:
            try:
                content = source_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Look for Flask app instantiation
            if re.search(r"Flask\s*\(", content):
                score += 0.4
                break

            if re.search(r"from\s+flask\s+import", content, re.IGNORECASE):
                score += 0.2

        # Check for common Flask directory structure
        flask_dirs = ["templates", "static", "routes", "blueprints"]
        for dir_name in flask_dirs:
            if (context.project_root / dir_name).exists():
                score += 0.05

        return min(score, 1.0)

    def analyze(
        self, context: OptimizerContext, graph: DependencyGraph
    ) -> None:
        """Analyze Flask project configuration."""
        from forger.core import (  # noqa: E402
            DependencyNode,
            NodeType,
        )

        # Add Flask as a dependency
        graph.add_node(
            DependencyNode.new("flask", NodeType.ExternalPackage).with_metadata(
                "discovered_by", "flask_optimizer"
            )
        )

        # Analyze source files for Flask app configuration
        for source_file in context.source_files:
            self._analyze_flask_app(source_file, context.project_root, graph)

        # Add common Flask resource directories
        for dir_name in ["templates", "static"]:
            dir_path = context.project_root / dir_name
            if dir_path.exists() and dir_path.is_dir():
                self._add_resource_directory(
                    dir_path, f"flask_{dir_name}", graph
                )

    def _analyze_flask_app(
        self,
        source_file: Path,
        project_root: Path,
        graph: DependencyGraph,
    ) -> None:
        """Analyze a source file for Flask app instantiation."""
        tree = self._parse_file(source_file)
        if tree is None:
            return

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = self._get_call_name(node.func)
            if not func_name or "Flask" not in func_name:
                continue
            self._process_flask_call(node, project_root, graph)

    def _parse_file(self, source_file: Path) -> ast.AST | None:
        """Parse a Python source file, returning None on error."""
        try:
            content = source_file.read_text(encoding="utf-8")
            return ast.parse(content, filename=str(source_file))
        except (OSError, UnicodeDecodeError, SyntaxError):
            return None

    def _process_flask_call(
        self,
        call_node: ast.Call,
        project_root: Path,
        graph: DependencyGraph,
    ) -> None:
        """Process a Flask(...) call node and register resources."""
        kwargs = self._extract_flask_kwargs(call_node)
        self._register_flask_resources(project_root, kwargs, graph)

    def _extract_flask_kwargs(
        self, call_node: ast.Call
    ) -> dict[str, str | None]:
        """Extract template_folder and static_folder from Flask kwargs."""
        result: dict[str, str | None] = {
            "template_folder": None,
            "static_folder": None,
        }
        for kw in call_node.keywords:
            if kw.arg == "template_folder":
                result["template_folder"] = self._get_constant_value(kw.value)
            elif kw.arg == "static_folder":
                result["static_folder"] = self._get_constant_value(kw.value)
        return result

    def _register_flask_resources(
        self,
        project_root: Path,
        kwargs: dict[str, str | None],
        graph: DependencyGraph,
    ) -> None:
        """Register Flask resource directories with the graph."""
        if kwargs["template_folder"]:
            tp = project_root / kwargs["template_folder"]
            if tp.exists():
                self._add_resource_directory(tp, "flask_templates", graph)

        if kwargs["static_folder"]:
            sp = project_root / kwargs["static_folder"]
            if sp.exists():
                self._add_resource_directory(sp, "flask_static", graph)

    def _get_call_name(self, node: ast.expr) -> str | None:
        """Extract function name from a call target."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value_name = self._get_call_name(node.value)
            if value_name:
                return f"{value_name}.{node.attr}"
        return None

    def _get_constant_value(self, node: ast.expr) -> str | None:
        """Extract a constant string value from an AST node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _add_resource_directory(
        self,
        dir_path: Path,
        label: str,
        graph: DependencyGraph,
    ) -> None:
        """Add all files in a resource directory to the graph."""
        from forger.core import (  # noqa: E402
            DependencyNode,
            NodeType,
        )

        if not dir_path.exists():
            return

        for file_path in dir_path.rglob("*"):
            if file_path.is_file():
                node_id = str(file_path.relative_to(dir_path.parent))
                graph.add_node(
                    DependencyNode.new(
                        node_id, NodeType.Resource
                    ).with_metadata("discovered_by", f"flask_optimizer:{label}")
                )
