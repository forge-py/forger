"""Per-platform CPython build recipes.

Each recipe knows how to turn an extracted CPython source tree into a
compiled runtime for its target: the configure invocation, the compile
command, the environment (cross toolchains, NDK, emsdk), and the strip
tool used to produce a stripped binary.

Mobile and WASM support follows upstream CPython guidance:
- Android/iOS cross builds need CPython >= 3.13 and a *build* Python of
  the same version running on the host.
- WASM builds go through ``emconfigure`` as documented in Tools/wasm.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from forger.build.targets import TargetPlatform

logger = logging.getLogger(__name__)

# CPython versions with first-class Android/iOS cross-compile support.
MOBILE_MIN_CPYTHON = "3.13"


def _host_arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


class BuildRecipe(ABC):
    """Strategy for configuring and compiling CPython for one target."""

    def __init__(self, target: TargetPlatform, python_version: str) -> None:
        self.target = target
        self.python_version = python_version

    # -- Introspection ---------------------------------------------------------

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    def is_cross(self) -> bool:
        """True when this build is a cross-compile from the current host."""
        host = sys.platform
        if self.target.os == "windows":
            return not host.startswith("win") or self.target.arch != _host_arch()
        if self.target.os == "linux":
            return not host.startswith("linux") or self.target.arch != _host_arch()
        if self.target.os == "macos":
            return host != "darwin" or self.target.arch != _host_arch()
        return True  # ios / android / emscripten always cross

    def describe(self) -> str:
        return f"{self.name}({self.target.triple})"

    # -- Build steps -----------------------------------------------------------

    @abstractmethod
    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        """Configure invocation run inside ``build_dir``. None = no step."""

    @abstractmethod
    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        """Compile invocation run inside ``build_dir``."""

    @abstractmethod
    def env(self) -> dict[str, str]:
        """Environment for configure/build."""

    def check_tools(self) -> list[str]:
        """External executables this recipe needs on PATH."""
        return []

    def strip_tool(self) -> Path | None:
        """Tool able to strip binaries for this target (None = skip)."""
        if self.target.os == "windows":
            return None
        which = shutil.which("strip")
        return Path(which) if which else None

    def with_extra_configure_args(
        self, configure_cmd: list[str], extra: list[str]
    ) -> list[str]:
        """Merge pruning configure args into a configure command.

        Default: append before any ``KEY=VALUE`` assignments so flags stay
        grouped with the other options. Recipes without a configure step
        override this to return the command unchanged.
        """
        if not extra:
            return configure_cmd
        split = len(configure_cmd)
        for i, part in enumerate(configure_cmd):
            if "=" in part and not part.startswith("-"):
                split = i
                break
        return configure_cmd[:split] + extra + configure_cmd[split:]

    def validate(self) -> list[str]:
        """Return human-readable problems preventing this build."""
        problems: list[str] = []
        for tool in self.check_tools():
            if shutil.which(tool) is None:
                problems.append(f"required tool '{tool}' not found on PATH")
        if self.is_cross and self.target.is_mobile:
            if _version_key(self.python_version) < _version_key(MOBILE_MIN_CPYTHON):
                problems.append(
                    f"{self.target.os} cross builds require CPython "
                    f">= {MOBILE_MIN_CPYTHON} (got {self.python_version})"
                )
            if not self.build_python():
                problems.append(
                    f"a host 'build' Python {self.python_version} is required "
                    "for cross compilation (--build-python)"
                )
        return problems

    def build_python(self) -> str | None:
        """Path to a host interpreter of the same version (cross builds)."""
        candidate = os.environ.get("FORGER_BUILD_PYTHON") or shutil.which(
            f"python{self.python_version.rsplit('.', 1)[0]}"
        )
        return candidate


# ---------------------------------------------------------------------------
# Desktop
# ---------------------------------------------------------------------------


class LinuxRecipe(BuildRecipe):
    def check_tools(self) -> list[str]:
        return ["make", "gcc"]

    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        cmd = [str(source_dir / "configure"), "--prefix=/forger"]
        if self.target.env == "musl":
            cc = shutil.which("musl-gcc")
            if cc:
                cmd += [f"CC={cc}"]
        return cmd

    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        return ["make", f"-j{jobs}"]

    def env(self) -> dict[str, str]:
        return dict(os.environ)


class MacosRecipe(BuildRecipe):
    def check_tools(self) -> list[str]:
        return ["clang", "make"]

    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        return [str(source_dir / "configure"), "--prefix=/forger"]

    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        return ["make", f"-j{jobs}"]

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.target.arch == "arm64" and _host_arch() != "arm64":
            env["ARCHFLAGS"] = "-arch arm64"
        return env


class WindowsRecipe(BuildRecipe):
    """CPython built with its own MSVC driver (PCbuild/build.py)."""

    def check_tools(self) -> list[str]:
        return []  # PCbuild locates MSVC itself

    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        return None  # PCbuild has no configure step

    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        plat = {"x64": "x64", "arm64": "ARM64"}[self.target.arch]
        return [
            sys.executable,
            str(source_dir / "PCbuild" / "build.py"),
            "-p",
            plat,
            "-c",
            "Release",
            "--no-test-modules",
        ]

    def env(self) -> dict[str, str]:
        return dict(os.environ)

    def with_extra_configure_args(
        self, configure_cmd: list[str], extra: list[str]
    ) -> list[str]:
        # PCbuild has no configure step; pruning happens through Setup.local,
        # so extra configure args are dropped.
        return configure_cmd

    def build_python(self) -> str | None:
        return sys.executable  # PCbuild drives itself with current interpreter


# ---------------------------------------------------------------------------
# Emscripten / WASM
# ---------------------------------------------------------------------------


class EmscriptenRecipe(BuildRecipe):
    """CPython compiled to WebAssembly via the Emscripten SDK."""

    MIN_EMSCRIPTEN = (3, 1, 58)

    def check_tools(self) -> list[str]:
        return ["emcc", "make"]

    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        cmd = [
            "emconfigure",
            str(source_dir / "configure"),
            "--prefix=/forger",
            # WASM defaults recommended by Tools/wasm for a lean runtime:
            "--without-pymalloc",
            "--disable-wasm-dynamic-linking",
        ]
        if _version_key(self.python_version) < (3, 12):
            cmd += ["--with-emscripten-target=browser"]
        return cmd

    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        return ["make", f"-j{jobs}"]

    def env(self) -> dict[str, str]:
        return dict(os.environ)

    def validate(self) -> list[str]:
        problems = super().validate()
        emcc = shutil.which("emcc")
        if emcc:
            try:
                out = subprocess.run(  # noqa: S603
                    [emcc, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                first = out.stdout.splitlines()[0]
                version_part = first.rsplit(" ", 1)[-1]
                got = tuple(int(x) for x in version_part.split(".")[:3])
                if got < self.MIN_EMSCRIPTEN:
                    problems.append(
                        f"emcc >= {'.'.join(map(str, self.MIN_EMSCRIPTEN))} "
                        f"required (found {version_part})"
                    )
            except (OSError, subprocess.SubprocessError, ValueError):
                problems.append("could not determine emcc version")
        return problems


# ---------------------------------------------------------------------------
# Android
# ---------------------------------------------------------------------------

_ANDROID_TRIPLE_BASE = {"arm64": "aarch64-linux-android", "x64": "x86_64-linux-android"}
_ANDROID_HOST_TAG = {
    "win32": "windows-x86_64",
    "darwin": "darwin-x86_64",
}


class AndroidRecipe(BuildRecipe):
    """CPython cross-built against the Android NDK clang toolchain."""

    DEFAULT_API_LEVEL = "24"

    def __init__(
        self,
        target: TargetPlatform,
        python_version: str,
        ndk_home: str | None = None,
    ) -> None:
        super().__init__(target, python_version)
        self.ndk_home = ndk_home or os.environ.get("ANDROID_NDK_HOME") or os.environ.get(
            "ANDROID_NDK_ROOT", ""
        )

    # -- NDK layout -------------------------------------------------------------

    def _toolchain_bin(self) -> Path:
        if not self.ndk_home:
            raise RuntimeError(
                "Android NDK not found; set ANDROID_NDK_HOME to your NDK root"
            )
        tag = _ANDROID_HOST_TAG.get(sys.platform, "linux-x86_64")
        return Path(self.ndk_home) / "toolchains" / "llvm" / "prebuilt" / tag / "bin"

    def _clang_target_triple(self) -> str:
        base = _ANDROID_TRIPLE_BASE.get(self.target.arch)
        if base is None:
            raise ValueError(f"Unsupported Android arch: {self.target.arch}")
        api = self.target.effective_min_version or self.DEFAULT_API_LEVEL
        return f"{base}{api}"

    # -- Build steps --------------------------------------------------------------

    def check_tools(self) -> list[str]:
        return ["make"]

    def _has_ndk(self) -> bool:
        if not self.ndk_home:
            return False
        try:
            return self._toolchain_bin().is_dir()
        except RuntimeError:
            return False

    def validate(self) -> list[str]:
        problems = super().validate()
        if not self._has_ndk():
            problems.append(
                "Android NDK not found: set ANDROID_NDK_HOME to your NDK root"
            )
        return problems

    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        build_py = self.build_python()
        assert build_py is not None  # validated by validate()
        cmd = [
            str(source_dir / "configure"),
            "--prefix=/forger",
            f"--host={_ANDROID_TRIPLE_BASE[self.target.arch]}",
            f"--with-build-python={build_py}",
            "ac_cv_file__dev_ptmx=yes",
            "ac_cv_file__dev_ptc=no",
        ]
        return cmd

    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        return ["make", f"-j{jobs}"]

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        bin_dir = self._toolchain_bin()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["ANDROID_API_LEVEL"] = (
            self.target.effective_min_version or self.DEFAULT_API_LEVEL
        )
        env["CC"] = "clang"
        env["CXX"] = "clang++"
        env["AR"] = "llvm-ar"
        env["READELF"] = "llvm-readelf"
        env["STRIP"] = "llvm-strip"
        return env

    def strip_tool(self) -> Path | None:
        try:
            strip = self._toolchain_bin() / "llvm-strip"
            return strip if strip.exists() else None
        except RuntimeError:
            return None

    def build_python(self) -> str | None:
        explicit = os.environ.get("FORGER_BUILD_PYTHON")
        if explicit:
            return explicit
        major_minor = self.python_version.rsplit(".", 1)[0]
        return shutil.which(f"python{major_minor}")


# ---------------------------------------------------------------------------
# iOS
# ---------------------------------------------------------------------------


def xcrun_sdk_path(sdk_name: str) -> str:
    """Resolve an Apple SDK path via xcrun ('' when unavailable)."""
    try:
        result = subprocess.run(  # noqa: S603
            ["xcrun", "--sdk", sdk_name, "--show-sdk-path"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


_IOS_SDK = {"arm64": "iphoneos", "x64": "iphonesimulator"}


class IosRecipe(BuildRecipe):
    """CPython cross-built with Apple clang against the iOS SDK."""

    DEFAULT_MIN_IOS = "12.0"

    def _min_ios(self) -> str:
        return self.target.effective_min_version or self.DEFAULT_MIN_IOS

    def _sdk_path(self) -> str:
        override = os.environ.get("IOS_SDKROOT")
        if override:
            return override
        sdk = _IOS_SDK.get(self.target.arch, "iphoneos")
        return xcrun_sdk_path(sdk)

    def check_tools(self) -> list[str]:
        return ["clang", "make"]

    def configure_cmd(self, source_dir: Path, build_dir: Path) -> list[str] | None:
        build_py = self.build_python()
        assert build_py is not None  # validated by validate()
        host_triple = {
            "arm64": "aarch64-apple-ios",
            "x64": "aarch64-apple-ios-simulator",
        }[self.target.arch]
        cmd = [
            str(source_dir / "configure"),
            "--prefix=/forger",
            f"--host={host_triple}",
            f"--with-build-python={build_py}",
            "ac_cv_file__dev_ptmx=yes",
            "ac_cv_file__dev_ptc=no",
        ]
        return cmd

    def build_cmd(self, source_dir: Path, build_dir: Path, jobs: int) -> list[str]:
        return ["make", f"-j{jobs}"]

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        min_ios = self._min_ios()
        sdk = self._sdk_path()
        arch_flag = "-arch arm64"
        env["CC"] = "clang"
        env["CFLAGS"] = f"{arch_flag} -isysroot {sdk} -mios-version-min={min_ios}".strip()
        env["LDFLAGS"] = f"{arch_flag} -isysroot {sdk}".strip()
        # Tell CPython's sysconfig the platform so extension builds agree.
        suffix = "" if self.target.arch == "arm64" else "-simulator"
        env["_PYTHON_HOST_PLATFORM"] = f"iOS-arm64{suffix}"
        return env

    def strip_tool(self) -> Path | None:
        which = shutil.which("strip")
        return Path(which) if which else None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def recipe_for(target: TargetPlatform, python_version: str) -> BuildRecipe:
    """Create the appropriate build recipe for ``target``."""
    if target.os == "windows":
        return WindowsRecipe(target, python_version)
    if target.os == "linux":
        return LinuxRecipe(target, python_version)
    if target.os == "macos":
        return MacosRecipe(target, python_version)
    if target.os == "android":
        return AndroidRecipe(target, python_version)
    if target.os == "ios":
        return IosRecipe(target, python_version)
    if target.os == "emscripten":
        return EmscriptenRecipe(target, python_version)
    raise ValueError(f"No CPython build recipe for target: {target.triple}")
