"""Tests for the forger.py API — defineConfig, ForgerConfig, ForgerConfigDict."""

from __future__ import annotations

import pytest

from forger.api import ForgerConfig, defineConfig, get_context


@pytest.fixture(autouse=True)
def _reset_context():
    """Clear the forger context for a clean test."""
    import forger.api as api_module
    api_module._context = None


def test_defineconfig_minimal() -> None:
    cfg = defineConfig({})
    assert isinstance(cfg, ForgerConfig)
    assert cfg.entry == ""
    assert cfg.include == []
    assert cfg.exclude == []


def test_defineconfig_entry() -> None:
    cfg = defineConfig({"entry": "manage.py"})
    assert cfg.entry == "manage.py"


def test_defineconfig_include() -> None:
    cfg = defineConfig(
        {
            "include": [
                "templates/**/*",
                "static/**/*",
            ]
        }
    )
    assert "templates/**/*" in cfg.include
    assert "static/**/*" in cfg.include


def test_defineconfig_exclude() -> None:
    cfg = defineConfig(
        {
            "exclude": [
                "**/__pycache__",
                "*.pyc",
            ]
        }
    )
    assert "**/__pycache__" in cfg.exclude
    assert "*.pyc" in cfg.exclude


def test_defineconfig_optimizers() -> None:
    cfg = defineConfig(
        {
            "optimizers": {
                "django": {"settings_module": "myblog.settings"},
            }
        }
    )
    assert "django" in cfg.optimizers
    assert cfg.optimizers["django"]["settings_module"] == "myblog.settings"


def test_defineconfig_targets() -> None:
    cfg = defineConfig({"targets": ["linux-x64", "windows-x64"]})
    assert "linux-x64" in cfg.targets
    assert "windows-x64" in cfg.targets


def test_defineconfig_metadata() -> None:
    cfg = defineConfig({"metadata": {"author": "test", "version": "1.0"}})
    assert cfg.metadata["author"] == "test"
    assert cfg.metadata["version"] == "1.0"


def test_defineconfig_forger_config_instance() -> None:
    fc = ForgerConfig(entry="app.py")
    cfg = defineConfig(fc)
    assert cfg is fc
    assert cfg.entry == "app.py"


def test_context_stores_config() -> None:
    cfg = defineConfig({"entry": "main.py"})
    ctx = get_context()
    assert ctx.config is cfg


def test_forger_py_execution() -> None:
    """Test executing a forger.py file."""
    import runpy
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        forger_py = Path(tmpdir) / "forger.py"
        forger_py.write_text(
            "from forger import defineConfig\n"
            "defineConfig({\n"
            '    "entry": "manage.py",\n'
            '    "include": ["templates/**/*", "static/**/*"],\n'
            '    "exclude": ["**/__pycache__"],\n'
            '})\n'
        )

        _reset_context()
        runpy.run_path(
            str(forger_py),
            init_globals={"__file__": str(forger_py)},
            run_name="__forger__",
        )

        ctx = get_context()
        assert ctx.config is not None
        assert ctx.config.entry == "manage.py"
        assert "templates/**/*" in ctx.config.include
        assert "static/**/*" in ctx.config.include
        assert "**/__pycache__" in ctx.config.exclude


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402


def _reset_context() -> None:
    """Clear the forger context for a clean test."""
    import forger.api as api_module
    api_module._context = None
