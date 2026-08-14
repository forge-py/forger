"""Forger — A Python application compiler, analyzer, bundler, and cross-platform executable builder."""

__version__ = "0.1.0"

# Re-export the forger.py build-time API so projects can do:
#   from forger import include, include_module, include_resource, metadata
from forger.api import (
    include,
    include_module,
    include_resource,
    metadata,
)
