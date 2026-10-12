"""Deterministic manifest discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .parsers import PARSERS

# Directories never traversed during recursive scans.
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".tox",
    ".nox",
    "target",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".gradle",
    ".idea",
    ".vscode",
    ".dart_tool",
    "Pods",
}

# Filenames recognised as dependency manifests but not parsed by any parser.
# Mapping: filename -> (ecosystem, note)
UNSUPPORTED_FILES = {
    "Pipfile": ("python", "Pipfile is not supported (Pipenv projects)"),
    "setup.cfg": ("python", "setup.cfg is not supported (setuptools projects)"),
    "Package.swift": (
        "swift",
        "Package.swift is executable Swift and is not statically parsed",
    ),
    "mix.exs": (
        "elixir",
        "mix.exs is executable Elixir and is not statically parsed",
    ),
    "build.sbt": (
        "scala",
        "build.sbt is executable Scala and is not statically parsed",
    ),
    "conanfile.py": (
        "cpp",
        "conanfile.py is executable Python and is not statically parsed",
    ),
    "pubspec.yaml": (
        "dart",
        "pubspec.yaml is recognised but Dart parsing is not implemented",
    ),
    "pnpm-lock.yaml": (
        "javascript",
        "pnpm-lock.yaml is recognised but its format is not parsed",
    ),
    "cpanfile.snapshot": (
        "perl",
        "cpanfile.snapshot (Carmel) is recognised but not parsed",
    ),
}


@dataclass
class InventoryItem:
    path: str
    ecosystem: str
    kind: str  # manifest | lockfile | unsupported
    supported: bool
    note: str = ""

    def to_dict(self):
        return {
            "path": self.path,
            "ecosystem": self.ecosystem,
            "kind": self.kind,
            "supported": self.supported,
            "note": self.note or None,
        }


def _rel(directory, root, name):
    rel = directory.relative_to(root)
    if str(rel) == ".":
        return name
    return str(rel / name)


def iter_project_dirs(root, recursive):
    yield root
    if not recursive:
        return
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if not path.is_dir():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        yield path


def scan(root, recursive=False):
    """Return an inventory of recognised manifests for reporting."""
    root = Path(root)
    items = []
    for directory in iter_project_dirs(root, recursive):
        for parser in PARSERS:
            for name in parser.manifest_filenames:
                if (directory / name).is_file():
                    items.append(InventoryItem(
                        _rel(directory, root, name),
                        parser.ecosystem,
                        "manifest",
                        True,
                    ))
            for pattern in parser.manifest_globs:
                for candidate in sorted(directory.glob(pattern)):
                    if candidate.is_file():
                        items.append(InventoryItem(
                            _rel(directory, root, candidate.name),
                            parser.ecosystem,
                            "manifest",
                            True,
                        ))
            for name in parser.lockfile_filenames:
                if (directory / name).is_file():
                    items.append(InventoryItem(
                        _rel(directory, root, name),
                        parser.ecosystem,
                        "lockfile",
                        True,
                    ))
        for name, (ecosystem, note) in UNSUPPORTED_FILES.items():
            if (directory / name).is_file():
                items.append(InventoryItem(
                    _rel(directory, root, name),
                    ecosystem,
                    "unsupported",
                    False,
                    note,
                ))
    # De-duplicate by (path, ecosystem, kind), preserving order.
    seen = set()
    unique = []
    for item in items:
        key = (item.path, item.ecosystem, item.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def project_dirs(root, recursive=False):
    """Directories (including ``root``) containing a recognised manifest."""
    root = Path(root)
    dirs = []
    for directory in iter_project_dirs(root, recursive):
        found = False
        for parser in PARSERS:
            names = list(parser.manifest_filenames)
            for pattern in parser.manifest_globs:
                names.extend(p.name for p in directory.glob(pattern) if p.is_file())
            if any((directory / name).is_file() for name in names):
                found = True
                break
        if not found:
            for name in UNSUPPORTED_FILES:
                if (directory / name).is_file():
                    found = True
                    break
        if found:
            dirs.append(directory)
    return dirs or [root]
