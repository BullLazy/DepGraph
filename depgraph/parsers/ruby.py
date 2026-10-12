"""Ruby parser: Gemfile and Gemfile.lock."""

from __future__ import annotations

import re
from pathlib import Path

from ..diagnostics import ManifestError
from ..models import DEV, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import (
    apply_resolved_versions,
    expand_transitive,
    read_text,
    transitive_unavailable,
)

_GEM_RE = re.compile(
    r"""^\s*gem\s+['"](?P<name>[^'"]+)['"]"""
    r"""(?:\s*,\s*['"](?P<version>[^'"]+)['"])?"""
)
_GROUP_RE = re.compile(r"^\s*group\s+(?P<groups>.+?)\s+do\s*$")
_SPEC_RE = re.compile(r"^\s{4}(?P<name>\S+)\s+\((?P<version>[^)]+)\)")


class RubyParser(Parser):
    ecosystem = "ruby"
    display_name = "Ruby"
    manifest_filenames = ("Gemfile",)
    lockfile_filenames = ("Gemfile.lock",)
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = True
    limitations = (
        "Gemfile is parsed with a bounded subset (gem, group blocks); "
        "arbitrary Ruby is never executed.",
        "Gemfile.lock resolves transitive gem edges when --transitive is "
        "used; without it only declared dependencies are graphed.",
        "gemspec, .ruby-version and path/git sources are not resolved.",
    )

    def detect(self, project_dir):
        return (project_dir / "Gemfile").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        warnings = []
        diagnostics = []
        dependencies = []

        group_stack = []
        for raw in read_text(project_dir / "Gemfile").splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            match = _GROUP_RE.match(line)
            if match:
                groups = {
                    g.strip().strip(":").lower()
                    for g in match.group("groups").replace("'", "")
                    .replace('"', "").split(",")
                }
                group_stack.append(groups)
                continue
            if line.strip() == "end":
                if group_stack:
                    group_stack.pop()
                continue
            match = _GEM_RE.match(line)
            if not match:
                continue
            name = match.group("name")
            version = match.group("version")
            active = set().union(*group_stack) if group_stack else set()
            category = RUNTIME
            if active & {"development", "test"}:
                category = DEV
            if not options.includes(category):
                continue
            dependencies.append(Dependency(
                ecosystem="ruby",
                name=name,
                normalized_name=normalize_simple(name),
                category=category,
                specifier=version,
                source="Gemfile",
                provenance="bundler",
            ))

        lockfiles = []
        coverage = "declared_only"
        edges = []
        transitive_resolved = False
        have_lock = (project_dir / "Gemfile.lock").is_file()
        if (options.use_lockfile or options.resolve_transitive) and have_lock:
            resolved = _parse_lock(project_dir / "Gemfile.lock", warnings)
            unmatched = apply_resolved_versions(
                dependencies, resolved, "Gemfile.lock", warnings,
                diagnostics, "ruby",
            )
            lockfiles.append("Gemfile.lock")
            coverage = "partial" if resolved else "declared_only"
            if options.resolve_transitive:
                graph = _parse_lock_graph(project_dir / "Gemfile.lock")
                transitive, edges = expand_transitive(
                    dependencies, graph, "ruby", "Gemfile.lock",
                    warnings, diagnostics,
                )
                dependencies.extend(transitive)
                if graph:
                    transitive_resolved = True
                    coverage = "partial" if unmatched else "full"
        elif options.resolve_transitive:
            transitive_unavailable(
                "ruby", "Gemfile.lock", warnings, diagnostics,
                "no Gemfile.lock found to expand the dependency graph",
            )

        return ParseResult(
            ecosystem="ruby",
            project_name=project_dir.name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["Gemfile"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=transitive_resolved,
            transitive_edges=edges,
        )


def _parse_lock_graph(path):
    """Return the resolved package graph of a Gemfile.lock."""
    graph = {}
    in_specs = False
    current = None
    for raw in read_text(path).splitlines():
        if raw.startswith("  specs:"):
            in_specs = True
            continue
        if in_specs and raw and not raw.startswith(" "):
            in_specs = False
        if not in_specs:
            continue
        match = _SPEC_RE.match(raw)
        if match:
            name = match.group("name")
            version = match.group("version").split()[-1]
            current = name
            graph.setdefault(name, []).append({
                "name": name,
                "version": version,
                "requires": [],
            })
            continue
        if current is not None and raw.startswith("      "):
            dep_name = raw.strip().split(None, 1)[0]
            if dep_name:
                for entry in graph[current]:
                    entry["requires"].append((dep_name, None))
    return graph


def _parse_lock(path, warnings):
    resolved = {}
    in_specs = False
    for raw in read_text(path).splitlines():
        if raw.startswith("  specs:"):
            in_specs = True
            continue
        if in_specs and raw and not raw.startswith(" "):
            in_specs = False
        if not in_specs:
            continue
        match = _SPEC_RE.match(raw)
        if not match:
            continue
        name = match.group("name")
        version = match.group("version").split()[-1]
        resolved.setdefault(name, set()).add(version)
    return resolved
