"""Optimizer framework.

Framework-specific intelligence lives in Python optimizer modules.
Optimizers detect their target framework, analyze project configuration,
and contribute dependencies to the unified dependency graph.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forger.core import DependencyGraph  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


@dataclass
class OptimizerContext:
    """Context passed to optimizers during analysis."""

    project_root: Path
    source_files: list[Path] = field(default_factory=list)
    installed_packages: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    def has_package(self, name: str) -> bool:
        """Check if a package is installed in the project."""
        return name.lower() in {k.lower() for k in self.installed_packages}

    def get_package_version(self, name: str) -> str | None:
        """Get the version of an installed package."""
        for key, value in self.installed_packages.items():
            if key.lower() == name.lower():
                return value
        return None


class Optimizer(ABC):
    """Base class for framework optimizers."""

    # Name of the framework this optimizer handles
    name: str = ""

    # Minimum confidence threshold for detection (0.0 - 1.0)
    min_confidence: float = 0.5

    @abstractmethod
    def detect(self, context: OptimizerContext) -> float:
        """Detect whether this framework is present in the project.

        Returns a confidence score between 0.0 and 1.0.
        Scores below min_confidence are treated as non-detection.
        """

    @abstractmethod
    def analyze(self, context: OptimizerContext, graph: DependencyGraph) -> None:  # type: ignore[name-defined]
        """Analyze the project and contribute dependencies to the graph.

        This is called only if detect() returns a score >= min_confidence.
        """

    def description(self) -> str:
        """Human-readable description of this optimizer."""
        return f"Optimizer for {self.name}"


def discover_optimizers() -> list[type[Optimizer]]:
    """Discover available optimizers by importing the optimizers package."""
    import importlib
    import pkgutil

    optimizers: list[type[Optimizer]] = []

    try:
        import forger.optimizers as opt_package

        for _importer, modname, _ispkg in pkgutil.iter_modules(
            opt_package.__path__, opt_package.__name__ + "."
        ):
            try:
                module = importlib.import_module(modname)
                # Look for Optimizer subclasses
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, Optimizer)
                        and attr is not Optimizer
                    ):
                        optimizers.append(attr)
            except ImportError as e:
                logger.warning("Failed to import optimizer %s: %s", modname, e)
    except ImportError as e:
        logger.warning("Optimizers package not available: %s", e)

    return optimizers


def run_optimizers(
    context: OptimizerContext,
    graph: DependencyGraph,  # type: ignore[name-defined]
) -> list[str]:
    """Run all detected optimizers and return the list of active optimizer names."""
    active: list[str] = []
    optimizer_classes = discover_optimizers()

    for opt_class in optimizer_classes:
        optimizer = opt_class()
        confidence = optimizer.detect(context)

        if confidence >= optimizer.min_confidence:
            logger.info(
                "Running optimizer %s (confidence: %.2f)", optimizer.name, confidence
            )
            try:
                optimizer.analyze(context, graph)
                active.append(optimizer.name)
            except Exception as e:
                logger.error(
                    "Optimizer %s failed: %s", optimizer.name, e, exc_info=True
                )
        else:
            logger.debug(
                "Skipping optimizer %s (confidence: %.2f < %.2f)",
                optimizer.name,
                confidence,
                optimizer.min_confidence,
            )

    return active
