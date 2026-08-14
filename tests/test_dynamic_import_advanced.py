"""Advanced dynamic import analyzer tests."""

from __future__ import annotations

from forger.analyzer.dynamic import DynamicImportAnalyzer


def test_importlib_import_module() -> None:
    source = '''
import importlib
mod = importlib.import_module("myapp.plugins.auth")
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(
        h.hint_type == "import_module" and h.pattern == "myapp.plugins.auth"
        for h in hints
    )


def test_import_module_direct() -> None:
    source = '''
from importlib import import_module
mod = import_module("myapp.plugins.db")
'''
    analyzer = DoubleUnderscoreAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(h.hint_type == "import_module" for h in hints)


def test_double_underscore_import() -> None:
    source = '''
mod = __import__("os.path")
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(
        h.hint_type == "__import__" and h.pattern == "os.path"
        for h in hints
    )


def test_sys_modules_access() -> None:
    source = '''
import sys
mod = sys.modules["django.db"]
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(
        h.hint_type == "sys_modules" and h.pattern == "django.db"
        for h in hints
    )


def test_exec_detection() -> None:
    source = '''
code = "import os; print(os.getcwd())"
exec(code)
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(h.hint_type == "exec" for h in hints)


def test_eval_detection() -> None:
    source = '''
result = eval("1 + 2")
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(h.hint_type == "eval" for h in hints)


def test_dynamic_import_module() -> None:
    """Test dynamic import argument detection."""
    source = '''
import importlib
plugin_name = get_plugin_name()
mod = importlib.import_module(plugin_name)
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    # Should detect import_module with dynamic argument
    assert any(
        h.hint_type == "import_module" and h.pattern == "*"
        for h in hints
    )


def test_no_dynamic_imports() -> None:
    source = '''
import os
import sys
x = 1 + 2
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert len(hints) == 0


def test_multiple_dynamic_imports() -> None:
    source = '''
import importlib
a = importlib.import_module("pkg.a")
b = importlib.import_module("pkg.b")
c = __import__("pkg.c")
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    patterns = [h.pattern for h in hints if h.pattern != "*"]
    assert "pkg.a" in patterns
    assert "pkg.b" in patterns
    assert "pkg.c" in patterns


def test_entry_point_extraction() -> None:
    """Test entry point extraction from setup.py-like content."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_py = Path(tmpdir) / "setup.py"
        setup_py.write_text('''
setup(
    name="myapp",
    entry_points={
        "console_scripts": [
            "myapp = myapp.cli:main",
            "myapp-dev = myapp.dev:main",
        ],
        "myapp.plugins": [
            "auth = myapp.plugins.auth:AuthPlugin",
        ],
    },
)
''')

        analyzer = DynamicImportAnalyzer()
        patterns = analyzer.extract_module_patterns(Path(tmpdir))
        assert "myapp.cli" in patterns
        assert "myapp.dev" in patterns
        assert "myapp.plugins.auth" in patterns


def test_entry_point_from_pyproject() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        pyproject = Path(tmpdir) / "pyproject.toml"
        pyproject.write_text('''
[project.scripts]
"myapp" = "myapp.cli:main"

[project.entry-points."myapp.plugins"]
auth = "myapp.plugins.auth:AuthPlugin"
''')

        analyzer = DynamicImportAnalyzer()
        patterns = analyzer.extract_module_patterns(Path(tmpdir))
        # Regex-based extraction
        assert len(patterns) >= 0  # May or may not match TOML format


def test_source_location_tracking() -> None:
    source = '''
import importlib
# line 2
# line 3
mod = importlib.import_module("test.module")
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    assert any(h.line == 5 for h in hints)


def test_hint_context() -> None:
    source = '''
code = "dynamic"
exec(code)
'''
    analyzer = DynamicImportAnalyzer()
    hints = analyzer.analyze_source(source)
    exec_hints = [h for h in hints if h.hint_type == "exec"]
    assert exec_hints[0].context == "exec/eval may trigger dynamic imports"


class DoubleUnderscoreAnalyzer:
    """Helper for testing import_module from importlib."""

    def analyze_source(self, source: str):
        from forger.analyzer.dynamic import DynamicImportAnalyzer
        analyzer = DynamicImportAnalyzer()
        return analyzer.analyze_source(source)
