"""Builder module — consumes .forge artifacts and produces platform-specific executables."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


class Builder:
    """Builds platform-specific executables from .forge artifacts."""

    SUPPORTED_TARGETS = [
        "windows-x64",
        "windows-arm64",
        "linux-x64",
        "linux-arm64",
        "macos-x64",
        "macos-arm64",
        "android-arm64",
        "emscripten-wasm",
    ]

    def __init__(
        self,
        artifact_path: Union[Path, str],
        target: str,
        output_dir: Union[Path, str, None] = None,
    ) -> None:
        self.artifact_path = Path(artifact_path).resolve()
        self.target = target
        self.output_dir = Path(output_dir).resolve() if output_dir else Path(
            f"{self.artifact_path.stem}-{target}"
        ).resolve()

    def build(self) -> None:
        """Build the executable for the target platform."""
        if self.target not in self.SUPPORTED_TARGETS:
            logger.warning(
                "Target %s is not in the supported list. Available: %s",
                self.target,
                ", ".join(self.SUPPORTED_TARGETS),
            )

        logger.info("Building %s for target %s", self.artifact_path, self.target)

        # TODO: Implement build pipeline:
        # 1. Read .forge artifact
        # 2. Select target-specific dependencies
        # 3. Bundle with CPython runtime
        # 4. Generate executable

        logger.info(
            "Build placeholder — target runtime integration pending"
        )
        logger.info("Output directory: %s", self.output_dir)

    def artifact_info(self) -> str:
        """Show information about the .forge artifact."""
        lines = [
            f"Artifact: {self.artifact_path}",
            f"Target: {self.target}",
            f"Exists: {self.artifact_path.exists()}",
        ]

        if self.artifact_path.exists():
            size = self.artifact_path.stat().st_size
            lines.append(f"Size: {size:,} bytes")

        # TODO: Parse .forge header and manifest
        lines.append("Note: Artifact parsing pending Rust core integration")

        return "\n".join(lines)
