"""Tests for Cython-aware analysis (cimport parsing, .pyx discovery).

Covers the analyzer's two paths (AST + text fallback), the module-name
mapping, and the compiler integration that turns cimports into native
dependency edges.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forger.analyzer.cython import (
    CYTHON_SUFFIXES,
    CythonImportAnalyzer,
    SymbolChecker,
    collect_used_names,
    count_cython_markers,
    cython_module_name,
    discover_cython_sources,
    enumerate_imports,
)

# ---------------------------------------------------------------------------
# CythonImportAnalyzer — AST path
# ---------------------------------------------------------------------------


def test_plain_imports_in_parseable_pyx(tmp_path: Path) -> None:
    f = tmp_path / "mod.pyx"
    f.write_text("import os\nimport json as j\n", encoding="utf-8")
    imports = CythonImportAnalyzer().analyze_file(f)
    modules = {i.module for i in imports}
    assert modules == {"os", "json"}


def test_cimport_statement_via_text_fallback(tmp_path: Path) -> None:
    # `cimport` alone makes ast.parse fail -> scanner path
    f = tmp_path / "ext.pyx"
    f.write_text(
        "cimport numpy as np\n"
        "cimport libc.math\n",
        encoding="utf-8",
    )
    imports = CythonImportAnalyzer().analyze_file(f)
    modules = {i.module for i in imports}
    assert modules == {"numpy", "libc.math"}
    assert all(not i.is_from for i in imports)


def test_from_cimport_names_and_alias(tmp_path: Path) -> None:
    f = tmp_path / "ext.pyx"
    f.write_text(
        "from libc.stdlib cimport malloc, free\n"
        "from numpy.random cimport bitgen as bg\n",
        encoding="utf-8",
    )
    imports = CythonImportAnalyzer().analyze_file(f)
    by_mod = {i.module: i for i in imports}
    assert set(by_mod) == {"libc.stdlib", "numpy.random"}
    assert by_mod["libc.stdlib"].names == ["malloc", "free"]
    assert by_mod["numpy.random"].names == ["bitgen"]  # alias stripped


def test_relative_cimports_levels(tmp_path: Path) -> None:
    f = tmp_path / "pkg" / "sub.pyx"
    f.parent.mkdir()
    f.write_text(
        "from . cimport helper\n"
        "from ..shared cimport util\n"
        "from .helpers cimport fast_func\n",
        encoding="utf-8",
    )
    imports = CythonImportAnalyzer().analyze_file(f)
    levels = {(i.module, i.level) for i in imports}
    assert ("", 1) in levels
    assert ("..shared", 2) in levels or ("shared", 2) in levels
    assert (".helpers", 1) in levels or ("helpers", 1) in levels


def test_ast_path_still_finds_python_imports_in_pyx(tmp_path: Path) -> None:
    # Pure-Python-syntax .pyx parses via AST; both kinds must appear.
    f = tmp_path / "mixed.pyx"
    f.write_text("import json\nfrom os import path\n", encoding="utf-8")
    imports = CythonImportAnalyzer().analyze_file(f)
    assert {i.module for i in imports} == {"json", "os"}


def test_mixed_cdef_file_uses_scanner(tmp_path: Path) -> None:
    f = tmp_path / "heavy.pyx"
    f.write_text(
        "cdef int _compute(double x) noexcept nogil:\n"
        "    return int(x * 2)\n"
        "\n"
        "cimport cython\n"
        "from libc.string cimport memcpy\n"
        "\n"
        "def run():\n"
        '    s = "not a real import"\n'
        "    return _compute(3.0)\n",
        encoding="utf-8",
    )
    analyzer = CythonImportAnalyzer()
    imports = analyzer.analyze_file(f)
    mods = [(i.module, i.is_from) for i in imports]
    assert ("cython", False) in mods
    assert ("libc.string", True) in mods
    assert not any("real import" in m[0] for m in mods), "string content leaked"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_discover_cython_sources_skips_build_dirs(tmp_path: Path) -> None:
    (tmp_path / "src.pyx").write_text("", encoding="utf-8")
    (tmp_path / ".venv" / "lib.pyx").parent.mkdir()
    (tmp_path / ".venv" / "lib.pyx").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pxd").write_text("", encoding="utf-8")

    found = discover_cython_sources(tmp_path)
    names = [f.name for f in found]
    assert names == ["src.pyx"]


def test_cython_module_name_mapping(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    ext = pkg / "fast.pyx"
    ext.write_text("", encoding="utf-8")

    assert cython_module_name(tmp_path, ext) == "pkg.fast"
    assert cython_module_name(tmp_path, tmp_path / "top.pyx") == "top"
    # A file outside the project root has no project-relative module name.
    outside = tmp_path.parent / "elsewhere.pyx"
    assert cython_module_name(tmp_path, outside) is None


def test_count_markers() -> None:
    src = "cdef int f() nogil:\n    pass\ncpdef g():\n    pass\n"
    assert count_cython_markers(src) == 3


def test_suffixes_constant() -> None:
    assert set(CYTHON_SUFFIXES) == {".pyx", ".pxd"}


# ---------------------------------------------------------------------------
# Compiler integration: relative cimport from a package __init__.pyx
# ---------------------------------------------------------------------------


def test_relative_cimport_in_package_init(tmp_path: Path) -> None:
    """``from .core cimport X`` in ``pkg/__init__.pyx`` resolves to ``pkg.core``."""
    from forger.compiler import Compiler
    from forger.core import NodeType

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.pyx").write_text("cdef int x = 1\n", encoding="utf-8")
    (pkg / "__init__.pyx").write_text(
        "from .core cimport x\n",
        encoding="utf-8",
    )

    compiler = Compiler(
        project_root=tmp_path,
        entry_point="pkg",
        output_path=tmp_path / "app.forge",
    )
    compiler.analyze()

    pkg_node = compiler.graph.get_node("pkg")
    assert pkg_node is not None
    # The relative cimport must have created a NativeDependency edge from
    # the package node to its submodule.
    deps = compiler.graph.dependencies_of("pkg")
    assert "pkg.core" in deps, f"expected pkg.core in deps, got {deps}"
    core_node = compiler.graph.get_node("pkg.core")
    assert core_node is not None
    assert core_node.node_type == NodeType.NativeExtension


@pytest.fixture()
def proj(tmp_path: Path) -> Path:
    """A tiny project with helpers.py defining two functions."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "helpers.py").write_text(
        "def used_fn():\n    return 1\n\n\ndef other_fn():\n    return 2\n",
        encoding="utf-8",
    )
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("value = 42\n", encoding="utf-8")
    (root / "pkg" / "sub.py").write_text(
        "CONSTANT = 7\n\n\ndef fn():\n    return CONSTANT\n",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# SymbolChecker — existence and usage
# ---------------------------------------------------------------------------


def test_missing_symbol_detected(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "from helpers import used_fn, nonexistent\n",
        filename="main.py",
    )
    missing = [i for i in issues if i.kind == "missing_symbol"]
    assert len(missing) == 1
    assert "nonexistent" in missing[0].detail
    assert "helpers.nonexistent" == missing[0].symbol


def test_existing_symbols_not_flagged(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "from helpers import used_fn\nprint(used_fn())\n",
        filename="main.py",
    )
    assert issues == []


def test_unused_import_detected(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "from helpers import used_fn\n",
        filename="main.py",
    )
    unused = [i for i in issues if i.kind == "unused_import"]
    assert len(unused) == 1
    assert unused[0].symbol == "used_fn"


def test_attribute_access_counts_as_usage(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "from helpers import used_fn\nresult = [used_fn]\n",
        filename="main.py",
    )
    assert issues == []


def test_module_import_unused(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "import helpers\n",
        filename="main.py",
    )
    unused = [i for i in issues if i.kind == "unused_import"]
    assert len(unused) == 1
    assert unused[0].symbol == "helpers"


def test_module_import_used_via_attr(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "import helpers\nhelpers.used_fn()\n",
        filename="main.py",
    )
    assert issues == []


def test_third_party_modules_skipped(proj: Path) -> None:
    checker = SymbolChecker(proj)
    # Third-party/stdlib modules can't be existence-checked (no source in
    # project), but usage is still checked — so use them.
    issues = checker.check_source(
        "from flask import Flask\n"
        "app = Flask('x')\n"
        "from collections import OrderedDict\n"
        "d = OrderedDict()\n",
        filename="main.py",
    )
    assert issues == []


def test_relative_import_resolution(proj: Path) -> None:
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "from .sub import MISSING_THING\nx = 1\n",
        filename=str(proj / "pkg" / "user.py"),
        resolve_relative_to="pkg.user",
    )
    missing = [i for i in issues if i.kind == "missing_symbol"]
    assert len(missing) == 1
    assert "MISSING_THING" in missing[0].detail


def test_package_init_exports_submodules(proj: Path) -> None:
    checker = SymbolChecker(proj)
    # pkg/__init__.py exists; `from pkg import sub` resolves to the submodule.
    issues = checker.check_source(
        "from pkg import sub\nprint(sub.CONSTANT)\n",
        filename="main.py",
    )
    assert issues == []


def test_unparseable_source_conservative_usage(proj: Path) -> None:
    # Cython-style source: usage check falls back to token scan.
    checker = SymbolChecker(proj)
    issues = checker.check_source(
        "from helpers import used_fn\ncdef void x() nogil:\n    used_fn()\n",
        filename="ext.pyx",
    )
    assert issues == []


def test_check_project_end_to_end(proj: Path) -> None:
    # Add a file with a bad import so the whole-project sweep finds it.
    (proj / "broken.py").write_text(
        "from helpers import nope_never\n",
        encoding="utf-8",
    )
    checker = SymbolChecker(proj)
    issues = checker.check_project()
    bad = [
        i for i in issues
        if i.kind == "missing_symbol" and "nope_never" in i.detail
    ]
    assert len(bad) == 1


# ---------------------------------------------------------------------------
# Shared enumeration helpers
# ---------------------------------------------------------------------------


def test_enumerate_imports_forms() -> None:
    src = (
        "import os\n"
        "from sys import path\n"
        "from . import sibling\n"
    )
    imps = enumerate_imports(src)
    assert [(i.module, i.level, i.is_from) for i in imps] == [
        ("os", 0, False),
        ("sys", 0, True),
        ("", 1, True),
    ]


def test_collect_used_names_attrs_and_tokens() -> None:
    assert collect_used_names("a.b(c)") >= {"a", "b", "c"}
    assert "cdef" in collect_used_names("cdef int x")  # token fallback


# ---------------------------------------------------------------------------
# Compiler integration
# ---------------------------------------------------------------------------


def test_compiler_registers_native_extension_nodes(tmp_path: Path) -> None:
    from forger.compiler import Compiler
    from forger.core import NodeType

    (tmp_path / "main.py").write_text(
        "import fastmath\nprint(fastmath.go())\n",
        encoding="utf-8",
    )
    (tmp_path / "fastmath.pyx").write_text(
        "cimport cython\n\n\ndef go():\n    return 2.0\n",
        encoding="utf-8",
    )

    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "app.forge",
    )
    compiler.analyze()

    assert compiler.graph is not None
    native_nodes = compiler.graph.nodes_by_type(NodeType.NativeExtension)
    ids = {n.id for n in native_nodes}
    assert "fastmath" in ids

    # The cimport inside fastmath.pyx must NOT create a phantom project node;
    # `cython` isn't a project module so it's skipped.
    assert compiler.graph.get_node("cython") is None


def test_compiler_symbol_issues_collected(tmp_path: Path) -> None:
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text(
        "from helpers import used_fn\nprint(used_fn())\n",
        encoding="utf-8",
    )
    (tmp_path / "helpers.py").write_text(
        "def used_fn():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.py").write_text(
        "from helpers import does_not_exist\n",
        encoding="utf-8",
    )
    (tmp_path / "excess_imports.py").write_text(
        "import helpers\nx = 1\n",
        encoding="utf-8",
    )

    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "app.forge",
    )
    compiler.analyze()

    kinds = {i.kind for i in compiler.symbol_issues}
    # missing_symbol: the explicit `does_not_exist` import.
    assert "missing_symbol" in kinds
    # Whole-module `import X` that never names an attribute of X.
    assert "unused_import" in kinds

    summary = compiler.diagnostic_summary()
    assert "Symbol issues:" in summary


def test_compiler_no_cython_no_crash(tmp_path: Path) -> None:
    from forger.compiler import Compiler

    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    compiler = Compiler(
        project_root=tmp_path,
        entry_point="main",
        output_path=tmp_path / "app.forge",
    )
    compiler.analyze()
    assert compiler.symbol_issues == []
    assert compiler.cython_sources == []
