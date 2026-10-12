import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import depgraph


DEPGRAPH = Path(__file__).resolve().parent.parent / "depgraph.py"


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(DEPGRAPH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


class DepGraphTests(unittest.TestCase):
    def test_normalize_package_name(self):
        self.assertEqual(
            depgraph.normalize_name("My_Package.Name"),
            "my-package-name",
        )

    def test_parse_requirement(self):
        result = depgraph.parse_requirement(
            'Requests[security]>=2.0; python_version >= "3.10"'
        )
        self.assertEqual(result["normalized_name"], "requests")
        self.assertEqual(result["extras"], ["security"])
        self.assertEqual(result["specifier"], ">=2.0")
        self.assertEqual(
            result["marker"],
            'python_version >= "3.10"',
        )

    def test_comments_and_blank_lines(self):
        self.assertIsNone(depgraph.parse_requirement(""))
        self.assertIsNone(depgraph.parse_requirement("# comment"))

    def test_duplicate_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "Requests>=2\nrequests>=2\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )

    def test_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "httpx>=0.27\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            encoded = json.dumps(report)
            self.assertEqual(json.loads(encoded)["tool"], "DepGraph")

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                depgraph.analyze(Path(tmp) / "missing")

    def test_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\n'
                'name = "sample"\n'
                'version = "0.1.0"\n'
                'dependencies = ["httpx>=0.27", "packaging>=24"]\n',
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(report["project"]["manifest"], "pyproject.toml")
            self.assertEqual(
                report["analysis"]["dependency_count"], 2
            )

    # -- Parsing / validation ------------------------------------------------

    def test_invalid_specifiers_rejected(self):
        for text in (
            "validpkg !!!",
            "requests>>2",
            "requests>=",
            "requests ==",
            "requests @",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    depgraph.parse_requirement(text)

    def test_valid_specifiers_accepted(self):
        single = {
            "pkg>=2.0": ">=2.0",
            "pkg~=1.4.2": "~=1.4.2",
            "pkg==1.0.*": "==1.0.*",
            "pkg!=2.0": "!=2.0",
            "pkg>=1.0.0rc1": ">=1.0.0rc1",
            "pkg==1.0.post1": "==1.0.post1",
            "pkg===lore": "===lore",
        }
        for text, expected in single.items():
            with self.subTest(text=text):
                self.assertEqual(
                    depgraph.parse_requirement(text)["specifier"], expected
                )

    def test_comma_separated_constraints(self):
        result = depgraph.parse_requirement("pkg>=1.0,<2.0")
        self.assertIn(">=1.0", result["specifier"])
        self.assertIn("<2.0", result["specifier"])

    def test_local_version_constraint(self):
        result = depgraph.parse_requirement("pkg==1.0+local.1")
        self.assertEqual(result["specifier"], "==1.0+local.1")

    def test_local_version_with_ordering_operator_rejected(self):
        # PEP 440 forbids local version labels with ordering operators.
        with self.assertRaises(ValueError):
            depgraph.parse_requirement("pkg>=2.0+local.1")

    def test_direct_reference(self):
        result = depgraph.parse_requirement(
            "pkg @ https://example.com/pkg.whl"
        )
        self.assertEqual(result["url"], "https://example.com/pkg.whl")
        self.assertIsNone(result["specifier"])

    def test_extras_are_normalized_and_deduplicated(self):
        result = depgraph.parse_requirement("Pkg[Foo_Bar,foo-bar,BAZ]")
        self.assertEqual(result["extras"], ["baz", "foo-bar"])

    def test_marker_compound_expression(self):
        result = depgraph.parse_requirement(
            "pkg; python_version >= '3.10' and sys_platform == 'linux'"
        )
        self.assertEqual(
            result["marker"],
            'python_version >= "3.10" and sys_platform == "linux"',
        )

    def test_invalid_package_names_rejected(self):
        for name in ("pkg.", "pkg-", "pkg_"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    depgraph.parse_requirement(name)

    def test_non_string_requirement_raises_valueerror(self):
        with self.assertRaises(ValueError):
            depgraph.parse_requirement(123)

    def test_inline_comment_stripped(self):
        result = depgraph.parse_requirement("pkg>=1.0  # pinned")
        self.assertEqual(result["specifier"], ">=1.0")

    def test_url_fragment_preserved(self):
        result = depgraph.parse_requirement(
            "pkg @ https://example.com/pkg.whl#sha256=abc"
        )
        self.assertEqual(
            result["url"], "https://example.com/pkg.whl#sha256=abc"
        )

    # -- Manifest handling ---------------------------------------------------

    def test_invalid_requirement_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "valid-package>=1.0\n"
                "this is not a valid requirement !!!\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )
            self.assertTrue(report["warnings"])
            self.assertIn("line 2", report["warnings"][0])

    def test_invalid_pyproject_dependency_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\n'
                'name = "sample"\n'
                'version = "0.1.0"\n'
                'dependencies = [123]\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                depgraph.analyze(project)

    def test_requirements_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "sample-package>=1.0,\\\n"
                " <2.0\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )
            self.assertFalse(report["warnings"])
            self.assertEqual(
                report["dependencies"][0]["specifier"],
                "<2.0,>=1.0",
            )

    def test_requirements_bom_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_bytes(
                b"\xef\xbb\xbfhttpx>=0.27\n"
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )
            self.assertEqual(
                report["dependencies"][0]["normalized_name"], "httpx"
            )

    def test_requirements_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "# a comment\n"
                "\n"
                "httpx>=0.27  # inline\n"
                "   \n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )
            self.assertFalse(report["warnings"])

    def test_directive_lines_warn_not_misparsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "-r base.txt\n"
                "--index-url https://pypi.org/simple\n"
                "-e .\n"
                "requests>=2\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )
            self.assertEqual(len(report["warnings"]), 3)

    def test_non_string_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\n'
                'name = 123\n'
                'dependencies = ["httpx>=0.27"]\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                depgraph.analyze(project)

    def test_dependencies_not_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\n'
                'name = "sample"\n'
                'dependencies = "httpx>=0.27"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                depgraph.analyze(project)

    def test_malformed_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project\nname = "x"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                depgraph.analyze(project)

    def test_unsupported_poetry_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[tool.poetry.dependencies]\n'
                'requests = "^2.31"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                depgraph.analyze(project)
            self.assertIn("poetry", str(ctx.exception).lower())

    def test_unsupported_pipfile_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "Pipfile").write_text(
                '[packages]\nrequests = "*"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                depgraph.analyze(project)
            self.assertIn("pipfile", str(ctx.exception).lower())

    def test_pyproject_masks_requirements_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\n'
                'name = "sample"\n'
                'dependencies = ["httpx>=0.27"]\n',
                encoding="utf-8",
            )
            (project / "requirements.txt").write_text(
                "requests>=2\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(report["project"]["manifest"], "pyproject.toml")
            self.assertTrue(
                any("requirements.txt" in w for w in report["warnings"])
            )

    def test_pyproject_without_deps_falls_back_to_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\nname = "sample"\n',
                encoding="utf-8",
            )
            (project / "requirements.txt").write_text(
                "requests>=2\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(report["project"]["manifest"], "requirements.txt")
            self.assertEqual(
                report["analysis"]["dependency_count"], 1
            )
            self.assertTrue(report["warnings"])

    def test_pyproject_without_deps_and_no_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\nname = "sample"\n',
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(report["project"]["manifest"], "pyproject.toml")
            self.assertEqual(
                report["analysis"]["dependency_count"], 0
            )
            self.assertTrue(report["warnings"])

    def test_optional_dependencies_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pyproject.toml").write_text(
                '[project]\n'
                'name = "sample"\n'
                'dependencies = ["httpx>=0.27"]\n'
                '[project.optional-dependencies]\n'
                'test = ["pytest>=7"]\n',
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertTrue(
                any("Optional" in w for w in report["warnings"])
            )

    # -- Analysis / graph ----------------------------------------------------

    def test_distinct_constraints_share_one_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "pkg>=1\npkg<2\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(
                report["analysis"]["dependency_count"], 2
            )
            self.assertEqual(report["analysis"]["node_count"], 2)
            self.assertEqual(report["analysis"]["edge_count"], 2)

    def test_output_is_deterministic_and_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "zeta>=1\nalpha>=2\nMid>=3\n",
                encoding="utf-8",
            )
            first = depgraph.analyze(project)
            second = depgraph.analyze(project)
            self.assertEqual(first, second)
            names = [d["normalized_name"] for d in first["dependencies"]]
            self.assertEqual(names, sorted(names))

    def test_schema_version_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "httpx>=0.27\n",
                encoding="utf-8",
            )
            report = depgraph.analyze(project)
            self.assertEqual(report["schema_version"], depgraph.SCHEMA_VERSION)

    def test_file_passed_as_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "requirements.txt"
            target.write_text("httpx>=0.27\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                depgraph.analyze(target)

    def test_nonexistent_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                depgraph.analyze(Path(tmp) / "nope")

    # -- CLI -----------------------------------------------------------------

    def test_cli_version(self):
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("DepGraph", result.stdout)

    def test_cli_help(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())

    def test_cli_json_output_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "httpx>=0.27\n", encoding="utf-8"
            )
            result = run_cli(str(project), "--format", "json")
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["tool"], "DepGraph")
            self.assertEqual(payload["schema_version"], depgraph.SCHEMA_VERSION)

    def test_cli_missing_manifest_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(tmp)
            self.assertEqual(result.returncode, 1)
            self.assertIn("DepGraph error", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_cli_nonexistent_path_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(str(Path(tmp) / "nope"))
            self.assertEqual(result.returncode, 1)
            self.assertIn("DepGraph error", result.stderr)

    def test_cli_strict_warnings_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "broken>>1\n", encoding="utf-8"
            )
            result = run_cli(str(project), "--strict")
            self.assertEqual(result.returncode, 3)

    def test_cli_unknown_option_exit_2(self):
        result = run_cli("--definitely-not-an-option")
        self.assertEqual(result.returncode, 2)

    def test_cli_quiet_suppresses_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "requirements.txt").write_text(
                "broken>>1\n", encoding="utf-8"
            )
            loud = run_cli(str(project))
            quiet = run_cli(str(project), "--quiet")
            self.assertIn("Warnings", loud.stdout)
            self.assertNotIn("Warnings", quiet.stdout)


if __name__ == "__main__":
    unittest.main()
