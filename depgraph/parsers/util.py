"""Shared parsing helpers for ecosystem parsers."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError
from ..models import Dependency
from ..naming import normalize_simple

# Guard against pathological input sizes (bytes).
MAX_FILE_SIZE = 20 * 1024 * 1024


def transitive_unavailable(ecosystem, lockfile, warnings, diagnostics, reason):
    """Report that transitive resolution is unavailable for this project."""
    warnings.append(
        f"{lockfile}: transitive resolution unavailable - {reason}; "
        "only declared dependencies were graphed."
    )
    diagnostics.append(Diagnostic(
        "warning", f"{ecosystem}.transitive.unavailable", reason,
        source=lockfile,
    ))


def expand_transitive(dependencies, graph, ecosystem, lockfile, warnings,
                      diagnostics):
    """Expand declared dependencies into a transitive graph from a lockfile.

    ``graph`` maps each package name to a list of concrete locked nodes:
    ``[{"version": "1.0.0", "requires": [("child", None)]}]`` where the second
    element of each requirement is the pinned child version if the lockfile
    states it, else ``None``.

    Resolution is driven entirely by the lockfile - the project code and the
    declared constraints never guess a version. A requirement is followed
    only when it can be pinned to exactly one locked node; ambiguous
    requirements are skipped instead of guessed, and cycles terminate.
    Returns ``(transitive_dependencies, edges)`` where each edge is
    ``{"from": <parent>, "from_version": ..., "to": ...,
    "to_version": ..., "category": ...}``.
    """
    if not graph:
        return [], []

    nodes = {}          # (normalized_name, version) -> node
    by_name = {}        # normalized_name -> [node, ...] in graph order
    for name, entries in graph.items():
        normalized = normalize_simple(name)
        for entry in entries:
            node = dict(entry)
            node.setdefault("name", name)
            nodes[(normalized, node["version"])] = node
            by_name.setdefault(normalized, []).append(node)

    def follow(child_name, child_version):
        """Return ``(normalized_name, node)`` a requirement resolves to."""
        normalized = normalize_simple(child_name)
        if (normalized, child_version) in nodes:
            return normalized, nodes[(normalized, child_version)]
        candidates = by_name.get(normalized)
        if candidates and len(candidates) == 1:
            return normalized, candidates[0]
        return None, None

    transitive = []
    edges = []
    seen = {}

    # Roots are the declared, already-resolved dependencies. Roots with no
    # unique locked version were reported by apply_resolved_versions and are
    # not expanded (their subgraph would be a guess).
    queue = []
    for dep in dependencies:
        if dep.resolved_version is None:
            continue
        root = (dep.normalized_name, dep.resolved_version)
        if root in nodes and root not in seen:
            seen[root] = dep.category
            queue.append((root, dep.category))

    while queue:
        parent_key, category = queue.pop(0)
        parent = nodes[parent_key]
        for child_name, child_version in parent.get("requires", ()):
            child_norm, child = follow(child_name, child_version)
            if child is None:
                continue
            child_key = (child_norm, child["version"])
            edges.append({
                "from": parent_key[0],
                "from_version": parent_key[1],
                "to": child_key[0],
                "to_version": child_key[1],
                "category": category,
            })
            if child_key in seen:
                continue
            seen[child_key] = category
            transitive.append(Dependency(
                ecosystem=ecosystem,
                name=child.get("name") or child_name,
                normalized_name=child_norm,
                category=category,
                resolved_version=child["version"],
                direct=False,
                source=lockfile,
                provenance=ecosystem,
            ))
            queue.append((child_key, category))

    return transitive, edges


def _check_size(path):
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ManifestError(f"Cannot stat {path.name}: {exc}") from exc
    if size > MAX_FILE_SIZE:
        raise ManifestError(
            f"{path.name} is too large to analyze ({size} bytes)"
        )


def read_text(path):
    path = Path(path)
    _check_size(path)
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"Cannot decode {path.name} as UTF-8: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read {path.name}: {exc}") from exc


def read_json(path):
    path = Path(path)
    text = read_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Cannot parse {path.name}: {exc}") from exc


def read_toml(path):
    path = Path(path)
    _check_size(path)
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"Cannot parse {path.name}: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read {path.name}: {exc}") from exc


def as_str(value):
    """Return a trimmed string or ``None``."""
    if isinstance(value, str):
        return value.strip() or None
    return None


def split_url_specifier(spec):
    """Split a version constraint from a direct URL/path reference."""
    if not isinstance(spec, str):
        return None, None
    spec = spec.strip()
    if not spec:
        return None, None
    lowered = spec.lower()
    urlish = (
        lowered.startswith(("http://", "https://", "git+", "git://", "ssh://",
                            "github:", "gitlab:", "bitbucket:", "file:",
                            "link:", "portal:", "./", "../", "/"))
        or lowered.startswith("workspace:")
    )
    if urlish:
        return None, spec
    if spec in ("*", "latest", "x", "X"):
        return None, None
    return spec, None


def apply_resolved_versions(dependencies, resolved, lockfile, warnings,
                            diagnostics, ecosystem):
    """Attach concrete versions from a lockfile to declared dependencies.

    Only declared dependencies with exactly one locked version are enriched.
    Dependencies with several locked versions, or none at all, never guess a
    version; they produce a warning instead. Platform dependencies (e.g. the
    ``php`` requirement) are skipped because lockfiles typically do not track
    them.

    If the lockfile yielded no packages at all (e.g. an unsupported format),
    nothing is reported here; the caller's own warning covers that case.

    Returns the number of declared (non-platform) dependencies that could not
    be pinned to exactly one locked version, so callers can avoid claiming
    "full" lockfile coverage when some declared dependency is unmatched.
    """
    if not resolved:
        return 0
    unmatched = 0
    for dep in dependencies:
        if dep.category == "platform":
            continue
        versions = resolved.get(dep.name)
        if not versions:
            unmatched += 1
            warnings.append(
                f"{lockfile}: declared dependency {dep.name!r} has no matching "
                "entry; resolved version is unavailable."
            )
            diagnostics.append(Diagnostic(
                "warning", f"{ecosystem}.lockfile.no_match",
                f"no lockfile entry for declared dependency {dep.name}",
                source=lockfile,
            ))
            continue
        if len(versions) == 1:
            dep.resolved_version = next(iter(versions))
        else:
            unmatched += 1
            warnings.append(
                f"{lockfile}: multiple locked versions for {dep.name!r}: "
                + ", ".join(sorted(versions))
            )
            diagnostics.append(Diagnostic(
                "warning", f"{ecosystem}.lockfile.multiple_versions",
                f"multiple locked versions for {dep.name}", source=lockfile,
            ))
    return unmatched
