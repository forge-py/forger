"""Public API for forger.py project extensions.

This module provides the build-time API that projects use in their forger.py
to declare dependencies that cannot be inferred automatically.

Two styles are supported:

1. **Declarative** (Vite-style ``defineConfig``):
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

2. **Imperative** (legacy ``include`` / ``include_module``):
   ```python
   from forger import include, include_module, metadata

   include("templates/**/*")
   include_module("myapp.plugins.auth")
   metadata(entry_point="manage.py")
   ```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OptimizerConfig:
    """Configuration for a single framework optimizer."""

    name: str
    options: dict[str, Any] = field(default_factory=dict)


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
    """

    entry: str = ""
    project: str = ""
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    optimizers: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal build context (populated by forger.py at compile time)
# ---------------------------------------------------------------------------


class _ForgerContext:
    """Holds build-time state shared between forger.py and the compiler."""

    def __init__(self) -> None:
        self._included_paths: list[Path] = []
        self._included_globs: list[str] = []
        self._included_modules: list[str] = []
        self._included_resources: list[Path] = []
        self._metadata: dict[str, str] = {}
        self._config: ForgerConfig | None = None

    def add_path(self, path: Path) -> None:
        self._included_paths.append(path.resolve())

    def add_glob(self, pattern: str) -> None:
        self._included_globs.append(pattern)

    def add_module(self, module: str) -> None:
        self._included_modules.append(module)

    def add_resource(self, path: Path) -> None:
        self._included_resources.append(path.resolve())

    def set_metadata(self, key: str, value: str) -> None:
        self._metadata[key] = value

    @property
    def included_paths(self) -> list[Path]:
        return list(self._included_paths)

    @property
    def included_globs(self) -> list[str]:
        return list(self._included_globs)

    @property
    def included_modules(self) -> list[str]:
        return list(self._included_modules)

    @property
    def included_resources(self) -> list[Path]:
        return list(self._included_resources)

    @property
    def metadata(self) -> dict[str, str]:
        return dict(self._metadata)

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
    config: dict[str, Any] | ForgerConfig,
) -> ForgerConfig:
    """Define a Forger build configuration (Vite-style).

    Accepts either a ``ForgerConfig`` instance or a plain dict that will be
    used to construct one.  The returned config is stored in the global
    forger context so the compiler can read it at build time.

    Args:
        config: A ``ForgerConfig`` or dict with keys matching
            ``ForgerConfig`` fields.

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
            metadata={
                k: str(v)
                for k, v in config.get("metadata", {}).items()
            },
        )

    _get_context().set_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Imperative API (legacy)
# ---------------------------------------------------------------------------


def include(pattern: str | Path, *, recursive: bool = True) -> None:
    """Include files matching a glob pattern or a single path.

    Args:
        pattern: Glob pattern (e.g., ``"templates/**/*"``) or a single
            file/directory path.
        recursive: Whether to recurse into subdirectories (default ``True``).

    Example:
        ```python
        from forger import include
        include("templates/**/*")
        include("generated/", recursive=True)
        ```
    """
    ctx = _get_context()
    path = Path(pattern) if not isinstance(pattern, Path) else pattern

    if path.exists():
        if path.is_dir():
            glob_pat = str(pattern)
            if not glob_pat.endswith("/"):
                glob_pat += "/"
            glob_pat += ("**/*" if recursive else "*")
            ctx.add_glob(glob_pat)
        else:
            ctx.add_path(path.resolve())
    elif "*" in str(pattern) or "/" in str(pattern) or "\\" in str(pattern):
        ctx.add_glob(str(pattern))
    else:
        ctx.add_glob(str(pattern))


def include_module(module_name: str) -> None:
    """Include a specific Python module by fully qualified name.

    Args:
        module_name: Fully qualified module name
            (e.g., ``"app.plugins.my_plugin"``).

    Example:
        ```python
        from forger import include_module
        include_module("myapp.plugins.auth")
        ```
    """
    ctx = _get_context()
    ctx.add_module(module_name)


def include_resource(path: str | Path) -> None:
    """Include a specific resource file or directory.

    Args:
        path: Path to the resource file or directory.

    Example:
        ```python
        from forger import include_resource
        include_resource("data/schema.json")
        ```
    """
    ctx = _get_context()
    p = Path(path)
    if p.exists():
        ctx.add_resource(p.resolve())
    else:
        ctx.add_glob(str(path))


def metadata(key: str, value: str) -> None:
    """Set build-time metadata key-value pair.

    Args:
        key: Metadata key.
        value: Metadata value.
    """
    ctx = _get_context()
    ctx.set_metadata(key, value)


def get_context() -> _ForgerContext:
    """Get the current forger build context (for advanced usage)."""
    return _get_context()
