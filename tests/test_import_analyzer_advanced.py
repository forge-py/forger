"""Advanced import analyzer tests — edge cases and real-world patterns."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.analyzer.imports import ImportAnalyzer


# --- Basic import patterns ---

def test_import_star() -> None:
    source = "from os.path import *\n"
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert len(imports) >= 1
    assert imports[0].module == "os.path"


def test_import_alias() -> None:
    source = "import numpy as np\nimport pandas as pd\n"
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert len(imports) == 2
    assert imports[0].module == "numpy"
    assert imports[1].module == "pandas"


def test_conditional_import() -> None:
    source = """
try:
    import ssl
except ImportError:
    ssl = None

try:
    import ujson as json
except ImportError:
    import json
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "ssl" in modules
    assert "json" in modules


def test_lazy_import_in_function() -> None:
    source = """
def process():
    import heavy_module
    return heavy_module.do_work()

def other():
    from collections import Counter
    return Counter()
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "heavy_module" in modules
    assert "collections" in modules


def test_wildcard_from_import() -> None:
    source = "from typing import *\n"
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert imports[0].module == "typing"
    assert imports[0].is_from_import


def test_relative_import_levels() -> None:
    source = """
from . import sibling
from .sub import thing
from .. import parent
from ..sibling import stuff
from ... import grandparent
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert imports[0].level == 1
    assert imports[1].level == 1
    assert imports[2].level == 2
    assert imports[3].level == 2
    assert imports[4].level == 3


def test_absolute_vs_relative_disambiguation() -> None:
    source = """
import os
from os import path
from . import os as local_os
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert imports[0].level == 0  # absolute
    assert imports[1].level == 0  # absolute
    assert imports[2].level == 1  # relative


# --- Real-world package patterns ---

def test_django_style_imports() -> None:
    source = """
from django.conf import settings
from django.db import models
from django.urls import path, include
from django.contrib.auth import get_user_model
from rest_framework import serializers, viewsets
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "django.conf" in modules
    assert "django.db" in modules
    assert "django.urls" in modules
    assert "django.contrib.auth" in modules
    assert "rest_framework" in modules


def test_flask_style_imports() -> None:
    source = """
from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user
from flask_migrate import Migrate
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "flask" in modules
    assert "flask_sqlalchemy" in modules
    assert "flask_login" in modules
    assert "flask_migrate" in modules


def test_fastapi_style_imports() -> None:
    source = """
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Annotated
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "fastapi" in modules
    assert "pydantic" in modules
    assert "sqlalchemy.orm" in modules
    assert "typing" in modules


def test_scikit_learn_style_imports() -> None:
    source = """
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
import pandas as pd
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "numpy" in modules
    assert "sklearn.model_selection" in modules
    assert "sklearn.ensemble" in modules
    assert "sklearn.metrics" in modules
    assert "pandas" in modules


# --- File-based analysis ---

def test_analyze_real_package_structure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create package structure
        (root / "mypackage").mkdir()
        (root / "mypackage" / "__init__.py").write_text(
            "from .core import Engine\nfrom .utils import helper\n"
        )
        (root / "mypackage" / "core.py").write_text(
            "import json\nimport os\nfrom pathlib import Path\n"
        )
        (root / "mypackage" / "utils.py").write_text(
            "from collections import defaultdict\n"
        )

        analyzer = ImportAnalyzer()

        # Analyze __init__.py
        init_imports = analyzer.analyze_file(root / "mypackage" / "__init__.py")
        assert len(init_imports) == 2

        # Analyze core.py
        core_imports = analyzer.analyze_file(root / "mypackage" / "core.py")
        modules = {i.module for i in core_imports}
        assert modules == {"json", "os", "pathlib"}


def test_analyze_empty_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "empty.py").write_text("")
        analyzer = ImportAnalyzer()
        imports = analyzer.analyze_file(Path(tmpdir) / "empty.py")
        assert len(imports) == 0


def test_analyze_syntax_error_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "broken.py").write_text("def foo( invalid syntax")
        analyzer = ImportAnalyzer()
        imports = analyzer.analyze_file(Path(tmpdir) / "broken.py")
        assert len(imports) == 0


def test_analyze_nonexistent_file() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_file(Path("/nonexistent/file.py"))
    assert len(imports) == 0


# --- Import resolution ---

def test_resolve_target_absolute() -> None:
    analyzer = ImportAnalyzer()
    from forger.analyzer.imports import ImportInfo

    imp = ImportInfo(module="os.path", source_file="app.py", line=1)
    target = analyzer._resolve_target(imp, "app")
    assert target == "os.path"


def test_resolve_target_from_import() -> None:
    analyzer = ImportAnalyzer()
    from forger.analyzer.imports import ImportInfo

    imp = ImportInfo(
        module="collections",
        names=["OrderedDict"],
        source_file="app.py",
        line=1,
        is_from_import=True,
    )
    target = analyzer._resolve_target(imp, "app")
    assert target == "collections.OrderedDict"


def test_resolve_target_relative_same_package() -> None:
    analyzer = ImportAnalyzer()
    from forger.analyzer.imports import ImportInfo

    imp = ImportInfo(
        module="utils",
        level=1,
        source_file="mypkg/main.py",
        line=1,
        is_from_import=True,
    )
    target = analyzer._resolve_target(imp, "mypkg.main")
    assert target == "mypkg.utils"


def test_resolve_target_relative_parent() -> None:
    analyzer = ImportAnalyzer()
    from forger.analyzer.imports import ImportInfo

    imp = ImportInfo(
        module="config",
        level=2,
        source_file="pkg/sub/mod.py",
        line=1,
        is_from_import=True,
    )
    target = analyzer._resolve_target(imp, "pkg.sub.mod")
    assert target == "pkg.config"


# --- Edge cases ---

def test_multiline_import() -> None:
    source = """
from collections import (
    OrderedDict,
    defaultdict,
    Counter,
    namedtuple,
)
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert len(imports) == 1
    assert set(imports[0].names) == {
        "OrderedDict",
        "defaultdict",
        "Counter",
        "namedtuple",
    }


def test_import_in_class_body() -> None:
    source = """
class MyClass:
    import os

    def method(self):
        from pathlib import Path
        return Path()
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "os" in modules
    assert "pathlib" in modules


def test_type_hint_only_import() -> None:
    source = """
from __future__ import annotations
import typing
from typing import List, Dict, Optional
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "__future__" in modules
    assert "typing" in modules


def test_future_import() -> None:
    source = "from __future__ import annotations, division\n"
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert imports[0].module == "__future__"


def test_import_with_comments() -> None:
    source = """
import os  # operating system
import sys  # system stuff
# import unused  # this is commented out
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "os" in modules
    assert "sys" in modules
    assert "unused" not in modules


def test_nested_function_imports() -> None:
    source = """
def outer():
    import a
    def inner():
        import b
        def deep():
            from c import d
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert modules == {"a", "b", "c"}
