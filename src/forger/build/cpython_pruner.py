"""CPython C-level pruning driven by application requirements.

Turns a project's stdlib imports into a concrete CPython build plan:

* configure flags to disable whole feature domains the app never touches
* a ``Modules/Setup.local`` that builds only required extension modules
* the list of native extensions (.pyd/.so) to ship in the runtime

The analysis itself is performed by the Rust core
(``forger_core.CpythonModuleRegistry``), which knows module -> C source,
C dependency edges and always-built modules. This module is the thin
Python orchestration layer: it feeds it the app's imported stdlib modules
and converts the structured result into build inputs.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Modules that are effectively part of the interpreter core: any real
# application needs them for imports, encodings, or startup itself. They are
# always treated as required regardless of what static analysis finds.
_CORE_STDLIB = frozenset({
    "builtins",
    "sys",
    "_imp",
    "marshal",
    "os",
    "os.path",
    "posixpath",
    "ntpath",
    "stat",
    "io",
    "abc",
    "_collections_abc",
    "types",
    "importlib",
    "importlib._bootstrap",
    "importlib._bootstrap_external",
    "importlib.machinery",
    "importlib.util",
    "warnings",
    "encodings",
    "codecs",
})


@dataclass
class CPythonBuildPlan:
    """A complete pruning plan for one CPython runtime build."""

    python_version: str
    # Top-level stdlib module names the application actually uses.
    used_stdlib_modules: set[str] = field(default_factory=set)
    # Extra configure arguments enabling/disabling optional feature domains.
    configure_args: list[str] = field(default_factory=list)
    # Content of Modules/Setup.local to write into the source tree ("" = none).
    setup_local: str = ""
    # Extension modules that must be compiled/shipped.
    required_extensions: set[str] = field(default_factory=set)
    # Extension modules that will NOT be compiled/shipped.
    excluded_extensions: set[str] = field(default_factory=set)
    # Human-readable log of decisions made during planning.
    log: list[str] = field(default_factory=list)


def collect_stdlib_imports(
    imported_modules: set[str],
    python_version: str = "3.12",
) -> set[str]:
    """Filter an app's imported top-level names down to stdlib modules.

    ``imported_modules`` are top-level module names discovered by the
    compiler's import analysis (project code + third-party + stdlib mixed).
    Returns only those that belong to the standard library of the target
    Python version, plus the interpreter-core baseline.
    """
    try:
        stdlib_names = set(sys.stdlib_module_names)
    except AttributeError:  # pragma: no cover - <3.10
        stdlib_names = set()

    used: set[str] = set(_CORE_STDLIB)
    for name in imported_modules:
        if name in stdlib_names or name in _CORE_STDLIB:
            used.add(name)
        elif "." in name:
            # Dotted entries: consider their top level too.
            top = name.split(".")[0]
            if top in stdlib_names:
                used.add(name)

    logger.debug(
        "Stdlib requirement: %d modules (%s)",
        len(used),
        ", ".join(sorted(used)[:15]),
    )
    return used


class CPythonPruner:
    """Builds :class:`CPythonBuildPlan` instances via the Rust core."""

    def __init__(self, python_version: str = "3.12") -> None:
        self.python_version = python_version
        self._registry = None

    @property
    def registry(self):  # noqa: ANN201 - pyo3 type not importable at runtime typing
        if self._registry is None:
            from forger.forger_core import CpythonModuleRegistry  # type: ignore[import-not-found]

            ver_parts = self.python_version.split(".")
            major = int(ver_parts[0])
            minor = int(ver_parts[1]) if len(ver_parts) > 1 else 0
            self._registry = CpythonModuleRegistry((major, minor))
        return self._registry

    def build_plan(self, used_stdlib_modules: set[str]) -> CPythonBuildPlan:
        """Analyze requirements and produce the concrete build plan."""
        plan = CPythonBuildPlan(python_version=self.python_version)
        plan.used_stdlib_modules = set(used_stdlib_modules)

        analysis = self.registry.analyze_required_sources(sorted(used_stdlib_modules))

        plan.required_extensions = {
            m for m in analysis.required_c_modules
            if not self._is_always_built(m)
        }
        plan.excluded_extensions = {
            m for m in analysis.excluded_c_modules
            if not self._is_always_built(m)
        }

        plan.configure_args = self._domain_configure_args(used_stdlib_modules)
        plan.setup_local = self.registry.generate_setup_file(sorted(used_stdlib_modules))

        plan.log.extend(analysis.analysis_log)
        plan.log.append(
            f"excluded {analysis.excluded_source_count} of "
            f"{analysis.total_c_sources} known C sources"
        )
        return plan

    def _is_always_built(self, module_name: str) -> bool:
        info = self.registry.get_module_info(module_name)
        if info is None:
            return False
        return bool(getattr(info, "always_built", False))

    def _domain_configure_args(self, used: set[str]) -> list[str]:
        """Disable feature domains the app demonstrably never uses."""
        args: list[str] = []

        if not any(u == "tkinter" or u.startswith("tkinter.") for u in used):
            args.append("--without-tk")

        if not any(u.startswith("test") for u in used):
            args.append("--disable-test-modules")

        args.append("--without-ensurepip")
        return args


def write_setup_local(source_dir: Path, setup_local: str) -> Path | None:
    """Write Modules/Setup.local into a CPython source tree.

    Returns the written path, or None when there is nothing to write.
    An existing file is left untouched when content is empty so repeated
    builds do not clobber user customizations with nothing.
    """
    if not setup_local.strip():
        return None
    target = source_dir / "Modules" / "Setup.local"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(setup_local, encoding="utf-8")
    logger.info("Wrote %s (%d bytes)", target, len(setup_local))
    return target
