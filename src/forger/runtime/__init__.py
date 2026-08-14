"""Runtime/bootstrap infrastructure.

Provides the bootstrap code that runs inside a Forger-built executable
to initialize the VFS, set up sys.path, and launch the application.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def bootstrap(entry_point: str, vfs_root: Path | None = None) -> None:
    """Bootstrap the Forger runtime environment.

    Sets up sys.path, VFS, and imports the entry point module.

    Args:
        entry_point: Fully qualified module name to execute.
        vfs_root: Path to the VFS root (if using extracted VFS).
    """
    logger.info("Forger runtime bootstrap")
    logger.info("Entry point: %s", entry_point)

    # Configure sys.path
    if vfs_root:
        sys.path.insert(0, str(vfs_root))
        logger.info("VFS root: %s", vfs_root)

    # Import and execute entry point
    try:
        import importlib

        module = importlib.import_module(entry_point)
        logger.info("Entry point module loaded: %s", module)

        # If the module has a main function, call it
        if hasattr(module, "main"):
            logger.info("Calling main() in %s", entry_point)
            module.main()
        else:
            logger.info("Module %s has no main() — execution complete", entry_point)

    except ImportError as e:
        logger.error("Failed to import entry point %s: %s", entry_point, e)
        sys.exit(1)
    except Exception as e:
        logger.error("Entry point execution failed: %s", e, exc_info=True)
        sys.exit(1)


def setup_sys_path(paths: list[str]) -> None:
    """Set up sys.path with the given paths."""
    for path in paths:
        if path not in sys.path:
            sys.path.insert(0, path)


def get_runtime_info() -> dict[str, str]:
    """Get information about the runtime environment."""
    return {
        "python_version": sys.version,
        "platform": sys.platform,
        "executable": sys.executable,
        "prefix": sys.prefix,
        "path_count": str(len(sys.path)),
    }
