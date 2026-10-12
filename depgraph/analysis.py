"""Top-level analysis orchestration."""

from __future__ import annotations

from pathlib import Path

from . import discovery
from .diagnostics import UnsupportedProjectError
from .graph import build_report
from .parsers import PARSERS, parser_for_filename
from .parsers.base import ParseOptions


def _selected_parsers(options):
    if options.ecosystems:
        wanted = set(options.ecosystems)
        return [parser for parser in PARSERS if parser.ecosystem in wanted]
    return list(PARSERS)


def _explicit_analysis(root, options, inventory):
    manifest = Path(options.explicit_manifest)
    if not manifest.is_absolute():
        manifest = root / manifest
    if not manifest.is_file():
        raise ValueError(f"manifest does not exist: {manifest}")
    manifest = manifest.resolve()

    parser = parser_for_filename(manifest.name)
    if parser is None:
        raise UnsupportedProjectError(
            f"unsupported manifest: {manifest.name}",
            notes=[f"{manifest.name} is not a supported manifest"],
        )
    directory = manifest.parent
    result = parser.parse(directory, options)
    return build_report(directory, [result], inventory, options)


def analyze(project_dir, options=None):
    """Analyze a project directory and return a report dictionary."""
    options = options or ParseOptions()
    root = Path(project_dir)

    if not root.exists():
        raise ValueError(f"path does not exist: {root}")
    if not root.is_dir():
        raise ValueError(
            f"expected a project directory but got a file: {root}"
        )
    root = root.resolve()

    inventory = discovery.scan(root, recursive=options.recursive)

    if options.explicit_manifest:
        return _explicit_analysis(root, options, inventory)

    directories = (
        discovery.project_dirs(root, recursive=True)
        if options.recursive else [root]
    )

    results = []
    notes = []
    ecosystems_supported = []

    for directory in directories:
        for parser in _selected_parsers(options):
            supported, parser_notes = parser.detect(directory)
            if supported:
                results.append(parser.parse(directory, options))
                if parser.ecosystem not in ecosystems_supported:
                    ecosystems_supported.append(parser.ecosystem)
            else:
                notes.extend(parser_notes)

    # Global recognisers (formats with no parser at all).
    for directory in directories:
        for name, (ecosystem, note) in discovery.UNSUPPORTED_FILES.items():
            if (directory / name).is_file() and ecosystem not in ecosystems_supported:
                notes.append(note)

    if not options.ecosystems and not options.all_ecosystems:
        if len(ecosystems_supported) > 1:
            listing = ", ".join(sorted(ecosystems_supported))
            raise UnsupportedProjectError(
                f"multiple ecosystems detected ({listing}); use --ecosystem to "
                "select one or --all-ecosystems to analyze all of them",
                notes=[],
            )

    if not results:
        unique_notes = list(dict.fromkeys(notes))
        detail = f" ({'; '.join(unique_notes)})" if unique_notes else ""
        raise UnsupportedProjectError(
            "No supported manifest found: expected a supported manifest "
            f"file{detail}",
            notes=unique_notes,
        )

    return build_report(root, results, inventory, options)
