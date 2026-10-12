"""Command-line interface for DepGraph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyze
from .output import print_terminal, render_json
from .parsers import supported_ecosystems
from .parsers.base import ParseOptions
from .version import __version__

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_STRICT_WARNINGS = 3


def build_parser():
    parser = argparse.ArgumentParser(
        prog="depgraph",
        description=(
            "Analyze declared dependencies in a Python or multilingual "
            "project. Static analysis only: project code is never executed."
        ),
    )
    parser.add_argument("project", help="Path to a project directory")
    parser.add_argument(
        "--format", choices=("terminal", "json"), default="terminal",
        help="Output format (default: terminal)",
    )
    parser.add_argument(
        "--ecosystem", action="append", default=[], metavar="NAME",
        help=(
            "Restrict analysis to an ecosystem (repeatable). Supported: "
            + ", ".join(supported_ecosystems())
        ),
    )
    parser.add_argument(
        "--all-ecosystems", action="store_true",
        help="Analyze every detected ecosystem in one report",
    )
    parser.add_argument(
        "--manifest", metavar="PATH",
        help="Use an explicit manifest file instead of discovery",
    )
    parser.add_argument(
        "--include-dev", action="store_true",
        help="Include development dependencies",
    )
    parser.add_argument(
        "--include-test", action="store_true",
        help="Include test-scoped dependencies (implied by --include-dev)",
    )
    parser.add_argument(
        "--include-optional", action="store_true",
        help="Include optional dependencies",
    )
    parser.add_argument(
        "--include-peer", action="store_true",
        help="Include peer dependencies",
    )
    parser.add_argument(
        "--include-build", action="store_true",
        help="Include build-time dependencies",
    )
    parser.add_argument(
        "--lockfile", action="store_true",
        help="Include resolved versions from supported lockfiles",
    )
    parser.add_argument(
        "--transitive", action="store_true",
        help=(
            "Resolve transitive packages from lockfiles and graph "
            "package-to-package edges (implies --lockfile)"
        ),
    )
    parser.add_argument(
        "--recursive", action="store_true",
        help="Scan the directory tree for multiple projects",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Omit warnings and the explanatory note (terminal only)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit with status 3 if any warnings were produced",
    )
    parser.add_argument(
        "--version", action="version", version=f"DepGraph {__version__}",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    invalid = [
        name for name in args.ecosystem
        if name not in supported_ecosystems()
    ]
    if invalid:
        parser.error(
            "unsupported ecosystem(s): "
            + ", ".join(invalid)
            + "; supported: "
            + ", ".join(supported_ecosystems())
        )

    options = ParseOptions(
        include_dev=args.include_dev,
        include_test=args.include_test,
        include_optional=args.include_optional,
        include_peer=args.include_peer,
        include_build=args.include_build,
        use_lockfile=args.lockfile or args.transitive,
        resolve_transitive=args.transitive,
        explicit_manifest=args.manifest,
        ecosystems=tuple(args.ecosystem),
        all_ecosystems=args.all_ecosystems,
        recursive=args.recursive,
    )

    try:
        report = analyze(Path(args.project), options)
    except RuntimeError as exc:
        print(f"DepGraph error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (ValueError, OSError) as exc:
        print(f"DepGraph error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.format == "json":
        print(render_json(report))
    else:
        print_terminal(report, quiet=args.quiet)

    if args.strict and report["warnings"]:
        return EXIT_STRICT_WARNINGS
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
