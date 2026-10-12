"""Perl parser: cpanfile."""

from __future__ import annotations

import re
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError
from ..models import BUILD, DEV, OPTIONAL, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import read_text, transitive_unavailable

_REQ_RE = re.compile(
    r"""^\s*(?P<kind>requires|recommends|suggests|test_requires|
        configure_requires|build_requires|runtime_requires)\s+
        ['"](?P<name>[^'"]+)['"]
        (?:\s*,\s*['"](?P<version>[^'"]*)['"])?""",
    re.VERBOSE,
)

_KIND_CATEGORY = {
    "requires": RUNTIME,
    "runtime_requires": RUNTIME,
    "recommends": OPTIONAL,
    "suggests": OPTIONAL,
    "test_requires": DEV,
    "configure_requires": BUILD,
    "build_requires": BUILD,
}


class PerlParser(Parser):
    ecosystem = "perl"
    display_name = "Perl"
    manifest_filenames = ("cpanfile",)
    lockfile_filenames = ()
    can_parse_direct = True
    can_parse_resolved = False
    can_resolve_transitive = False
    limitations = (
        "cpanfile is parsed with a bounded subset; arbitrary Perl is never "
        "executed.",
        "cpanfile.snapshot (Carmel) is recognised but not used to resolve "
        "versions.",
        "Core modules are not distinguished from external distributions.",
    )

    def detect(self, project_dir):
        return (project_dir / "cpanfile").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        warnings = []
        diagnostics = []
        dependencies = []
        seen = set()

        for raw in read_text(project_dir / "cpanfile").splitlines():
            line = raw.split("#", 1)[0]
            match = _REQ_RE.match(line)
            if not match:
                continue
            kind = match.group("kind")
            category = _KIND_CATEGORY.get(kind, RUNTIME)
            if not options.includes(category):
                continue
            name = match.group("name")
            version = match.group("version") or None
            key = (name, category)
            if key in seen:
                continue
            seen.add(key)
            dependencies.append(Dependency(
                ecosystem="perl",
                name=name,
                normalized_name=normalize_simple(name),
                category=category,
                specifier=version,
                source="cpanfile",
                provenance="cpanfile",
            ))

        lockfiles = []
        if options.use_lockfile and (project_dir / "cpanfile.snapshot").is_file():
            warnings.append(
                "cpanfile.snapshot is recognised but not parsed; declared "
                "dependencies are not enriched with resolved versions."
            )
            diagnostics.append(Diagnostic(
                "warning", "perl.snapshot_unsupported",
                "cpanfile.snapshot is not parsed", source="cpanfile.snapshot",
            ))

        if options.resolve_transitive:
            transitive_unavailable(
                "perl", "cpanfile.snapshot", warnings, diagnostics,
                "cpanfile.snapshot (Carmel) is not parsed",
            )

        return ParseResult(
            ecosystem="perl",
            project_name=project_dir.name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["cpanfile"],
            lockfiles=lockfiles,
            lock_coverage="declared_only",
            transitive=False,
        )
