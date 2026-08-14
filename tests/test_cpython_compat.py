"""CPython compatibility tests — verify Forger analysis matches CPython behavior."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from forger.analyzer.imports import ImportAnalyzer
from forger.compiler import Compiler


def test_stdlib_import_detection() -> None:
    """Verify Forger detects the same stdlib imports as CPython."""
    source = """
import os
import sys
import json
import pathlib
import collections
import itertools
import functools
import typing
import logging
import unittest
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)

    modules = {i.module for i in imports}
    expected = {
        "os", "sys", "json", "pathlib", "collections",
        "itertools", "functools", "typing", "logging", "unittest",
    }
    assert modules == expected


def test_stdlib_submodule_imports() -> None:
    """Verify Forger detects stdlib submodule imports."""
    source = """
from os.path import join, exists
from collections import OrderedDict, defaultdict
from typing import List, Dict, Optional
from unittest import TestCase
from logging import getLogger
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)

    modules = {i.module for i in imports}
    expected = {"os.path", "collections", "typing", "unittest", "logging"}
    assert modules == expected


def test_import_semantics_match_cpython() -> None:
    """Verify that Forger import resolution matches CPython."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Set up a package that CPython can import
        pkg = root / "testpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .sub import func\n")
        (pkg / "sub.py").write_text("def func(): return 42\n")

        import sys
        original_path = list(sys.path)
        try:
            sys.path.insert(0, str(root))

            # CPython can import it
            import importlib
            testpkg = importlib.import_module("testpkg")
            assert hasattr(testpkg, "func")

            # Forger should also detect it
            analyzer = ImportAnalyzer()
            imports = analyzer.analyze_file(pkg / "__init__.py")
            assert len(imports) >= 1

        finally:
            sys.path[:] = original_path
            if "testpkg" in sys.modules:
                del sys.modules["testpkg"]
            if "testpkg.sub" in sys.modules:
                del sys.modules["testpkg.sub"]


def test_dynamic_import_semantics() -> None:
    """Verify Forger detects dynamic imports that CPython would resolve."""
    source = """
import importlib
mod = importlib.import_module("os.path")
"""
    from forger.analyzer.dynamic import DynamicImportAnalyzer

    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(h.pattern == "os.path" for h in hints)


def test_package_init_import_order() -> None:
    """Verify Forger handles __init__.py import order like CPython."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        pkg = root / "ordered_pkg"
        pkg.mkdir()
        # __init__ imports in specific order
        (pkg / "__init__.py").write_text(
            "from .a import A\n"
            "from .b import B\n"
            "from .c import C\n"
        )
        (pkg / "a.py").write_text("class A: pass\n")
        (pkg / "b.py").write_text("class B: pass\n")
        (pkg / "c.py").write_text("class C: pass\n")

        analyzer = ImportAnalyzer()
        imports = analyzer.analyze_file(pkg / "__init__.py")

        # Should preserve order
        assert imports[0].module == ".a"
        assert imports[1].module == ".b"
        assert imports[2].module == ".c"


def test_sys_modules_tracking() -> None:
    """Verify sys.modules access is tracked."""
    source = """
import sys
mod = sys.modules["os"]
"""
    from forger.analyzer.dynamic import DynamicImportAnalyzer

    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(h.hint_type == "sys_modules" for h in hints)


def test_import_error_handling() -> None:
    """Verify Forger handles ImportError patterns like CPython."""
    source = """
try:
    import ssl
except ImportError:
    ssl = None

try:
    import _sqlite3
except ImportError:
    _sqlite3 = None
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "ssl" in modules
    assert "_sqlite3" in modules


def test_conditional_stdlib_imports() -> None:
    """Test conditional stdlib imports based on platform."""
    source = """
import sys
if sys.platform == 'win32':
    import winreg
    import msvcrt
elif sys.platform == 'darwin':
    import ctypes
else:
    import resource
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    # All branches should be detected
    assert "sys" in modules
    # At least some platform-specific imports should be found
    assert len(modules) >= 2


def test_lazy_import_detection() -> None:
    """Verify Forger detects lazy imports (inside functions)."""
    source = """
def heavy_operation():
    import numpy as np
    import pandas as pd
    return np.array([1, 2, 3])

def light_operation():
    import json
    return json.dumps({"key": "value"})
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    modules = {i.module for i in imports}
    assert "numpy" in modules
    assert "pandas" in modules
    assert "json" in modules


def test_relative_import_resolution() -> None:
    """Verify relative imports resolve correctly per CPython semantics."""
    source = """
from . import sibling
from .submodule import thing
from ..parent import parent_thing
from ...grandparent import gp_thing
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)

    # Check levels
    assert imports[0].level == 1
    assert imports[1].level == 1
    assert imports[2].level == 2
    assert imports[3].level == 3


def test_future_import_handling() -> None:
    """Verify __future__ imports are detected."""
    source = """
from __future__ import annotations
from __future__ import division
from __future__ import print_function
"""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(source)
    assert all(i.module == "__future__" for i in imports)


def test_compiler_respects_import_semantics() -> None:
    """End-to-end: compiler should trace imports like CPython does."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a chain: main -> utils -> helpers -> constants
        (root / "main.py").write_text("from utils import process\n")
        (root / "utils.py").write_text(
            "from helpers import transform\n"
            "import json\n"
            "def process(): return transform()\n"
        )
        (root / "helpers.py").write_text(
            "from constants import MAX_SIZE\n"
            "def transform(): return MAX_SIZE\n"
        )
        (root / "constants.py").write_text("MAX_SIZE = 100\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None
        reachable = compiler.graph.find_reachable()
        assert "main" in reachable
        assert "utils" in reachable
        assert "helpers" in reachable
        assert "constants" in reachable
