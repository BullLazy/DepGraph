"""Terminal and JSON serialization."""

from __future__ import annotations

import json
import sys


def _supports_color(stream):
    return hasattr(stream, "isatty") and stream.isatty()


def render_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False)


def _format_dependency(dep, show_ecosystem):
    line = "  - "
    if not dep.get("direct", True):
        line += "(transitive) "
    if show_ecosystem:
        line += f"[{dep['ecosystem']}] "
    line += dep["name"]
    if dep["extras"]:
        line += "[" + ", ".join(dep["extras"]) + "]"
    if dep["specifier"]:
        line += f" {dep['specifier']}"
    if dep["url"]:
        line += f" @ {dep['url']}"
    if dep["resolved_version"]:
        line += f" (locked {dep['resolved_version']})"
    if dep["marker"]:
        line += f" ; {dep['marker']}"
    if dep["category"] not in ("runtime", None):
        line += f" [{dep['category']}]"
    return line


def print_terminal(report, quiet=False):
    """Render a human-readable report to stdout."""
    project = report["project"]
    analysis = report["analysis"]
    use_color = _supports_color(sys.stdout)

    def bold(text):
        return f"\033[1m{text}\033[0m" if use_color else text

    def yellow(text):
        return f"\033[33m{text}\033[0m" if use_color else text

    ecosystems = project.get("ecosystems") or []
    show_ecosystem = len(ecosystems) > 1

    print(bold(f"DepGraph {report['version']}"))
    print(f"Project: {project['name']}")
    print(f"Manifest: {project['manifest']}")
    if ecosystems:
        print(f"Ecosystems: {', '.join(ecosystems)}")
    transitive_count = analysis.get("transitive_dependency_count") or 0
    if transitive_count:
        print(
            "Dependencies: "
            f"{analysis['dependency_count']} "
            f"({analysis.get('direct_dependency_count', 0)} direct, "
            f"{transitive_count} transitive)"
        )
    else:
        print(f"Direct dependencies: {analysis['dependency_count']}")
    if report.get("lockfiles"):
        print(
            "Lockfile coverage: "
            f"{analysis.get('lockfile_coverage', 'none')}"
        )

    if not quiet:
        print()

    if report["dependencies"]:
        for dep in report["dependencies"]:
            print(_format_dependency(dep, show_ecosystem))
    elif not quiet:
        print("No supported dependencies found.")

    if report["warnings"] and not quiet:
        print(yellow(f"\nWarnings ({len(report['warnings'])}):"))
        for warning in report["warnings"]:
            print(yellow(f"  ! {warning}"))

    unsupported = [
        item for item in report.get("manifests", [])
        if item.get("kind") == "unsupported"
    ]
    if unsupported and not quiet:
        print(f"\nUnsupported manifests ({len(unsupported)}):")
        for item in unsupported:
            print(f"  ? {item['path']}: {item['note']}")

    if not quiet:
        if analysis.get("transitive_dependencies_resolved"):
            print(
                "\nNote: transitive packages were resolved from lockfiles; "
                "edges between packages reflect the locked graph exactly."
            )
        else:
            print(
                "\nNote: this report describes declared dependencies only; it "
                "does not resolve transitive dependencies or evaluate "
                "environment markers."
            )
