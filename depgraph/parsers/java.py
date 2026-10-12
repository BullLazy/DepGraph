"""Java parser: Maven pom.xml and a bounded Gradle DSL subset."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError
from ..models import BUILD, OPTIONAL, PLATFORM, RUNTIME, TEST, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import read_text, split_url_specifier, transitive_unavailable

_SCOPE_CATEGORY = {
    "compile": RUNTIME,
    "runtime": RUNTIME,
    "test": TEST,
    "provided": PLATFORM,
    "system": PLATFORM,
    "import": PLATFORM,
}

_GRADLE_CONFIG_CATEGORY = {
    "implementation": RUNTIME,
    "api": RUNTIME,
    "compile": RUNTIME,
    "compileonly": BUILD,
    "compileonlyapi": BUILD,
    "annotationprocessor": BUILD,
    "kapt": BUILD,
    "runtimeonly": RUNTIME,
    "testimplementation": TEST,
    "testcompileonly": TEST,
    "testruntimeonly": TEST,
    "classpath": BUILD,
}


def _local(tag):
    return tag.split("}", 1)[-1]


def _child(element, local_name):
    if element is None:
        return None
    for child in element:
        if _local(child.tag) == local_name:
            return child
    return None


def _descendants(element, local_name):
    if element is None:
        return
    for child in element.iter():
        if _local(child.tag) == local_name:
            yield child


def _props(root):
    props = {}
    element = _child(root, "properties")
    if element is not None:
        for child in element:
            props[_local(child.tag)] = (child.text or "").strip()
    return props


def _resolve(version, props):
    if version is None:
        return None
    match = re.fullmatch(r"\$\{([^}]+)\}", version.strip())
    if match:
        return props.get(match.group(1))
    return version.strip() or None


def _parse_pom(path, options, warnings, diagnostics):
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ManifestError(f"Cannot parse pom.xml: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read pom.xml: {exc}") from exc

    root = tree.getroot()
    if _local(root.tag) != "project":
        raise ManifestError("pom.xml root element is not <project>")

    props = _props(root)
    dependencies = []

    managed = {}
    mgmt = _child(root, "dependencyManagement")
    for dep in _descendants(mgmt, "dependency"):
        group = _text(dep, "groupId")
        artifact = _text(dep, "artifactId")
        version = _resolve(_text(dep, "version"), props)
        if group and artifact and version:
            managed[f"{group}:{artifact}"] = version

    dependencies_element = _child(root, "dependencies")
    for dep in _descendants(dependencies_element, "dependency"):
        group = _text(dep, "groupId")
        artifact = _text(dep, "artifactId")
        if not group or not artifact:
            continue
        version = _resolve(_text(dep, "version"), props)
        if version is None:
            version = managed.get(f"{group}:{artifact}")
            if version is not None:
                warnings.append(
                    f"pom.xml: version for {group}:{artifact} came from "
                    "dependencyManagement"
                )
        if version is None:
            warnings.append(
                f"pom.xml: could not resolve version for {group}:{artifact}"
            )
            diagnostics.append(Diagnostic(
                "warning", "java.unresolved_version",
                f"unresolved version for {group}:{artifact}", source="pom.xml",
            ))
        scope = (_text(dep, "scope") or "compile").lower()
        category = _SCOPE_CATEGORY.get(scope, RUNTIME)
        if (_text(dep, "optional") or "").lower() == "true":
            category = OPTIONAL
        if not options.includes(category):
            continue
        name = f"{group}:{artifact}"
        dependencies.append(Dependency(
            ecosystem="java",
            name=name,
            normalized_name=normalize_simple(name),
            category=category,
            specifier=version,
            source="pom.xml",
            provenance="maven",
        ))

    if _child(root, "parent") is not None:
        warnings.append(
            "pom.xml declares a parent POM; inherited dependencies and "
            "properties are not resolved"
        )

    return dependencies


def _text(element, name):
    child = _child(element, name)
    if child is not None and child.text:
        return child.text.strip()
    return None


_GRADLE_STRING = re.compile(
    r"""^\s*(?P<config>[A-Za-z]+)\s+
        (?:
            ['"](?P<coord>[^'"]+)['"]
          |
            group:\s*['"](?P<group>[^'"]+)['"]\s*,\s*
            name:\s*['"](?P<name>[^'"]+)['"](?:\s*,\s*
            version:\s*['"](?P<version>[^'"]+)['"])?
        )""",
    re.VERBOSE,
)


def _parse_gradle(path, options, warnings, diagnostics):
    text = read_text(path)
    dependencies = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        match = _GRADLE_STRING.match(line)
        if not match:
            if re.match(r"^\s*(implementation|api|testImplementation)\b", line):
                warnings.append(
                    f"{path.name}: could not statically parse dependency "
                    f"declaration: {line}"
                )
                diagnostics.append(Diagnostic(
                    "warning", "java.unparsed_gradle",
                    f"unparsed Gradle declaration: {line}", source=path.name,
                ))
            continue
        config = match.group("config").lower()
        category = _GRADLE_CONFIG_CATEGORY.get(config, RUNTIME)
        if not options.includes(category):
            continue

        if match.group("coord"):
            parts = match.group("coord").split(":")
            if len(parts) < 2:
                continue
            group, artifact = parts[0], parts[1]
            version = parts[2] if len(parts) > 2 else None
        else:
            group = match.group("group")
            artifact = match.group("name")
            version = match.group("version")

        if version and ("$" in version or "+" in version):
            warnings.append(
                f"{path.name}: dynamic version {version!r} for "
                f"{group}:{artifact} is not resolved"
            )
            version = None

        name = f"{group}:{artifact}"
        dependencies.append(Dependency(
            ecosystem="java",
            name=name,
            normalized_name=normalize_simple(name),
            category=category,
            specifier=version,
            source=path.name,
            provenance="gradle",
        ))
    return dependencies


class JavaParser(Parser):
    ecosystem = "java"
    display_name = "Java"
    manifest_filenames = ("pom.xml", "build.gradle", "build.gradle.kts")
    lockfile_filenames = ()
    can_parse_direct = True
    can_parse_resolved = False
    can_resolve_transitive = False
    limitations = (
        "Gradle is parsed with a bounded static subset; arbitrary or dynamic "
        "build script expressions are reported, not executed.",
        "Parent POMs and BOM imports are not resolved.",
        "No lockfile support.",
    )

    def detect(self, project_dir):
        for name in self.manifest_filenames:
            if (project_dir / name).is_file():
                return True, []
        return False, []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        warnings = []
        diagnostics = []
        dependencies = []
        manifests = []

        if (project_dir / "pom.xml").is_file():
            dependencies.extend(
                _parse_pom(project_dir / "pom.xml", options, warnings,
                           diagnostics)
            )
            manifests.append("pom.xml")

        for name in ("build.gradle", "build.gradle.kts"):
            path = project_dir / name
            if path.is_file():
                dependencies.extend(
                    _parse_gradle(path, options, warnings, diagnostics)
                )
                manifests.append(name)

        if not manifests:
            raise ManifestError("No Java build file found")

        if options.resolve_transitive:
            transitive_unavailable(
                "java", "pom.xml", warnings, diagnostics,
                "no supported JVM lockfile with dependency relationships",
            )

        return ParseResult(
            ecosystem="java",
            project_name=project_dir.name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=manifests,
            lock_coverage="declared_only",
            transitive=False,
        )
