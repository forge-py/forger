"""Build target infrastructure.

Defines target platforms and their build configurations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TargetPlatform:
    """Specification for a build target platform."""

    os: str  # windows, linux, macos, android, emscripten
    arch: str  # x64, arm64, wasm
    env: str = ""  # gnu, musl, etc.

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
        elif self.os == "macos":
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
