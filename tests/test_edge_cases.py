"""Edge case and boundary tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.analyzer.imports import ImportAnalyzer
from forger.compiler import Compiler


def test_empty_source() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source("")
    assert len(imports) == 0


def test_only_comments() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "# This is a comment\n# Another comment\n"
    )
    assert len(imports) == 0


def test_only_strings() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        '"import os"  # This is a string, not an import\n'
        "'import sys'  # Another string\n"
    )
    assert len(imports) == 0


def test_import_in_string_not_detected() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        'msg = "import os"\n'
        "x = 'import sys'\n"
    )
    # AST should not detect imports in strings
    assert len(imports) == 0


def test_unicode_identifiers() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "import os\n"
        "变量 = 42\n"
    )
    assert len(imports) == 1


def test_multiline_string_with_import() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        '"""This is a docstring with import os inside."""\n'
        "import json\n"
    )
    # Only the real import should be detected
    assert len(imports) == 1
    assert imports[0].module == "json"


def test_triple_quoted_string_import() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        'x = """\nimport os\nimport sys\n"""\n'
        "import json\n"
    )
    assert len(imports) == 1
    assert imports[0].module == "json"


def test_import_in_try_except_finally() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "try:\n"
        "    import ssl\n"
        "except ImportError:\n"
        "    import _ssl\n"
        "finally:\n"
        "    import sys\n"
    )
    modules = {i.module for i in imports}
    assert modules == {"ssl", "_ssl", "sys"}


def test_import_in_with_statement() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "with open('f.txt') as f:\n"
        "    import json\n"
    )
    assert len(imports) == 1
    assert imports[0].module == "json"


def test_import_in_comprehension() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "x = [import_os for _ in []]  # Not a real import\n"
        "import os\n"
    )
    assert len(imports) == 1


def test_class_with_imports() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "class Foo:\n"
        "    import os\n"
        "    def method(self):\n"
        "        import json\n"
    )
    modules = {i.module for i in imports}
    assert modules == {"os", "json"}


def test_generator_expression_import() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "import os\n"
        "data = (x for x in [1, 2, 3])\n"
    )
    assert len(imports) == 1


def test_lambda_no_imports() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "f = lambda x: x + 1\n"
        "g = lambda: None\n"
    )
    assert len(imports) == 0


def test_decorated_function() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "import functools\n"
        "@functools.lru_cache\n"
        "def cached(): pass\n"
    )
    assert len(imports) == 1


def test_type_annotations() -> None:
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "from typing import List, Dict, Optional, Union\n"
        "from typing import get_type_hints\n"
    )
    assert len(imports) == 2


def test_compiled_file_handling() -> None:
    """Test handling of .pyc files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")
        pycache = root / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-310.pyc").write_bytes(b"compiled")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Should only find .py files
        assert all(f.suffix == ".py" for f in compiler.source_files)


def test_hidden_files_handling() -> None:
    """Test that hidden files are handled correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")
        (root / ".hidden.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Hidden files may or may not be included depending on config
        assert len(compiler.source_files) >= 1


def test_symlink_handling() -> None:
    """Test that symlinks don't break the compiler."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")

        # Try to create a symlink (may not work on all systems)
        try:
            link = root / "link.py"
            link.symlink_to(root / "main.py")
        except (OSError, NotImplementedError):
            pass

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None


def test_special_characters_in_path() -> None:
    """Test handling of special characters in file paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "main.py").write_text("pass\n")

        # Create a subdirectory with spaces
        subdir = root / "my package"
        subdir.mkdir()
        (subdir / "module with spaces.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert compiler.graph is not None


def test_very_long_module_name() -> None:
    """Test handling of very long module names."""
    analyzer = ImportAnalyzer()
    long_name = ".".join(["part"] * 50)
    imports = analyzer.analyze_source(f"import {long_name}\n")
    assert len(imports) == 1
    assert len(imports[0].module) > 100


def test_mixed_line_endings() -> None:
    """Test handling of mixed line endings."""
    analyzer = ImportAnalyzer()
    source = "import os\r\nimport sys\nimport json\r\n"
    imports = analyzer.analyze_source(source)
    assert len(imports) == 3


def test_no_entry_point_file() -> None:
    """Test compiler when entry point file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "other.py").write_text("pass\n")

        output = root / "app.forge"
        compiler = Compiler(root, "nonexistent", output)
        compiler.analyze()

        # Should still work with the entry point registered
        assert compiler.graph is not None
        assert "nonexistent" in compiler.graph.entry_points()


def test_duplicate_imports() -> None:
    """Test handling of duplicate imports."""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "import os\n"
        "import os\n"
        "import os\n"
    )
    assert len(imports) == 3  # All three are detected


def test_from_import_with_none_module() -> None:
    """Test from import where module is None (top-level relative)."""
    analyzer = ImportAnalyzer()
    imports = analyzer.analyze_source(
        "from . import sibling\n"
    )
    assert imports[0].module == ""
    assert imports[0].level == 1
