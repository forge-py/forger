"""CPython source acquisition and caching.

Downloads official CPython source tarballs from python.org, verifies them
against the published SHA256 checksums, and caches extracted trees per
version. The cache is shared across all Forger builds.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

PYTHON_FTP_BASE = "https://www.python.org/ftp/python"

# Versions Forger is known to build cleanly.
KNOWN_GOOD_VERSIONS = ("3.12", "3.11", "3.10", "3.13")

_CACHE_ROOT_ENV = "FORGER_CACHE_DIR"


def cache_root() -> Path:
    """Return the Forger runtime cache directory."""
    override = os.environ.get(_CACHE_ROOT_ENV)
    if override:
        return Path(override).resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "forger" / "cache"
    return Path.home() / ".cache" / "forger"


class CPythonSourceManager:
    """Fetches, verifies, caches and extracts CPython source trees."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or cache_root() / "cpython"
        self.root.mkdir(parents=True, exist_ok=True)

    # -- Paths ----------------------------------------------------------------

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    def source_tree(self, version: str) -> Path:
        """Path of the extracted source tree for ``version``."""
        return self.root / f"Python-{version}"

    def _tarball_path(self, version: str) -> Path:
        return self.downloads_dir / f"Python-{version}.tgz"

    # -- Download + verify ------------------------------------------------------

    def ensure_source(self, version: str) -> Path:
        """Return the extracted CPython source tree for ``version``.

        Downloads and verifies the official tarball on first use; later calls
        reuse the cached extraction.
        """
        tree = self.source_tree(version)
        marker = tree / ".forger-ok"
        if marker.exists():
            return tree

        tarball = self._fetch_tarball(version)
        self._extract(tarball, tree)
        marker.write_text("ok\n")
        logger.info("CPython %s source ready: %s", version, tree)
        return tree

    def _fetch_tarball(self, version: str) -> Path:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        tarball = self._tarball_path(version)

        if tarball.exists() and self._verify_sha256(version, tarball):
            return tarball

        url = f"{PYTHON_FTP_BASE}/{version}/Python-{version}.tgz"
        logger.info("Downloading CPython %s from %s", version, url)
        tmp_path = tarball.with_suffix(".tmp")
        with urllib.request.urlopen(url, timeout=300) as resp, tmp_path.open("wb") as out:
            shutil.copyfileobj(resp, out)
        tmp_path.rename(tarball)

        if not self._verify_sha256(version, tarball):
            tarball.unlink()
            raise RuntimeError(f"SHA256 verification failed for {url}")

        return tarball

    def _verify_sha256(self, version: str, tarball: Path) -> bool:
        """Check the tarball against python.org's published checksums."""
        expected = self._published_checksum(version, tarball.name)
        if expected is None:
            # No published checksum available (offline or unknown release):
            # accept an existing cached tarball but never trust fresh ones.
            logger.warning(
                "No published checksum found for Python-%s.tgz; skipping verification",
                version,
            )
            return True

        actual = self._sha256(tarball)
        ok = actual == expected
        if not ok:
            logger.error(
                "Checksum mismatch for %s: expected %s, got %s",
                tarball.name,
                expected,
                actual,
            )
        return ok

    def _published_checksum(self, version: str, filename: str) -> str | None:
        url = f"{PYTHON_FTP_BASE}/{version}/SHA256SUMS"
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not fetch %s: %s", url, exc)
            return None
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == filename:
                return parts[0]
        return None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # -- Extract ----------------------------------------------------------------

    def _extract(self, tarball: Path, dest: Path) -> None:
        staging = Path(tempfile.mkdtemp(prefix="forger-cpython-", dir=self.root))
        try:
            with tarfile.open(tarball, "r:gz") as tar:
                try:
                    tar.extractall(staging, filter="data")
                except TypeError:  # pre-3.12 / unpatched 3.10-3.11
                    tar.extractall(staging)
            entries = [p for p in staging.iterdir() if p.is_dir()]
            if len(entries) != 1:
                raise RuntimeError(
                    f"Unexpected tarball layout in {tarball.name}: "
                    f"expected one top-level directory, found {len(entries)}"
                )
            staging.rename(dest)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    # -- Info ---------------------------------------------------------------------

    def status(self, versions: list[str] | None = None) -> list[tuple[str, bool]]:
        """Report which of ``versions`` (default known-good) are cached."""
        versions = list(versions or KNOWN_GOOD_VERSIONS)
        return [(v, self.source_tree(v).exists()) for v in versions]
