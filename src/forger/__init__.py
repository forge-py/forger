"""Forger — Python application compiler, analyzer, bundler, and builder."""

__version__ = "0.1.0"

# Re-export the forger.py build-time API
from forger.api import (
    ForgerConfig as ForgerConfig,
)
from forger.api import (
    ForgerConfigDict as ForgerConfigDict,
)
from forger.api import (
    OptimizerOptions as OptimizerOptions,
)
from forger.api import (
    defineConfig as defineConfig,
)
