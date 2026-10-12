"""Go parser: go.mod and go.sum (checksums only)."""

from __future__ import annotations

import re
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError
from ..models import RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import read_text, transitive_unavailable

_DIRECTIVE_RE = re.compile(r"^\s*(require|replace|exclude)\s+(.+?)\s*$")
_MODULE_RE = re.compile(r"^\s*module\s+(\S+)\s*$")
_VERSION_RE = re.compile(r"^(\S+)\s+(v\S+)(?:\s*//.*)?$")


class GoParser(Parser):
    ecosystem = "go"
    display_name = "Go"
    manifest_filenames = ("go.mod",)
    lockfile_filenames = ("go.sum",)
    can_parse_direct = True
    can_parse_resolved = True
    can_resolve_transitive = False
    limitations = (
        "go.sum contains checksums only; it is not treated as a dependency "
        "graph and never creates dependencies.",
        "Indirect requirements are marked as transitive but no parent edges "
        "are invented.",
        "replace and exclude directives are reported, not applied.",
    )

    def detect(self, project_dir):
        return (project_dir / "go.mod").is_file(), []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        manifest = project_dir / "go.mod"
        text = read_text(manifest)

        warnings = []
        diagnostics = []
        dependencies = []
        module_path = None
        replaces = []

        lines = text.splitlines()
        index = 0
        while index < len(lines):
            raw = lines[index]
            stripped = raw.strip()
            index += 1
            if not stripped or stripped.startswith("//"):
                continue
            match = _MODULE_RE.match(raw)
            if match:
                module_path = match.group(1)
                continue
            if stripped.startswith(("go ", "toolchain ")):
                continue

            # Block form: require ( ... ), replace ( ... ), exclude ( ... )
            block = re.match(r"^(require|replace|exclude)\s*\(\s*$", stripped)
            if block:
                directive = block.group(1)
                while index < len(lines):
                    inner = lines[index].strip()
                    index += 1
                    if inner.startswith(")"):
                        break
                    if not inner or inner.startswith("//"):
                        continue
                    if directive == "require":
                        _handle_require(
                            inner, dependencies, warnings, diagnostics
                        )
                    elif directive == "replace":
                        replaces.append(inner)
                continue

            match = _DIRECTIVE_RE.match(raw)
            if match:
                directive, rest = match.groups()
                if directive == "require":
                    _handle_require(rest, dependencies, warnings, diagnostics)
                elif directive == "replace":
                    replaces.append(rest)

        if replaces:
            warnings.append(
                f"go.mod declares {len(replaces)} replace directive(s); "
                "they are reported but not applied."
            )

        lockfiles = []
        coverage = "declared_only"
        if options.use_lockfile and (project_dir / "go.sum").is_file():
            lockfiles.append("go.sum")
            checksums = _count_go_sum(project_dir / "go.sum")
            if checksums:
                warnings.append(
                    "go.sum provides module checksums only; resolved versions "
                    "were not derived from it."
                )
                diagnostics.append(Diagnostic(
                    "info", "go.checksums_only",
                    f"go.sum contains {checksums} checksum entries",
                    source="go.sum",
                ))

        if options.resolve_transitive:
            transitive_unavailable(
                "go", "go.sum", warnings, diagnostics,
                "go.sum records checksums, not the module dependency graph",
            )

        name = module_path or project_dir.name
        return ParseResult(
            ecosystem="go",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=["go.mod"],
            lockfiles=lockfiles,
            lock_coverage=coverage,
            transitive=False,
        )


def _handle_require(text, dependencies, warnings, diagnostics):
    indirect = "// indirect" in text
    cleaned = text.split("//", 1)[0].strip()
    parts = cleaned.split()
    if len(parts) < 2:
        warnings.append(f"go.mod: cannot parse require entry {text!r}")
        diagnostics.append(Diagnostic(
            "warning", "go.unparsed_require",
            f"cannot parse require entry: {text}", source="go.mod",
        ))
        return
    path, version = parts[0], parts[1]
    dependencies.append(Dependency(
        ecosystem="go",
        name=path,
        normalized_name=normalize_simple(path),
        category=RUNTIME,
        specifier=version,
        resolved_version=version if not version.startswith("v0.0.0-") else None,
        direct=not indirect,
        source="go.mod",
        provenance="go",
    ))


def _count_go_sum(path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())
