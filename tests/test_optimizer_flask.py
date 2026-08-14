"""Flask optimizer integration tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.core import DependencyGraph
from forger.optimizer import OptimizerContext
from forger.optimizers.flask import FlaskOptimizer


def test_flask_detect_with_flask_import() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
        )

        ctx = OptimizerContext(
            project_root=root,
            source_files=[root / "app.py"],
            installed_packages={"flask": "3.0"},
        )

        optimizer = FlaskOptimizer()
        confidence = optimizer.detect(ctx)
        assert confidence >= 0.5


def test_flask_detect_without_flask() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        ctx = OptimizerContext(
            project_root=root,
            installed_packages={},
        )

        optimizer = FlaskOptimizer()
        confidence = optimizer.detect(ctx)
        assert confidence < 0.5


def test_flask_detect_with_templates_dir() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "templates").mkdir()
        (root / "static").mkdir()
        (root / "app.py").write_text("from flask import Flask\n")

        ctx = OptimizerContext(
            project_root=root,
            source_files=[root / "app.py"],
            installed_packages={"flask": "3.0"},
        )

        optimizer = FlaskOptimizer()
        confidence = optimizer.detect(ctx)
        assert confidence >= 0.5


def test_flask_analyze_template_folder() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__, template_folder='custom_templates')\n"
        )

        templates = root / "custom_templates"
        templates.mkdir()
        (templates / "index.html").write_text("<html></html>")

        ctx = OptimizerContext(
            project_root=root,
            source_files=[root / "app.py"],
            installed_packages={"flask": "3.0"},
        )

        graph = DependencyGraph()
        optimizer = FlaskOptimizer()
        optimizer.analyze(ctx, graph)

        # Should have added Flask as dependency
        assert graph.get_node("flask") is not None


def test_flask_static_folder() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__, static_folder='custom_static')\n"
        )

        static = root / "custom_static"
        static.mkdir()
        (static / "style.css").write_text("body {}")

        ctx = OptimizerContext(
            project_root=root,
            source_files=[root / "app.py"],
            installed_packages={"flask": "3.0"},
        )

        graph = DependencyGraph()
        optimizer = FlaskOptimizer()
        optimizer.analyze(ctx, graph)


def test_flask_default_folders() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "app.py").write_text("from flask import Flask\n")

        # Default folders
        (root / "templates").mkdir()
        (root / "templates" / "base.html").write_text("<html></html>")
        (root / "static").mkdir()
        (root / "static" / "app.js").write_text("console.log(1);")

        ctx = OptimizerContext(
            project_root=root,
            source_files=[root / "app.py"],
            installed_packages={"flask": "3.0"},
        )

        graph = DependencyGraph()
        optimizer = FlaskOptimizer()
        optimizer.analyze(ctx, graph)

        # Should have discovered template and static resources
        resources = graph.nodes_by_type("resource")
        assert len(resources) >= 2


def test_flask_no_source_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        ctx = OptimizerContext(
            project_root=root,
            source_files=[],
            installed_packages={"flask": "3.0"},
        )

        graph = DependencyGraph()
        optimizer = FlaskOptimizer()
        optimizer.analyze(ctx, graph)

        # Should not crash
        assert graph.node_count() >= 1  # at least flask
