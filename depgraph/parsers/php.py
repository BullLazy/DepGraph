"""PHP parser: composer.json and composer.lock."""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import ManifestError
from ..models import DEV, PLATFORM, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import (
    apply_resolved_versions,
    expand_transitive,
    read_json,
    split_url_specifier,
    transitive_unavailable,
)

_REGISTRY = ("php", "hhvm", "composer", "composer-plugin-api",
             "composer-runtime-api", "composer-plugin-api")


def _is_platform(name):
    lowered = name.lower()
    # Composer platform packages (php, php-64bit, ext-*, lib-*, composer-*)
    # never contain a vendor slash; a slashed name is always a real package
    # (e.g. "php-http/discovery"), which must not be misclassified.
    if "/" in lowered:
        return False
    return (
        lowered in _REGISTRY
        or lowered.startswith("ext-")
        or lowered.startswith("lib-")
        or lowered.startswith("composer-")
        or lowered.startswith("php-")
    )


class PhpParser(Parser):
    ecosystem = "php"
    display_name = "PHP"
    manifest_filenames = ("composer.json",)
    lockfile_filenames = ("composer.lock",)
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = True
    limitations = (
        "composer.lock resolves transitive package edges when --transitive is "
        "used; without it only declared dependencies are graphed.",
        "Composer platform requirements (php, ext-*, lib-*) are marked with "
        "the platform category and never expanded into package-to-package "
        "edges.",
        "aliases, repositories and scripts are not evaluated.",
    )

    def detect(self, project_dir):
        return (project_dir / "composer.json").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        manifest = project_dir / "composer.json"
        data = read_json(manifest)
        if not isinstance(data, dict):
            raise ManifestError("composer.json is not a JSON object")

        warnings = []
        diagnostics = []
        dependencies = []

        sections = []
        if options.includes(RUNTIME):
            sections.append(("require", RUNTIME))
        if options.includes(DEV):
            sections.append(("require-dev", DEV))

        for field, category in sections:
            entries = data.get(field) or {}
            if not isinstance(entries, dict):
                warnings.append(f"composer.json: {field} must be an object")
                continue
            for name, spec in entries.items():
                effective = category
                if _is_platform(name):
                    effective = PLATFORM
                if not options.includes(effective):
                    continue
                specifier, url = split_url_specifier(spec)
                dependencies.append(Dependency(
                    ecosystem="php",
                    name=name,
                    normalized_name=normalize_simple(name),
                    category=effective,
                    specifier=specifier,
                    url=url,
                    source="composer.json",
                    provenance="composer",
                ))

        lockfiles = []
        coverage = "declared_only"
        edges = []
        transitive_resolved = False
        have_lock = (project_dir / "composer.lock").is_file()
        unmatched = 0
        if (options.use_lockfile or options.resolve_transitive) and have_lock:
            resolved = _parse_composer_lock(
                project_dir / "composer.lock", warnings, diagnostics
            )
            unmatched = apply_resolved_versions(
                dependencies, resolved, "composer.lock", warnings,
                diagnostics, "php",
            )
            lockfiles.append("composer.lock")
            coverage = "partial" if resolved else "declared_only"
            if options.resolve_transitive:
                graph = _parse_composer_lock_graph(project_dir / "composer.lock")
                transitive, edges = expand_transitive(
                    dependencies, graph, "php", "composer.lock",
                    warnings, diagnostics,
                )
                dependencies.extend(transitive)
                if graph:
                    transitive_resolved = True
                    coverage = "partial" if unmatched else "full"
        elif options.resolve_transitive:
            transitive_unavailable(
                "php", "composer.lock", warnings, diagnostics,
                "no composer.lock found to expand the dependency graph",
            )

        name = data.get("name") or project_dir.name
        return ParseResult(
            ecosystem="php",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["composer.json"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=transitive_resolved,
            transitive_edges=edges,
        )


def _parse_composer_lock_graph(path):
    """Return the resolved package graph of a composer.lock."""
    data = read_json(path)
    graph = {}
    for field in ("packages", "packages-dev"):
        entries = data.get(field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            version = entry.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                continue
            requires = []
            reqs = entry.get("require")
            if isinstance(reqs, dict):
                for child in reqs:
                    if not _is_platform(child):
                        requires.append((child, None))
            graph.setdefault(name, []).append({
                "name": name,
                "version": version,
                "requires": requires,
            })
    return graph


def _parse_composer_lock(path, warnings, diagnostics):
    data = read_json(path)
    resolved = {}
    for field in ("packages", "packages-dev"):
        entries = data.get(field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            version = entry.get("version")
            if isinstance(name, str) and isinstance(version, str):
                resolved.setdefault(name, set()).add(version)
    return resolved
