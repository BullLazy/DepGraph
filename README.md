# DepGraph

DepGraph is a read-only, **multilingual** dependency manifest analyzer. It reads
the **declared** dependencies of a project and reports them as a graph, in either
a human-readable terminal format or stable JSON.

It is intentionally **not** a resolver: it never installs anything, never
downloads from the network, and never executes project code (no Gradle, Gemfile,
`conanfile.py`, `mix.exs`, `Package.swift`, or CMake scripts). It tells you what
a manifest *declares*, and it never fabricates names, versions, URLs, or edges.

By default it reports **declared** dependencies only. With `--transitive` it can
also graph the resolved package-to-package edges recorded in supported
lockfiles — again, without guessing: a package edge is emitted only when the
lockfile pins both endpoints to exactly one version.

Auto-discovery parses one manifest per directory (`requirements.txt` for
Python). Additional files such as `requirements-dev.txt` are recognised in the
manifest inventory and can be analyzed explicitly with
`--manifest requirements-dev.txt`.

## Supported ecosystems

| Ecosystem | Manifests | Lockfiles |
| --- | --- | --- |
| Python | `pyproject.toml` (PEP 621), `requirements.txt` (other `requirements*.txt` are recognised and analyzable via `--manifest`) | — |
| JavaScript/TypeScript | `package.json` | `package-lock.json`, `npm-shrinkwrap.json` (v1/v2/v3), `yarn.lock` (v1) |
| Rust | `Cargo.toml` | `Cargo.lock` |
| Go | `go.mod` | `go.sum` (checksums noted only) |
| Java | `pom.xml`, `build.gradle`, `build.gradle.kts` (bounded) | — |
| C#/.NET | `*.csproj`/`*.vbproj`/`*.fsproj`, `packages.config` | — |
| PHP | `composer.json` | `composer.lock` |
| R | `DESCRIPTION` | `renv.lock` |
| Julia | `Project.toml` | `Manifest.toml` |
| C/C++ | `vcpkg.json`, `conanfile.txt`, `CMakeLists.txt` (bounded) | — |
| Ruby | `Gemfile` | `Gemfile.lock` |
| Perl | `cpanfile` | — |

### Lockfiles and transitive resolution

Lockfiles are always used to **attach resolved versions** to declared
dependencies where unambiguous. With `--transitive` (which implies
`--lockfile`), the same lockfiles are also used to reconstruct the resolved
package graph and its package-to-package edges:

| Lockfile | Version enrichment | Transitive graph (`--transitive`) |
| --- | --- | --- |
| `package-lock.json` / `npm-shrinkwrap.json` | yes | yes |
| `yarn.lock` (v1) | yes | yes |
| `Cargo.lock` | yes | yes |
| `composer.lock` | yes | yes (platform requirements skipped) |
| `renv.lock` | yes | yes (`Suggests`/`Enhances` are not hard edges) |
| `Manifest.toml` | yes | yes (references by name or UUID) |
| `Gemfile.lock` | yes | yes |
| `go.sum` | checksums only | no (no dependency relationships) |

If a declared dependency cannot be pinned to exactly one locked version
(several locked versions, or none), it is reported with a warning and its
subgraph is **not** expanded, rather than guessing.

### Recognised but unsupported

These formats are detected and reported explicitly, rather than silently
returning an empty graph:

- `Pipfile` / `setup.cfg` (Python)
- `Package.swift` (Swift)
- `mix.exs` (Elixir)
- `build.sbt` (Scala)
- `conanfile.py` (C/C++, executable Python)
- `pubspec.yaml` (Dart)
- `pnpm-lock.yaml` (JavaScript; recognised but not parsed)
- `cpanfile.snapshot` (Perl, Carmel; recognised but not parsed)

## Requirements

- Python **3.11+** (uses the standard-library `tomllib`).
- [`packaging`](https://pypi.org/project/packaging/) (only third-party runtime
  dependency, used for Python PEP 508/440 handling).

## Installation

From a checkout:

```sh
python3 -m pip install .
# or, for an editable development install:
python3 -m pip install -e .
```

This installs a `depgraph` console script. You can also run it directly without
installing:

```sh
python3 depgraph.py <project-directory>
python3 -m depgraph <project-directory>
```

## Usage

```sh
# Analyze a single-ecosystem project (terminal output)
depgraph /path/to/project

# JSON output for automation
depgraph /path/to/project --format json

# A directory containing several ecosystems: select one or all
depgraph /path/to/project --ecosystem python
depgraph /path/to/project --all-ecosystems

# Include optional scopes and lockfile resolution
depgraph /path/to/project --include-dev --include-peer --include-optional --lockfile

# Resolve and graph transitive packages from the lockfile
depgraph /path/to/project --transitive

# Scan a monorepo
depgraph /path/to/monorepo --recursive --all-ecosystems
```

If more than one ecosystem is detected and neither `--ecosystem` nor
`--all-ecosystems` is given, DepGraph reports the ambiguity instead of silently
picking one.

### Terminal example

```console
$ depgraph samples/demo
DepGraph 0.4.0
Project: demo
Manifest: requirements.txt
Ecosystems: python
Direct dependencies: 3

  - Flask >=3.0
  - packaging >=24.0 ; python_version >= "3.10"
  - requests >=2.31

Note: this report describes declared dependencies only; it does not resolve transitive dependencies or evaluate environment markers.
```

### JSON example

```jsonc
{
  "tool": "DepGraph",
  "version": "0.4.0",
  "schema_version": 2,
  "project": {
    "name": "demo",
    "path": "/abs/path/samples/demo",
    "manifest": "requirements.txt",
    "ecosystems": ["python"]
  },
  "analysis": {
    "kind": "declared_dependencies",
    "transitive_dependencies_resolved": false,
    "transitive_dependency_count": 0,
    "direct_dependency_count": 3,
    "dependency_count": 3,
    "node_count": 4,
    "edge_count": 3,
    "ecosystems": ["python"],
    "lockfile_coverage": "declared_only"
  },
  "dependencies": [ /* one object per dependency (direct and transitive) */ ],
  "graph": { "nodes": [ /* project + package nodes */ ], "edges": [ /* project -> package and package -> package */ ] },
  "warnings": [],
  "manifests": [ /* inventory of recognised files */ ],
  "lockfiles": [],
  "diagnostics": []
}
```

The `dependencies`, `nodes`, and `edges` arrays are always consistent with each
other. JSON is deterministic: dependency, node, and edge ordering is sorted and
no timestamps are emitted. When `--transitive` is used, `graph.edges` also
contains `direct: false` package-to-package edges, and package nodes that share a
name are distinguished by resolved version in their `id`.

## CLI options

| Option | Description |
| --- | --- |
| `PROJECT` | Path to a project directory (required). |
| `--format {terminal,json}` | Output format (default: `terminal`). |
| `--ecosystem NAME` | Restrict analysis to one ecosystem (repeatable). |
| `--all-ecosystems` | Analyze every detected ecosystem in one report. |
| `--manifest PATH` | Use an explicit manifest file instead of discovery. |
| `--include-dev` | Include development dependencies. |
| `--include-test` | Include test-scoped dependencies (implied by `--include-dev`). |
| `--include-optional` | Include optional dependencies. |
| `--include-peer` | Include peer dependencies. |
| `--include-build` | Include build-time dependencies. |
| `--lockfile` | Attach resolved versions from supported lockfiles. |
| `--transitive` | Resolve transitive packages from lockfiles and graph package-to-package edges (implies `--lockfile`). |
| `--recursive` | Scan the directory tree for multiple projects. |
| `--quiet` | Omit warnings and the explanatory note (terminal only). |
| `--strict` | Exit with status 3 if any warnings were produced. |
| `--version` | Print the version and exit. |
| `--help` | Show help and exit. |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Successful analysis with no warnings (or warnings without `--strict`). |
| `1` | Analysis or I/O error (missing path, unreadable/malformed manifest, missing `packaging`). |
| `2` | Invalid command-line usage (argparse). |
| `3` | Warnings were produced and `--strict` was set. |

Error messages are written to stderr, so `--format json` output on stdout is
never corrupted and is safe to pipe. (The report's own `warnings` and
`diagnostics` fields are part of the JSON payload on stdout.)

## How scopes map

Environments differ, so DepGraph normalises scopes into a shared vocabulary:
`runtime`, `dev`, `test`, `optional`, `peer`, `build`, `platform`, and
`unknown`. Examples:

- `devDependencies` (npm) → `dev`; `peerDependencies` → `peer`;
  `optionalDependencies` → `optional`.
- Maven `test`/`provided` scopes → `test`/`platform`.
- Composer `require-dev` → `dev`; platform requirements (`php`, `ext-*`,
  `lib-*`) → `platform`.
- R `Suggests` → `optional`; `LinkingTo` → `build`.

By default only `runtime`, `platform`, and `unknown` are included. Other scopes
require the matching `--include-*` flag.

## Limitations and unsupported features

- **Declared dependencies by default.** Without `--transitive`, transitive
  dependencies are not resolved and lockfiles never create edges; they only
  enrich declared dependencies with `resolved_version` where unambiguous. With
  `--transitive`, package edges are taken only from lockfiles, never invented,
  and ambiguous packages are skipped with a warning.
- **No project code is executed.** Executable build scripts are parsed with a
  bounded static subset or reported as unsupported:
  - Gradle: `implementation`/`api`/`testImplementation`/… with string or
    map-notation coordinates. Dynamic versions (`$var`, `+`) are reported, not
    resolved.
  - CMake: only `find_package` and `FetchContent_Declare` are recognised.
  - `conanfile.py`, `Gemfile` Ruby logic, `mix.exs`, `Package.swift`, and
    `build.sbt` are never executed.
- **No parent POM / BOM resolution** for Maven; no workspace expansion (Cargo
  workspaces, npm workspaces) beyond running `--recursive`.
- **Environment markers are captured, not evaluated.**
- **Version specifiers are reported, not compared or ordered.**
- **`go.sum` is checksums only**, and `cpanfile.snapshot`, `pnpm-lock.yaml`, and
  Yarn Berry locks are recognised but not parsed; `--transitive` reports
  `*.transitive.unavailable` for ecosystems without a parseable lockfile graph
  (Python, Java, .NET, C/C++, Perl, Go).
- **Package identity** is namespaced by ecosystem, with lower-cased
  normalisation per registry conventions.
- `-r`/`-c` include files in `requirements.txt` are not followed.

## Development and testing

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary directories and do not depend on the network or on your
actual project files.
