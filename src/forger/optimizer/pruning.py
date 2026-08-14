"""Module graph reachability analysis and unreachable module pruning.

Implements a mark-and-sweep reachability pass over the dependency graph.
Uses an iterative worklist (not recursion) to handle arbitrarily deep graphs.

Complexity: O(V + E) time, O(V) space.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forger.core import DependencyGraph  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retention reasons
# ---------------------------------------------------------------------------


class RetainReason:
    """Why a module was retained."""

    ROOT = "root"
    EXPLICIT_KEEP = "explicit_keep"
    STATIC_DEPENDENCY = "static_dependency"
    DYNAMIC_DEPENDENCY = "dynamic_dependency"
    SIDE_EFFECT = "side_effect"


# ---------------------------------------------------------------------------
# Pruning result
# ---------------------------------------------------------------------------


@dataclass
class PruningResult:
    """Result of a pruning pass."""

    reachable_modules: set[str] = field(default_factory=set)
    pruned_modules: set[str] = field(default_factory=set)
    retain_reasons: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pruning pass
# ---------------------------------------------------------------------------


class ModuleGraphPruner:
    """Mark-and-sweep module graph reachability analysis.

    Given entry points and a dependency graph, determines which modules are
    reachable and which can be safely pruned.

    Handles:
    - normal dependency chains
    - branching / shared dependencies
    - circular imports
    - self-references
    - multiple entry points
    - disconnected components
    - large graphs without recursion limit issues
    """

    def __init__(self, graph: DependencyGraph) -> None:
        self.graph = graph
        self._reachable: set[str] = set()
        self._reasons: dict[str, str] = {}

    def add_roots(self, roots: set[str]) -> None:
        """Add root modules that are always reachable."""
        for root in roots:
            self._reachable.add(root)
            self._reasons[root] = RetainReason.ROOT

    def run(self) -> PruningResult:
        """Execute the pruning pass.

        Returns a result with reachable and pruned module sets.
        """
        # Iterative BFS worklist
        worklist: deque[str] = deque(self._reachable)

        while worklist:
            module_id = worklist.popleft()
            deps = self._get_dependencies(module_id)
            for dep in deps:
                if dep not in self._reachable:
                    self._reachable.add(dep)
                    self._reasons[dep] = RetainReason.STATIC_DEPENDENCY
                    worklist.append(dep)

        # Determine all modules and pruned set
        all_modules = self._all_module_ids()
        pruned = all_modules - self._reachable

        logger.info(
            "Pruning: %d reachable, %d pruned out of %d total modules",
            len(self._reachable),
            len(pruned),
            len(all_modules),
        )

        result = PruningResult(
            reachable_modules=set(self._reachable),
            pruned_modules=set(pruned),
            retain_reasons=dict(self._reasons),
        )

        # Mark reachable/required in the graph
        for mod_id in self._reachable:
            node = self.graph.get_node(mod_id)
            if node is not None:
                node.required = True

        return result

    def _get_dependencies(self, module_id: str) -> list[str]:
        """Get all dependencies of a module from the graph."""
        return self.graph.dependencies_of(module_id)

    def _all_module_ids(self) -> set[str]:
        """Get all module IDs in the graph."""
        ids: set[str] = set()
        for node in self.graph.all_nodes().values():
            ids.add(node.id)
        return ids

    def explain_retained(self, module_id: str) -> str:
        """Get the reason a module was retained."""
        return self._reasons.get(module_id, RetainReason.STATIC_DEPENDENCY)

    def explain_pruned(self, module_id: str) -> str:
        """Get the reason a module was pruned."""
        return "no reachable module depends on it"
