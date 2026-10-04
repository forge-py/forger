"""Scope-aware name minifier for Forger.

A *plugin* (not a built-in compiler pass) that renames Python identifiers
to short forms (``a``, ``b``, ..., ``z``, ``aa``, ``ab``, ...) per the
spec (MINIFIER.md §2, §3). Operates after tree shaking so dead names are
already gone, then walks each module's AST in scope order, collecting:

  - reserved names (Python keywords + builtins + ``__dunder__``)
  - publicly-kept names (config-supplied or inferred from
    ``__all__`` / decorator-marked exports)
  - scope-local names

For each non-reserved, non-public name, the minifier emits a short
identifier. Local scopes share a per-function counter (so ``i``, ``j``,
``k`` recur instead of growing) and top-level scopes share a per-module
counter.

The minifier is opt-in: it runs only when ``ForgerConfig.minify_names``
is truthy. The compiler dispatches it via the plugin chain
(MINIFIER.md §20 — plugins should be able to protect names).
"""

from __future__ import annotations

import ast
import builtins
import keyword
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from forger.optimizer import PluginContext

# Runtime import: MinificationPass is a plugin, must be BasePlugin.
from forger.optimizer import BasePlugin

logger = logging.getLogger(__name__)

# ---- Reserved names -------------------------------------------------------

_PYTHON_BUILTINS: frozenset[str] = frozenset(dir(builtins))


def _is_reserved(name: str) -> bool:
    """True for names the minifier must never rename."""
    if name.startswith("__") and name.endswith("__"):
        return True
    if name.startswith("_"):
        # Single-underscore names are conventionally "private but used" —
        # safer to leave alone than to silently shadow external references.
        return True
    if keyword.iskeyword(name):
        return True
    if name in _PYTHON_BUILTINS:
        return True
    return False


# ---- Identifier generation -----------------------------------------------


def _generate_name(index: int) -> str:
    """Return the short name for position ``index`` (a..z, aa..zz, aaa..).

    Sequence: a(0), b(1), ..., z(25), aa(26), ab(27), ..., az(51),
    ba(52), ..., zz(701), aaa(702), ... Implemented as a bijective
    base-26 conversion (no zero digit), so each position produces a
    distinct identifier with no leading-zero ambiguity.
    """
    if index < 0:
        raise ValueError("index must be non-negative")
    digits: list[int] = []
    n = index + 1  # bijective base-26 works in 1-indexed space
    while n > 0:
        n -= 1
        digits.append(n % 26)
        n //= 26
    return "".join(chr(ord("a") + d) for d in reversed(digits))


@dataclass
class NameGenerator:
    """Per-scope counter producing short identifiers."""

    _counter: int = 0
    # Pool of names that have been used in any scope of this module and
    # must not be re-issued (avoids name collisions across nested scopes).
    _used: set[str] = field(default_factory=set)

    def next(self) -> str:
        while True:
            candidate = _generate_name(self._counter)
            self._counter += 1
            if candidate in self._used:
                continue
            self._used.add(candidate)
            return candidate

    def reserve(self, name: str) -> None:
        """Prevent this name from being issued later."""
        self._used.add(name)


# ---- Scope walker --------------------------------------------------------


class _ScopeCollector(ast.NodeVisitor):
    """Phase 1: collect names defined in each scope (no rewriting yet).

    Tracks which names must NOT be renamed (globals referenced across
    modules, names listed in ``__all__``, decorated symbols).
    """

    PUBLIC_DECORATORS: ClassVar[set[str]] = {
        "property",
        "staticmethod",
        "classmethod",
        "app.route",
        "pytest.fixture",
    }

    def __init__(self, keep_public: set[str]) -> None:
        self.keep_public: set[str] = keep_public
        # module-level: name -> reason it's kept
        self.kept: dict[str, str] = {}

    def _keep(self, name: str, reason: str) -> None:
        if not _is_reserved(name):
            self.kept.setdefault(name, reason)

    @staticmethod
    def _decorator_matches(dec_text: str) -> bool:
        """Return True if a decorator's unparsed text names a public-keeping
        decorator.

        Decorators in real code are often called with arguments
        (``@app.route('/')``, ``@pytest.fixture(scope='module')``); we
        match the bare name (the part before any ``(``) against the
        known public list.
        """
        bare = dec_text.split("(", 1)[0].strip()
        return bare in {
            "property",
            "staticmethod",
            "classmethod",
            "app.route",
            "pytest.fixture",
        }

    def _keep(self, name: str, reason: str) -> None:
        if not _is_reserved(name):
            self.kept.setdefault(name, reason)

    def visit_Module(self, node: ast.Module) -> None:
        # Honor __all__ if present.
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                        if isinstance(stmt.value, (ast.List, ast.Tuple)):
                            for elt in stmt.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    self._keep(elt.value, "__all__")
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                # docstring at module top — leave alone, irrelevant here
                pass
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Default: rename. Public-API names are kept only when explicitly
        # marked via:
        #   - ``__all__`` in the module body (handled in visit_Module)
        #   - a public-keeping decorator (property, app.route, etc.)
        # Underscore-prefixed names are also left alone (conservative:
        # they may be referenced externally by other modules' users).
        for dec in node.decorator_list:
            text = ast.unparse(dec) if hasattr(ast, "unparse") else ""
            if self._decorator_matches(text):
                self._keep(node.name, "decorated")
                break
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # No automatic "class is public" rule. Renames by default; opt
        # in via ``__all__`` or a user-supplied keep set.
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Module-level ALL_CAPS or names listed in keep_public are kept.
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and self._is_module_level(node):
                if tgt.id.isupper() or tgt.id in self.keep_public:
                    self._keep(tgt.id, "config")
        self.generic_visit(node)

    def _is_module_level(self, node: ast.stmt) -> bool:
        # Approximate: the parent of the stmt is ast.Module. Cheap
        # enough for module-level detection; nested defs re-enter via
        # generic_visit and find the *real* module parent.
        return getattr(node, "_scope_level", 0) == 0


class _PublicMarkPass(ast.NodeTransformer):
    """Annotate every statement with ``_scope_level`` (0 = module, 1 = function, ...)."""

    def __init__(self) -> None:
        self._level = 0

    def generic_visit(self, node):  # type: ignore[override]
        # No-op visitor — used only to walk and stamp.
        for child in ast.iter_child_nodes(node):
            child._scope_level = self._level
            self._walk(child)
        return node

    def _walk(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                saved = self._level
                self._level += 1
                child._scope_level = self._level
                for grandchild in ast.iter_child_nodes(child):
                    grandchild._scope_level = self._level
                self._generic(child)
                self._level = saved
            else:
                child._scope_level = self._level
                self._walk(child)

    def _generic(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self._walk(child)


# ---- Name rewrite pass ---------------------------------------------------


class _Rewriter(ast.NodeTransformer):
    """Replace eligible identifiers with short forms in a single AST."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id in self.mapping:
            return ast.Name(id=self.mapping[node.id], ctx=node.ctx)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        # Rename parameters and decorators before recursing into body.
        new_args = self._rename_arguments(node.args)
        new_decorators = [self._rename_expr(d) for d in node.decorator_list]
        new_returns = self._rename_annotation(node.returns)
        # Rename the function name itself when in the mapping.
        new_name = self.mapping.get(node.name, node.name)
        self.generic_visit(node)
        node.name = new_name
        node.args = new_args
        node.decorator_list = new_decorators
        node.returns = new_returns
        return node

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        new_name = self.mapping.get(node.name, node.name)
        new_decorators = [self._rename_expr(d) for d in node.decorator_list]
        new_bases = [self._rename_expr(b) for b in node.bases]
        new_keywords = [self._rename_keyword(k) for k in node.keywords]
        self.generic_visit(node)
        node.name = new_name
        node.decorator_list = new_decorators
        node.bases = new_bases
        node.keywords = new_keywords
        return node

    def visit_Import(self, node: ast.Import) -> ast.Import:
        node.names = [self._rename_alias(a) for a in node.names]
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        node.names = [self._rename_alias(a) for a in node.names]
        return node

    # -- helpers --

    def _rename_arguments(self, args: ast.arguments) -> ast.arguments:
        def rename_arg(a: ast.arg) -> ast.arg:
            new = ast.arg(
                arg=self.mapping.get(a.arg, a.arg),
                annotation=self._rename_annotation(a.annotation),
            )
            return new

        return ast.arguments(
            posonlyargs=[rename_arg(a) for a in args.posonlyargs],
            args=[rename_arg(a) for a in args.args],
            vararg=args.vararg and rename_arg(args.vararg),
            kwonlyargs=[rename_arg(a) for a in args.kwonlyargs],
            kw_defaults=args.kw_defaults,
            kwarg=args.kwarg and rename_arg(args.kwarg),
            defaults=args.defaults,
        )

    @staticmethod
    def _rename_annotation(ann: ast.expr | None) -> ast.expr | None:
        # Annotations reference real names (the runtime evaluates them),
        # so we must not rename inside them.
        return ann

    def _rename_expr(self, expr: ast.expr) -> ast.expr:
        new = self.visit(expr)
        return new if new is not None else expr

    def _rename_alias(self, alias: ast.alias) -> ast.alias:
        new_asname = self.mapping.get(alias.asname, alias.asname) if alias.asname else None
        return ast.alias(name=alias.name, asname=new_asname)

    @staticmethod
    def _rename_keyword(k: ast.keyword) -> ast.keyword:
        # ``cls=`` in classmethod etc. — the *name* is a Python-level
        # keyword name we shouldn't rewrite.
        return k


# ---- Top-level driver ----------------------------------------------------


def minify_module(
    tree: ast.Module,
    keep: set[str] | None = None,
) -> tuple[ast.Module, dict[str, str]]:
    """Return a (new tree, rename-map) after scope-aware minification.

    The mapping records ``original → new`` for every renamed symbol so
    tests / debuggers can decode. The keep set adds names to the
    no-rename list (e.g. from ``__all__`` or plugin-supplied keepers).
    """
    keep = set(keep or set())
    combined_keep = _collect_kept_names(tree, keep)
    gen = NameGenerator()
    for name in combined_keep:
        gen.reserve(name)

    mapping: dict[str, str] = {}
    for stmt in tree.body:
        _add_top_level_renames(stmt, combined_keep, gen, mapping)
    _add_import_alias_renames(tree.body, combined_keep, gen, mapping)

    if not mapping:
        return tree, mapping

    rewriter = _Rewriter(mapping)
    new_tree = rewriter.visit(tree)
    if isinstance(new_tree, ast.Module):
        ast.fix_missing_locations(new_tree)
        return new_tree, mapping
    return tree, mapping


def _collect_kept_names(tree: ast.Module, keep: set[str]) -> set[str]:
    """Walk the tree to discover module-level keep set + user-supplied keep."""
    marker = _PublicMarkPass()
    marker.generic_visit(tree)
    collector = _ScopeCollector(keep)
    collector.visit(tree)
    return set(collector.kept) | keep


def _add_top_level_renames(
    stmt: ast.stmt,
    combined_keep: set[str],
    gen: NameGenerator,
    mapping: dict[str, str],
) -> None:
    """Maybe add ``stmt``'s binding name to the rename map."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if stmt.name not in combined_keep:
            mapping[stmt.name] = gen.next()
        return
    if isinstance(stmt, ast.Assign):
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name) and tgt.id not in combined_keep:
                mapping[tgt.id] = gen.next()
        return
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        if stmt.target.id not in combined_keep:
            mapping[stmt.target.id] = gen.next()


def _add_import_alias_renames(
    body: list[ast.stmt],
    combined_keep: set[str],
    gen: NameGenerator,
    mapping: dict[str, str],
) -> None:
    """``import x as y`` / ``from x import y as z`` → rename the alias."""
    for stmt in body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.asname and alias.asname not in combined_keep:
                    mapping.setdefault(alias.asname, gen.next())
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.asname and alias.asname not in combined_keep:
                    mapping.setdefault(alias.asname, gen.next())


class MinificationPass(BasePlugin):
    """Plugin that minifies names in cached ASTs.

    Opt-in: callers add this to the plugin chain (or the compiler does
    so when ``ForgerConfig.minify_names`` is truthy). The pass works on
    the same cached ASTs that ``transform_ast`` already mutates — the
    compiler unparses the new tree into node content downstream.
    """

    name = "minification"

    def __init__(self, *, extra_keep: set[str] | None = None) -> None:
        self.extra_keep = set(extra_keep or set())
        self._rename_count = 0
        self._module_count = 0

    def transform_ast(
        self,
        module_id: str,
        tree: ast.Module,
        *,
        context: PluginContext,
    ) -> None:
        new_tree, mapping = minify_module(tree, keep=self.extra_keep)
        if mapping:
            # Replace the AST in place — the compiler unparses the
            # cached tree into node content after this hook returns.
            tree.body = new_tree.body
            # Carry over other module-level fields.
            tree.type_ignores = new_tree.type_ignores
            self._rename_count += len(mapping)
        self._module_count += 1

    @property
    def rename_count(self) -> int:
        return self._rename_count

    @property
    def module_count(self) -> int:
        return self._module_count
