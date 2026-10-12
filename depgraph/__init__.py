"""DepGraph: a read-only, multilingual dependency manifest analyzer.

The package re-exports the historical public API (``normalize_name``,
``parse_requirement``, ``load_manifest``, ``analyze``, ``print_terminal``,
``main``) so that existing integrations keep working.
"""

from __future__ import annotations

from .analysis import analyze
from .cli import main
from .diagnostics import (
    DepGraphError,
    ManifestError,
    UnsupportedProjectError,
)
from .models import Dependency
from .naming import normalize_extra, normalize_name, normalize_simple
from .output import print_terminal, render_json
from .parsers import (
    PARSERS,
    parser_for_ecosystem,
    parser_for_filename,
    supported_ecosystems,
)
from .parsers.base import ParseOptions, ParseResult, Parser
from .parsers.python import load_manifest, parse_requirement
from .version import __version__, SCHEMA_VERSION

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "analyze",
    "main",
    "print_terminal",
    "render_json",
    "normalize_name",
    "normalize_extra",
    "normalize_simple",
    "parse_requirement",
    "load_manifest",
    "Dependency",
    "Parser",
    "ParseOptions",
    "ParseResult",
    "PARSERS",
    "parser_for_ecosystem",
    "parser_for_filename",
    "supported_ecosystems",
    "DepGraphError",
    "ManifestError",
    "UnsupportedProjectError",
]
