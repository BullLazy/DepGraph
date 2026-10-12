"""C/C++ parser: vcpkg.json, conanfile.txt, and a bounded CMake subset."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError
from ..models import BUILD, RUNTIME, Dependency
from ..naming import normalize_simple
from .base import ParseResult, Parser
from .util import read_json, read_text, transitive_unavailable

_FIND_PACKAGE_RE = re.compile(
    r"""^\s*find_package\s*\(\s*([A-Za-z0-9_.+-]+)""", re.IGNORECASE
)
_FETCH_RE = re.compile(
    r"""^\s*FetchContent_Declare\s*\(\s*([A-Za-z0-9_.+-]+)""", re.IGNORECASE
)
_CONAN_DEP_RE = re.compile(
    r"""^\s*(?P<name>[A-Za-z0-9_.+-]+)/(?P<version>[^\s@#]+)"""
    r"""(?:@(?P<user>[^\s/#]+)/(?P<channel>[^\s/#]+))?"""
)


def _parse_vcpkg(path, options, warnings):
    data = read_json(path)
    entries = data.get("dependencies") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        warnings.append("vcpkg.json: no 'dependencies' array found")
        return []
    dependencies = []
    for entry in entries:
        if isinstance(entry, str):
            name, version = entry, None
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            name = entry["name"]
            version = entry.get("version>=") or entry.get("version")
        else:
            continue
        if not options.includes(RUNTIME):
            continue
        dependencies.append(Dependency(
            ecosystem="cpp",
            name=name,
            normalized_name=normalize_simple(name),
            category=RUNTIME,
            specifier=version if isinstance(version, str) else None,
            source=path.name,
            provenance="vcpkg",
        ))
    return dependencies


def _parse_conanfile(path, options, warnings):
    dependencies = []
    section = None
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if not line or line.startswith("#"):
            continue
        if section == "requires":
            category = RUNTIME
        elif section in ("build_requires", "tool_requires", "build"):
            category = BUILD
        else:
            continue
        match = _CONAN_DEP_RE.match(line)
        if not match:
            continue
        if not options.includes(category):
            continue
        name = match.group("name")
        dependencies.append(Dependency(
            ecosystem="cpp",
            name=name,
            normalized_name=normalize_simple(name),
            category=category,
            specifier=match.group("version"),
            source=path.name,
            provenance="conan",
        ))
    return dependencies


def _parse_cmake(path, options, warnings):
    dependencies = []
    seen = set()
    for raw in read_text(path).splitlines():
        line = raw.split("#", 1)[0]
        for regex, origin in ((_FIND_PACKAGE_RE, "cmake"),
                              (_FETCH_RE, "cmake")):
            match = regex.match(line)
            if not match:
                continue
            name = match.group(1)
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            if not options.includes(RUNTIME):
                continue
            dependencies.append(Dependency(
                ecosystem="cpp",
                name=name,
                normalized_name=normalize_simple(name),
                category=RUNTIME,
                specifier=None,
                source=path.name,
                provenance=origin,
            ))
    return dependencies


class CppParser(Parser):
    ecosystem = "cpp"
    display_name = "C/C++"
    manifest_filenames = ("vcpkg.json", "conanfile.txt", "CMakeLists.txt")
    lockfile_filenames = ()
    can_parse_direct = True
    can_parse_resolved = False
    can_resolve_transitive = False
    limitations = (
        "CMake is parsed with a bounded subset (find_package, "
        "FetchContent_Declare); CMake script logic is never executed.",
        "conanfile.py is recognised but not parsed because it is executable "
        "Python.",
        "Version ranges and CMake package components are not resolved.",
    )

    def detect(self, project_dir):
        for name in ("vcpkg.json", "conanfile.txt", "CMakeLists.txt"):
            if (project_dir / name).is_file():
                return True, []
        return False, []

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)
        warnings = []
        diagnostics = []
        dependencies = []
        manifests = []

        if (project_dir / "vcpkg.json").is_file():
            dependencies.extend(
                _parse_vcpkg(project_dir / "vcpkg.json", options, warnings)
            )
            manifests.append("vcpkg.json")

        if (project_dir / "conanfile.txt").is_file():
            dependencies.extend(
                _parse_conanfile(project_dir / "conanfile.txt", options,
                                 warnings)
            )
            manifests.append("conanfile.txt")

        if (project_dir / "conanfile.py").is_file():
            warnings.append(
                "conanfile.py is recognised but not parsed (executable "
                "Python); dependencies from it are not discovered."
            )
            diagnostics.append(Diagnostic(
                "warning", "cpp.conanfile_py_unsupported",
                "conanfile.py is not parsed", source="conanfile.py",
            ))

        if (project_dir / "CMakeLists.txt").is_file():
            dependencies.extend(
                _parse_cmake(project_dir / "CMakeLists.txt", options, warnings)
            )
            manifests.append("CMakeLists.txt")

        if not manifests:
            raise ManifestError("No C/C++ dependency manifest found")

        if options.resolve_transitive:
            transitive_unavailable(
                "cpp", "vcpkg.json", warnings, diagnostics,
                "no supported C/C++ lockfile with dependency relationships",
            )

        return ParseResult(
            ecosystem="cpp",
            project_name=project_dir.name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=manifests,
            lock_coverage="declared_only",
            transitive=False,
        )
