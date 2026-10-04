"""Advanced resource analyzer tests."""

from __future__ import annotations

from forger.analyzer.resources import ResourceAnalyzer


def test_open_call_detection() -> None:
    source = 'with open("config.json") as f: data = f.read()\n'
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.path_expr == "config.json" for a in accesses)


def test_path_read_text() -> None:
    source = '''
from pathlib import Path
content = Path("templates/index.html").read_text()
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any("templates/index.html" in a.path_expr for a in accesses)


def test_glob_pattern() -> None:
    source = '''
import glob
files = glob.glob("data/*.csv")
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.path_expr == "data/*.csv" for a in accesses)


def test_os_listdir() -> None:
    source = '''
import os
entries = os.listdir("static")
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.path_expr == "static" for a in accesses)


def test_dynamic_path_not_resolved() -> None:
    source = '''
path = get_config_path()
with open(path) as f:
    pass
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    # Should detect open() but mark as dynamic
    assert any(a.is_dynamic for a in accesses)


def test_fstring_path() -> None:
    source = '''
name = "config"
with open(f"{name}.json") as f:
    pass
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.is_dynamic for a in accesses)


def test_pkgutil_get_data() -> None:
    source = '''
import pkgutil
data = pkgutil.get_data("mypackage", "data/schema.json")
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert len(accesses) >= 1


def test_importlib_resources() -> None:
    source = '''
from importlib.resources import read_text
content = read_text("mypackage.data", "config.yaml")
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert len(accesses) >= 1


def test_no_resource_access() -> None:
    source = '''
x = 1 + 2
def foo():
    return x * 2
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert len(accesses) == 0


def test_multiple_accesses() -> None:
    source = '''
with open("a.txt") as f: pass
with open("b.txt") as f: pass
with open("c.txt") as f: pass
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert len(accesses) == 3


def test_path_glob() -> None:
    source = '''
from pathlib import Path
files = list(Path("src").glob("*.py"))
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.path_expr == "*.py" for a in accesses)


def test_path_rglob() -> None:
    source = '''
from pathlib import Path
files = list(Path("src").rglob("*.py"))
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.path_expr == "*.py" for a in accesses)


def test_scandir() -> None:
    source = '''
import os
with os.scandir("data") as entries:
    for entry in entries:
        pass
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.path_expr == "data" for a in accesses)


def test_access_type_tracking() -> None:
    source = '''
with open("file.txt") as f: pass
from pathlib import Path
Path("file.txt").read_text()
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    types = {a.access_type for a in accesses}
    assert "open" in types
    assert "Path.read_text" in types


def test_source_location_tracking() -> None:
    source = '''
import os
# line 2 comment
with open("test.txt") as f: pass  # line 4
'''
    analyzer = ResourceAnalyzer()
    accesses = analyzer.analyze_source(source)
    assert any(a.line >= 1 for a in accesses)


def test_parents_subscript_resolves() -> None:
    """``Path(__file__).parents[N] / "x"`` is treated as a file-dir root."""
    source = (
        "from pathlib import Path\n"
        "p = Path(__file__).parents[2] / 'data' / 'shared.csv'\n"
        "p.read_text()\n"
    )
    accesses = ResourceAnalyzer().analyze_source(source)
    assert accesses, "expected resource access to be discovered"
    assert accesses[0].segments == ["data", "shared.csv"]
    assert accesses[0].is_dynamic is False


def test_bare_open_string_under_with() -> None:
    """`with open("name") as f:` is detected even without an explicit receiver."""
    source = 'with open("a/b.txt") as f: f.read()\n'
    accesses = ResourceAnalyzer().analyze_source(source)
    assert accesses
    # `a/b.txt` is not anchored to a file dir, so segments stay None;
    # the path expression is still recorded for diagnostics.
    assert accesses[0].path_expr == "a/b.txt"
