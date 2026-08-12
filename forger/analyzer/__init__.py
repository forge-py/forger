"""Static analysis for Python source files.

Uses Python's AST module to analyze imports, resource access,
dynamic imports, and other dependency-generating constructs.
"""

from forger.analyzer.imports import ImportAnalyzer
from forger.analyzer.resources import ResourceAnalyzer
from forger.analyzer.dynamic import DynamicImportAnalyzer

__all__ = [
    "ImportAnalyzer",
    "ResourceAnalyzer",
    "DynamicImportAnalyzer",
]
