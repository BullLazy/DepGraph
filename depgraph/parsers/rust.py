"""Rust parser: Cargo.toml and Cargo.lock."""

from __future__ import annotations

import tomllib
from pathlib import Path

from ..diagnostics import ManifestError
from ..models import BUILD, DEV, OPTIONAL, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import (
    apply_resolved_versions,
    expand_transitive,
    read_toml,
    split_url_specifier,
    transitive_unavailable,
)

# Cargo dependency tables that are not package dependencies.
_NON_DEP_TABLES = {"features", "metadata", "package", "lib", "bin", "profile"}


def _dependency(name, value, category, source, warnings, optional_in_name=False):
    package_name = name
    specifier = None
    url = None
    optional = optional_in_name

    if isinstance(value, str):
        specifier, url = split_url_specifier(value)
    elif isinstance(value, dict):
        package_name = value.get("package", name) or name
        version = value.get("version")
        if isinstance(version, str):
            specifier, url = split_url_specifier(version)
        for key in ("git", "path", "registry"):
            if isinstance(value.get(key), str):
                url = value[key]
                break
        optional = optional or bool(value.get("optional"))
    else:
        warnings.append(f"Cargo.toml: ignoring unsupported dependency {name!r}")
        return None

    if optional and category == RUNTIME:
        category = OPTIONAL
    return Dependency(
        ecosystem="rust",
        name=package_name,
        normalized_name=normalize_simple(package_name),
        category=category,
        specifier=specifier,
        url=url,
        source=source,
        provenance="rust",
    )


def _parse_dep_table(table, category, source, warnings):
    deps = []
    if not isinstance(table, dict):
        return deps
    for name, value in table.items():
        dep = _dependency(name, value, category, source, warnings)
        if dep is not None:
            deps.append(dep)
    return deps


def _parse_cargo_lock(path, warnings):
    data = read_toml(path)
    packages = data.get("package", [])
    if not isinstance(packages, list):
        return {}
    resolved = {}
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if isinstance(name, str) and isinstance(version, str):
            resolved.setdefault(name, set()).add(version)
    return resolved


def _parse_cargo_lock_graph(path):
    """Return the resolved package graph of a Cargo.lock."""
    data = read_toml(path)
    graph = {}
    packages = data.get("package", [])
    if not isinstance(packages, list):
        return graph
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        requires = []
        for spec in entry.get("dependencies") or []:
            if not isinstance(spec, str):
                continue
            parts = spec.split()
            child_name = parts[0]
            child_version = parts[1] if len(parts) > 1 and "(" not in parts[1] else None
            requires.append((child_name, child_version))
        entries = graph.setdefault(name, [])
        entries.append({
            "name": name,
            "version": version,
            "requires": requires,
        })
    return graph


class RustParser(Parser):
    ecosystem = "rust"
    display_name = "Rust"
    manifest_filenames = ("Cargo.toml",)
    lockfile_filenames = ("Cargo.lock",)
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = True
    limitations = (
        "Cargo.lock resolves transitive package edges when --transitive is "
        "used; without it only declared dependencies are graphed.",
        "Feature and target-specific tables are parsed but features are not "
        "resolved.",
        "Workspace members are not expanded automatically.",
    )

    def detect(self, project_dir):
        return (project_dir / "Cargo.toml").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        manifest = project_dir / "Cargo.toml"
        data = read_toml(manifest)
        if not isinstance(data, dict):
            raise ManifestError("Cargo.toml is not a TOML table")

        warnings = []
        diagnostics = []
        dependencies = []

        resolved = {}
        lockfiles = []
        coverage = "declared_only"
        edges = []
        transitive_resolved = False
        have_lock = (project_dir / "Cargo.lock").is_file()
        if (options.use_lockfile or options.resolve_transitive) and have_lock:
            resolved = _parse_cargo_lock(project_dir / "Cargo.lock", warnings)
            lockfiles.append("Cargo.lock")
            coverage = "partial" if resolved else "declared_only"

        sections = []
        if options.includes(RUNTIME):
            sections.append(("dependencies", RUNTIME))
        if options.includes(DEV):
            sections.append(("dev-dependencies", DEV))
        if options.includes(BUILD):
            sections.append(("build-dependencies", BUILD))

        for key, category in sections:
            dependencies.extend(
                _parse_dep_table(data.get(key), category, "Cargo.toml",
                                 warnings)
            )

        # Target-specific dependency tables.
        target = data.get("target")
        if isinstance(target, dict):
            for _, cfg in target.items():
                if not isinstance(cfg, dict):
                    continue
                for key, category in sections:
                    dependencies.extend(
                        _parse_dep_table(cfg.get(key), category, "Cargo.toml",
                                         warnings)
                    )

        # Workspace dependencies.
        workspace = data.get("workspace")
        if isinstance(workspace, dict):
            if workspace.get("members"):
                warnings.append(
                    "Cargo workspace members are not expanded automatically."
                )
            if options.includes(RUNTIME):
                dependencies.extend(
                    _parse_dep_table(
                        workspace.get("dependencies"), RUNTIME, "Cargo.toml",
                        warnings,
                    )
                )

        unmatched = 0
        if resolved:
            unmatched = apply_resolved_versions(
                dependencies, resolved, "Cargo.lock", warnings, diagnostics,
                "rust",
            )

        if options.resolve_transitive:
            if have_lock:
                graph = _parse_cargo_lock_graph(project_dir / "Cargo.lock")
                transitive, edges = expand_transitive(
                    dependencies, graph, "rust", "Cargo.lock",
                    warnings, diagnostics,
                )
                dependencies.extend(transitive)
                if graph:
                    transitive_resolved = True
                    coverage = "partial" if unmatched else "full"
            else:
                transitive_unavailable(
                    "rust", "Cargo.lock", warnings, diagnostics,
                    "no Cargo.lock found to expand the dependency graph",
                )

        package = data.get("package")
        if isinstance(package, dict) and isinstance(package.get("name"), str):
            name = package["name"]
        else:
            name = project_dir.name

        return ParseResult(
            ecosystem="rust",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["Cargo.toml"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=transitive_resolved,
            transitive_edges=edges,
        )
