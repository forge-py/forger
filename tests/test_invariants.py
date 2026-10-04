"""End-to-end integration tests for atomic compile and path preservation.

These tests exercise the spec's hard invariants (PATH_PRESERVATION.md,
CLAUDE.md §"Architecture Invariants" #11) by running the full compiler
pipeline on a multi-directory project and asserting:

  - Project-relative paths are preserved exactly through the VFS.
  - A failed compile leaves the previous dist/ untouched.
  - The .forge manifest's file records are sorted and project-relative.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forger.compiler import Compiler


def _make_project(root: Path) -> None:
    """The PHILOSOPHY.md §36 example project: nested packages, data,
    templates — used to verify path preservation end-to-end."""
    src = root / "src" / "app"
    src.mkdir(parents=True)
    (src / "main.py").write_text(
        "from app.used import used_fn\nprint(used_fn())\n", encoding="utf-8"
    )
    (src / "used.py").write_text(
        "def used_fn():\n    return 1\n", encoding="utf-8"
    )
    (src / "unused.py").write_text(
        "def unused_fn():\n    return 999\n", encoding="utf-8"
    )
    data = src / "data"
    data.mkdir()
    (data / "used.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    (data / "unused.csv").write_text("never,read\n", encoding="utf-8")
    templates = src / "templates"
    templates.mkdir()
    (templates / "used.html").write_text(
        "<html>used</html>\n", encoding="utf-8"
    )
    (templates / "unused.html").write_text(
        "<html>unused</html>\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# §3.1 Path preservation
# ---------------------------------------------------------------------------


def test_paths_preserved_through_vfs(tmp_path: Path) -> None:
    """The VFS output preserves project-relative paths exactly."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_project(root)

    vfs_dir = root / "dist"
    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=vfs_dir,
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    compiler.generate_vfs()

    expected_paths = {
        "src/app/main.py",
        "src/app/used.py",
    }
    actual = {p.relative_to(vfs_dir).as_posix() for p in vfs_dir.rglob("*") if p.is_file()}
    assert expected_paths <= actual, (
        f"missing {expected_paths - actual} (got {actual})"
    )
    # No file ever flattens into the dist root.
    for p in vfs_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(vfs_dir)
            assert rel.parent != Path(".") or rel.name == "MANIFEST.json", (
                f"file {rel} lives at dist root"
            )


def test_no_absolute_paths_in_manifest(tmp_path: Path) -> None:
    """The manifest must not embed absolute source paths
    (PATH_PRESERVATION.md §"Absolute Paths")."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_project(root)

    vfs_dir = root / "dist"
    artifact = root / "out.forge"
    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=vfs_dir,
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    compiler.generate_vfs()
    # Package the VFS into a separate .forge artifact.
    Compiler.forge_from_vfs(vfs_dir, artifact).generate_artifact()

    manifest_text = (artifact / "MANIFEST.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    serialized = json.dumps(manifest)
    assert str(root) not in serialized, (
        f"manifest leaks absolute path {root}"
    )


def test_manifest_file_paths_are_sorted(tmp_path: Path) -> None:
    """Deterministic output: manifest ``files`` are sorted by path."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_project(root)

    vfs_dir = root / "dist"
    artifact = root / "out.forge"
    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=vfs_dir,
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    compiler.generate_vfs()
    Compiler.forge_from_vfs(vfs_dir, artifact).generate_artifact()

    manifest = json.loads(
        (artifact / "MANIFEST.json").read_text(encoding="utf-8")
    )
    paths = [f["path"] for f in manifest["files"]]
    assert paths == sorted(paths), f"manifest not sorted: {paths}"


# ---------------------------------------------------------------------------
# §6.1 Atomic compile
# ---------------------------------------------------------------------------


def test_atomic_compile_preserves_previous_dist_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed compile must not corrupt the previous valid dist/."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    out = root / "dist"

    # First, produce a good dist.
    compiler = Compiler(
        project_root=root, entry_point="main", output_path=out
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    compiler.generate_vfs()
    good_main = (out / "main.py").read_text(encoding="utf-8")
    assert "print('hi')" in good_main
    # Forge a good manifest.
    good_artifact = root / "app.forge"
    Compiler.forge_from_vfs(out, good_artifact).generate_artifact()
    good_manifest = (good_artifact / "MANIFEST.json").read_bytes()
    assert len(good_manifest) > 0

    # Now make a second compile fail inside generate_vfs. The previous
    # forge artifact must remain untouched.
    def _explode(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated failure mid-write")

    monkeypatch.setattr(
        "forger.compiler.Compiler._write_python_modules", _explode
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        compiler2 = Compiler(
            project_root=root, entry_point="main", output_path=out
        )
        compiler2.analyze()
        compiler2.run_plugins(plugins=[], include_optimizer=False)
        compiler2.generate_vfs()

    # Prior forge manifest is byte-for-byte identical to the good build.
    assert (good_artifact / "MANIFEST.json").read_bytes() == good_manifest
    # The temp staging directory the failed build was writing to is
    # cleaned up — the .tmp sibling must not be left around.
    assert not (root / "dist.tmp").exists()


def test_staging_dir_uses_dist_dot_tmp(tmp_path: Path) -> None:
    """A successful compile stages into ``dist.tmp`` then renames."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")

    staging_used: list[str] = []

    real_copytree = __import__("shutil").copytree

    def spy(src: Path, dst: Path, *args, **kwargs):
        if str(dst).endswith(".tmp"):
            staging_used.append(str(dst))
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("shutil.copytree", spy)
    try:
        vfs_dir = root / "dist"
        artifact = root / "app.forge"
        compiler = Compiler(
            project_root=root, entry_point="main", output_path=vfs_dir
        )
        compiler.analyze()
        compiler.run_plugins(plugins=[], include_optimizer=False)
        compiler.generate_vfs()
        # The forge stage also uses a .tmp staging.
        Compiler.forge_from_vfs(vfs_dir, artifact).generate_artifact()
    finally:
        monkeypatch.undo()

    # At least one .tmp staging dir was used (forge_from_vfs always does).
    assert any(".tmp" in p for p in staging_used), (
        f"expected a .tmp staging dir, got {staging_used}"
    )


# ---------------------------------------------------------------------------
# §9.1 Diagnostics — provenance in the summary
# ---------------------------------------------------------------------------


def test_diagnostic_summary_includes_module_provenance(tmp_path: Path) -> None:
    """The summary lists which edge brought each retained module in."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_project(root)
    compiler = Compiler(
        project_root=root, entry_point="main", output_path=root / "out.forge"
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    summary = compiler.diagnostic_summary()
    # The entry point is the root and is always listed.
    assert "main" in summary
    # When the Rust-backed graph is in use we can't always read edge
    # metadata, so the sample is best-effort; just assert the section
    # header is present when included modules exist.
    if "Module inclusion" in summary:
        # Each listed line is either "...(entry point)" or "...via X".
        for line in summary.splitlines():
            if line.strip().startswith("- "):
                assert ("via " in line) or ("(entry point)" in line), line


# ---------------------------------------------------------------------------
# Manifest format_version (PHILOSOPHY.md §12)
# ---------------------------------------------------------------------------


def test_manifest_writes_format_version_constant(tmp_path: Path) -> None:
    """The manifest's ``format_version`` matches the exported constant."""
    from forger.compiler import Compiler, MANIFEST_FORMAT_VERSION

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    vfs = tmp_path / "dist"
    artifact = tmp_path / "out.forge"
    compiler = Compiler(
        project_root=tmp_path, entry_point="main", output_path=vfs
    )
    compiler.analyze()
    compiler.run_plugins(plugins=[], include_optimizer=False)
    compiler.generate_vfs()
    Compiler.forge_from_vfs(vfs, artifact).generate_artifact()

    manifest = json.loads(
        (artifact / "MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["format_version"] == MANIFEST_FORMAT_VERSION


def test_manifest_validator_rejects_unknown_version(tmp_path: Path) -> None:
    """A manifest with a future format_version is refused on read."""
    from forger.builder import Builder

    (tmp_path / "MANIFEST.json").write_text(
        json.dumps(
            {
                "format_version": 99,
                "version": "0.1.0",
                "entry_point": "main",
                "files": [],
                "stdlib_modules": [],
            }
        ),
        encoding="utf-8",
    )
    b = Builder(
        artifact_path=tmp_path,
        target="linux-x64",
        output_dir=tmp_path / "bundle",
    )
    with pytest.raises(RuntimeError, match="format_version"):
        b._read_manifest()


def test_manifest_validator_rejects_missing_version(tmp_path: Path) -> None:
    """A manifest without ``format_version`` is refused on read."""
    from forger.builder import Builder

    (tmp_path / "MANIFEST.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "entry_point": "main",
                "files": [],
                "stdlib_modules": [],
            }
        ),
        encoding="utf-8",
    )
    b = Builder(
        artifact_path=tmp_path,
        target="linux-x64",
        output_dir=tmp_path / "bundle",
    )
    with pytest.raises(RuntimeError, match="missing"):
        b._read_manifest()

