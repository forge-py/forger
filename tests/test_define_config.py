"""Tests for the defineConfig API."""

from __future__ import annotations

import pytest

from forger.api import ForgerConfig, defineConfig, get_context


@pytest.fixture(autouse=True)
def _reset_context(monkeypatch: pytest.MonkeyPatch):
    """Reset the global forger context before each test."""
    from forger import api

    api._context = None


def test_defineconfig_with_dict() -> None:
    cfg = defineConfig(
        {
            "entry": "manage.py",
            "project": "myblog",
            "include": ["templates/**/*", "static/**/*"],
            "exclude": ["**/__pycache__"],
            "optimizers": {"django": {"settings_module": "myblog.settings"}},
            "targets": ["linux-x64"],
        }
    )

    assert isinstance(cfg, ForgerConfig)
    assert cfg.entry == "manage.py"
    assert cfg.project == "myblog"
    assert cfg.include == ["templates/**/*", "static/**/*"]
    assert cfg.exclude == ["**/__pycache__"]
    assert cfg.optimizers == {"django": {"settings_module": "myblog.settings"}}
    assert cfg.targets == ["linux-x64"]


def test_defineconfig_with_forger_config_instance() -> None:
    fc = ForgerConfig(entry="app.py", project="test")
    cfg = defineConfig(fc)

    assert cfg is fc
    assert cfg.entry == "app.py"


def test_defineconfig_stores_in_context() -> None:
    cfg = defineConfig({"entry": "main.py"})
    ctx = get_context()
    assert ctx.config is cfg


def test_defineconfig_defaults() -> None:
    cfg = defineConfig({})
    assert cfg.entry == ""
    assert cfg.project == ""
    assert cfg.include == []
    assert cfg.exclude == []
    assert cfg.optimizers == {}
    assert cfg.targets == []
    assert cfg.metadata == {}


def test_defineconfig_metadata_coerced_to_str() -> None:
    cfg = defineConfig({"metadata": {"key": 42}})
    assert cfg.metadata == {"key": "42"}


def test_forger_config_dataclass_defaults() -> None:
    fc = ForgerConfig()
    assert fc.entry == ""
    assert fc.project == ""
    assert fc.include == []
    assert fc.exclude == []
    assert fc.optimizers == {}
    assert fc.targets == []
    assert fc.metadata == {}
