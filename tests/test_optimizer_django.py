"""Django optimizer integration tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.optimizers.django import DjangoOptimizer
from forger.optimizer import OptimizerContext
from forger.core import DependencyGraph, NodeType


def test_django_detect_with_package() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "manage.py").write_text("# Django\n")

        ctx = OptimizerContext(
            project_root=root,
            installed_packages={"django": "4.2"},
        )

        optimizer = DjangoOptimizer()
        confidence = optimizer.detect(ctx)
        assert confidence >= 0.5


def test_django_detect_without_package() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        ctx = OptimizerContext(
            project_root=root,
            installed_packages={},
        )

        optimizer = DjangoOptimizer()
        confidence = optimizer.detect(ctx)
        assert confidence < 0.5


def test_django_find_settings() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "settings.py").write_text(
            "INSTALLED_APPS = ['myapp']\n"
        )

        optimizer = DjangoOptimizer()
        settings = optimizer._find_settings(root)
        assert settings is not None
        assert settings.name == "settings.py"


def test_django_find_settings_nested() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config = root / "config"
        config.mkdir()
        (config / "settings.py").write_text(
            "INSTALLED_APPS = ['myapp']\n"
        )

        optimizer = DjangoOptimizer()
        settings = optimizer._find_settings(root)
        assert settings is not None


def test_django_parse_settings() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        settings = root / "settings.py"
        settings.write_text(
            "import os\n"
            "BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n"
            "INSTALLED_APPS = [\n"
            "    'django.contrib.admin',\n"
            "    'myapp',\n"
            "]\n"
            "TEMPLATES = [{\n"
            '    "DIRS": [BASE_DIR / "templates"],\n'
            '    "APP_DIRS": True,\n'
            "}]\n"
            "STATICFILES_DIRS = []\n"
        )

        templates = root / "templates"
        templates.mkdir()

        optimizer = DjangoOptimizer()
        apps, template_dirs, static_dirs = optimizer._parse_settings(
            settings, root
        )

        assert "myapp" in apps
        assert "django.contrib.admin" in apps


def test_django_add_app_resources() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create app structure
        myapp = root / "myapp"
        myapp.mkdir()
        (myapp / "models.py").write_text("pass\n")
        (myapp / "views.py").write_text("pass\n")

        templates = myapp / "templates"
        templates.mkdir()
        (templates / "index.html").write_text("<html></html>")

        static = myapp / "static"
        static.mkdir()
        (static / "style.css").write_text("body {}")

        graph = DependencyGraph()
        optimizer = DjangoOptimizer()

        optimizer._add_django_app("myapp", root, graph)

        # Should have added the app node
        assert graph.get_node("myapp") is not None


def test_django_full_analysis() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # settings.py
        (root / "settings.py").write_text(
            "INSTALLED_APPS = ['myapp']\n"
            "TEMPLATES = [{'DIRS': [], 'APP_DIRS': False}]\n"
        )

        # myapp/
        myapp = root / "myapp"
        myapp.mkdir()
        (myapp / "models.py").write_text("pass\n")
        (myapp / "views.py").write_text("pass\n")
        (myapp / "admin.py").write_text("pass\n")

        ctx = OptimizerContext(
            project_root=root,
            installed_packages={"django": "4.2"},
        )

        graph = DependencyGraph()
        optimizer = DjangoOptimizer()
        optimizer.analyze(ctx, graph)

        # Should have added Django as external package
        assert graph.get_node("django") is not None


def test_django_no_settings() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        ctx = OptimizerContext(
            project_root=root,
            installed_packages={"django": "4.2"},
        )

        graph = DependencyGraph()
        optimizer = DjangoOptimizer()
        optimizer.analyze(ctx, graph)

        # Should not crash even without settings
        assert graph.node_count() >= 0
