"""CLI implementation for Forger.

Provides the `forger compile` and `forger build` commands.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
