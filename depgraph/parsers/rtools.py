"""R parser: DESCRIPTION (DCF) and renv.lock."""

from __future__ import annotations

import re
from pathlib import Path

from ..diagnostics import ManifestError
from ..models import BUILD, OPTIONAL, PLATFORM, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import (
    apply_resolved_versions,
    expand_transitive,
    read_json,
    read_text,
    transitive_unavailable,
)

_FIELD_CATEGORY = {
    "Depends": RUNTIME,
    "Imports": RUNTIME,
    "LinkingTo": BUILD,
    "Suggests": OPTIONAL,
    "Enhances": OPTIONAL,
}

_PKG_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9._]*)\s*(?:\(([^)]*)\))?\s*$")


def _parse_description(path):
    """Parse a Debian-control (DCF) file into a dict of fields."""
    fields = {}
    current = None
    for line in read_text(path).splitlines():
        if not line.strip():
            current = None
            continue
        if line[0] in " \t" and current:
            fields[current] += " " + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            current = key.strip()
            fields[current] = value.strip()
    return fields


def _packages(value):
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        match = _PKG_RE.match(item)
        if not match:
            continue
        name, constraint = match.groups()
        specifier = None
        if constraint:
            specifier = constraint.replace(" ", "")
        yield name, specifier


class RParser(Parser):
    ecosystem = "r"
    display_name = "R"
    manifest_filenames = ("DESCRIPTION",)
    lockfile_filenames = ("renv.lock",)
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = True
    limitations = (
        "Only DESCRIPTION dependency fields are parsed; R code is never run.",
        "renv.lock resolves transitive package edges when --transitive is "
        "used; without it only declared dependencies are graphed.",
        "Suggests/Enhances packages are never graphed as hard edges.",
    )

    def detect(self, project_dir):
        return (project_dir / "DESCRIPTION").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        fields = _parse_description(project_dir / "DESCRIPTION")

        warnings = []
        diagnostics = []
        dependencies = []

        resolved = {}
        lockfiles = []
        coverage = "declared_only"
        edges = []
        have_lock = (project_dir / "renv.lock").is_file()
        if (options.use_lockfile or options.resolve_transitive) and have_lock:
            resolved = _parse_renv_lock(project_dir / "renv.lock", warnings)
            lockfiles.append("renv.lock")
            coverage = "partial" if resolved else "declared_only"

        for field, category in _FIELD_CATEGORY.items():
            if not options.includes(category):
                continue
            value = fields.get(field)
            if not value:
                continue
            for name, specifier in _packages(value):
                if name.lower() == "r":
                    category_local = PLATFORM
                else:
                    category_local = category
                dependencies.append(Dependency(
                    ecosystem="r",
                    name=name,
                    normalized_name=normalize_simple(name),
                    category=category_local,
                    specifier=specifier,
                    source="DESCRIPTION",
                    provenance="r",
                ))

        unmatched = 0
        if resolved:
            unmatched = apply_resolved_versions(
                dependencies, resolved, "renv.lock", warnings, diagnostics, "r",
            )

        transitive_resolved = False
        if options.resolve_transitive:
            if have_lock:
                graph = _parse_renv_lock_graph(project_dir / "renv.lock")
                transitive, edges = expand_transitive(
                    dependencies, graph, "r", "renv.lock",
                    warnings, diagnostics,
                )
                dependencies.extend(transitive)
                if graph:
                    transitive_resolved = True
                    coverage = "partial" if unmatched else "full"
            else:
                transitive_unavailable(
                    "r", "renv.lock", warnings, diagnostics,
                    "no renv.lock found to expand the dependency graph",
                )

        name = fields.get("Package") or project_dir.name
        return ParseResult(
            ecosystem="r",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["DESCRIPTION"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=transitive_resolved,
            transitive_edges=edges,
        )


def _parse_renv_lock(path, warnings):
    data = read_json(path)
    packages = data.get("Packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        if data is not None:
            warnings.append("renv.lock: no 'Packages' section found")
        return {}
    resolved = {}
    for name, info in packages.items():
        if isinstance(info, dict) and isinstance(info.get("Version"), str):
            resolved.setdefault(name, set()).add(info["Version"])
    return resolved


def _parse_renv_lock_graph(path):
    """Return the resolved package graph of a renv.lock."""
    data = read_json(path)
    packages = data.get("Packages")
    graph = {}
    if not isinstance(packages, dict):
        return graph
    hard_fields = ("Imports", "Depends", "LinkingTo")
    for name, info in packages.items():
        if not isinstance(info, dict):
            continue
        version = info.get("Version")
        if not isinstance(version, str):
            continue
        requires = []
        for field in hard_fields:
            entries = info.get(field)
            if isinstance(entries, list):
                for child in entries:
                    if isinstance(child, str) and child.lower() != "r":
                        requires.append((child, None))
        graph[name] = [{
            "name": name,
            "version": version,
            "requires": list(dict.fromkeys(requires)),
        }]
    return graph
