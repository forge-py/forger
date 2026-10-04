"""Forger error hierarchy.

A small, opinionated set of exceptions used by the compiler and plugin
runner. The structure follows PLUGIN_ARCHITECTURE.md §20 / PHILOSOPHY.md
§34: a failed plugin must surface its plugin name, the hook that
failed, and the module/source file the hook was operating on so the
diagnostic carries enough context to act on.
"""

from __future__ import annotations

from typing import Any


class ForgerError(Exception):
    """Base class for all Forger-emitted errors."""


class CompilerError(ForgerError):
    """A configuration or pipeline invariant was violated."""


class PluginError(ForgerError):
    """A plugin hook raised.

    Carries enough metadata for the diagnostic to point at the
    offending plugin + hook + module without re-running the build.
    """

    def __init__(
        self,
        message: str,
        *,
        plugin_name: str,
        hook: str,
        module_id: str | None = None,
        source_file: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.plugin_name = plugin_name
        self.hook = hook
        self.module_id = module_id
        self.source_file = source_file
        self.cause = cause

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "plugin_error",
            "message": str(self),
            "plugin": self.plugin_name,
            "hook": self.hook,
            "module_id": self.module_id,
            "source_file": self.source_file,
            "cause": repr(self.cause) if self.cause else None,
        }

    def __str__(self) -> str:
        where = self.module_id or self.source_file or "<global>"
        return f"Plugin {self.plugin_name!r} failed in {self.hook} on {where}: {self.args[0]}"


class SourceError(ForgerError):
    """A user source file has invalid syntax or otherwise broke parsing.

    Distinct from :class:`PluginError` so the diagnostic can say
    "your code is wrong" rather than "a plugin failed while processing
    your code".
    """


class ResourceError(ForgerError):
    """A required resource could not be located or read."""


# Hooks whose failure must abort the build. ``resolve_id`` and ``load``
# silently producing wrong results would corrupt downstream graph state,
# so failures there must surface as exceptions. Transform-style hooks
# keep the legacy log-and-continue behavior to preserve existing
# expectations.
STRICT_HOOKS: frozenset[str] = frozenset({"resolve_id", "load"})
