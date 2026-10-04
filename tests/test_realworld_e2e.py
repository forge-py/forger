"""End-to-end tests: real-world-shaped multi-module projects.

Runs a project with packages, relative imports, resources, stdlib use and
Cython sources through the full pipeline: compile -> .forge artifact ->
target bundle assembly. Emscripten bundles are asserted to be
self-contained (wasm + glue + launcher + app tree), runnable in principle
without any host Python.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from forger.builder import Builder
from forger.compiler import Compiler

# ---------------------------------------------------------------------------
# Fixture project: realistic multi-package layout
# ---------------------------------------------------------------------------


def _make_realworld_project(root: Path) -> None:
    """A small but realistic project: pkg + services + resources + cython."""
    # Package with __init__, submodule graph and relative imports.
    pkg = root / "shop"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from .core import Store\n\n__all__ = [\"Store\"]\n",
        encoding="utf-8",
    )
    (pkg / "core.py").write_text(
        "import json\nfrom . import pricing\n\n\nclass Store:\n"
        "    def quote(self, item: str) -> str:\n"
        "        rate = pricing.rate_for(item)\n"
        "        return json.dumps({\"item\": item, \"rate\": rate})\n",
        encoding="utf-8",
    )
    (pkg / "pricing.py").write_text(
        "TAX = 0.2\n\n\ndef rate_for(item: str) -> float:\n"
        "    return 9.99 * (1 + TAX)\n",
        encoding="utf-8",
    )

    # Second top-level package consumed via absolute import.
    util = root / "textutil"
    util.mkdir()
    (util / "__init__.py").write_text(
        "from .slug import slugify\n\n__all__ = [\"slugify\"]\n",
        encoding="utf-8",
    )
    (util / "slug.py").write_text(
        "import re\n\n\ndef slugify(s: str) -> str:\n"
        "    return re.sub(r\"[^a-z0-9]+\", \"-\", s.lower()).strip(\"-\")\n",
        encoding="utf-8",
    )

    # A resource read at runtime through Path(__file__).
    data_dir = pkg / "data"
    data_dir.mkdir()
    (data_dir / "catalog.txt").write_text("widget\nsprocket\ngear\n", encoding="utf-8")
    (pkg / "resources.py").write_text(
        "from pathlib import Path\n\n\ndef catalog() -> list[str]:\n"
        "    p = Path(__file__).parent / \"data\" / \"catalog.txt\"\n"
        "    return p.read_text(encoding=\"utf-8\").split()\n",
        encoding="utf-8",
    )

    # Cython extension imported from pure-Python code.
    (root / "fastmath.pyx").write_text(
        "cdef inline double _sq(double x) nogil:\n    return x * x\n\n\n"
        "def square(double v):\n    return _sq(v)\n",
        encoding="utf-8",
    )
    (pkg / "accel.py").write_text(
        "def boost(x):\n    try:\n        import fastmath\n"
        "        return fastmath.square(x)\n    except ImportError:\n"
        "        return x * x\n",
        encoding="utf-8",
    )

    # Entry point wiring everything together.
    (root / "main.py").write_text(
        "from shop import Store\nfrom textutil import slugify\n"
        "from shop.resources import catalog\nfrom shop.accel import boost\n\n"
        "def main():\n"
        "    store = Store()\n"
        "    print(store.quote(slugify(\"Widget Pro\")))\n"
        "    print(catalog())\n"
        "    print(boost(4))\n\n"
        "main()\n",
        encoding="utf-8",
    )


@pytest.fixture()
def realworld_artifact(tmp_path: Path) -> Path:
    """Compile the fixture project into a .forge artifact."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_realworld_project(root)

    compiler = Compiler(
        project_root=root,
        entry_point="main",
        output_path=root / "app.forge",
    )
    compiler.analyze()
    compiler.generate_vfs()
    compiler.generate_artifact()
    return root / "app.forge"


# ---------------------------------------------------------------------------
# Compile-stage correctness on the real-world shape
# ---------------------------------------------------------------------------


def test_compile_preserves_full_module_graph(realworld_artifact: Path) -> None:
    manifest = json.loads((realworld_artifact / "MANIFEST.json").read_text())
    # Manifest stores per-file records; extract the path list.
    files = {entry["path"] for entry in manifest["files"]}

    # Every module the entry reaches must survive tree-shaking.
    for expected in (
        "main.py",
        "shop/__init__.py",
        "shop/core.py",
        "shop/pricing.py",
        "textutil/__init__.py",
        "textutil/slug.py",
        "shop/resources.py",
        "shop/accel.py",
    ):
        assert expected in files, f"missing {expected}"

    # The resource it reads must be bundled too.
    assert "shop/data/catalog.txt" in files

    # stdlib requirements were extracted for runtime pruning.
    mods = manifest["stdlib_modules"]
    assert "json" in mods or "sys" in mods  # baseline present

    # Per-file records carry path / kind / size / hash.
    main_record = next(f for f in manifest["files"] if f["path"] == "main.py")
    assert main_record["kind"] == "python"
    assert main_record["size"] > 0
    # SHA-256 hex digest is 64 hex characters.
    assert len(main_record["hash"]) == 64, main_record["hash"]
    catalog_record = next(
        f for f in manifest["files"] if f["path"] == "shop/data/catalog.txt"
    )
    assert catalog_record["kind"] == "resource"

    # format_version is set; the absolute project_root is NOT embedded.
    assert manifest["format_version"] == 1
    assert "project_root" not in manifest


def test_vfs_app_actually_runs(realworld_artifact: Path, tmp_path: Path) -> None:
    """The compiled VFS behaves like the source under normal CPython."""
    out = subprocess.run(
        [
            "python",
            "-c",
            f"import sys; sys.path.insert(0, r'{realworld_artifact}'); import main",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert '"item": "widget-pro"' in out.stdout
    assert "['widget', 'sprocket', 'gear']" in out.stdout
    assert out.stdout.strip().endswith("16")


# ---------------------------------------------------------------------------
# Bundle assembly per target
# ---------------------------------------------------------------------------


def test_build_linux_bundle_with_fake_runtime(
    realworld_artifact: Path, tmp_path: Path
) -> None:
    fake_runtime = tmp_path / "rt" / "linux-x64-3.12"
    (fake_runtime / "bin").mkdir(parents=True)
    (fake_runtime / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    lib = fake_runtime / "lib" / "python3.12"
    lib.mkdir(parents=True)
    (lib / "os.py").write_text("", encoding="utf-8")

    output = tmp_path / "bundle"

    class FakeResult:
        triple = "linux-x64"
        version = "3.12"
        stripped_count = 1
        stdlib_module_count = 1
        pruned_extensions = 0
        reused_existing = True
        runtime_dir = fake_runtime

    from unittest.mock import patch

    with patch(
        "forger.build.runtime_builder.CpythonRuntimeBuilder.ensure_runtime",
        autospec=True,
        return_value=FakeResult(),
    ):
        result = Builder(
            artifact_path=realworld_artifact,
            target="linux-x64",
            output_dir=output,
            python_version="3.12",
        ).build()

    assert result == output
    launcher_text = (output / "run.sh").read_text(encoding="utf-8")
    assert "runtime/bin/python" in launcher_text
    assert (output / "app" / "main.py").is_file()


def test_emscripten_bundle_is_self_contained(
    realworld_artifact: Path, tmp_path: Path
) -> None:
    """WASM bundle carries wasm+glue+launcher+app; nothing external needed."""
    fake_runtime = tmp_path / "rt" / "emscripten-wasm-3.13"
    bindir = fake_runtime / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python.wasm").write_bytes(b"\0asmfake")
    (bindir / "python.mjs").write_text(
        "export default function createModule(){return {}}\n", encoding="utf-8"
    )
    lib = fake_runtime / "lib" / "python3.13"
    lib.mkdir(parents=True)
    (lib / "os.py").write_text("", encoding="utf-8")

    output = tmp_path / "wasm-bundle"

    class FakeResult:
        triple = "emscripten-wasm"
        version = "3.13"
        stripped_count = 0
        stdlib_module_count = 1
        pruned_extensions = 0
        reused_existing = True
        runtime_dir = fake_runtime

    from unittest.mock import patch

    with patch(
        "forger.build.runtime_builder.CpythonRuntimeBuilder.ensure_runtime",
        autospec=True,
        return_value=FakeResult(),
    ):
        bundle = Builder(
            artifact_path=realworld_artifact,
            target="emscripten-wasm",
            output_dir=output,
            python_version="3.13",
        ).build()

    # Self-contained layout assertions.
    assert (bundle / "run_app.mjs").is_file()
    assert (bundle / "runtime" / "bin" / "python.wasm").is_file()
    assert (bundle / "runtime" / "bin" / "python.mjs").is_file()
    assert (bundle / "app" / "main.py").is_file()
    assert (bundle / "app" / "shop" / "core.py").is_file()
    assert (bundle / "app" / "shop" / "data" / "catalog.txt").is_file()
    assert (bundle / "runtime" / "lib" / "python3.13" / "os.py").is_file()

    # Launcher references the real glue + entry, not a stub.
    launcher = (bundle / "run_app.mjs").read_text(encoding="utf-8")
    assert "python.mjs" in launcher
    assert "import main" in launcher
    assert "/forger/app" in launcher
    assert "console.log(\"Forger WASM app:" not in launcher  # old stub gone

    # BUNDLE.json records target and entry correctly.
    meta = json.loads((bundle / "BUNDLE.json").read_text(encoding="utf-8"))
    assert meta["target"] == "emscripten-wasm"
    assert meta["entry_point"] == "main"


def test_emscripten_missing_glue_fails_loudly(tmp_path: Path) -> None:
    """Assembly refuses to emit a bundle that could never instantiate."""
    from forger.build.runtime_builder import CpythonRuntimeBuilder, RuntimeSpec

    spec = RuntimeSpec(target_triple="emscripten-wasm", python_version="3.13")
    builder = CpythonRuntimeBuilder(spec, cache_root=tmp_path)
    build_dir = builder.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "python.wasm").write_bytes(b"\0asm")

    with pytest.raises(RuntimeError, match="[Gg]lue"):
        builder._assemble_emscripten(build_dir, tmp_path / "out")


def test_emscripten_glue_from_source_tree_is_picked_up(tmp_path: Path) -> None:
    """Glue living next to the source tree still lands in the bundle."""
    from forger.build.cpython_source import CPythonSourceManager
    from forger.build.runtime_builder import CpythonRuntimeBuilder, RuntimeSpec

    sources = CPythonSourceManager(root=tmp_path / "cpython-src")
    spec = RuntimeSpec(target_triple="emscripten-wasm", python_version="3.13")
    builder = CpythonRuntimeBuilder(spec, cache_root=tmp_path, sources=sources)

    source_tree = sources.source_tree("3.13")
    source_tree.mkdir(parents=True)
    (source_tree / "python.wasm").write_bytes(b"\0asm")
    (source_tree / "python.js").write_text("// classic glue\n", encoding="utf-8")

    out = tmp_path / "rt-out"
    stripped = builder._assemble_emscripten(tmp_path / "empty-build", out)
    assert stripped == 0
    assert (out / "bin" / "python.wasm").is_file()
    assert (out / "bin" / "python.js").is_file()


def _node_available() -> bool:
    import shutil

    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node not installed")
def test_wasm_launcher_is_valid_esm(tmp_path: Path) -> None:
    """The generated run_app.mjs must parse under Node's ESM parser."""
    from forger.build.wasm_launcher import emit_wasm_launcher

    bindir = tmp_path / "runtime" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python.mjs").write_text("export default () => ({})", encoding="utf-8")
    lib = tmp_path / "runtime" / "lib" / "python3.13"
    lib.mkdir(parents=True)
    (lib / "os.py").write_text("", encoding="utf-8")

    launcher = emit_wasm_launcher(tmp_path, "main")
    result = subprocess.run(
        ["node", "--check", str(launcher)],  # noqa: S607 - well-known binary
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
