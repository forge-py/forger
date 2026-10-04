"""Builder module — consumes .forge artifacts and produces platform-specific bundles.

The .forge artifact is an uncompressed directory (VFS tree + MANIFEST.json).
Building for a target means: obtain/build a stripped CPython runtime for the
target, copy the VFS into the bundle, and emit a launcher appropriate to the
platform (script/exe on desktop, WASM entry on emscripten).
"""

from __future__ import annotations

import json
import logging
import shutil
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_TARGETS = [
    "windows-x64",
    "windows-arm64",
    "linux-x64",
    "linux-arm64",
    "macos-x64",
    "macos-arm64",
    "ios-arm64",
    "android-arm64",
    "emscripten-wasm",
]


class Builder:
    """Builds target bundles from uncompressed .forge directory artifacts."""

    def __init__(
        self,
        artifact_path: Path | str,
        target: str,
        output_dir: Path | str | None = None,
        python_version: str = "3.12",
    ) -> None:
        self.artifact_path = Path(artifact_path).resolve()
        self.target = target
        self.output_dir = (
            Path(output_dir).resolve()
            if output_dir
            else Path(f"{self.artifact_path.stem}-{target}").resolve()
        )
        self.python_version = python_version

    # -- Artifact access --------------------------------------------------------

    def _read_manifest(self) -> dict[str, object]:
        manifest_path = self.artifact_path / "MANIFEST.json"
        if not manifest_path.exists():
            raise RuntimeError(f"Not a .forge directory artifact: {self.artifact_path}")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"Malformed MANIFEST.json in {self.artifact_path}")
        # Validate the schema version. Bumping the format is a breaking
        # change; the read path refuses to operate on unknown versions
        # so users see a clear migration error instead of silent
        # schema drift (PHILOSOPHY.md §12).
        from forger.compiler import MANIFEST_FORMAT_VERSION

        version = data.get("format_version")
        if version != MANIFEST_FORMAT_VERSION:
            actual = "missing" if version is None else f"v{version}"
            raise RuntimeError(
                f"MANIFEST.json {self.artifact_path} has format_version={actual}; "
                f"this build of forger supports v{MANIFEST_FORMAT_VERSION}. "
                f"Re-run the project with a matching compiler version."
            )
        return data

    def artifact_info(self) -> str:
        """Show information about the .forge artifact."""
        lines = [
            f"Artifact: {self.artifact_path}",
            f"Target: {self.target}",
            f"Exists: {self.artifact_path.exists()}",
        ]

        if self.artifact_path.exists():
            total = sum(
                f.stat().st_size for f in self.artifact_path.rglob("*") if f.is_file()
            )
            lines.append(f"Size: {total:,} bytes")
            manifest = self._read_manifest()
            files = manifest.get("files")
            lines.append(f"Files: {len(files) if isinstance(files, list) else 0}")
            ep = manifest.get("entry_point", "")
            if ep:
                lines.append(f"Entry point: {ep}")

        return "\n".join(lines)

    # -- Build -------------------------------------------------------------------

    def build(self) -> Path:
        """Build the bundle for the target platform and return its directory."""
        from forger.build.runtime_builder import CpythonRuntimeBuilder, RuntimeSpec

        manifest = self._read_manifest()
        entry_point = str(manifest.get("entry_point", ""))

        logger.info("Building %s for %s", self.artifact_path, self.target)
        logger.info("Entry point: %s", entry_point or "<none>")

        stdlib_modules = manifest.get("stdlib_modules")
        spec = RuntimeSpec(
            target_triple=self.target,
            python_version=self.python_version,
            # Prune the runtime to the app's actual stdlib requirements.
            required_modules=(
                list(stdlib_modules) if isinstance(stdlib_modules, list) else []
            ),
        )
        runtime_builder = CpythonRuntimeBuilder(spec)
        runtime = runtime_builder.ensure_runtime()

        # Stage into temp dir, then swap — failed builds never corrupt output.
        staging = self.output_dir.with_name(self.output_dir.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        app_dir = staging / "app"
        shutil.copytree(
            self.artifact_path,
            app_dir,
            ignore=shutil.ignore_patterns(".forger-ok"),
        )

        # Copy the built runtime into the bundle (stripped binaries + stdlib).
        shutil.copytree(
            runtime.runtime_dir,
            staging / "runtime",
            ignore=shutil.ignore_patterns(".forger-ok"),
        )

        self._emit_launcher(staging, entry_point)
        self._write_bundle_manifest(staging, runtime, manifest)

        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        staging.rename(self.output_dir)

        logger.info("Bundle ready: %s", self.output_dir)
        return self.output_dir

    # -- Launchers -----------------------------------------------------------------

    def _emit_launcher(self, staging: Path, entry_point: str) -> None:
        from forger.build.targets import TargetPlatform
        from forger.build.wasm_launcher import emit_wasm_launcher

        plat = TargetPlatform.parse(self.target)

        if plat.os == "emscripten":
            emit_wasm_launcher(staging, entry_point)
            return

        bin_name = "python.exe" if plat.os == "windows" else "python"

        if plat.os == "windows":
            launcher = staging / "run.bat"
            launcher.write_text(
                "@echo off\r\n"
                '"%~dp0\\runtime\\bin\\' + bin_name + '" '
                "-c \"import sys; sys.path.insert(0, 'app'); "
                f"import {entry_point}\"\r\n",
                encoding="ascii",
                errors="replace",
            )
            return

        launcher = staging / "run.sh"
        launcher.write_text(
            "#!/bin/sh\n"
            'exec "$(dirname "$0")/runtime/bin/python" -c "import sys; '
            "sys.path.insert(0, 'app'); "
            f'import {entry_point}"\n',
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _write_bundle_manifest(
        self,
        staging: Path,
        runtime: object,
        forge_manifest: dict[str, object],
    ) -> None:
        files = forge_manifest.get("files")
        info = {
            "target": self.target,
            "python_version": getattr(runtime, "version", self.python_version),
            "runtime_reused": bool(getattr(runtime, "reused_existing", False)),
            "entry_point": forge_manifest.get("entry_point"),
            "app_files": len(files) if isinstance(files, list) else 0,
        }
        (staging / "BUNDLE.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def build_target(
    artifact: Path | str,
    target: str,
    output: Path | str | None = None,
) -> Path:
    """Convenience wrapper used by CLI."""
    builder = Builder(artifact_path=artifact, target=target, output_dir=output)
    return builder.build()
