"""Python parser: requirements.txt and PEP 621 pyproject.toml."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ..diagnostics import Diagnostic, ManifestError, UnsupportedProjectError
from ..models import Dependency
from ..naming import normalize_extra, normalize_name
from .base import ParseOptions, ParseResult, Parser
from .util import transitive_unavailable

try:  # pragma: no cover - missing packaging is handled at call time
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError as exc:  # pragma: no cover
    InvalidRequirement = ValueError
    Requirement = None
    _PACKAGING_IMPORT_ERROR = exc
else:
    _PACKAGING_IMPORT_ERROR = None


# PEP 508 package names must start and end with an alphanumeric character.
PACKAGE_NAME_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])$"
)


def _ensure_packaging():
    if _PACKAGING_IMPORT_ERROR is not None:
        raise RuntimeError(
            "the 'packaging' library is required for standards-compliant "
            "requirement parsing. Install it with:\n"
            "    python3 -m pip install packaging"
        ) from _PACKAGING_IMPORT_ERROR


def parse_requirement(line):
    """Parse a single requirement line into a structured dictionary.

    Returns ``None`` for blank lines and comments. Raises ``ValueError`` for
    malformed or unsupported input.
    """
    if not isinstance(line, str):
        raise ValueError(
            f"dependency entry must be a string, got {type(line).__name__}"
        )

    line = line.strip()

    if not line or line.startswith("#"):
        return None

    # Strip a simple inline comment. A URL fragment is preserved because it is
    # not preceded by whitespace.
    line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
    if not line:
        return None

    if line.startswith("-"):
        raise ValueError(
            "pip options and include/editable directives are not supported"
        )

    _ensure_packaging()

    try:
        requirement = Requirement(line)
    except InvalidRequirement as exc:
        raise ValueError(f"invalid requirement syntax: {exc}") from exc

    if not PACKAGE_NAME_PATTERN.match(requirement.name):
        raise ValueError(f"invalid package name: {requirement.name!r}")

    extras = sorted({normalize_extra(item) for item in requirement.extras})
    specifier = str(requirement.specifier) or None
    marker = str(requirement.marker) if requirement.marker else None
    url = requirement.url

    return {
        "name": requirement.name,
        "normalized_name": normalize_name(requirement.name),
        "extras": extras,
        "specifier": specifier,
        "marker": marker,
        "url": url,
    }


def _read_pyproject(path):
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"Cannot parse pyproject.toml: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read pyproject.toml: {exc}") from exc


def _read_requirements_lines(path):
    """Read a requirements file, joining backslash continuations.

    Returns a list of ``(first_line_number, logical_text)`` tuples.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ManifestError(
            f"Cannot decode {path.name} as UTF-8: {exc}"
        ) from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read {path.name}: {exc}") from exc

    entries = []
    buffer = ""
    start = None

    for number, physical in enumerate(text.splitlines(), start=1):
        if start is None:
            start = number
        if physical.endswith("\\"):
            buffer += physical[:-1]
            continue
        buffer += physical
        entries.append((start, buffer))
        buffer = ""
        start = None

    if start is not None:
        entries.append((start, buffer))

    return entries


def unsupported_notes(project_dir, pyproject_data):
    """Notes about Python-adjacent layouts we recognise but do not analyze."""
    notes = []
    if (project_dir / "setup.cfg").is_file():
        notes.append("setup.cfg is not supported (setuptools projects)")
    if (project_dir / "Pipfile").is_file():
        notes.append("Pipfile is not supported (Pipenv projects)")
    if isinstance(pyproject_data, dict):
        tool = pyproject_data.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict) and "dependencies" in poetry:
            notes.append(
                "[tool.poetry.dependencies] is not supported (Poetry "
                "projects); only PEP 621 [project].dependencies is analyzed"
            )
    return notes


def load_manifest(project_dir):
    """Select and load a supported Python manifest.

    Returns ``(project_name, source, entries, warnings)`` where ``entries`` is a
    list of ``(location, text)`` tuples. Retained for backwards compatibility.
    """
    project_dir = Path(project_dir)
    pyproject = project_dir / "pyproject.toml"
    requirements = project_dir / "requirements.txt"
    warnings = []

    pyproject_data = _read_pyproject(pyproject) if pyproject.is_file() else None

    project_table = None
    if isinstance(pyproject_data, dict):
        project_table = pyproject_data.get("project")
    if project_table is not None and not isinstance(project_table, dict):
        raise ManifestError("[project] must be a TOML table")

    if isinstance(project_table, dict) and "dependencies" in project_table:
        dependencies = project_table["dependencies"]
        if not isinstance(dependencies, list):
            raise ManifestError("[project].dependencies must be an array")
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, str):
                raise ManifestError(
                    f"[project].dependencies[{index}] must be a string, got "
                    f"{type(dependency).__name__}"
                )
        name = project_table.get("name")
        if name is not None and not isinstance(name, str):
            raise ManifestError("[project].name must be a string")
        if project_table.get("optional-dependencies"):
            warnings.append(
                "Optional dependency groups are not included in this "
                "analysis; only [project].dependencies are reported."
            )
        if requirements.is_file():
            warnings.append(
                "Both pyproject.toml and requirements.txt were found; using "
                "pyproject.toml [project].dependencies and ignoring "
                "requirements.txt."
            )
        entries = [
            (f"dependencies[{index}]", dependency)
            for index, dependency in enumerate(dependencies)
        ]
        return name or project_dir.name, "pyproject.toml", entries, warnings

    if requirements.is_file():
        if pyproject_data is not None:
            warnings.append(
                "pyproject.toml has no [project].dependencies; falling back "
                "to requirements.txt."
            )
        entries = [
            (f"line {number}", text)
            for number, text in _read_requirements_lines(requirements)
        ]
        return project_dir.name, "requirements.txt", entries, warnings

    if isinstance(project_table, dict):
        warnings.append(
            "pyproject.toml has a [project] table but no dependencies array; "
            "reporting zero dependencies."
        )
        name = project_table.get("name")
        if name is not None and not isinstance(name, str):
            raise ManifestError("[project].name must be a string")
        return name or project_dir.name, "pyproject.toml", [], warnings

    notes = unsupported_notes(project_dir, pyproject_data)
    detail = f" ({'; '.join(notes)})" if notes else ""
    raise UnsupportedProjectError(
        "No supported Python manifest found: expected pyproject.toml with "
        f"[project].dependencies or a requirements.txt file{detail}",
        notes=notes,
    )


def _looks_like_requirements(name):
    lowered = name.lower()
    return lowered.endswith(".txt")


class PythonParser(Parser):
    ecosystem = "python"
    display_name = "Python"
    manifest_filenames = ("requirements.txt", "pyproject.toml")
    manifest_globs = ("requirements*.txt",)
    lockfile_filenames = ()
    can_parse_direct = True
    can_parse_resolved = False
    can_resolve_transitive = False
    limitations = (
        "No lockfile is used; resolved versions are not available.",
        "Environment markers are captured but not evaluated.",
        "pip include directives (-r/-c/-e) are reported, not followed.",
        "Poetry, Pipenv and setup.cfg layouts are recognised but not parsed.",
    )

    def detect(self, project_dir):
        project_dir = Path(project_dir)
        notes = []
        pyproject = project_dir / "pyproject.toml"
        requirements = project_dir / "requirements.txt"

        if pyproject.is_file():
            try:
                data = _read_pyproject(pyproject)
            except ManifestError:
                # Malformed: treat as supported so parse() raises a clear error.
                return True, notes
            table = data.get("project") if isinstance(data, dict) else None
            if isinstance(table, dict):
                return True, unsupported_notes(project_dir, data)
            notes = unsupported_notes(project_dir, data)

        if requirements.is_file():
            return True, notes
        return False, notes

    def parse(self, project_dir, options):
        project_dir = Path(project_dir)

        explicit = options.explicit_manifest
        if explicit and _looks_like_requirements(Path(explicit).name):
            manifest = Path(explicit)
            if not manifest.is_file():
                manifest = project_dir / Path(explicit).name
            entries = [
                (f"line {number}", text)
                for number, text in _read_requirements_lines(manifest)
            ]
            name = project_dir.name
            source = manifest.name
            warnings = []
        else:
            name, source, entries, warnings = load_manifest(project_dir)

        dependencies = []
        seen = set()
        diagnostics = []

        if options.resolve_transitive:
            transitive_unavailable(
                "python", source, warnings, diagnostics,
                "no supported Python lockfile with dependency relationships",
            )

        for location, text in entries:
            try:
                item = parse_requirement(text)
            except ValueError as exc:
                message = f"{location}: {exc}"
                warnings.append(message)
                diagnostics.append(
                    Diagnostic("warning", "python.invalid_requirement",
                               str(exc), source=source)
                )
                continue
            if item is None:
                continue
            key = (
                item["normalized_name"],
                item["specifier"],
                item["marker"],
                tuple(item["extras"]),
                item["url"],
            )
            if key in seen:
                continue
            seen.add(key)
            dependencies.append(Dependency(
                ecosystem="python",
                name=item["name"],
                normalized_name=item["normalized_name"],
                extras=item["extras"],
                specifier=item["specifier"],
                marker=item["marker"],
                url=item["url"],
                source=source,
                provenance="python",
            ))

        return ParseResult(
            ecosystem="python",
            project_name=name,
            dependencies=dependencies,
            warnings=warnings,
            diagnostics=diagnostics,
            manifests=[source] if source else [],
            lock_coverage="declared_only",
            transitive=False,
        )
