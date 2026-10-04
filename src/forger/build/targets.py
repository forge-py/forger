"""Build target infrastructure.

Defines target platforms and their build configurations. Each target carries
the metadata needed to configure a CPython runtime build: platform family,
cross-compilation hints, and minimum OS/API versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TargetPlatform:
    """Specification for a build target platform."""

    os: str  # windows, linux, macos, ios, android, emscripten
    arch: str  # x64, arm64, wasm
    env: str = ""  # gnu, musl, android, etc.
    min_version: str | None = None  # Minimum OS / API level for mobile targets

    @property
    def triple(self) -> str:
        """Get the target triple string."""
        if self.env:
            return f"{self.os}-{self.arch}-{self.env}"
        return f"{self.os}-{self.arch}"

    @classmethod
    def parse(cls, triple: str) -> TargetPlatform:
        """Parse a target triple string."""
        parts = triple.split("-")
        if len(parts) < 2:
            raise ValueError(f"Invalid target triple: {triple}")

        return cls(
            os=parts[0],
            arch=parts[1],
            env=parts[2] if len(parts) > 2 else "",
        )

    # -- Platform families ---------------------------------------------------

    @property
    def family(self) -> str:
        """Coarse platform family used by build recipes."""
        if self.os == "windows":
            return "windows"
        if self.os == "emscripten":
            return "emscripten"
        return "posix"  # linux, macos, ios, android

    @property
    def is_desktop(self) -> bool:
        return self.os in ("windows", "linux", "macos")

    @property
    def is_mobile(self) -> bool:
        return self.os in ("android", "ios")

    @property
    def is_wasm(self) -> bool:
        return self.os == "emscripten"

    # -- Output naming -------------------------------------------------------

    @property
    def executable_ext(self) -> str:
        """Get the executable extension for this platform."""
        if self.os == "windows":
            return ".exe"
        return ""

    @property
    def lib_ext(self) -> str:
        """Get the shared library extension for this platform."""
        if self.os == "windows":
            return ".dll"
        elif self.os in ("macos", "ios"):
            return ".dylib"
        elif self.os == "emscripten":
            return ".wasm"
        else:
            return ".so"

    @property
    def python_ext(self) -> str:
        """Get the Python extension module format for this platform."""
        if self.os == "windows":
            return ".pyd"
        elif self.os == "emscripten":
            return ".wasm"
        else:
            return ".so"

    # -- Cross-compilation defaults ------------------------------------------

    @property
    def default_min_version(self) -> str | None:
        """Default minimum OS version / API level for this target."""
        if self.os == "android":
            return "24"  # NDK minimum supported API level
        if self.os == "ios":
            return "12.0"
        return None

    @property
    def effective_min_version(self) -> str | None:
        return self.min_version or self.default_min_version


@dataclass
class TargetRegistry:
    """Known targets and their canonical definitions."""

    _targets: dict[str, TargetPlatform] = field(default_factory=dict)

    def register(self, triple: str, platform: TargetPlatform) -> None:
        self._targets[triple] = platform

    def get(self, triple: str) -> TargetPlatform | None:
        return self._targets.get(triple)

    def all(self) -> dict[str, TargetPlatform]:
        return dict(self._targets)


def default_registry() -> TargetRegistry:
    """Build the registry of all first-class Forger targets."""
    reg = TargetRegistry()
    for triple in (
        "windows-x64",
        "windows-arm64",
        "linux-x64",
        "linux-x64-musl",
        "linux-arm64",
        "linux-arm64-musl",
        "macos-x64",
        "macos-arm64",
        "ios-arm64",
        "ios-x64",  # simulator
        "android-arm64",
        "android-x64",
        "emscripten-wasm",
    ):
        reg.register(triple, TargetPlatform.parse(triple))
    return reg
