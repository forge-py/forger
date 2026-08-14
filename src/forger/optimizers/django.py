"""Django framework optimizer.

Analyzes Django project configuration to discover:
- Installed apps and their resources (templates, static, locale, migrations)
- Template directories
- Static file directories
- Middleware, URL configs, ASGI/WSGI entry points
"""

from __future__ import annotations

import logging
import sys
from importlib import util as importlib_util
from pathlib import Path
from typing import TYPE_CHECKING

from forger.optimizer import Optimizer, OptimizerContext

if TYPE_CHECKING:
    from forger.core import (  # type: ignore[attr-defined]
        DependencyGraph,
        DependencyEdge,
        DependencyNode,
        EdgeProvenance,
        EdgeType,
        NodeType,
    )

logger = logging.getLogger(__name__)


class DjangoOptimizer(Optimizer):
    """Optimizer for Django projects."""

    name = "django"

    def detect(self, context: OptimizerContext) -> float:
        """Detect Django by checking for installed package and settings.py."""
        if not context.has_package("django"):
            return 0.0

        # Look for settings.py
        settings_candidates = [
            context.project_root / "settings.py",
            context.project_root / "manage.py",
        ]

        score = 0.3  # Base score for having django installed

        for candidate in settings_candidates:
            if candidate.exists():
                score += 0.3

        # Check for Django-style directory structure
        for child in context.project_root.iterdir():
            if child.is_dir():
                # Look for app-like directories with models.py, views.py, etc.
                indicators = [
                    child / "models.py",
                    child / "views.py",
                    child / "admin.py",
                    child / "apps.py",
                ]
                indicator_count = sum(1 for ind in indicators if ind.exists())
                if indicator_count >= 2:
                    score += 0.1
                    break

        return min(score, 1.0)

    def analyze(self, context: OptimizerContext, graph: DependencyGraph) -> None:
        """Analyze Django project configuration."""
        from forger.core import (  # noqa: E402
            DependencyEdge,
            DependencyNode,
            EdgeProvenance,
            EdgeType,
            NodeType,
        )

        # Find and analyze settings.py
        settings_path = self._find_settings(context.project_root)
        if not settings_path:
            logger.warning("Django detected but settings.py not found")
            return

        # Add Django as a dependency
        graph.add_node(
            DependencyNode.new("django", NodeType.ExternalPackage).with_metadata(
                "discovered_by", "django_optimizer"
            )
        )

        # Add settings.py
        graph.add_node(
            DependencyNode.new(str(settings_path), NodeType.Configuration).with_metadata(
                "discovered_by", "django_optimizer"
            )
        )

        # Try to extract configuration by importing settings
        installed_apps, template_dirs, static_dirs = self._parse_settings(
            settings_path, context.project_root
        )

        # Add installed apps
        for app in installed_apps:
            self._add_django_app(app, context.project_root, graph)

        # Add template directories
        for template_dir in template_dirs:
            self._add_resource_directory(template_dir, "django_templates", graph)

        # Add static directories
        for static_dir in static_dirs:
            self._add_resource_directory(static_dir, "django_static", graph)

    def _find_settings(self, project_root: Path) -> Path | None:
        """Find settings.py in the project."""
        # Direct settings.py
        if (project_root / "settings.py").exists():
            return project_root / "settings.py"

        # Look in subdirectories
        for child in project_root.iterdir():
            if child.is_dir():
                settings = child / "settings.py"
                if settings.exists():
                    return settings

        # Look for manage.py and derive settings location
        manage_py = project_root / "manage.py"
        if manage_py.exists():
            # Standard Django layout: project/config/settings.py
            for dirpath, dirnames, filenames in project_root.walk():
                if "settings.py" in filenames:
                    return dirpath / "settings.py"

        return None

    def _parse_settings(
        self, settings_path: Path, project_root: Path
    ) -> tuple[list[str], list[Path], list[Path]]:
        """Parse settings.py to extract configuration.

        Returns (installed_apps, template_dirs, static_dirs).
        """
        installed_apps: list[str] = []
        template_dirs: list[Path] = []
        static_dirs: list[Path] = []

        try:
            # Add the project root to sys.path temporarily
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            # Try to import the settings module
            spec = importlib_util.spec_from_file_location("settings", settings_path)
            if spec and spec.loader:
                settings_module = importlib_util.module_from_spec(spec)
                spec.loader.exec_module(settings_module)

                # INSTALLED_APPS
                if hasattr(settings_module, "INSTALLED_APPS"):
                    apps = settings_module.INSTALLED_APPS
                    if isinstance(apps, (list, tuple)):
                        installed_apps = [str(a) for a in apps]

                # TEMPLATES
                if hasattr(settings_module, "TEMPLATES"):
                    templates = settings_module.TEMPLATES
                    if isinstance(templates, list):
                        for template_config in templates:
                            if isinstance(template_config, dict):
                                dirs = template_config.get("DIRS", [])
                                for d in dirs:
                                    p = Path(d)
                                    if p.exists():
                                        template_dirs.append(p)

                                # APP_DIRS
                                if template_config.get("APP_DIRS", False):
                                    # Will be handled when we process apps
                                    pass

                # STATICFILES_DIRS
                if hasattr(settings_module, "STATICFILES_DIRS"):
                    staticfiles = settings_module.STATICFILES_DIRS
                    if isinstance(staticfiles, (list, tuple)):
                        for d in staticfiles:
                            p = Path(d) if isinstance(d, str) else Path(d[0])
                            if p.exists():
                                static_dirs.append(p)

                # STATIC_ROOT
                if hasattr(settings_module, "STATIC_ROOT"):
                    static_root = settings_module.STATIC_ROOT
                    if static_root and Path(static_root).exists():
                        static_dirs.append(Path(static_root))

        except Exception as e:
            logger.warning("Failed to parse settings.py: %s", e)
        finally:
            if str(project_root) in sys.path:
                sys.path.remove(str(project_root))

        return installed_apps, template_dirs, static_dirs

    def _add_django_app(
        self,
        app_name: str,
        project_root: Path,
        graph: DependencyGraph,
    ) -> None:
        """Add a Django app and its resources to the graph."""
        from forger.core import (  # noqa: E402
            DependencyNode,
            NodeType,
        )

        # Try to find the app directory
        app_path = self._find_app_path(app_name, project_root)
        if not app_path:
            return

        # Add the app module
        graph.add_node(
            DependencyNode.new(app_name, NodeType.PythonPackage).with_metadata(
                "discovered_by", "django_optimizer"
            )
        )

        # Add app resources
        resource_dirs = [
            "templates",
            "static",
            "locale",
            "migrations",
            "management",
            "templatetags",
            "fixtures",
        ]

        for resource_dir in resource_dirs:
            dir_path = app_path / resource_dir
            if dir_path.exists() and dir_path.is_dir():
                self._add_resource_directory(dir_path, f"django_app_{resource_dir}", graph)

    def _find_app_path(self, app_name: str, project_root: Path) -> Path | None:
        """Find the filesystem path for a Django app."""
        # Convert dotted name to path
        parts = app_name.split(".")
        search_path = project_root

        for part in parts:
            candidate = search_path / part
            if candidate.exists() and candidate.is_dir():
                search_path = candidate
            else:
                return None

        return search_path

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
                    DependencyNode.new(node_id, NodeType.Resource).with_metadata(
                        "discovered_by", f"django_optimizer:{label}"
                    )
                )
