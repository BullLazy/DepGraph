"""Shared parser interface and result containers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from ..models import Dependency, PLATFORM, RUNTIME, UNKNOWN

# Categories included without any explicit include flag.
_DEFAULT_INCLUDED = {RUNTIME, PLATFORM, UNKNOWN}


@dataclass
class ParseOptions:
    include_dev: bool = False
    include_optional: bool = False
    include_peer: bool = False
    include_build: bool = False
    include_test: bool = False
    use_lockfile: bool = False
    resolve_transitive: bool = False
    explicit_manifest: Optional[str] = None
    ecosystems: Tuple[str, ...] = ()
    all_ecosystems: bool = False
    recursive: bool = False

    def includes(self, category):
        if category in _DEFAULT_INCLUDED:
            return True
        if category == "dev":
            return self.include_dev
        if category == "test":
            return self.include_test or self.include_dev
        if category == "optional":
            return self.include_optional
        if category == "peer":
            return self.include_peer
        if category == "build":
            return self.include_build
        return True


@dataclass
class ParseResult:
    ecosystem: str
    project_name: str
    dependencies: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    manifests: list = field(default_factory=list)
    lockfiles: list = field(default_factory=list)
    lock_coverage: str = "none"  # none | declared_only | partial | full
    transitive: bool = False
    transitive_edges: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Parser:
    """Base class for ecosystem parsers.

    Subclasses declare their filenames and capabilities and implement
    :meth:`parse`. Parsers must never execute project code.
    """

    ecosystem = ""
    display_name = ""
    manifest_filenames: Tuple[str, ...] = ()
    manifest_globs: Tuple[str, ...] = ()
    lockfile_filenames: Tuple[str, ...] = ()
    can_parse_direct = True
    can_parse_resolved = False
    can_resolve_transitive = False
    limitations: Tuple[str, ...] = ()

    def detect(self, project_dir):
        """Return ``(is_supported, notes)`` for ``project_dir``."""
        for name in self.manifest_filenames:
            if (project_dir / name).is_file():
                return True, []
        return False, []

    def parse(self, project_dir, options):
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------
    def make_dependency(self, name, normalized_name=None, **kwargs):
        from ..naming import normalize_simple

        if normalized_name is None:
            normalized_name = normalize_simple(name)
        return Dependency(
            ecosystem=self.ecosystem,
            name=name,
            normalized_name=normalized_name,
            **kwargs,
        )
