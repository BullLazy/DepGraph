"""Julia parser: Project.toml and Manifest.toml."""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import ManifestError
from ..models import OPTIONAL, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import apply_resolved_versions, expand_transitive, read_toml, transitive_unavailable


class JuliaParser(Parser):
    ecosystem = "julia"
    display_name = "Julia"
    manifest_filenames = ("Project.toml",)
    lockfile_filenames = ("Manifest.toml",)
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = True
    limitations = (
        "Packages are identified by name; UUIDs are reported where available "
        "but not used as the graph identity.",
        "Manifest.toml resolves transitive package edges when --transitive is "
        "used; without it only declared dependencies are graphed.",
        "[extras] and [targets] (test/docs) are marked optional.",
    )

    def detect(self, project_dir):
        # Project.toml is also used by Rust-like tooling; require Julia shape.
        path = project_dir / "Project.toml"
        if not path.is_file():
            return False, []
        try:
            data = read_toml(path)
        except ManifestError:
            return True, []
        if isinstance(data, dict) and "deps" in data:
            return True, []
        return False, []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        data = read_toml(project_dir / "Project.toml")
        if not isinstance(data, dict):
            raise ManifestError("Project.toml is not a TOML table")

        warnings = []
        diagnostics = []
        dependencies = []

        resolved = {}
        lockfiles = []
        coverage = "declared_only"
        edges = []
        have_manifest = (project_dir / "Manifest.toml").is_file()
        if (options.use_lockfile or options.resolve_transitive) and have_manifest:
            resolved = _parse_manifest(project_dir / "Manifest.toml", warnings)
            lockfiles.append("Manifest.toml")
            coverage = "partial" if resolved else "declared_only"

        compat = data.get("compat") if isinstance(data.get("compat"), dict) else {}
        deps = data.get("deps") if isinstance(data.get("deps"), dict) else {}
        extras = data.get("extras") if isinstance(data.get("extras"), dict) else {}

        def add(name, category):
            if not options.includes(category):
                return
            specifier = compat.get(name) if isinstance(compat.get(name), str) else None
            if isinstance(specifier, str):
                specifier = specifier.strip() or None
            dependencies.append(Dependency(
                ecosystem="julia",
                name=name,
                normalized_name=normalize_simple(name),
                category=category,
                specifier=specifier,
                source="Project.toml",
                provenance="julia",
            ))

        for name in deps:
            add(name, RUNTIME)

        for name in extras:
            if name not in deps:
                add(name, OPTIONAL)

        unmatched = 0
        if resolved:
            unmatched = apply_resolved_versions(
                dependencies, resolved, "Manifest.toml", warnings,
                diagnostics, "julia",
            )

        transitive_resolved = False
        if options.resolve_transitive:
            if have_manifest:
                graph = _parse_manifest_graph(project_dir / "Manifest.toml")
                transitive, edges = expand_transitive(
                    dependencies, graph, "julia", "Manifest.toml",
                    warnings, diagnostics,
                )
                dependencies.extend(transitive)
                if graph:
                    transitive_resolved = True
                    coverage = "partial" if unmatched else "full"
            else:
                transitive_unavailable(
                    "julia", "Manifest.toml", warnings, diagnostics,
                    "no Manifest.toml found to expand the dependency graph",
                )

        name = data.get("name") or project_dir.name
        return ParseResult(
            ecosystem="julia",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["Project.toml"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=transitive_resolved,
            transitive_edges=edges,
        )


def _manifest_packages(path):
    data = read_toml(path)
    packages = {}
    if not isinstance(data, dict):
        return packages
    deps = data.get("deps")
    entries = {}
    if isinstance(deps, dict):
        entries = deps
    elif isinstance(deps, list):
        for entry in deps:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                entries[entry["name"]] = entry
    for name, info in entries.items():
        if isinstance(info, list):
            info = info[0] if info else None
        if isinstance(info, dict):
            packages[name] = info
    return packages


def _parse_manifest_graph(path):
    """Return the resolved package graph of a Manifest.toml.

    Julia resolves dependency references by UUID, so the graph maps each
    package UUID to its name before rewriting the ``deps`` UUID list.
    """
    graph = {}
    packages = _manifest_packages(path)
    if not packages:
        return graph
    names_by_uuid = {}
    for name, info in packages.items():
        uuid = info.get("uuid")
        if isinstance(uuid, str):
            names_by_uuid[uuid] = name
    for name, info in packages.items():
        if not isinstance(info.get("version"), str):
            continue
        requires = []
        deps = info.get("deps")
        if isinstance(deps, list):
            for reference in deps:
                if not isinstance(reference, str):
                    continue
                # Manifest v2 lists package names; v1 lists UUIDs.
                child = reference if reference in packages else names_by_uuid.get(reference)
                if child is not None and child != name:
                    requires.append((child, None))
        graph[name] = [{
            "name": name,
            "version": info["version"],
            "requires": list(dict.fromkeys(requires)),
        }]
    return graph


def _parse_manifest(path, warnings):
    data = read_toml(path)
    deps = data.get("deps") if isinstance(data, dict) else None
    resolved = {}
    if isinstance(deps, dict):
        for name, info in deps.items():
            resolved.update(_extract(name, info))
    elif isinstance(deps, list):
        for entry in deps:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                resolved.update(_extract(entry["name"], entry))
    else:
        warnings.append("Manifest.toml: no 'deps' section found")
    return resolved


def _extract(name, info):
    if isinstance(info, list):
        info = info[0] if info else None
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        return {name: {info["version"]}}
    return {}
