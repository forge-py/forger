"""CPython runtime building for Forger targets.

Orchestrates: source acquisition -> configure -> compile -> install-style
assembly of a stripped runtime (interpreter + required stdlib) into the
Forger runtime cache. Uses the Rust core's module analysis when available
to prune the standard library conservatively.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from forger.build.cpython_source import CPythonSourceManager
from forger.build.recipes import BuildRecipe, recipe_for
from forger.build.targets import TargetPlatform, default_registry

logger = logging.getLogger(__name__)

RUNTIME_CACHE_ENV = "FORGER_RUNTIME_CACHE"


def runtime_cache_root() -> Path:
    """Root directory where built runtimes are cached."""
    override = os.environ.get(RUNTIME_CACHE_ENV)
    if override:
        return Path(override).resolve()
    from forger.build.cpython_source import cache_root

    return cache_root() / "runtimes"


@dataclass
class RuntimeSpec:
    """What to build and how."""

    target_triple: str
    python_version: str = "3.12"
    # Modules that must be present in the pruned stdlib. When empty, the
    # full Lib/ is installed (conservative default).
    required_modules: list[str] = field(default_factory=list)
    strip_binaries: bool = True
    jobs: int = field(default_factory=lambda: os.cpu_count() or 4)


@dataclass
class RuntimeBuildResult:
    triple: str
    version: str
    runtime_dir: Path
    stripped_count: int = 0
    stdlib_module_count: int = 0
    reused_existing: bool = False
    # Number of C extension modules excluded from the build by pruning.
    pruned_extensions: int = 0


class CpythonRuntimeBuilder:
    """Builds stripped CPython runtimes for arbitrary targets."""

    def __init__(
        self,
        spec: RuntimeSpec,
        sources: CPythonSourceManager | None = None,
        cache_root: Path | None = None,
    ) -> None:
        registry = default_registry()
        platform_def = registry.get(spec.target_triple)
        if platform_def is None:
            platform_def = TargetPlatform.parse(spec.target_triple)
        self.platform = platform_def
        self.spec = spec
        self.sources = sources or CPythonSourceManager()
        self.cache_root = cache_root or runtime_cache_root()

        self.recipe: BuildRecipe = recipe_for(self.platform, spec.python_version)

    # -- Paths -----------------------------------------------------------------

    @property
    def build_key(self) -> str:
        """Cache key: target-version pair."""
        return f"{self.platform.triple}-{self.spec.python_version}"

    @property
    def runtime_dir(self) -> Path:
        return self.cache_root / self.build_key

    @property
    def source_dir(self) -> Path:
        return self.sources.source_tree(self.spec.python_version)

    @property
    def build_dir(self) -> Path:
        """Out-of-tree build directory for this exact target+version."""
        return (
            self.sources.root
            / "builds"
            / f"{self.platform.triple}-{self.spec.python_version}"
        )

    # -- Public API --------------------------------------------------------------

    def ensure_runtime(
        self, force_rebuild: bool = False, dry_run: bool = False
    ) -> RuntimeBuildResult:
        """Return a ready runtime dir, building it if needed.

        With ``dry_run`` no commands are executed; problems are raised as
        RuntimeError after validation.
        """
        problems = self.recipe.validate()
        if problems:
            raise RuntimeError(
                "Cannot build runtime for "
                f"{self.platform.triple}:\n  - " + "\n  - ".join(problems)
            )

        if dry_run:
            return RuntimeBuildResult(
                triple=self.platform.triple,
                version=self.spec.python_version,
                runtime_dir=self.runtime_dir,
                reused_existing=self.runtime_dir.exists(),
            )

        if not force_rebuild and self._is_complete():
            logger.info("Runtime cached: %s", self.runtime_dir)
            return RuntimeBuildResult(
                triple=self.platform.triple,
                version=self.spec.python_version,
                runtime_dir=self.runtime_dir,
                reused_existing=True,
            )

        source = self.sources.ensure_source(self.spec.python_version)
        self._run_build(source)
        result = self._assemble()

        marker = self.runtime_dir / ".forger-ok"
        marker.write_text("ok\n")
        logger.info("Runtime ready: %s", self.runtime_dir)

        return RuntimeBuildResult(
            triple=self.platform.triple,
            version=self.spec.python_version,
            runtime_dir=self.runtime_dir,
            stripped_count=result.get("stripped", 0),
            stdlib_module_count=result.get("stdlib_modules", 0),
            pruned_extensions=result.get("pruned_extensions", 0),
        )

    # -- Validation ---------------------------------------------------------------

    def _is_complete(self) -> bool:
        return (self.runtime_dir / ".forger-ok").exists()

    # -- Compile -------------------------------------------------------------------

    def _run_build(self, source: Path) -> None:
        build_dir = self.build_dir
        build_dir.mkdir(parents=True, exist_ok=True)
        env = self.recipe.env()

        # C-level pruning: derive a build plan from the app's stdlib needs and
        # apply it to the source tree (Modules/Setup.local) + configure flags.
        plan = self._build_prune_plan()
        if plan is not None:
            from forger.build.cpython_pruner import write_setup_local

            write_setup_local(source, plan.setup_local)
            logger.info(
                "Pruning: %d extensions required, %d excluded",
                len(plan.required_extensions),
                len(plan.excluded_extensions),
            )
            for line in plan.log:
                logger.debug("prune: %s", line)

        configure_cmd = self.recipe.configure_cmd(source, build_dir)
        if configure_cmd:
            if plan is not None:
                configure_cmd = self.recipe.with_extra_configure_args(
                    configure_cmd, plan.configure_args
                )
            logger.info("[%s] configure: %s", self.recipe.describe(), " ".join(configure_cmd))
            self._run(configure_cmd, cwd=build_dir, env=env)

        build_cmd = self.recipe.build_cmd(source, build_dir, self.spec.jobs)
        logger.info("[%s] build: %s", self.recipe.describe(), " ".join(build_cmd))
        self._run(build_cmd, cwd=build_dir, env=env)

    def _build_prune_plan(self):
        """Return the CPythonBuildPlan for this spec, or None when pruning is off."""
        if not self.spec.required_modules:
            return None  # conservative default: full interpreter, no pruning
        try:
            from forger.build.cpython_pruner import CPythonPruner, collect_stdlib_imports

            used = collect_stdlib_imports(set(self.spec.required_modules), self.spec.python_version)
            pruner = CPythonPruner(self.spec.python_version)
            return pruner.build_plan(used)
        except ImportError:
            logger.warning("Rust core unavailable; building unpruned CPython")
            return None

    @staticmethod
    def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            tail = "\n".join(proc.stdout.splitlines()[-30:])
            err_tail = "\n".join(proc.stderr.splitlines()[-30:])
            raise RuntimeError(
                f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
                f"--- stdout tail ---\n{tail}\n"
                f"--- stderr tail ---\n{err_tail}"
            )
        if proc.stdout.strip():
            logger.debug("[%s stdout]", cmd[0])
            logger.debug(proc.stdout[-2000:])

    # -- Assembly ------------------------------------------------------------

    def _assemble(self) -> dict[str, int]:
        """Collect interpreter + stdlib into the runtime cache dir.

        Returns counters used in reporting.
        """
        build_dir = self.build_dir
        out = self.runtime_dir
        version = self.spec.python_version
        major_minor = version.rsplit(".", 1)[0]

        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)

        stripped = 0
        stdlib_modules = 0

        if self.platform.os == "windows":
            stripped += self._assemble_windows(build_dir, out)
        elif self.platform.os == "emscripten":
            stripped += self._assemble_emscripten(build_dir, out)
        else:
            stripped += self._assemble_posix(build_dir, out)

        # Prune + copy the pure-python stdlib.
        stdlib_src = self._stdlib_source(build_dir, major_minor)
        stdlib_dst = out / "lib" / f"python{major_minor}"
        stdlib_modules = self._copy_pruned_stdlib(stdlib_src, stdlib_dst)

        return {"stripped": stripped, "stdlib_modules": stdlib_modules}

    # -- Platform assembly helpers ---------------------------------------------

    def _find_interpreter_binary(self, build_dir: Path) -> Path | None:
        name = "python.exe" if self.platform.os == "windows" else "python"
        candidate = build_dir / name
        return candidate if candidate.is_file() else None

    def _assemble_windows(self, build_dir: Path, out: Path) -> int:
        pcbuild = build_dir / "PCbuild" / ("AMD64" if self.platform.arch == "x64" else "ARM64")
        src_bin = pcbuild / "python.exe"
        if not src_bin.exists():
            raise RuntimeError(f"Expected PCbuild output missing: {src_bin}")
        bin_dir = out / "bin"
        bin_dir.mkdir(parents=True)
        # python.exe + the vcruntime + pythonXY.dll trio
        for pattern in ("python.exe", "python*.dll", "vcruntime*.dll"):
            for dll in pcbuild.glob(pattern):
                shutil.copy2(dll, bin_dir / dll.name)
        return 0

    def _assemble_posix(self, build_dir: Path, out: Path) -> int:
        binary = self._find_interpreter_binary(build_dir)
        if binary is None:
            raise RuntimeError(f"No interpreter binary found in {build_dir}")

        bin_dir = out / "bin"
        bin_dir.mkdir(parents=True)
        dest = bin_dir / "python"
        shutil.copy2(binary, dest)

        stripped = 0
        if self.spec.strip_binaries:
            strip_tool = self.recipe.strip_tool()
            if strip_tool is not None:
                rc = subprocess.run(  # noqa: S603
                    [str(strip_tool), str(dest)],
                    capture_output=True,
                    text=True,
                )
                if rc.returncode == 0:
                    stripped += 1
                else:
                    logger.warning("strip failed (%d): %s", rc.returncode, rc.stderr.strip())
        else:
            stripped += 1  # count as processed
        return stripped

    def _assemble_emscripten(self, build_dir: Path, out: Path) -> int:
        """Collect the WASM interpreter and its Emscripten glue into out/.

        A CPython/emscripten build yields ``python.wasm`` plus a JS loader
        (``python.mjs`` for ESM targets on CPython >= 3.13, ``python.js``
        otherwise). Both are required to instantiate the runtime, so both
        are copied; missing glue is a hard error because the resulting
        bundle could never run.
        """
        wasm = self._find_wasm_artifact(build_dir)
        glue = self._find_wasm_glue(build_dir)
        if wasm is None:
            raise RuntimeError(f"WASM output missing in {build_dir} (python.wasm)")
        if glue is None:
            raise RuntimeError(
                f"Emscripten glue missing in {build_dir} "
                "(expected python.mjs or python.js next to python.wasm)"
            )

        bin_dir = out / "bin"
        bin_dir.mkdir(parents=True)
        shutil.copy2(wasm, bin_dir / wasm.name)
        shutil.copy2(glue, bin_dir / glue.name)

        # Sidecar workers ship with some configurations (e.g. pthread builds).
        for sidecar in build_dir.glob("python*.mjs"):
            if sidecar.name != glue.name:
                shutil.copy2(sidecar, bin_dir / sidecar.name)
        return 0

    def _find_wasm_artifact(self, build_dir: Path) -> Path | None:
        for candidate in (
            build_dir / "python.wasm",
            self.source_dir / "python.wasm",
        ):
            if candidate.is_file():
                return candidate
        return None

    def _find_wasm_glue(self, build_dir: Path) -> Path | None:
        for name in ("python.mjs", "python.js"):
            for base in (build_dir, self.source_dir):
                candidate = base / name
                if candidate.is_file():
                    return candidate
        return None

    # -- Stdlib handling -------------------------------------------------------

    def _stdlib_source(self, build_dir: Path, major_minor: str) -> Path | None:
        candidates = [
            build_dir / "Lib",
            self.source_dir / "Lib",
        ]
        for c in candidates:
            if (c / "os.py").exists():
                return c
        return None

    def _copy_pruned_stdlib(self, src: Path | None, dst: Path) -> int:
        if src is None:
            logger.warning("No stdlib Lib/ found; runtime will have no Python-level stdlib")
            return 0
        dst.mkdir(parents=True, exist_ok=True)
        required = set(self.spec.required_modules)

        if not required:
            # Conservative: full Lib/ minus junk we know is irrelevant at runtime.
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    "test",
                    "tests",
                    "idlelib",
                    "tkinter",
                    "turtledemo",
                    "site-packages",
                    "*.dist-info",
                ),
            )
            return sum(1 for _ in dst.rglob("*") if _.suffix == ".py")

        # Required-modules mode: copy each requested module (package dirs
        # wholesale) plus the always-needed core stdlib.
        expanded = self._expand_required_modules(required)
        copied = 0
        for mod in sorted(expanded):
            top_level = mod.split(".")[0]
            for candidate in (src / top_level, src / f"{top_level}.py"):
                if candidate.exists():
                    dest = dst / candidate.name
                    if candidate.is_dir():
                        if not dest.exists():
                            shutil.copytree(
                                candidate,
                                dest,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                            )
                            copied += 1
                    elif not dest.exists():
                        shutil.copy2(candidate, dest)
                        copied += 1
                    break
        return copied

    def _expand_required_modules(self, required: set[str]) -> set[str]:
        """Expand required modules with their Rust-computed dependencies."""
        try:
            from forger_core import CpythonModuleRegistry  # type: ignore[import-not-found]

            ver_parts = self.spec.python_version.split(".")
            registry = CpythonModuleRegistry((int(ver_parts[0]), int(ver_parts[1])))
            analysis = registry.analyze_required_sources(sorted(required))
            report = analysis.format_report()
            # format_report is prose; extract identifiers conservatively.
            import re

            tokens = set(re.findall(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*", report))
            return tokens & self._available_stdlib_names() | required
        except ImportError:
            logger.debug("Rust core unavailable; using unexpanded required module list")
            return set(required)

    def _available_stdlib_names(self) -> set[str]:
        lib = self.source_dir / "Lib"
        names: set[str] = set()
        if not lib.exists():
            return names
        for child in lib.iterdir():
            if child.suffix == ".py":
                names.add(child.stem)
            elif child.is_dir() and (child / "__init__.py").exists():
                names.add(child.name)
        return names

    # -- Info ---------------------------------------------------------------------

    def info(self) -> dict[str, object]:
        recipe_problems = self.recipe.validate()
        return {
            "target": self.platform.triple,
            "version": self.spec.python_version,
            "runtime_dir": str(self.runtime_dir),
            "cached": self._is_complete(),
            "cross": self.recipe.is_cross,
            "recipe": self.recipe.describe(),
            "problems": recipe_problems,
        }
