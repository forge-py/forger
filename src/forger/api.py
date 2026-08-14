"""Public API for forger.py project extensions.

This module provides the build-time API that projects use in their forger.py
to declare dependencies that cannot be inferred automatically.

```python
from forger import defineConfig

forger_config = defineConfig({
    "entry": "manage.py",
    "include": ["templates/**/*", "static/**/*"],
    "exclude": ["**/__pycache__"],
    "optimizers": {"django": {"settings_module": "myblog.settings"}},
    "targets": ["linux-x64", "windows-x64"],
})
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict


class OptimizerOptions(TypedDict, total=False):
    """Typed options passed to a framework optimizer."""

    settings_module: NotRequired[str]
    root_urlconf: NotRequired[str]
    templates_dir: NotRequired[str]
    static_dir: NotRequired[str]
    extra: NotRequired[dict[str, Any]]


class ForgerConfigDict(TypedDict, total=False):
    """Typed dict for the defineConfig input parameter.

    All keys are optional — missing keys fall back to defaults on
    ``ForgerConfig``.
    """

    entry: NotRequired[str]
    project: NotRequired[str]
    include: NotRequired[list[str]]
    exclude: NotRequired[list[str]]
    optimizers: NotRequired[dict[str, OptimizerOptions]]
    targets: NotRequired[list[str]]
    metadata: NotRequired[dict[str, str]]
    dist_dir: NotRequired[str]


@dataclass
class ForgerConfig:
    """Top-level build configuration (Vite-style defineConfig).

    Attributes:
        entry: Entry-point module or script (e.g. ``"manage.py"``).
        project: Project name (for diagnostics).
        include: Glob patterns for files/directories to bundle.
        exclude: Glob patterns for files/directories to skip.
        optimizers: Framework optimizer configurations.
        targets: Target platforms to build for.
        metadata: Arbitrary key-value metadata.
        dist_dir: Output directory name (default: ``"dist"``).
    """

    entry: str = ""
    project: str = ""
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    optimizers: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    dist_dir: str = "dist"


# ---------------------------------------------------------------------------
# Internal build context (populated by forger.py at compile time)
# ---------------------------------------------------------------------------


class _ForgerContext:
    """Holds build-time state shared between forger.py and the compiler."""

    def __init__(self) -> None:
        self._config: ForgerConfig | None = None

    @property
    def config(self) -> ForgerConfig | None:
        return self._config

    def set_config(self, cfg: ForgerConfig) -> None:
        self._config = cfg


# Global context instance — populated by forger.py during build.
_context: _ForgerContext | None = None


def _get_context() -> _ForgerContext:
    global _context
    if _context is None:
        _context = _ForgerContext()
    return _context


# ---------------------------------------------------------------------------
# Declarative API (Vite-style)
# ---------------------------------------------------------------------------


def defineConfig(  # noqa: N802
    config: ForgerConfigDict | ForgerConfig,
) -> ForgerConfig:
    """Define a Forger build configuration (Vite-style).

    Accepts either a ``ForgerConfig`` instance or a typed dict that will be
    used to construct one.  The returned config is stored in the global
    forger context so the compiler can read it at build time.

    Args:
        config: A ``ForgerConfig`` or ``ForgerConfigDict`` with keys
            matching ``ForgerConfig`` fields.

    Returns:
        The resolved ``ForgerConfig``.

    Example:
        ```python
        from forger import defineConfig

        forger_config = defineConfig({
            "entry": "manage.py",
            "project": "myblog",
            "include": [
                "templates/**/*",
                "static/**/*",
                "locale/**/*",
            ],
            "exclude": [
                "**/__pycache__",
                "*.pyc",
                "*.sqlite3",
            ],
            "optimizers": {
                "django": {
                    "settings_module": "myblog.settings",
                },
            },
            "targets": ["linux-x64", "windows-x64"],
        })
        ```
    """
    if isinstance(config, ForgerConfig):
        cfg = config
    else:
        cfg = ForgerConfig(
            entry=config.get("entry", ""),
            project=config.get("project", ""),
            include=config.get("include", []),
            exclude=config.get("exclude", []),
            optimizers=config.get("optimizers", {}),
            targets=config.get("targets", []),
            metadata={k: str(v) for k, v in config.get("metadata", {}).items()},
            dist_dir=config.get("dist_dir", "dist"),
        )

    _get_context().set_config(cfg)
    return cfg


def get_context() -> _ForgerContext:
    """Get the current forger build context (for advanced usage)."""
    return _get_context()
