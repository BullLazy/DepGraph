"""C# / .NET parser: SDK-style projects, packages.config, central versions."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError
from ..models import DEV, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import read_text, transitive_unavailable


def _local(tag):
    return tag.split("}", 1)[-1]


def _child_text(element, local_name):
    if element is None:
        return None
    for child in element:
        if _local(child.tag) == local_name and child.text:
            return child.text.strip()
    return None


def _parse_xml(path):
    try:
        return ET.fromstring(read_text(path))
    except ET.ParseError as exc:
        raise ManifestError(f"Cannot parse {path.name}: {exc}") from exc


def _load_central_versions(project_dir, warnings):
    versions = {}
    path = project_dir / "Directory.Packages.props"
    if not path.is_file():
        return versions
    root = _parse_xml(path)
    for element in root.iter():
        if _local(element.tag) == "PackageVersion":
            name = element.get("Include") or element.get("Update")
            version = element.get("Version")
            if version is None:
                version = _child_text(element, "Version")
            if name and version:
                versions[name] = version
    if versions:
        warnings.append(
            f"Directory.Packages.props provides central versions for "
            f"{len(versions)} package(s)"
        )
    return versions


def _parse_project(path, central, warnings, diagnostics):
    root = _parse_xml(path)
    dependencies = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "PackageReference":
            name = element.get("Include") or element.get("Update")
            if not name:
                continue
            version = element.get("Version")
            if version is None:
                version = _child_text(element, "Version")
            if version is None:
                version = central.get(name)
            if version is None:
                warnings.append(
                    f"{path.name}: no version for PackageReference {name!r}"
                )
                diagnostics.append(Diagnostic(
                    "warning", "dotnet.unresolved_version",
                    f"no version for PackageReference {name}", source=path.name,
                ))
            category = RUNTIME
            private_assets = _child_text(element, "PrivateAssets")
            if private_assets and "All" in private_assets:
                category = DEV
            dependencies.append(Dependency(
                ecosystem="dotnet",
                name=name,
                normalized_name=normalize_simple(name),
                category=category,
                specifier=version,
                source=path.name,
                provenance="dotnet",
            ))
    return dependencies


def _parse_packages_config(path, warnings):
    root = _parse_xml(path)
    dependencies = []
    for element in root.iter():
        if _local(element.tag) != "package":
            continue
        name = element.get("id")
        version = element.get("version")
        if not name:
            continue
        dependencies.append(Dependency(
            ecosystem="dotnet",
            name=name,
            normalized_name=normalize_simple(name),
            category=RUNTIME,
            specifier=version,
            source=path.name,
            provenance="packages.config",
        ))
    return dependencies


class DotNetParser(Parser):
    ecosystem = "dotnet"
    display_name = "C#/.NET"
    manifest_filenames = ("packages.config",)
    manifest_globs = ("*.csproj", "*.vbproj", "*.fsproj")
    lockfile_filenames = ()
    can_parse_direct = True
    can_parse_resolved = False
    can_resolve_transitive = False
    limitations = (
        "Only PackageReference and PackageVersion elements are parsed; "
        "arbitrary MSBuild evaluation is not supported.",
        "ProjectReference entries are not treated as package dependencies.",
    )

    def detect(self, project_dir):
        project_dir = Path(project_dir)
        if (project_dir / "packages.config").is_file():
            return True, []
        for pattern in self.manifest_globs:
            if any(p.is_file() for p in project_dir.glob(pattern)):
                return True, []
        return False, []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        warnings = []
        diagnostics = []
        dependencies = []
        manifests = []

        central = _load_central_versions(project_dir, warnings)

        for pattern in self.manifest_globs:
            for path in sorted(project_dir.glob(pattern)):
                dependencies.extend(
                    _parse_project(path, central, warnings, diagnostics)
                )
                manifests.append(path.name)

        if (project_dir / "packages.config").is_file():
            dependencies.extend(
                _parse_packages_config(project_dir / "packages.config",
                                       warnings)
            )
            manifests.append("packages.config")

        if not manifests:
            raise ManifestError("No .NET project file found")

        if options.resolve_transitive:
            transitive_unavailable(
                "dotnet", "packages.lock.json", warnings, diagnostics,
                "no supported .NET lockfile with dependency relationships",
            )

        return ParseResult(
            ecosystem="dotnet",
            project_name=project_dir.name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=manifests,
            lock_coverage="declared_only",
            transitive=False,
        )
