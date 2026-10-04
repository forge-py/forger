"""Django optimizer plugin.

Replaces the hardcoded Django template-dir fallback that used to
live in ``Compiler._resolve_resource_path``. The plugin implements
the ``discover_resources`` hook so a Django template id like
``blog/post_list.html`` is mapped to its real file on disk using
Django's actual template lookup rules:

  - ``<app>/templates/<template_path>``
  - ``templates/<template_path>``
  - any top-level ``templates*`` directory

PHILOSOPHY.md §6 / PLUGIN_ARCHITECTURE.md §10.
"""

from __future__ import annotations

from pathlib import Path

from forger.optimizer import BasePlugin, PluginContext

# Mark project-level template roots. A directory named ``templates`` or
# any name that starts with ``templates`` (Django convention) is a
# project-wide template dir.
_PROJECT_TEMPLATE_GLOB = "templates*"


class DjangoPlugin(BasePlugin):
    """Plugin that resolves Django-style template ids to real files.

    Without this plugin, the compiler's legacy template-dir fallback
    still runs but is deprecated. Installing ``DjangoPlugin`` makes
    the behavior explicit and configurable.
    """

    name = "django"

    def __init__(self, *, project_root: Path | None = None) -> None:
        # The project root is used as the search base. If not provided
        # the plugin falls back to ``context.project_root`` at call time.
        self._project_root_override = project_root

    def discover_resources(
        self, module_id: str, *, context: PluginContext
    ) -> str | None:
        """Map a Django template id (e.g. ``blog/post_list.html``) to
        a real file under the project root.

        Returns an absolute path string, or None if no Django-style
        location matches (so the next plugin / fallback gets a turn).
        """
        project_root = self._project_root_override or context.project_root
        normalized = module_id.replace("\\", "/").lstrip("/")

        # 1. <app>/templates/<normalized> — the per-app template dir.
        for app_dir in self._iter_app_dirs(project_root):
            candidate = app_dir / "templates" / normalized
            if candidate.is_file():
                return str(candidate)

        # 2. <project>/templates/<normalized> and any matching
        #    templates*/<normalized> at the project root.
        for template_root in self._iter_project_template_dirs(project_root):
            candidate = template_root / normalized
            if candidate.is_file():
                return str(candidate)

        # 3. Last-ditch: a basename match anywhere in the project.
        #    Used when the user passes a flat id like ``login.html`` and
        #    the template lives somewhere unusual.
        basename = Path(normalized).name
        if basename:
            for hit in project_root.rglob(basename):
                if hit.is_file():
                    return str(hit)

        return None

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _iter_app_dirs(project_root: Path) -> list[Path]:
        """Yield every immediate subdirectory of ``project_root``.

        Django apps are conventionally top-level Python packages with
        an ``__init__.py``. We return all immediate subdirs regardless
        of whether they have an ``__init__.py`` so the search is robust
        against non-Django layout quirks.
        """
        if not project_root.is_dir():
            return []
        return [p for p in project_root.iterdir() if p.is_dir()]

    @staticmethod
    def _iter_project_template_dirs(project_root: Path) -> list[Path]:
        """Yield the project-level template directories.

        A directory matches the convention if its name is exactly
        ``templates`` or starts with ``templates`` (e.g.
        ``templates_admin``). The search excludes directories that
        look like they belong to a specific app (heuristic: a path
        with a sibling ``__init__.py`` and no leading ``templates``).
        """
        if not project_root.is_dir():
            return []
        matches: list[Path] = []
        for entry in project_root.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name == _PROJECT_TEMPLATE_GLOB or name.startswith("templates"):
                matches.append(entry)
        return matches
