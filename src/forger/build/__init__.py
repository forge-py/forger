"""Build subsystem.

Consumes `.forge` artifacts and target definitions to produce deployable
applications backed by real CPython runtimes.
"""

from __future__ import annotations

from forger.build.cpython_pruner import (
    CPythonBuildPlan,
    CPythonPruner,
    collect_stdlib_imports,
)
from forger.build.cpython_source import CPythonSourceManager
from forger.build.recipes import BuildRecipe, recipe_for
from forger.build.runtime_builder import (
    CpythonRuntimeBuilder,
    RuntimeBuildResult,
    RuntimeSpec,
)
from forger.build.targets import TargetPlatform, TargetRegistry, default_registry

__all__ = [
    "TargetPlatform",
    "TargetRegistry",
    "default_registry",
    "CPythonSourceManager",
    "CPythonPruner",
    "CPythonBuildPlan",
    "collect_stdlib_imports",
    "BuildRecipe",
    "recipe_for",
    "CpythonRuntimeBuilder",
    "RuntimeSpec",
    "RuntimeBuildResult",
]
