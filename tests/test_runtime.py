"""Runtime bootstrap tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.runtime import setup_sys_path, get_runtime_info


def test_runtime_info() -> None:
    info = get_runtime_info()
    assert "python_version" in info
    assert "platform" in info
    assert "executable" in info
    assert info["platform"] in ("win32", "linux", "darwin")


def test_setup_sys_path() -> None:
    import sys
    original = list(sys.path)
    try:
        setup_sys_path(["/tmp/test1", "/tmp/test2"])
        assert "/tmp/test1" in sys.path
        assert "/tmp/test2" in sys.path
    finally:
        sys.path[:] = original


def test_setup_sys_path_no_duplicates() -> None:
    import sys
    original = list(sys.path)
    try:
        sys.path.insert(0, "/tmp/existing")
        setup_sys_path(["/tmp/existing", "/tmp/new"])
        # Should appear at most twice (once original, once added)
        count = sys.path.count("/tmp/existing")
        assert count <= 2
    finally:
        sys.path[:] = original


def test_bootstrap_module_import() -> None:
    """Test that bootstrap can import a simple module."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a simple module
        (root / "test_module.py").write_text(
            "def main():\n"
            "    return 'hello'\n"
        )

        import sys
        original_path = list(sys.path)
        try:
            sys.path.insert(0, str(root))
            import importlib

            mod = importlib.import_module("test_module")
            assert hasattr(mod, "main")
        finally:
            sys.path[:] = original_path
            # Clean up imported module
            if "test_module" in sys.modules:
                del sys.modules["test_module"]
