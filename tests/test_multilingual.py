"""Tests for the multilingual ecosystem parsers and CLI selection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from depgraph import ParseOptions, UnsupportedProjectError, analyze


def _names(report):
    return {dep["name"] for dep in report["dependencies"]}


def _by_name(report, name):
    for dep in report["dependencies"]:
        if dep["name"] == name:
            return dep
    raise AssertionError(f"{name!r} not in report")


class ParserTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, name, data):
        return self.write(name, json.dumps(data, indent=2))

    def analyze(self, **kwargs):
        options = ParseOptions(**kwargs) if kwargs else None
        return analyze(str(self.root), options)


class JavaScriptTests(ParserTestCase):
    def test_package_json(self):
        self.write_json("package.json", {
            "name": "demo",
            "dependencies": {"left-pad": "^1.3.0"},
            "devDependencies": {"jest": "^29.0.0"},
        })
        report = self.analyze(ecosystems=("javascript",))
        self.assertEqual(report["project"]["ecosystems"], ["javascript"])
        dep = _by_name(report, "left-pad")
        self.assertEqual(dep["category"], "runtime")
        self.assertEqual(dep["specifier"], "^1.3.0")
        self.assertNotIn("jest", _names(report))

    def test_package_lock_v3_resolves(self):
        self.write_json("package.json", {
            "dependencies": {"left-pad": "^1.3.0"},
        })
        self.write_json("package-lock.json", {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/left-pad": {"version": "1.3.0"},
            },
        })
        report = self.analyze(ecosystems=("javascript",), use_lockfile=True)
        dep = _by_name(report, "left-pad")
        self.assertEqual(dep["resolved_version"], "1.3.0")
        self.assertEqual(report["analysis"]["lockfile_coverage"], "partial")

    def test_peer_and_optional_categories(self):
        self.write_json("package.json", {
            "peerDependencies": {"react": "^18.0.0"},
            "optionalDependencies": {"fsevents": "^2.0.0"},
        })
        report = self.analyze(
            ecosystems=("javascript",), include_peer=True, include_optional=True
        )
        self.assertEqual(_by_name(report, "react")["category"], "peer")
        self.assertEqual(_by_name(report, "fsevents")["category"], "optional")


class RustTests(ParserTestCase):
    def test_cargo_toml_and_lock(self):
        self.write("Cargo.toml", """
[package]
name = "demo"
version = "0.1.0"

[dependencies]
serde = { version = "1.0", features = ["derive"] }

[dev-dependencies]
criterion = "0.5"

[target.'cfg(unix)'.dependencies]
libc = "0.2"
""")
        self.write("Cargo.lock", """
[[package]]
name = "serde"
version = "1.0.193"

[[package]]
name = "libc"
version = "0.2.150"
""")
        report = self.analyze(ecosystems=("rust",), use_lockfile=True)
        self.assertEqual(report["project"]["name"], "demo")
        serde = _by_name(report, "serde")
        self.assertEqual(serde["specifier"], "1.0")
        self.assertEqual(serde["resolved_version"], "1.0.193")
        self.assertEqual(_by_name(report, "libc")["specifier"], "0.2")
        self.assertNotIn("criterion", _names(report))
        self.assertIn("criterion", _names(
            self.analyze(ecosystems=("rust",), include_dev=True)
        ))

    def test_renamed_package(self):
        self.write("Cargo.toml", """
[package]
name = "demo"
version = "0.1.0"

[dependencies]
serde_json = { package = "serde_json", version = "1" }
""")
        report = self.analyze(ecosystems=("rust",))
        self.assertIn("serde_json", _names(report))


class GoTests(ParserTestCase):
    def test_go_mod(self):
        self.write("go.mod", """module example.com/demo

go 1.21

require (
\tgithub.com/pkg/errors v0.9.1
\tgolang.org/x/text v0.14.0 // indirect
)

require github.com/stretchr/testify v1.8.4
""")
        self.write("go.sum", "github.com/pkg/errors v0.9.1 h1:aaa\n")
        report = self.analyze(ecosystems=("go",), use_lockfile=True)
        self.assertEqual(report["project"]["name"], "example.com/demo")
        direct = _by_name(report, "github.com/pkg/errors")
        self.assertTrue(direct["direct"])
        indirect = _by_name(report, "golang.org/x/text")
        self.assertFalse(indirect["direct"])
        self.assertEqual(_by_name(report, "github.com/stretchr/testify")["specifier"],
                         "v1.8.4")
        self.assertIn("go.sum", report["lockfiles"])


class JavaTests(ParserTestCase):
    def test_maven_pom(self):
        self.write("pom.xml", """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>1.0.0</version>
  <properties>
    <junit.version>5.10.0</junit.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>2.0.9</version>
    </dependency>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>${junit.version}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
""")
        report = self.analyze(ecosystems=("java",))
        self.assertIn("org.slf4j:slf4j-api", _names(report))
        self.assertNotIn("org.junit.jupiter:junit-jupiter", _names(report))
        with_test = self.analyze(ecosystems=("java",), include_test=True)
        junit = _by_name(with_test, "org.junit.jupiter:junit-jupiter")
        self.assertEqual(junit["specifier"], "5.10.0")
        self.assertEqual(junit["category"], "test")

    def test_gradle_subset(self):
        self.write("build.gradle", """
dependencies {
    implementation 'com.google.guava:guava:32.1.3-jre'
    testImplementation "org.mockito:mockito-core:5.5.0"
}
""")
        report = self.analyze(ecosystems=("java",))
        self.assertIn("com.google.guava:guava", _names(report))
        with_test = self.analyze(ecosystems=("java",), include_test=True)
        self.assertIn("org.mockito:mockito-core", _names(with_test))


class DotNetTests(ParserTestCase):
    def test_csproj_and_central_versions(self):
        self.write("demo.csproj", """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.1" />
    <PackageReference Include="Serilog" />
  </ItemGroup>
</Project>
""")
        self.write("Directory.Packages.props", """<Project>
  <ItemGroup>
    <PackageVersion Include="Serilog" Version="2.12.0" />
  </ItemGroup>
</Project>
""")
        report = self.analyze(ecosystems=("dotnet",))
        self.assertEqual(_by_name(report, "Newtonsoft.Json")["specifier"],
                         "13.0.1")
        self.assertEqual(_by_name(report, "Serilog")["specifier"], "2.12.0")


class PhpTests(ParserTestCase):
    def test_composer(self):
        self.write_json("composer.json", {
            "name": "acme/demo",
            "require": {"php": ">=8.1", "monolog/monolog": "^3.0"},
            "require-dev": {"phpunit/phpunit": "^10.0"},
        })
        self.write_json("composer.lock", {
            "packages": [{"name": "monolog/monolog", "version": "3.5.0"}],
            "packages-dev": [{"name": "phpunit/phpunit",
                              "version": "10.4.0"}],
        })
        report = self.analyze(ecosystems=("php",), use_lockfile=True)
        self.assertEqual(report["project"]["name"], "acme/demo")
        monolog = _by_name(report, "monolog/monolog")
        self.assertEqual(monolog["resolved_version"], "3.5.0")
        self.assertEqual(_by_name(report, "php")["category"], "platform")
        with_dev = self.analyze(
            ecosystems=("php",), use_lockfile=True, include_dev=True
        )
        self.assertEqual(_by_name(with_dev, "phpunit/phpunit")["resolved_version"],
                         "10.4.0")


class RTests(ParserTestCase):
    def test_description(self):
        self.write("DESCRIPTION", """Package: mypkg
Version: 0.1.0
Imports: R6 (>= 2.0), glue
Suggests: testthat (>= 3.0)
""")
        self.write_json("renv.lock", {
            "Packages": {"R6": {"Version": "2.5.1"}},
        })
        report = self.analyze(ecosystems=("r",), use_lockfile=True)
        self.assertEqual(report["project"]["name"], "mypkg")
        self.assertEqual(_by_name(report, "R6")["resolved_version"], "2.5.1")
        self.assertEqual(_by_name(report, "R6")["specifier"], ">=2.0")
        self.assertNotIn("testthat", _names(report))
        with_optional = self.analyze(ecosystems=("r",), include_optional=True)
        self.assertIn("testthat", _names(with_optional))


class JuliaTests(ParserTestCase):
    def test_project_and_manifest(self):
        self.write("Project.toml", '''
name = "MyPkg"
uuid = "00000000-0000-0000-0000-000000000001"

[deps]
Example = "7876af07-990d-54b4-ab0e-23690620f79a"
JSON = "682c06a0-de6a-54ab-a142-c8b1cf79cde6"

[compat]
Example = "0.5"

[extras]
Test = "8dfed614-e22c-5e08-85e1-65c5234f0b40"
''')
        self.write("Manifest.toml", '''
[[deps.Example]]
version = "0.5.3"

[[deps.JSON]]
version = "0.21.4"
''')
        report = self.analyze(ecosystems=("julia",), use_lockfile=True)
        self.assertEqual(report["project"]["name"], "MyPkg")
        self.assertEqual(_by_name(report, "Example")["resolved_version"],
                         "0.5.3")
        self.assertEqual(_by_name(report, "Example")["specifier"], "0.5")
        self.assertIn("Test", _names(
            self.analyze(ecosystems=("julia",), include_optional=True)
        ))


class CppTests(ParserTestCase):
    def test_vcpkg(self):
        self.write_json("vcpkg.json", {
            "name": "demo",
            "dependencies": [
                "fmt",
                {"name": "boost", "version>=": "1.83.0"},
            ],
        })
        report = self.analyze(ecosystems=("cpp",))
        self.assertIn("fmt", _names(report))
        self.assertEqual(_by_name(report, "boost")["specifier"], "1.83.0")

    def test_cmake_subset(self):
        self.write("CMakeLists.txt", """
cmake_minimum_required(VERSION 3.20)
find_package(ZLIB REQUIRED)
FetchContent_Declare(googletest GIT_REPOSITORY https://example.com/gt)
""")
        report = self.analyze(ecosystems=("cpp",))
        self.assertIn("ZLIB", _names(report))
        self.assertIn("googletest", _names(report))


class RubyTests(ParserTestCase):
    def test_gemfile_and_lock(self):
        self.write("Gemfile", """
source "https://rubygems.org"
gem "rake", "~> 13.0"
group :test do
  gem "rspec"
end
""")
        self.write("Gemfile.lock", """GEM
  remote: https://rubygems.org/
  specs:
    rake (13.0.6)
    rspec (3.12.0)
      rspec-core (~> 3.12.0)
    rspec-core (3.12.1)

PLATFORMS
  ruby

DEPENDENCIES
  rake
  rspec
""")
        report = self.analyze(ecosystems=("ruby",), use_lockfile=True)
        self.assertEqual(_by_name(report, "rake")["resolved_version"], "13.0.6")
        self.assertNotIn("rspec", _names(report))
        with_dev = self.analyze(
            ecosystems=("ruby",), use_lockfile=True, include_dev=True
        )
        self.assertEqual(_by_name(with_dev, "rspec")["resolved_version"],
                         "3.12.0")


class PerlTests(ParserTestCase):
    def test_cpanfile(self):
        self.write("cpanfile", """
requires 'Moose', '2.0';
requires 'Try::Tiny';
recommends 'JSON::MaybeXS';
test_requires 'Test::More', '0.96';
""")
        report = self.analyze(ecosystems=("perl",))
        self.assertEqual(_by_name(report, "Moose")["specifier"], "2.0")
        self.assertIn("Try::Tiny", _names(report))
        self.assertNotIn("JSON::MaybeXS", _names(report))
        with_optional = self.analyze(ecosystems=("perl",), include_optional=True)
        self.assertIn("JSON::MaybeXS", _names(with_optional))


class SelectionTests(ParserTestCase):
    def test_unsupported_dart(self):
        self.write("pubspec.yaml", "name: demo\n")
        with self.assertRaises(UnsupportedProjectError) as ctx:
            self.analyze()
        self.assertIn("pubspec.yaml", str(ctx.exception))

    def test_multi_ecosystem_requires_selection(self):
        self.write("pyproject.toml", """
[project]
name = "demo"
dependencies = ["requests>=2"]
""")
        self.write_json("package.json", {"dependencies": {"left-pad": "1.0.0"}})
        with self.assertRaises(UnsupportedProjectError):
            self.analyze()
        report = self.analyze(all_ecosystems=True)
        self.assertEqual(report["project"]["ecosystems"],
                         ["javascript", "python"])

    def test_supported_ecosystems_registry(self):
        from depgraph import supported_ecosystems

        ecosystems = set(supported_ecosystems())
        for expected in ("python", "javascript", "rust", "go", "java",
                         "dotnet", "php", "r", "julia", "cpp", "ruby", "perl"):
            self.assertIn(expected, ecosystems)


class LockfileWarningTests(ParserTestCase):
    def test_javascript_no_match_warns(self):
        self.write_json("package.json", {
            "dependencies": {"left-pad": "^1.3.0", "ghost": "^1.0.0"},
        })
        self.write_json("package-lock.json", {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/left-pad": {"version": "1.3.0"},
            },
        })
        report = self.analyze(ecosystems=("javascript",), use_lockfile=True)
        self.assertEqual(_by_name(report, "left-pad")["resolved_version"], "1.3.0")
        self.assertIsNone(_by_name(report, "ghost")["resolved_version"])
        self.assertTrue(any("ghost" in warning for warning in report["warnings"]))
        codes = {diag["code"] for diag in report["diagnostics"]}
        self.assertIn("javascript.lockfile.no_match", codes)

    def test_javascript_multiple_versions_warns(self):
        self.write_json("package.json", {"dependencies": {"foo": "1"}})
        self.write_json("package-lock.json", {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/foo": {"version": "1.0.0"},
                "app/node_modules/foo": {"version": "2.0.0"},
            },
        })
        report = self.analyze(ecosystems=("javascript",), use_lockfile=True)
        self.assertIsNone(_by_name(report, "foo")["resolved_version"])
        self.assertTrue(any("multiple locked versions" in w for w in report["warnings"]))
        codes = {diag["code"] for diag in report["diagnostics"]}
        self.assertIn("javascript.lockfile.multiple_versions", codes)

    def test_rust_no_match_warns(self):
        self.write("Cargo.toml", """
[package]
name = "demo"
version = "0.1.0"

[dependencies]
serde = "1.0"
tokio = "1.35"
""")
        self.write("Cargo.lock", """
[[package]]
name = "serde"
version = "1.0.193"
""")
        report = self.analyze(ecosystems=("rust",), use_lockfile=True)
        self.assertEqual(_by_name(report, "serde")["resolved_version"], "1.0.193")
        self.assertIsNone(_by_name(report, "tokio")["resolved_version"])
        codes = {diag["code"] for diag in report["diagnostics"]}
        self.assertIn("rust.lockfile.no_match", codes)

    def test_php_platform_requirements_never_warn(self):
        self.write_json("composer.json", {
            "require": {"php": ">=8.1", "ext-json": "*",
                        "monolog/monolog": "^3.0"},
        })
        self.write_json("composer.lock", {
            "packages": [{"name": "monolog/monolog", "version": "3.5.0"}],
        })
        report = self.analyze(ecosystems=("php",), use_lockfile=True)
        self.assertEqual(_by_name(report, "php")["category"], "platform")
        self.assertEqual(_by_name(report, "ext-json")["category"], "platform")
        self.assertEqual(_by_name(report, "monolog/monolog")["resolved_version"],
                         "3.5.0")
        self.assertFalse(any("php" in w for w in report["warnings"]))
        self.assertEqual(report["diagnostics"], [])

    def test_r_base_r_is_platform(self):
        self.write("DESCRIPTION", """Package: mypkg
Version: 0.1.0
Depends: R (>= 4.0.0)
Imports: data.table (>= 1.14.8)
""")
        self.write_json("renv.lock", {
            "Packages": {"data.table": {"Version": "1.14.10"}},
        })
        report = self.analyze(ecosystems=("r",), use_lockfile=True)
        self.assertEqual(_by_name(report, "R")["category"], "platform")
        self.assertEqual(_by_name(report, "data.table")["resolved_version"],
                         "1.14.10")
        self.assertEqual(report["diagnostics"], [])


class InventoryHonestyTests(ParserTestCase):
    def test_pnpm_lock_listed_unsupported(self):
        self.write_json("package.json", {"dependencies": {"x": "1"}})
        self.write("pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        report = self.analyze(ecosystems=("javascript",), use_lockfile=True)
        items = {item["path"]: item for item in report["manifests"]}
        self.assertIn("pnpm-lock.yaml", items)
        self.assertEqual(items["pnpm-lock.yaml"]["kind"], "unsupported")
        self.assertFalse(items["pnpm-lock.yaml"]["supported"])
        self.assertEqual(report["analysis"]["lockfile_coverage"], "declared_only")
        self.assertTrue(any("not parsed" in w for w in report["warnings"]))

    def test_cpanfile_snapshot_listed_unsupported(self):
        self.write("cpanfile", "requires 'Moose', '2.0';\n")
        self.write("cpanfile.snapshot", "# snapshot\n")
        report = self.analyze(ecosystems=("perl",), use_lockfile=True)
        items = {item["path"]: item for item in report["manifests"]}
        self.assertIn("cpanfile.snapshot", items)
        self.assertEqual(items["cpanfile.snapshot"]["kind"], "unsupported")
        self.assertFalse(items["cpanfile.snapshot"]["supported"])
        self.assertNotIn("cpanfile.snapshot", report["lockfiles"])


def _package_edges(report):
    return [edge for edge in report["graph"]["edges"] if not edge["direct"]]


class TransitiveTests(ParserTestCase):
    def _npm_project(self):
        self.write_json("package.json", {
            "name": "app",
            "dependencies": {"react": "^18.2.0"},
        })
        self.write_json("package-lock.json", {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"react": "^18.2.0"}},
                "node_modules/react": {
                    "version": "18.2.0",
                    "dependencies": {"loose-envify": "^1.1.0"},
                },
                "node_modules/loose-envify": {
                    "version": "1.4.0",
                    "dependencies": {"js-tokens": "^4.0.0"},
                },
                "node_modules/js-tokens": {"version": "4.0.0"},
            },
        })

    def test_default_does_not_expand(self):
        self._npm_project()
        report = self.analyze(ecosystems=("javascript",), use_lockfile=True)
        self.assertEqual(_names(report), {"react"})
        self.assertFalse(report["analysis"]["transitive_dependencies_resolved"])
        self.assertEqual(report["analysis"]["transitive_dependency_count"], 0)
        self.assertEqual(_package_edges(report), [])
        self.assertEqual(report["analysis"]["node_count"], 2)

    def test_npm_transitive_graph(self):
        self._npm_project()
        report = self.analyze(ecosystems=("javascript",),
                              resolve_transitive=True)
        self.assertTrue(report["analysis"]["transitive_dependencies_resolved"])
        self.assertEqual(report["analysis"]["transitive_dependency_count"], 2)
        self.assertEqual(report["analysis"]["lockfile_coverage"], "full")
        self.assertEqual(_names(report), {"react", "loose-envify", "js-tokens"})
        self.assertEqual(_by_name(report, "react")["resolved_version"], "18.2.0")
        self.assertFalse(_by_name(report, "loose-envify")["direct"])
        self.assertEqual(_by_name(report, "loose-envify")["resolved_version"],
                         "1.4.0")
        edges = {(e["from"], e["to"]) for e in _package_edges(report)}
        self.assertIn(("package:javascript:react",
                       "package:javascript:loose-envify"), edges)
        self.assertIn(("package:javascript:loose-envify",
                       "package:javascript:js-tokens"), edges)
        self.assertEqual(report["analysis"]["node_count"], 4)
        self.assertEqual(report["analysis"]["edge_count"], 3)

    def test_npm_cycle_terminates(self):
        self.write_json("package.json", {"dependencies": {"a": "1"}})
        self.write_json("package-lock.json", {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"a": "1"}},
                "node_modules/a": {"version": "1.0.0",
                                   "dependencies": {"b": "1"}},
                "node_modules/b": {"version": "1.0.0",
                                   "dependencies": {"a": "1"}},
            },
        })
        report = self.analyze(ecosystems=("javascript",),
                              resolve_transitive=True)
        self.assertEqual(_names(report), {"a", "b"})
        self.assertEqual(report["analysis"]["transitive_dependency_count"], 1)

    def test_multiple_versions_ambiguous_root_not_expanded(self):
        self.write_json("package.json", {"dependencies": {"foo": "1"}})
        self.write_json("package-lock.json", {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/foo": {"version": "1.0.0",
                                     "dependencies": {"bar": "1"}},
                "app/node_modules/foo": {"version": "2.0.0",
                                         "dependencies": {"baz": "1"}},
                "node_modules/bar": {"version": "1.0.0"},
                "app/node_modules/baz": {"version": "1.0.0"},
            },
        })
        report = self.analyze(ecosystems=("javascript",),
                              resolve_transitive=True)
        self.assertNotIn("bar", _names(report))
        self.assertNotIn("baz", _names(report))
        self.assertTrue(any("multiple locked versions" in w
                            for w in report["warnings"]))

    def test_cargo_transitive(self):
        self.write("Cargo.toml", """
[package]
name = "demo"
version = "0.1.0"

[dependencies]
serde = "1"
""")
        self.write("Cargo.lock", """
version = 3

[[package]]
name = "demo"
version = "0.1.0"
dependencies = ["serde"]

[[package]]
name = "serde"
version = "1.0.193"
dependencies = ["serde_derive"]

[[package]]
name = "serde_derive"
version = "1.0.193"
""")
        report = self.analyze(ecosystems=("rust",), resolve_transitive=True)
        self.assertTrue(report["analysis"]["transitive_dependencies_resolved"])
        self.assertEqual(_names(report), {"serde", "serde_derive"})
        self.assertEqual(_by_name(report, "serde_derive")["resolved_version"],
                         "1.0.193")
        self.assertFalse(_by_name(report, "serde_derive")["direct"])
        edges = _package_edges(report)
        self.assertEqual([(e["from"], e["to"]) for e in edges],
                         [("package:rust:serde", "package:rust:serde_derive")])

    def test_composer_transitive_skips_platform(self):
        self.write_json("composer.json", {
            "require": {"php": ">=8.1", "monolog/monolog": "^3.0"},
        })
        self.write_json("composer.lock", {
            "packages": [
                {"name": "monolog/monolog", "version": "3.5.0",
                 "require": {"php": ">=8.1", "psr/log": "^2.0 || ^3.0",
                             "ext-json": "*"}},
                {"name": "psr/log", "version": "3.0.0",
                 "require": {"php": ">=8.0"}},
            ],
        })
        report = self.analyze(ecosystems=("php",), resolve_transitive=True)
        self.assertEqual(_names(report), {"php", "monolog/monolog", "psr/log"})
        self.assertEqual(_by_name(report, "psr/log")["resolved_version"],
                         "3.0.0")
        edges = _package_edges(report)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "package:php:psr/log")
        self.assertNotIn("ext-json", _names(report))

    def test_gemfile_transitive(self):
        self.write("Gemfile", 'gem "rspec"\n')
        self.write("Gemfile.lock", """GEM
  remote: https://rubygems.org/
  specs:
    rspec (3.12.0)
      rspec-core (~> 3.12.0)
    rspec-core (3.12.1)
      rspec-support (~> 3.12.0)
    rspec-support (3.12.1)

PLATFORMS
  ruby

DEPENDENCIES
  rspec
""")
        report = self.analyze(ecosystems=("ruby",), resolve_transitive=True)
        self.assertEqual(_names(report),
                         {"rspec", "rspec-core", "rspec-support"})
        self.assertFalse(_by_name(report, "rspec-support")["direct"])
        edges = {(e["from"], e["to"]) for e in _package_edges(report)}
        self.assertIn(("package:ruby:rspec-core",
                       "package:ruby:rspec-support"), edges)

    def test_renv_transitive(self):
        self.write("DESCRIPTION", """Package: mypkg
Version: 0.1.0
Depends: R (>= 4.0.0)
Imports: ggplot2
""")
        self.write_json("renv.lock", {
            "Packages": {
                "ggplot2": {"Version": "3.4.4",
                            "Imports": ["cli", "glue"], "Depends": ["R"]},
                "cli": {"Version": "3.6.1"},
                "glue": {"Version": "1.6.2", "Imports": ["methods"]},
            },
        })
        report = self.analyze(ecosystems=("r",), resolve_transitive=True)
        self.assertEqual(_names(report), {"R", "ggplot2", "cli", "glue"})
        edges = {(e["from"], e["to"]) for e in _package_edges(report)}
        self.assertIn(("package:r:ggplot2", "package:r:cli"), edges)
        self.assertIn(("package:r:ggplot2", "package:r:glue"), edges)

    def test_julia_transitive_by_name(self):
        self.write("Project.toml", 'name = "Sig"\nuuid = "x"\n\n[deps]\nDSP = "aaaa"\n')
        self.write("Manifest.toml", """
julia_version = "1.10.0"

[[deps.DSP]]
deps = ["FFTW"]
uuid = "aaaa"
version = "0.7.8"

[[deps.FFTW]]
deps = ["LinearAlgebra"]
uuid = "bbbb"
version = "1.5.0"

[[deps.LinearAlgebra]]
uuid = "cccc"
version = "1.10.0"
""")
        report = self.analyze(ecosystems=("julia",), resolve_transitive=True)
        self.assertEqual(_names(report),
                         {"DSP", "FFTW", "LinearAlgebra"})
        self.assertEqual(_by_name(report, "FFTW")["resolved_version"], "1.5.0")
        edges = {(e["from"], e["to"]) for e in _package_edges(report)}
        self.assertIn(("package:julia:dsp", "package:julia:fftw"), edges)
        self.assertIn(("package:julia:fftw",
                       "package:julia:linearalgebra"), edges)

    def test_transitive_unavailable_without_lockfile(self):
        self.write_json("package.json", {"dependencies": {"x": "1"}})
        report = self.analyze(ecosystems=("javascript",),
                              resolve_transitive=True)
        codes = {diag["code"] for diag in report["diagnostics"]}
        self.assertIn("javascript.transitive.unavailable", codes)
        self.assertFalse(report["analysis"]["transitive_dependencies_resolved"])

    def test_go_transitive_unavailable(self):
        self.write("go.mod", "module example.com/x\n\ngo 1.21\n")
        self.write("go.sum", "github.com/pkg/errors v0.9.1 h1:abc=\n")
        report = self.analyze(ecosystems=("go",), resolve_transitive=True)
        codes = {diag["code"] for diag in report["diagnostics"]}
        self.assertIn("go.transitive.unavailable", codes)
        self.assertFalse(report["analysis"]["transitive_dependencies_resolved"])

    def test_coverage_partial_when_declared_unmatched(self):
        self.write_json("package.json", {
            "name": "app",
            "dependencies": {"react": "^18.2.0", "ghost": "^1.0.0"},
        })
        self.write_json("package-lock.json", {
            "name": "app",
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"react": "^18.2.0",
                                      "ghost": "^1.0.0"}},
                "node_modules/react": {
                    "version": "18.2.0",
                    "dependencies": {"loose-envify": "^1.1.0"},
                },
                "node_modules/loose-envify": {"version": "1.4.0"},
            },
        })
        report = self.analyze(ecosystems=("javascript",),
                              resolve_transitive=True)
        # The lockfile graph is parseable, so transitives are still resolved...
        self.assertTrue(report["analysis"]["transitive_dependencies_resolved"])
        self.assertEqual(report["analysis"]["transitive_dependency_count"], 1)
        # ...but a declared dependency has no lock entry, so coverage is not
        # "full": it must not overstate what the lockfile actually covers.
        ghost = _by_name(report, "ghost")
        self.assertTrue(ghost["direct"])
        self.assertIsNone(ghost["resolved_version"])
        self.assertEqual(report["analysis"]["lockfile_coverage"], "partial")
        self.assertTrue(any("no matching entry" in w
                            for w in report["warnings"]))

    def test_composer_php_http_is_not_platform(self):
        # "php-http/discovery" starts with "php-" but has a vendor slash, so it
        # is a real package and must not be misclassified as a platform
        # requirement. It must be preserved in the transitive graph.
        self.write_json("composer.json", {
            "name": "acme/app",
            "require": {"php-http/guzzle7-adapter": "^1.0", "php": "^8.1"},
        })
        self.write_json("composer.lock", {
            "packages": [
                {"name": "php-http/guzzle7-adapter", "version": "1.1.0",
                 "require": {"php": "^8.0",
                             "php-http/discovery": "^1.14",
                             "guzzlehttp/guzzle": "^7.0"}},
                {"name": "php-http/discovery", "version": "1.19.2",
                 "require": {"php": "^7.1 || ^8.0",
                             "psr/http-client-implementation": "*"}},
                {"name": "guzzlehttp/guzzle", "version": "7.8.1",
                 "require": {"php": "^8.0", "psr/http-client": "^1.0.2"}},
                {"name": "psr/http-client", "version": "1.0.3",
                 "require": {"php": "^7.0 || ^8.0",
                             "psr/http-message": "^1.0 || ^2.0"}},
                {"name": "psr/http-message", "version": "2.0.0",
                 "require": {"php": ">=7.2"}},
            ],
        })
        report = self.analyze(ecosystems=("php",), resolve_transitive=True)
        discovery = _by_name(report, "php-http/discovery")
        self.assertEqual(discovery["category"], "runtime")
        self.assertFalse(discovery["direct"])
        self.assertEqual(discovery["resolved_version"], "1.19.2")
        self.assertEqual(_by_name(report, "php")["category"], "platform")
        self.assertEqual(report["analysis"]["lockfile_coverage"], "full")
        edges = {(e["from"], e["to"]) for e in _package_edges(report)}
        self.assertIn(
            ("package:php:php-http/guzzle7-adapter",
             "package:php:php-http/discovery"),
            edges,
        )



if __name__ == "__main__":
    unittest.main()
