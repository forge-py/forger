"""Forger — Python application compiler, analyzer, bundler, and builder."""

__version__ = "0.1.0"

# Re-export the forger.py build-time API so projects can do:
#   from forger import defineConfig, ForgerConfig
#   from forger import include, include_module, include_resource, metadata
from forger.api import ForgerConfig as ForgerConfig
from forger.api import defineConfig as defineConfig
from forger.api import include as include
from forger.api import include_module as include_module
from forger.api import include_resource as include_resource
from forger.api import metadata as metadata
