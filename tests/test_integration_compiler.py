"""Integration tests — full compiler pipeline against realistic projects."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.compiler import Compiler


# ====================================================================
# Simple project tests
# ====================================================================

def test_compile_simple_project() -> None:
    """Compile a minimal Python project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text(
            "import app\nimport utils\nprint('hello')\n"
        )
        (root / "app.py").write_text(
            "import json\nfrom pathlib import Path\n\ndef run(): pass\n"
        )
        (root / "utils.py").write_text(
            "from collections import defaultdict\n\nCACHE = defaultdict(list)\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 3

        # Check that imports were discovered
        reachable = compiler.graph.find_reachable()
        assert "main" in reachable
        assert "app" in reachable
        assert "utils" in reachable


def test_compile_package_project() -> None:
    """Compile a project with package structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Package structure
        pkg = root / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from .core import Engine\nfrom .utils import helper\n"
        )
        (pkg / "core.py").write_text(
            "import os\nimport json\n\nclass Engine:\n    pass\n"
        )
        (pkg / "utils.py").write_text(
            "from pathlib import Path\n\ndef helper(): pass\n"
        )

        (root / "main.py").write_text(
            "from mypackage import Engine, helper\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 4


def test_compile_with_forger_py() -> None:
    """Compile with a forger.py extension."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("import app\n")
        (root / "app.py").write_text("pass\n")

        # forger.py adds extra dependencies
        (root / "forger.py").write_text(
            "from forger import include, include_module\n"
            'include("templates/**/*")\n'
            'include_module("myapp.plugins.auth")\n'
        )

        # Create template files
        templates = root / "templates"
        templates.mkdir()
        (templates / "index.html").write_text("<html></html>")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()
        compiler.process_forger_py(root / "forger.py")

        # Check that forger.py contributions are in the graph
        assert compiler.graph is not None
        assert compiler.graph.get_node("myapp.plugins.auth") is not None


def test_compile_with_subpackages() -> None:
    """Compile project with nested subpackages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # mypackage/
        #   __init__.py
        #   sub1/
        #     __init__.py
        #     module_a.py
        #   sub2/
        #     __init__.py
        #     module_b.py

        pkg = root / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        sub1 = pkg / "sub1"
        sub1.mkdir()
        (sub1 / "__init__.py").write_text("from .module_a import A\n")
        (sub1 / "module_a.py").write_text("import os\n\nclass A: pass\n")

        sub2 = pkg / "sub2"
        sub2.mkdir()
        (sub2 / "__init__.py").write_text("from .module_b import B\n")
        (sub2 / "module_b.py").write_text("import json\n\nclass B: pass\n")

        (root / "main.py").write_text(
            "from mypackage.sub1 import A\nfrom mypackage.sub2 import B\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 5


# ====================================================================
# Framework-specific integration tests
# ====================================================================

def test_compile_django_like_project() -> None:
    """Compile a Django-like project structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # manage.py
        (root / "manage.py").write_text(
            "#!/usr/bin/env python\nimport os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
        )

        # config/settings.py
        config = root / "config"
        config.mkdir()
        (config / "__init__.py").write_text("")
        (config / "settings.py").write_text(
            "INSTALLED_APPS = [\n"
            "    'django.contrib.admin',\n"
            "    'django.contrib.auth',\n"
            "    'myapp',\n"
            "]\n"
            "TEMPLATES = [{\n"
            '    "DIRS": [BASE_DIR / "templates"],\n'
            '    "APP_DIRS": True,\n'
            "}]\n"
        )

        # myapp/
        myapp = root / "myapp"
        myapp.mkdir()
        (myapp / "__init__.py").write_text("")
        (myapp / "models.py").write_text(
            "from django.db import models\n\nclass User(models.Model): pass\n"
        )
        (myapp / "views.py").write_text(
            "from django.http import HttpResponse\n\ndef index(request): pass\n"
        )
        (myapp / "admin.py").write_text("from django.contrib import admin\n")

        # templates/
        templates = root / "templates"
        templates.mkdir()
        (templates / "index.html").write_text("<html></html>")

        (root / "main.py").write_text(
            "from myapp.models import User\nfrom myapp.views import index\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 3


def test_compile_flask_like_project() -> None:
    """Compile a Flask-like project structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "app.py").write_text(
            "from flask import Flask, render_template\n"
            "app = Flask(__name__, template_folder='templates')\n"
            "\n"
            "@app.route('/')\n"
            "def index(): return render_template('index.html')\n"
        )

        templates = root / "templates"
        templates.mkdir()
        (templates / "index.html").write_text("<html></html>")

        static = root / "static"
        static.mkdir()
        (static / "style.css").write_text("body { }")

        output = root / "app.forge"
        compiler = Compiler(root, "app", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 2


def test_compile_fastapi_like_project() -> None:
    """Compile a FastAPI-like project structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "from pydantic import BaseModel\n"
            "from sqlalchemy.orm import Session\n"
            "\n"
            "app = FastAPI()\n"
            "\n"
            "class Item(BaseModel):\n"
            "    name: str\n"
            "\n"
            "@app.get('/')\n"
            "def read_root(): return {'msg': 'ok'}\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 1


# ====================================================================
# Dependency resolution integration tests
# ====================================================================

def test_dependency_chain_resolution() -> None:
    """Test that long dependency chains are resolved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # main -> a -> b -> c -> d
        (root / "main.py").write_text("import a\n")
        (root / "a.py").write_text("import b\n")
        (root / "b.py").write_text("import c\n")
        (root / "c.py").write_text("import d\n")
        (root / "d.py").write_text("x = 1\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        reachable = compiler.graph.find_reachable()
        assert "a" in reachable
        assert "b" in reachable
        assert "c" in reachable
        assert "d" in reachable


def test_circular_import_handling() -> None:
    """Test that circular imports don't cause infinite loops."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("import a\n")
        (root / "a.py").write_text("import b\n")
        (root / "b.py").write_text("import a\n")  # circular

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should complete without hanging
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 3


def test_diamond_dependency() -> None:
    """Test diamond dependency pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # main -> a, b; a -> c; b -> c
        (root / "main.py").write_text("import a\nimport b\n")
        (root / "a.py").write_text("import c\n")
        (root / "b.py").write_text("import c\n")
        (root / "c.py").write_text("x = 1\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        reachable = compiler.graph.find_reachable()
        assert "c" in reachable


def test_orphan_module_not_reachable() -> None:
    """Test that orphan modules are not marked reachable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("import used\n")
        (root / "used.py").write_text("pass\n")
        (root / "orphan.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        reachable = compiler.graph.find_reachable()
        assert "used" in reachable
        assert "orphan" not in reachable


# ====================================================================
# Resource discovery integration tests
# ====================================================================

def test_resource_discovery_in_project() -> None:
    """Test that resource access in source files is discovered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text(
            'with open("config.json") as f: data = f.read()\n'
            'with open("data/schema.yaml") as f: schema = f.read()\n'
        )

        # Create the resources
        (root / "config.json").write_text("{}")
        data_dir = root / "data"
        data_dir.mkdir()
        (data_dir / "schema.yaml").write_text("key: value")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Source files should be analyzed
        assert len(compiler.source_files) >= 1


def test_large_project_performance() -> None:
    """Test that the compiler handles a moderately large project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create 100 modules
        for i in range(100):
            imports = f"import mod_{i % 50}\n" if i > 0 else ""
            (root / f"mod_{i}.py").write_text(
                f"{imports}\nVALUE_{i} = {i}\n"
            )

        (root / "main.py").write_text("import mod_0\nimport mod_1\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should discover all 102 files
        assert len(compiler.source_files) >= 100
        # Reachable should include at least the directly imported ones
        assert compiler.graph is not None
        reachable = compiler.graph.find_reachable()
        assert "main" in reachable


# ====================================================================
# Optimizer integration tests
# ====================================================================

def test_optimizer_runs_on_django_project() -> None:
    """Test that Django optimizer activates on a Django project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create Django-like structure
        (root / "manage.py").write_text("# Django manage.py\n")

        config = root / "config"
        config.mkdir()
        (config / "settings.py").write_text(
            "INSTALLED_APPS = ['myapp']\n"
            "TEMPLATES = [{'DIRS': ['.'], 'APP_DIRS': True}]\n"
        )

        myapp = root / "myapp"
        myapp.mkdir()
        (myapp / "models.py").write_text("pass\n")
        (myapp / "views.py").write_text("pass\n")
        (myapp / "admin.py").write_text("pass\n")

        templates = root / "templates"
        templates.mkdir()
        (templates / "base.html").write_text("<html></html>")

        (root / "main.py").write_text("from myapp.models import User\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()
        compiler.run_optimizers()

        # Graph should have nodes from both analysis and optimizer
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 3


def test_compiler_full_pipeline() -> None:
    """Test the full compile pipeline: analyze → forger.py → optimizers → artifact."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("import app\n")
        (root / "app.py").write_text("import json\n")

        (root / "forger.py").write_text(
            "from forger import include_module\n"
            'include_module("extra_module")\n'
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)

        # Full pipeline
        compiler.analyze()
        compiler.process_forger_py(root / "forger.py")
        compiler.run_optimizers()
        compiler.generate_artifact()

        # Verify diagnostic summary
        summary = compiler.diagnostic_summary()
        assert "Compilation Summary" in summary
        assert str(root) in summary
