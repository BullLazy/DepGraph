"""JavaScript / TypeScript parser (npm ecosystem)."""

from __future__ import annotations

import re
from pathlib import Path

from ..diagnostics import ManifestError
from ..models import DEV, OPTIONAL, PEER, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import (
    apply_resolved_versions,
    expand_transitive,
    read_json,
    read_text,
    split_url_specifier,
    transitive_unavailable,
)

CATEGORY_FIELDS = (
    ("dependencies", RUNTIME),
    ("devDependencies", DEV),
    ("optionalDependencies", OPTIONAL),
    ("peerDependencies", PEER),
)

_YARN_VERSION_RE = re.compile(r'version\s+"?([^"\s]+)"?')
_YARN_HEADER_RE = re.compile(r'^"?([^"\s].*?)"?:\s*$')


def _parse_package_lock(path, warnings):
    """Return {name: [versions]} from an npm lockfile (v1/v2/v3)."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise ManifestError(f"{path.name} is not a JSON object")

    version = data.get("lockfileVersion", 1)
    resolved = {}

    if version in (2, 3):
        packages = data.get("packages")
        if not isinstance(packages, dict):
            warnings.append(
                f"{path.name}: lockfileVersion {version} has no 'packages' map"
            )
            return resolved
        for key, info in packages.items():
            if not key or not isinstance(info, dict):
                continue
            name = key.split("node_modules/")[-1]
            ver = info.get("version")
            if isinstance(ver, str):
                resolved.setdefault(name, set()).add(ver)
    elif version == 1:
        stack = [data.get("dependencies", {})]
        while stack:
            current = stack.pop()
            if not isinstance(current, dict):
                continue
            for name, info in current.items():
                if not isinstance(info, dict):
                    continue
                ver = info.get("version")
                if isinstance(ver, str):
                    resolved.setdefault(name, set()).add(ver)
                nested = info.get("dependencies")
                if isinstance(nested, dict):
                    stack.append(nested)
    else:
        warnings.append(
            f"{path.name}: unsupported lockfileVersion {version!r}; "
            "resolved versions were not read"
        )
    return resolved


def _parse_yarn_lock(path, warnings):
    text = read_text(path)
    if "__metadata:" in text:
        warnings.append(
            f"{path.name}: Yarn Berry lockfiles are not supported; "
            "resolved versions were not read"
        )
        return {}
    resolved = {}
    current = []
    header_re = re.compile(r"^(.+):\s*$")
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        if not raw[0].isspace() and raw.rstrip().endswith(":"):
            spec = raw.rstrip()[:-1]
            current = [part.strip().strip('"') for part in spec.split(",")]
        elif raw.strip().startswith("version"):
            match = _YARN_VERSION_RE.search(raw)
            if match:
                for entry in current:
                    name = entry.rsplit("@", 1)[0]
                    if name:
                        resolved.setdefault(name, set()).add(match.group(1))
    return resolved


def _merge_node(graph, name, version, requires):
    """Insert a package node into a graph keyed by name."""
    entries = graph.setdefault(name, [])
    for node in entries:
        if node["version"] == version:
            for req in requires:
                if req not in node["requires"]:
                    node["requires"].append(req)
            return
    entries.append({
        "name": name,
        "version": version,
        "requires": list(requires),
    })


def _parse_package_lock_graph(path, warnings):
    """Return the full resolved package graph of an npm lockfile."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise ManifestError(f"{path.name} is not a JSON object")
    version = data.get("lockfileVersion", 1)
    graph = {}

    if version in (2, 3):
        packages = data.get("packages")
        if not isinstance(packages, dict):
            return graph
        nodes = {}
        for key, info in packages.items():
            if not isinstance(info, dict) or info.get("link") is True:
                continue
            name = key.split("node_modules/")[-1]
            ver = info.get("version")
            if not isinstance(ver, str):
                continue
            req_names = []
            for field in ("dependencies", "optionalDependencies"):
                reqs = info.get(field)
                if isinstance(reqs, dict):
                    req_names.extend(reqs)
            nodes[key] = (name, ver, list(dict.fromkeys(req_names)))
        # Path-based ownership: the package at "P/node_modules/N" belongs to
        # the package located at P, mirroring npm's nearest-install layout.
        for key, (name, ver, req_names) in nodes.items():
            requires = []
            owner = key.rsplit("/node_modules/", 1)[0] if key else ""
            for req in req_names:
                child = None
                nested = f"{owner}/node_modules/{req}" if owner else f"node_modules/{req}"
                if nested in nodes:
                    child = nodes[nested]
                elif f"node_modules/{req}" in nodes:
                    child = nodes[f"node_modules/{req}"]
                if child is not None:
                    requires.append((child[0], child[1]))
            _merge_node(graph, name, ver, requires)
    elif version == 1:
        def walk(entries):
            if not isinstance(entries, dict):
                return
            for name, info in entries.items():
                if not isinstance(info, dict):
                    continue
                ver = info.get("version")
                children = info.get("dependencies") or {}
                requires = []
                for req, child in children.items():
                    if isinstance(child, dict):
                        requires.append((req, child.get("version")))
                if isinstance(ver, str):
                    _merge_node(graph, name, ver, requires)
                walk(children)

        walk(data.get("dependencies") or {})
    else:
        warnings.append(
            f"{path.name}: unsupported lockfileVersion {version!r}; "
            "resolved versions were not read"
        )
    return graph


def _parse_yarn_lock_graph(path, warnings):
    """Return the resolved package graph of a Yarn v1 lockfile."""
    text = read_text(path)
    if "__metadata:" in text:  # Yarn Berry
        return {}
    lines = text.splitlines()
    keys = []
    rows = []
    for index, raw in enumerate(lines):
        if not raw or raw.startswith("#"):
            continue
        if not raw[0].isspace() and raw.rstrip().endswith(":"):
            keys.append(raw.rstrip()[:-1].strip().strip('"'))
            rows.append(index)

    def name_of(key):
        return key.rsplit("@", 1)[0]

    blocks = {}
    for position, key in enumerate(keys):
        start = rows[position]
        end = rows[position + 1] if position + 1 < len(rows) else len(lines)
        version = None
        requires = []
        in_deps = False
        for raw in lines[start + 1:end]:
            if not raw or raw.startswith("#"):
                continue
            if not raw[0].isspace():  # top-level line: block ended
                break
            stripped = raw.strip()
            delta = len(raw) - len(raw.lstrip())
            if delta == 2:
                if stripped.startswith("version"):
                    match = _YARN_VERSION_RE.search(raw)
                    if match:
                        version = match.group(1)
                else:
                    in_deps = stripped in ("dependencies:", "optionalDependencies:")
            elif delta >= 4 and in_deps:
                parts = stripped.split(None, 1)
                if len(parts) == 1:
                    continue
                child_range = parts[1].strip('"')
                requires.append((parts[0], child_range))
        blocks[key] = {"name": name_of(key), "version": version,
                       "requires": requires}

    graph = {}
    for key, block in blocks.items():
        if block["version"] is None:
            continue
        requires = []
        for child_name, child_range in block["requires"]:
            target = None
            exact = f"{child_name}@{child_range}"
            if exact in blocks and blocks[exact]["version"] is not None:
                target = blocks[exact]
            if target is None:
                matches = [
                    other for other_key, other in blocks.items()
                    if other["name"] == child_name
                    and other["version"] is not None
                ]
                if len(matches) == 1:
                    target = matches[0]
            requires.append((child_name, target["version"] if target else None))
        _merge_node(graph, block["name"], block["version"], requires)
    return graph


class JavaScriptParser(Parser):
    ecosystem = "javascript"
    display_name = "JavaScript/TypeScript"
    manifest_filenames = ("package.json",)
    lockfile_filenames = (
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
    )
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = True
    limitations = (
        "package-lock.json/npm-shrinkwrap.json (v1/v2/v3) and yarn.lock (v1) "
        "resolve transitive package edges when --transitive is used; without "
        "it only declared dependencies are graphed.",
        "Yarn Berry lockfiles and pnpm-lock.yaml are recognised but not parsed.",
        "Workspace members are reported but not expanded automatically.",
    )

    def detect(self, project_dir):
        return (project_dir / "package.json").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        manifest = project_dir / "package.json"
        data = read_json(manifest)
        if not isinstance(data, dict):
            raise ManifestError("package.json is not a JSON object")

        warnings = []
        diagnostics = []
        dependencies = []

        for field, category in CATEGORY_FIELDS:
            if not options.includes(category):
                continue
            entries = data.get(field) or {}
            if not isinstance(entries, dict):
                warnings.append(
                    f"package.json: {field} must be an object"
                )
                continue
            for name, spec in entries.items():
                specifier, url = split_url_specifier(spec)
                dependencies.append(Dependency(
                    ecosystem="javascript",
                    name=name,
                    normalized_name=normalize_simple(name),
                    category=category,
                    specifier=specifier,
                    url=url,
                    source="package.json",
                    provenance="javascript",
                ))

        workspaces = data.get("workspaces")
        if workspaces:
            warnings.append(
                "package.json declares workspaces; member packages are not "
                "expanded (run with --recursive to scan them)."
            )

        lockfiles = []
        coverage = "declared_only"
        edges = []
        transitive_resolved = False
        use_locks = options.use_lockfile or options.resolve_transitive
        if use_locks:
            lock_path = None
            for candidate in ("package-lock.json", "npm-shrinkwrap.json"):
                if (project_dir / candidate).is_file():
                    lock_path = project_dir / candidate
                    break
            if lock_path is not None:
                resolved = _parse_package_lock(lock_path, warnings)
                unmatched = apply_resolved_versions(
                    dependencies, resolved, lock_path.name, warnings,
                    diagnostics, "javascript",
                )
                lockfiles.append(str(lock_path.name))
                coverage = "partial" if resolved else "declared_only"
                if options.resolve_transitive:
                    graph = _parse_package_lock_graph(lock_path, warnings)
                    transitive, edges = expand_transitive(
                        dependencies, graph, "javascript", lock_path.name,
                        warnings, diagnostics,
                    )
                    dependencies.extend(transitive)
                    if graph:
                        transitive_resolved = True
                        coverage = "partial" if unmatched else "full"
            elif (project_dir / "yarn.lock").is_file():
                resolved = _parse_yarn_lock(project_dir / "yarn.lock", warnings)
                unmatched = apply_resolved_versions(
                    dependencies, resolved, "yarn.lock", warnings,
                    diagnostics, "javascript",
                )
                lockfiles.append("yarn.lock")
                coverage = "partial" if resolved else "declared_only"
                if options.resolve_transitive:
                    graph = _parse_yarn_lock_graph(project_dir / "yarn.lock",
                                                   warnings)
                    transitive, edges = expand_transitive(
                        dependencies, graph, "javascript", "yarn.lock",
                        warnings, diagnostics,
                    )
                    dependencies.extend(transitive)
                    if graph:
                        transitive_resolved = True
                        coverage = "partial" if unmatched else "full"
            elif (project_dir / "pnpm-lock.yaml").is_file():
                warnings.append(
                    "pnpm-lock.yaml was found but its format is not parsed; "
                    "resolved versions were not read"
                )
                lockfiles.append("pnpm-lock.yaml")
                if options.resolve_transitive:
                    transitive_unavailable(
                        "javascript", "pnpm-lock.yaml", warnings, diagnostics,
                        "pnpm-lock.yaml provides no parseable dependency graph",
                    )
            elif options.resolve_transitive:
                transitive_unavailable(
                    "javascript", "package-lock.json", warnings, diagnostics,
                    "no supported lockfile found to expand the dependency graph",
                )

        name = data.get("name") or project_dir.name
        return ParseResult(
            ecosystem="javascript",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["package.json"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=transitive_resolved,
            transitive_edges=edges,
        )
