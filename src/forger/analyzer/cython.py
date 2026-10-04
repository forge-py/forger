"""Cython-aware static analysis for Forger.

Handles Cython source files (``.pyx``, ``.pxd``) and the Cython-specific
import forms that a plain Python AST analyzer misses:

* ``cimport module`` / ``from module cimport name``
* ``from . cimport name`` (relative cimports)
* ``from module cimport name as alias``
* ``cimport module as alias``

Cython modules become native-extension nodes in the dependency graph
(``NodeType.NativeExtension``) reached via ``EdgeType.NativeDependency``
edges, so tree-shaking and bundling treat them like any other native
artifact rather than pure-Python modules.

Also provides :class:`SymbolChecker`, which verifies that ``from X import
name`` statements reference symbols that actually exist in ``X`` (when
``X`` is part of the project), and that imported names are actually used
in the importing file. Results are diagnostics only — they never remove
code from the graph (correctness before optimization).
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CYTHON_SUFFIXES = (".pyx", ".pxd")

_CYTHON_KEYWORDS = frozenset({
    "cdef", "cpdef", "ctypedef", "cimport", "nogil", "gil",
    "inline", "api", "noexcept", "extern",
})

_SKIP_PARTS = {
    "__pycache__", ".venv", "venv", ".git",
    "node_modules", "target", "dist", "build",
}


@dataclass
class CythonImportInfo:
    """One import-like statement discovered in a Cython source file."""

    module: str  # dotted module path; dot-prefixed when relative
    names: list[str] = field(default_factory=list)  # for from...cimport
    level: int = 0  # relative import depth (0 = absolute)
    is_from: bool = False  # True for `from X import/cimport ...`
    source_file: str = ""
    line: int = 0


@dataclass
class SymbolIssue:
    """A diagnostic about an imported symbol."""

    kind: str  # "missing_symbol" | "unused_import"
    symbol: str
    source_file: str
    line: int
    detail: str = ""


class CythonImportAnalyzer:
    """Analyze Cython files for imports, cimports, and native deps.

    Parsing strategy mirrors the compiler's correctness-first philosophy:

    1. Try :func:`ast.parse`. Cython syntax is mostly a superset of Python;
       files without C-level declarations parse fine and give precise line
       numbers.
    2. If parsing fails (likely due to ``cdef``/``ctypedef``/...), fall
       back to a conservative regex scan. The scanner may over-report on
       strings/comments but never under-report real imports.
    """

    def __init__(self) -> None:
        self._imports: list[CythonImportInfo] = []

    def analyze_file(self, filepath: Path) -> list[CythonImportInfo]:
        self._imports = []
        try:
            source = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return []

        try:
            tree = ast.parse(source, filename=str(filepath))
        except SyntaxError:
            # Cython-only syntax present; use the conservative scanner.
            return self._scan_text(source, str(filepath))

        self._visit_tree(tree, str(filepath))
        return list(self._imports)

    def analyze_source(self, source: str, filename: str = "<string>") -> list[CythonImportInfo]:
        self._imports = []
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            return self._scan_text(source, filename)
        self._visit_tree(tree, filename)
        return list(self._imports)

    # -- AST path ---------------------------------------------------------------

    def _visit_tree(self, tree: ast.AST, filename: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._imports.append(
                        CythonImportInfo(
                            module=alias.name,
                            source_file=filename,
                            line=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                level = node.level or 0
                module = node.module or ""
                if level > 0 and module:
                    module = "." * level + module
                self._imports.append(
                    CythonImportInfo(
                        module=module,
                        names=[a.name for a in node.names],
                        level=level,
                        is_from=True,
                        source_file=filename,
                        line=node.lineno,
                    )
                )

    # -- Text fallback path ------------------------------------------------------

    def _scan_text(self, source: str, filename: str) -> list[CythonImportInfo]:
        results: list[CythonImportInfo] = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            m = re.match(r"cimport\s+([\w.]+)(?:\s+as\s+\w+)?\s*(?:#.*)?$", stripped)
            if m:
                results.append(
                    CythonImportInfo(module=m.group(1), source_file=filename, line=lineno)
                )
                continue

            m = re.match(r"from\s+((?:\.*[\w.]*)*)\s*cimport\s+(.+?)\s*(?:#.*)?$", stripped)
            if m:
                mod_part = m.group(1).strip()
                level = len(mod_part) - len(mod_part.lstrip("."))
                mod_part = mod_part[level:].strip()
                names = [n.strip().split(" as ")[0].strip() for n in m.group(2).split(",")]
                results.append(
                    CythonImportInfo(
                        module=mod_part,
                        names=[n for n in names if n],
                        level=level,
                        is_from=True,
                        source_file=filename,
                        line=lineno,
                    )
                )
                continue

            # Plain import/from lines still count while scanning non-parsing files.
            info = self._parse_plain_line(stripped)
            if info is not None:
                info.source_file = filename
                info.line = lineno
                results.append(info)

        return results

    @staticmethod
    def _parse_plain_line(stripped: str) -> CythonImportInfo | None:
        m = re.match(r"import\s+([\w.,\s]+?)(?:\s+as\s+\w+)?\s*(?:#.*)?$", stripped)
        if m:
            first = m.group(1).split(",")[0].strip()
            return CythonImportInfo(module=first) if first else None
        m = re.match(r"from\s+([\w.]+)\s+import\s+(.+?)\s*(?:#.*)?$", stripped)
        if m:
            names = [n.strip().split(" as ")[0].strip() for n in m.group(2).split(",")]
            return CythonImportInfo(module=m.group(1), names=[n for n in names if n], is_from=True)
        return None


def discover_cython_sources(project_root: Path) -> list[Path]:
    """Find .pyx/.pxd files under root, skipping caches/venvs/build dirs."""
    found: list[Path] = []
    for pattern in ("*.pyx", "*.pxd"):
        for f in sorted(project_root.rglob(pattern)):
            if any(part in _SKIP_PARTS for part in f.parts):
                continue
            found.append(f)
    return found


def cython_module_name(project_root: Path, pyx_path: Path) -> str | None:
    """Project-relative module name for a Cython file (None outside root)."""
    try:
        rel = pyx_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    parts = [p for p in parts if p != "__init__"]
    return ".".join(parts) if parts else None


def count_cython_markers(source: str) -> int:
    """Count occurrences of Cython-specific keywords (diagnostic helper)."""
    words = re.findall(r"[A-Za-z_]\w*", source)
    return sum(1 for w in words if w in _CYTHON_KEYWORDS)


class SymbolChecker:
    """Verifies imported symbols exist and are used within their importer.

    Two checks, both diagnostic-only:

    * existence: ``from X import name`` where ``X`` resolves to a project
      source file — does ``name`` actually exist at X's top level?
    * usage: is each imported name referenced anywhere in the importing
      file's body?

    Unresolvable modules (stdlib, third-party, Cython-compiled artifacts)
    are skipped silently — unknown means keep, not report.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self._modules_cache: dict[str, Path] | None = None
        self._defs_cache: dict[str, set[str]] = {}

    # -- public API --------------------------------------------------------------

    def check_source(
        self,
        source: str,
        filename: str,
        resolve_relative_to: str | None = None,
    ) -> list[SymbolIssue]:
        """Check one file's import statements for symbol problems."""
        issues: list[SymbolIssue] = []
        used_names = collect_used_names(source)

        for imp in enumerate_imports(source):
            targets = self._resolve_targets(imp, resolve_relative_to or "")
            target_mod = next(
                (t for t in targets if t in self.project_module_paths()), None
            )

            if imp.is_from:
                for name in imp.names:
                    if target_mod is not None:
                        defined = self._defined_symbols(target_mod)
                        if defined is not None and name not in defined:
                            issues.append(
                                SymbolIssue(
                                    kind="missing_symbol",
                                    symbol=f"{target_mod}.{name}",
                                    source_file=filename,
                                    line=imp.line,
                                    detail=(
                                        f"'{name}' not found in project module "
                                        f"'{target_mod}'"
                                    ),
                                )
                            )
                            continue  # usage check is moot for missing symbols
                    if name not in used_names:
                        issues.append(
                            SymbolIssue(
                                kind="unused_import",
                                symbol=name,
                                source_file=filename,
                                line=imp.line,
                                detail=(
                                    f"'{name}' imported from "
                                    f"'{target_mod or imp.module}' but never used"
                                ),
                            )
                        )
            else:
                bound = imp.module.split(".")[0]
                if bound and bound not in used_names:
                    issues.append(
                        SymbolIssue(
                            kind="unused_import",
                            symbol=bound,
                            source_file=filename,
                            line=imp.line,
                            detail=f"module '{imp.module}' imported but never used",
                        )
                    )
        return issues

    def check_project(self) -> list[SymbolIssue]:
        """Run the checks across all Python sources in the project root.

        Re-parses each source from disk. Callers that already have a
        compiled ``ast.Module`` should invoke :meth:`check_source` on each
        to avoid the double-parse.
        """
        issues: list[SymbolIssue] = []
        for py_file in sorted(self.project_root.rglob("*.py")):
            if any(part in _SKIP_PARTS for part in py_file.parts):
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            module_name = self._path_to_module(py_file)
            issues.extend(
                self.check_source(source, str(py_file), resolve_relative_to=module_name)
            )
        return issues

    def check_project_with_trees(
        self, trees: dict[str, tuple[str, ast.AST]]
    ) -> list[SymbolIssue]:
        """Same as :meth:`check_project` but reuses pre-parsed trees.

        ``trees`` maps module id → (source, tree). Sources that fail to
        parse are absent from the dict; the caller is responsible for
        building the cache. This avoids a second parse for every project
        source when the compiler already parsed them.
        """
        issues: list[SymbolIssue] = []
        for module_id, (source, _tree) in trees.items():
            # Resolve back to a file path for the diagnostic's source_file
            # field; the file path here is informational, not used to
            # re-read anything.
            file_path = self.project_module_paths().get(module_id)
            fake_path = str(file_path) if file_path else module_id
            issues.extend(
                self.check_source(source, fake_path, resolve_relative_to=module_id)
            )
        return issues

    # -- helpers -----------------------------------------------------------------

    def project_module_paths(self) -> dict[str, Path]:
        """Map project module name -> source file path (cached)."""
        if self._modules_cache is None:
            mods: dict[str, Path] = {}
            for py_file in sorted(self.project_root.rglob("*.py")):
                if any(part in _SKIP_PARTS for part in py_file.parts):
                    continue
                name = self._path_to_module(py_file)
                if name:
                    mods[name] = py_file
            self._modules_cache = mods
        return self._modules_cache

    def _path_to_module(self, py_file: Path) -> str | None:
        try:
            rel = py_file.resolve().relative_to(self.project_root)
        except ValueError:
            return None
        parts = [p for p in rel.with_suffix("").parts if p != "__init__"]
        return ".".join(parts) if parts else None

    def _resolve_targets(self, imp: CythonImportInfo, source_module: str) -> list[str]:
        """Resolve an import to candidate project-module names."""
        if imp.level == 0:
            candidates = [imp.module]
            if imp.is_from:
                # `from pkg import sub` may target the submodule.
                candidates.extend(f"{imp.module}.{n}" for n in imp.names)
            return [c for c in candidates if c]

        parts = source_module.split(".") if source_module else []
        if imp.level > len(parts):
            return []
        base_parts = parts[: len(parts) - imp.level]
        base = ".".join(base_parts)
        rest = imp.module.lstrip(".")
        if base and rest:
            return [f"{base}.{rest}"]
        return [base or rest]

    def _defined_symbols(self, module_name: str) -> set[str] | None:
        """Top-level names defined by a project module, or None if unreadable."""
        if module_name in self._defs_cache:
            return self._defs_cache[module_name]
        path = self.project_module_paths().get(module_name)
        if path is None:
            return None
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            return None

        try:
            tree = ast.parse(source)
        except SyntaxError:
            # Conservative: unparseable (Cython etc.) — assume anything exists.
            return None

        defs = _collect_top_level_defs(tree)
        if path.name == "__init__.py":
            # Packages implicitly export submodules unless __all__ narrows it.
            defs.update(_package_submodule_names(path.parent))

        self._defs_cache[module_name] = defs
        return defs


def _collect_top_level_defs(tree: ast.AST) -> set[str]:
    """Top-level names bound in a module body, including conditional defs."""
    defs: set[str] = set()
    for node in tree.body:
        _collect_stmt_def_names(node, defs)
    return defs


def _collect_stmt_def_names(node: ast.stmt, defs: set[str]) -> None:
    """Add the names one top-level statement binds."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defs.add(node.name)
    elif isinstance(node, ast.Assign):
        _collect_target_names(node.targets, defs)
    elif isinstance(node, ast.AnnAssign):
        _collect_target_names([node.target], defs)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            defs.add(alias.asname or alias.name.split(".")[0])
    elif isinstance(node, ast.If):
        # Conditional definitions still count (e.g. TYPE_CHECKING guards).
        for child in (*node.body, *node.orelse):
            _collect_stmt_def_names(child, defs)


def _collect_target_names(targets: list[ast.expr], defs: set[str]) -> None:
    for t in targets:
        if isinstance(t, ast.Name):
            defs.add(t.id)


def _package_submodule_names(pkg_dir: Path) -> set[str]:
    """Names a package implicitly exports via its submodules/files."""
    names: set[str] = set()
    for child in pkg_dir.iterdir():
        if child.suffix == ".py":
            names.add(child.stem)
        elif child.is_dir() and (child / "__init__.py").exists():
            names.add(child.name)
    return names


# ---------------------------------------------------------------------------
# Parse cache: many callers (symbol check, import enumeration, used-name
# collection) parse the same source repeatedly within a single compile.
# ---------------------------------------------------------------------------

_PARSE_CACHE_MAX = 1024
_PARSE_CACHE: dict[int, ast.Module | None] = {}


def _cached_parse(source: str) -> ast.Module | None:
    """Parse source, caching the result by source identity.

    Returns None when the source doesn't parse. Cache is bounded to
    keep memory usage predictable; eviction is naive FIFO.
    """
    key = id(source)
    if key in _PARSE_CACHE:
        return _PARSE_CACHE[key]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
        _PARSE_CACHE.pop(next(iter(_PARSE_CACHE)))
    _PARSE_CACHE[key] = tree
    return tree


def clear_parse_cache() -> None:
    """Reset the module-level parse cache (test helper)."""
    _PARSE_CACHE.clear()


def enumerate_imports(source: str) -> list[CythonImportInfo]:
    """Enumerate plain-Python import statements from parseable source.

    Module-level cache: most call sites re-parse the same source string
    when checking imports. Re-using the parse keeps work proportional to
    file size, not caller count.
    """
    tree = _cached_parse(source)
    if tree is None:
        return []
    out: list[CythonImportInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(CythonImportInfo(module=alias.name, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            out.append(
                CythonImportInfo(
                    module=node.module or "",
                    names=[a.name for a in node.names],
                    level=node.level or 0,
                    is_from=True,
                    line=node.lineno,
                )
            )
    return out


def collect_used_names(source: str) -> set[str]:
    """All identifiers referenced anywhere in the source (loads + attrs).

    Falls back to a token scan for sources that don't parse (Cython).
    Attribute access counts as usage: ``json.dumps(...)`` uses ``dumps``.

    Reuses a module-level cache so repeated calls on the same source
    don't re-parse.
    """
    tree = _cached_parse(source)
    if tree is None:
        return set(re.findall(r"\b[A-Za-z_]\w*\b", source))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    return used
