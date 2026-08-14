"""Public API for forger.py project extensions.

This module provides the build-time API that projects use in their forger.py
to declare dependencies that cannot be inferred automatically.
"""

from __future__ import annotations

from pathlib import Path


class _ForgerContext:
    """Holds build-time state shared between forger.py and the compiler."""

    def __init__(self) -> None:
        self._included_paths: list[Path] = []
        self._included_globs: list[str] = []
        self._included_modules: list[str] = []
        self._included_resources: list[Path] = []
        self._metadata: dict[str, str] = {}

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


# Global context instance — populated by forger.py during build.
_context: _ForgerContext | None = None


def _get_context() -> _ForgerContext:
    global _context
    if _context is None:
        _context = _ForgerContext()
    return _context


def include(pattern: str | Path, *, recursive: bool = True) -> None:
    """Include files matching a glob pattern or a single path.

    Args:
        pattern: Glob pattern (e.g., ``"templates/**/*"``) or a single file/directory path.
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
        module_name: Fully qualified module name (e.g., ``"app.plugins.my_plugin"``).

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
