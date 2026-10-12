"""Common graph construction and report assembly."""

from __future__ import annotations

from .naming import normalize_name
from .version import SCHEMA_VERSION, __version__

_COVERAGE_ORDER = ["none", "declared_only", "partial", "full"]


def _combine_coverage(results):
    coverage = "none"
    for result in results:
        value = result.lock_coverage or "none"
        if _COVERAGE_ORDER.index(value) > _COVERAGE_ORDER.index(coverage):
            coverage = value
    return coverage


def build_report(root, results, inventory, options):
    dependencies = []
    warnings = []
    diagnostics = []
    manifests = []
    lockfiles = []
    transitive = False
    ecosystems = []

    for result in results:
        dependencies.extend(result.dependencies)
        warnings.extend(result.warnings)
        diagnostics.extend(result.diagnostics)
        manifests.extend(result.manifests)
        lockfiles.extend(result.lockfiles)
        transitive = transitive or result.transitive
        if result.ecosystem not in ecosystems:
            ecosystems.append(result.ecosystem)

    # Ecosystem-aware de-duplication preserving meaningful distinctions.
    seen = set()
    unique = []
    for dep in dependencies:
        key = dep.identity()
        if key in seen:
            continue
        seen.add(key)
        unique.append(dep)
    unique.sort(key=lambda dep: dep.sort_key())

    if len(results) == 1:
        project_name = results[0].project_name
    else:
        project_name = root.name

    root_id = "project:" + normalize_name(project_name)

    # Version-aware node identity: when a package resolves to several locked
    # versions, each version keeps its own node instead of being merged by
    # display name.
    version_counts = {}
    for dep in unique:
        counts = version_counts.setdefault(dep.ecosystem, {})
        version_set = counts.setdefault(dep.normalized_name, set())
        version_set.add(dep.resolved_version)

    catalog = {}
    for dep in unique:
        suffix = ""
        if len(version_counts[dep.ecosystem][dep.normalized_name]) > 1:
            suffix = "@" + (dep.resolved_version or "<unresolved>")
        node_id = f"package:{dep.ecosystem}:{dep.normalized_name}{suffix}"
        catalog[(dep.ecosystem, dep.normalized_name,
                 dep.resolved_version)] = node_id

    nodes = [{
        "id": root_id,
        "name": project_name,
        "type": "project",
        "ecosystem": ecosystems[0] if len(ecosystems) == 1 else "multi",
    }]
    node_ids = {root_id}
    edges = []

    for dep in unique:
        node_id = catalog[
            (dep.ecosystem, dep.normalized_name, dep.resolved_version)
        ]
        if node_id not in node_ids:
            node_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "name": dep.name,
                "normalized_name": dep.normalized_name,
                "type": "package",
                "ecosystem": dep.ecosystem,
                "resolved_version": dep.resolved_version,
            })
        if dep.direct:
            edges.append({
                "from": root_id,
                "to": node_id,
                "ecosystem": dep.ecosystem,
                "specifier": dep.specifier,
                "marker": dep.marker,
                "extras": list(dep.extras),
                "url": dep.url,
                "category": dep.category,
                "direct": dep.direct,
                "resolved_version": dep.resolved_version,
            })

    # Package-to-package edges produced by lockfile-backed transitive
    # resolution. Endpoints are pinned to exact locked versions, so multiple
    # versions of the same package never blur together.
    for result in results:
        for item in result.transitive_edges:
            frm = catalog.get(
                (result.ecosystem, item["from"], item["from_version"])
            )
            to = catalog.get(
                (result.ecosystem, item["to"], item["to_version"])
            )
            if frm is None or to is None:
                continue
            edges.append({
                "from": frm,
                "to": to,
                "ecosystem": result.ecosystem,
                "specifier": None,
                "marker": None,
                "extras": [],
                "url": None,
                "category": item["category"],
                "direct": False,
                "resolved_version": item["to_version"],
            })

    nodes = [nodes[0]] + sorted(
        nodes[1:],
        key=lambda node: (node["ecosystem"], node["normalized_name"]),
    )
    edges.sort(key=lambda edge: (
        edge["ecosystem"], edge["to"], edge["specifier"] or "",
        edge["marker"] or "", edge["category"],
    ))

    warnings = list(dict.fromkeys(warnings))
    ecosystems = sorted(ecosystems)

    return {
        "tool": "DepGraph",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "project": {
            "name": project_name,
            "path": str(root),
            "manifest": manifests[0] if manifests else None,
            "ecosystems": ecosystems,
        },
        "analysis": {
            "kind": (
                "declared_and_resolved_dependencies"
                if transitive else "declared_dependencies"
            ),
            "transitive_dependencies_resolved": transitive,
            "transitive_dependency_count": sum(
                1 for dep in unique if not dep.direct
            ),
            "direct_dependency_count": sum(
                1 for dep in unique if dep.direct
            ),
            "dependency_count": len(unique),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "ecosystems": ecosystems,
            "lockfile_coverage": _combine_coverage(results),
        },
        "dependencies": [dep.to_dict() for dep in unique],
        "graph": {"nodes": nodes, "edges": edges},
        "warnings": warnings,
        "manifests": [item.to_dict() for item in inventory],
        "lockfiles": lockfiles,
        "diagnostics": [diag.to_dict() for diag in diagnostics],
    }
