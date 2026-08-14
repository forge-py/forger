"""AST-level symbol reachability analysis and tree shaking.

Implements a worklist-based fixed-point algorithm for determining which
Python definitions are actually referenced, starting from configured
entry points. Operates at the symbol level, not just module level.

Two distinct phases:
  Phase 1: Build symbol table + reference graph → reachability analysis
  Phase 2: AST pruning using reachable symbol set

Do NOT mutate the AST during reachability analysis.
"""

from __future__ import annotations

import ast
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Purity classification
# ---------------------------------------------------------------------------


class Purity:
    """Side-effect classification for AST statements."""

    PURE = "pure"
    IMPURE = "impure"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Symbol definition
# ---------------------------------------------------------------------------


@dataclass
class SymbolDef:
    """A single symbol definition in a module."""

    module_id: str  # e.g. "utils"
    name: str       # e.g. "used", "helper", "Foo"
    node: ast.AST   # The AST node defining this symbol
    scope: str      # "module", "function", "class"
    purity: str = Purity.UNKNOWN

    @property
    def symbol_id(self) -> str:
        """Stable identity: (module, name)."""
        return f"{self.module_id}.{self.name}"


# ---------------------------------------------------------------------------
# Symbol table
# ---------------------------------------------------------------------------


class SymbolTable:
    """Indexed symbol table for a single module."""

    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        self.definitions: dict[str, SymbolDef] = {}
        self.references: dict[str, set[str]] = {}  # symbol_id → set of referenced symbol_ids

    def add_definition(self, symbol: SymbolDef) -> None:
        self.definitions[symbol.symbol_id] = symbol

    def add_reference(self, from_id: str, to_id: str) -> None:
        if from_id not in self.references:
            self.references[from_id] = set()
        self.references[from_id].add(to_id)

    def get_definitions(self) -> dict[str, SymbolDef]:
        return self.definitions

    def get_references(self, symbol_id: str) -> set[str]:
        return self.references.get(symbol_id, set())


# ---------------------------------------------------------------------------
# Scope-aware symbol collector
# ---------------------------------------------------------------------------


class _ScopeCollector(ast.NodeVisitor):
    """Walk the AST and collect symbol definitions with proper scope tracking."""

    def __init__(self, module_id: str, table: SymbolTable) -> None:
        self.module_id = module_id
        self.table = table
        # Stack of (scope_name, {local_names})
        self._scope_stack: list[tuple[str, set[str]]] = [
            ("module", set())
        ]

    def _current_scope(self) -> str:
        return self._scope_stack[-1][0]

    def _current_locals(self) -> set[str]:
        return self._scope_stack[-1][1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_def_helper(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_def_helper(node, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_def_helper(node, "class")

    def _visit_def_helper(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        scope: str,
    ) -> None:
        sym = SymbolDef(
            module_id=self.module_id,
            name=node.name,
            node=node,
            scope=scope,
        )
        self.table.add_definition(sym)
        self._current_locals().add(node.name)
        self._scope_stack.append((scope, set()))
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._current_scope() == "module":
            for target in node.targets:
                names = self._extract_names(target)
                for name in names:
                    sym = SymbolDef(
                        module_id=self.module_id,
                        name=name,
                        node=node,
                        scope="module",
                        purity=self._classify_purity(node),
                    )
                    self.table.add_definition(sym)
                    self._current_locals().add(name)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.target is not None and self._current_scope() == "module":
            name = self._extract_name(node.target)
            if name:
                sym = SymbolDef(
                    module_id=self.module_id,
                    name=name,
                    node=node,
                    scope="module",
                    purity=self._classify_purity(node),
                )
                self.table.add_definition(sym)
                self._current_locals().add(name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            self._current_locals().add(name)
            sym = SymbolDef(
                module_id=self.module_id,
                name=name,
                node=node,
                scope="module",
            )
            self.table.add_definition(sym)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.names:
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                self._current_locals().add(name)
                sym = SymbolDef(
                    module_id=self.module_id,
                    name=name,
                    node=node,
                    scope="module",
                )
                self.table.add_definition(sym)
        self.generic_visit(node)

    # -- helpers --

    @staticmethod
    def _extract_names(target: ast.expr) -> list[str]:
        names: list[str] = []
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Tuple | ast.List):
            for elt in target.elts:
                names.extend(_ScopeCollector._extract_names(elt))
        return names

    @staticmethod
    def _extract_name(target: ast.expr) -> str | None:
        if isinstance(target, ast.Name):
            return target.id
        return None

    @staticmethod
    def _classify_purity(node: ast.AST) -> str:
        """Classify a module-level assignment for side-effect purity."""
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                return Purity.PURE
            if isinstance(
                value,
                (
                    ast.Constant,
                    ast.List,
                    ast.Dict,
                    ast.Set,
                    ast.Tuple,
                    ast.Ellipsis,
                    ast.NameConstant,
                ),
            ):
                return Purity.PURE
            # Function calls, attribute access, etc. are potentially impure
            return Purity.IMPURE
        return Purity.UNKNOWN


# ---------------------------------------------------------------------------
# Reference analyzer
# ---------------------------------------------------------------------------


class _ReferenceAnalyzer(ast.NodeVisitor):
    """Analyze AST expressions and determine what symbols they reference."""

    def __init__(self, module_id: str, table: SymbolTable) -> None:
        self.module_id = module_id
        self.table = table
        self._current_symbol: str | None = None
        self._scope_stack: list[tuple[str, set[str]]] = [
            ("module", set())
        ]

    def _current_locals(self) -> set[str]:
        return self._scope_stack[-1][1]

    # -- enter symbol scopes --

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_scope(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_scope(node.name, node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_scope(node.name, node)

    def _enter_scope(self, name: str, node: ast.AST) -> None:
        symbol_id = f"{self.module_id}.{name}"
        old_symbol = self._current_symbol
        self._current_symbol = symbol_id
        self._scope_stack.append(("scope", set()))

        # Collect params as locals
        args = node.args if hasattr(node, "args") else None
        if args:
            for arg_name in args.args:
                self._current_locals().add(arg_name.arg)
            for arg_name in args.posonlyargs:
                self._current_locals().add(arg_name.arg)
            for arg_name in args.kwonlyargs:
                self._current_locals().add(arg_name.arg)
            if args.vararg:
                self._current_locals().add(args.vararg.arg)
            if args.kwarg:
                self._current_locals().add(args.kwarg.arg)

        self.generic_visit(node)
        self._scope_stack.pop()
        self._current_symbol = old_symbol

    # -- name references --

    def visit_Name(self, node: ast.Name) -> None:
        if self._current_symbol and node.id not in self._current_locals():
            target = f"{self.module_id}.{node.id}"
            self.table.add_reference(self._current_symbol, target)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Mark the object as referenced
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)

    # -- decorator references --

    def visit_Decorated(self, node: ast.DeprecatedAlias) -> None:  # type: ignore[name-defined]
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# SymbolReachabilityAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class ReachabilityResult:
    """Result of symbol reachability analysis."""

    reachable_symbols: set[str] = field(default_factory=set)
    unreachable_symbols: set[str] = field(default_factory=set)
    resource_references: set[str] = field(default_factory=set)


class SymbolReachabilityAnalyzer:
    """Perform symbol-level reachability analysis.

    Starting from root symbols, determines which symbols are reachable
    through static references.

    Uses an iterative worklist (not recursion) to handle deep call chains.
    """

    def __init__(self) -> None:
        self._tables: dict[str, SymbolTable] = {}
        self._reachable: set[str] = set()
        self._resource_refs: set[str] = set()

    def add_module(self, module_id: str, tree: ast.AST) -> None:
        """Index a module's AST into the symbol table."""
        table = SymbolTable(module_id)
        collector = _ScopeCollector(module_id, table)
        collector.visit(tree)
        self._tables[module_id] = table

    def add_root(self, symbol_id: str) -> None:
        """Add a root symbol that is always reachable."""
        self._reachable.add(symbol_id)

    def analyze(self) -> ReachabilityResult:
        """Run the reachability analysis.

        Returns a result with reachable and unreachable symbol sets.
        """
        # Build cross-module reference graph
        self._build_references()

        # Iterative worklist BFS
        worklist: deque[str] = deque(self._reachable)

        while worklist:
            symbol_id = worklist.popleft()
            refs = self._get_all_references(symbol_id)
            for ref in refs:
                if ref not in self._reachable:
                    self._reachable.add(ref)
                    worklist.append(ref)

        # Compute unreachable
        all_symbols: set[str] = set()
        for table in self._tables.values():
            all_symbols.update(table.get_definitions().keys())

        unreachable = all_symbols - self._reachable

        logger.info(
            "Symbol reachability: %d reachable, %d unreachable out of %d total symbols",
            len(self._reachable),
            len(unreachable),
            len(all_symbols),
        )

        return ReachabilityResult(
            reachable_symbols=set(self._reachable),
            unreachable_symbols=set(unreachable),
            resource_references=set(self._resource_refs),
        )

    def _build_references(self) -> None:
        """Analyze each module's AST for symbol references."""
        for module_id, table in self._tables.items():
            # Find the module's top-level node
            mod_node = self._find_module_node(table)
            if mod_node is None:
                continue
            analyzer = _ReferenceAnalyzer(module_id, table)
            analyzer.visit(mod_node)

    def _find_module_node(self, table: SymbolTable) -> ast.Module | ast.AST | None:
        """Get the module AST node from the table."""
        for sym in table.get_definitions().values():
            if isinstance(sym.node, (ast.Module, ast.Expression, ast.Interactive)):
                return sym.node
        return None

    def _get_all_references(self, symbol_id: str) -> set[str]:
        """Get all references from a symbol, including cross-module."""
        refs: set[str] = set()
        module_id = symbol_id.split(".")[0] if "." in symbol_id else symbol_id
        table = self._tables.get(module_id)
        if table:
            refs.update(table.get_references(symbol_id))
        return refs


# ---------------------------------------------------------------------------
# ASTPruner
# ---------------------------------------------------------------------------


class ASTPruner:
    """Prune unreachable symbols from AST modules.

    Given a reachable symbol set, removes unreachable definitions from
    module ASTs while preserving:
    - source locations
    - decorators for retained definitions
    - required imports
    - required side effects
    - ordering
    - scope semantics
    """

    def __init__(self, reachable: set[str]) -> None:
        self.reachable = reachable

    def prune_module(
        self,
        module_id: str,
        tree: ast.AST,
    ) -> ast.AST:
        """Prune unreachable symbols from a module AST.

        Returns a new AST with unreachable definitions removed.
        """
        if not isinstance(tree, ast.Module):
            return tree

        # Determine which symbols in this module are reachable
        module_reachable: set[str] = set()
        for sym_id in self.reachable:
            if sym_id.startswith(f"{module_id}."):
                module_reachable.add(sym_id.split(".", 1)[1])

        # Also keep all side-effectful statements
        new_body: list[ast.stmt] = []

        for stmt in tree.body:
            if self._should_retain(stmt, module_id, module_reachable):
                new_body.append(stmt)

        pruned = ast.Module(body=new_body, type_ignores=[])
        ast.fix_missing_locations(pruned)

        return pruned

    def _should_retain(
        self,
        stmt: ast.stmt,
        module_id: str,
        module_reachable: set[str],
    ) -> bool:
        """Determine if a statement should be retained."""
        # Import statements are always retained (they have side effects)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            return True

        # Function/class definitions
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbol_id = f"{module_id}.{stmt.name}"
            if symbol_id in self.reachable:
                return True
            return False

        # Assignments at module level
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    symbol_id = f"{module_id}.{target.id}"
                    if symbol_id in self.reachable:
                        return True
            # If not reachable, check purity
            return self._has_side_effects(stmt)

        if isinstance(stmt, ast.AnnAssign):
            if stmt.target and isinstance(stmt.target, ast.Name):
                symbol_id = f"{module_id}.{stmt.target.id}"
                if symbol_id in self.reachable:
                    return True
            return self._has_side_effects(stmt)

        # Expressions, global/nonlocal declarations, etc. — retain conservatively
        return True

    @staticmethod
    def _has_side_effects(stmt: ast.stmt) -> bool:
        """Check if a statement likely has side effects."""
        if isinstance(stmt, ast.Expr):
            # Expression statement — could have side effects
            return True
        if isinstance(stmt, ast.Assign):
            # Assignment with a call or complex expression
            if stmt.value and not isinstance(
                stmt.value,
                (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple),
            ):
                return True
        return False