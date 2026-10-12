"""Parser registry.

Only ecosystems with a real, tested parser are registered here. Filenames that
are recognised but unsupported are listed in :mod:`depgraph.discovery`.
"""

from __future__ import annotations

import fnmatch

from .base import ParseOptions, ParseResult, Parser
from .python import PythonParser
from .javascript import JavaScriptParser
from .rust import RustParser
from .go import GoParser
from .java import JavaParser
from .dotnet import DotNetParser
from .php import PhpParser
from .rtools import RParser
from .julia import JuliaParser
from .cpp import CppParser
from .ruby import RubyParser
from .perl import PerlParser

PARSERS = [
    PythonParser(),
    JavaScriptParser(),
    RustParser(),
    GoParser(),
    JavaParser(),
    DotNetParser(),
    PhpParser(),
    RParser(),
    JuliaParser(),
    CppParser(),
    RubyParser(),
    PerlParser(),
]

_BY_ECOSYSTEM = {parser.ecosystem: parser for parser in PARSERS}


def parser_for_ecosystem(ecosystem):
    return _BY_ECOSYSTEM.get(ecosystem)


def parser_for_filename(name):
    for parser in PARSERS:
        if name in parser.manifest_filenames:
            return parser
        for pattern in parser.manifest_globs:
            if fnmatch.fnmatch(name, pattern):
                return parser
    return None


def supported_ecosystems():
    return tuple(parser.ecosystem for parser in PARSERS)
