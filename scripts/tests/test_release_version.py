#!/usr/bin/env python3

"""Tests for templates/release-automation-rust/release-version.sh, run rather than read.

The script is the Rust release's check-version job: it decides whether
a tag names the version the tree carries, and whether Cargo.lock agrees
with it. It parses both files with awk, so a wrong parse either blocks
every release or lets a mismatched version through, and neither shows
by reading it. These run the template copy, with its placeholders
filled in, against small throwaway trees.

Run with: python3 scripts/tests/test_release_version.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO_ROOT, 'templates', 'release-automation-rust', 'release-version.sh')

WORKSPACE_TOML = '''[workspace]
members = ["crates/app"]

[workspace.package]
version = "1.2.3"
edition = "2021"

[workspace.dependencies]
serde = { version = "1.0.0" }
'''

MEMBER_TOML = '''[package]
name = "app"
version.workspace = true

[dependencies]
serde = { version = "9.9.9" }
'''

PACKAGE_TOML = '''[package]
name = "app"
version = "0.4.0"

[dependencies]
serde = { version = "9.9.9" }
'''


def lockfile(version):
    return ('version = 4\n\n'
            '[[package]]\nname = "serde"\nversion = "1.0.0"\n\n'
            '[[package]]\nname = "app"\nversion = "%s"\n' % version)


class ReleaseVersionTest(unittest.TestCase):
    def make_tree(self, files, version_file='Cargo.toml', cargo_lock='Cargo.lock', crate='app'):
        root = tempfile.mkdtemp(prefix='release-version-')
        self.addCleanup(shutil.rmtree, root)
        os.mkdir(os.path.join(root, 'tools'))
        with open(SCRIPT) as f:
            script = f.read()
        script = (script.replace('{{CARGO_TOML}}', version_file)
                  .replace('{{CARGO_LOCK}}', cargo_lock)
                  .replace('{{CRATE_NAME}}', crate))
        path = os.path.join(root, 'tools', 'release-version.sh')
        with open(path, 'w') as f:
            f.write(script)
        os.chmod(path, 0o755)
        for name, content in files.items():
            full = os.path.join(root, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w') as f:
                f.write(content)
        return root

    def run_script(self, root, *args):
        return subprocess.run([os.path.join(root, 'tools', 'release-version.sh')] + list(args),
                              capture_output=True, text=True, timeout=30)

    def test_workspace_version_is_read_not_dependency_versions(self):
        root = self.make_tree({'Cargo.toml': WORKSPACE_TOML, 'Cargo.lock': lockfile('1.2.3')})
        result = self.run_script(root, 'get')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('1.2.3', result.stdout.strip())

    def test_the_workspace_version_wins_over_a_root_package_version(self):
        toml = ('[package]\nname = "app"\nversion = "9.0.0"\n\n'
                '[workspace]\nmembers = ["."]\n\n[workspace.package]\nversion = "1.2.3"\n')
        root = self.make_tree({'Cargo.toml': toml, 'Cargo.lock': lockfile('1.2.3')})
        self.assertEqual('1.2.3', self.run_script(root, 'get').stdout.strip())

    def test_package_version_is_read(self):
        root = self.make_tree({'Cargo.toml': PACKAGE_TOML, 'Cargo.lock': lockfile('0.4.0')})
        self.assertEqual('0.4.0', self.run_script(root, 'get').stdout.strip())

    def test_an_inherited_version_is_not_a_version(self):
        root = self.make_tree({'Cargo.toml': MEMBER_TOML, 'Cargo.lock': ''})
        result = self.run_script(root, 'get')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('no [workspace.package] or [package] version', result.stderr)

    def test_check_passes_on_a_matching_tag(self):
        root = self.make_tree({'Cargo.toml': WORKSPACE_TOML, 'Cargo.lock': lockfile('1.2.3')})
        result = self.run_script(root, 'check', 'refs/tags/v1.2.3')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('matches the version', result.stdout)

    def test_check_fails_on_a_mismatched_tag(self):
        root = self.make_tree({'Cargo.toml': WORKSPACE_TOML, 'Cargo.lock': lockfile('1.2.3')})
        result = self.run_script(root, 'check', 'refs/tags/v1.2.4')
        self.assertEqual(1, result.returncode)
        self.assertIn('Version mismatch', result.stdout)

    def test_check_fails_when_the_lockfile_disagrees(self):
        root = self.make_tree({'Cargo.toml': WORKSPACE_TOML, 'Cargo.lock': lockfile('1.2.2')})
        for ref in ('refs/tags/v1.2.3', 'refs/heads/develop'):
            result = self.run_script(root, 'check', ref)
            self.assertNotEqual(0, result.returncode, ref)
            self.assertIn('has app 1.2.2', result.stderr)

    def test_check_passes_a_branch_ref_once_the_files_agree(self):
        root = self.make_tree({'Cargo.toml': WORKSPACE_TOML, 'Cargo.lock': lockfile('1.2.3')})
        result = self.run_script(root, 'check', 'refs/heads/develop')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('Not a release tag', result.stdout)

    def test_check_without_a_lockfile(self):
        root = self.make_tree({'Cargo.toml': PACKAGE_TOML}, cargo_lock='', crate='')
        self.assertEqual(0, self.run_script(root, 'check', 'refs/tags/v0.4.0').returncode)

    def test_set_then_get_round_trips_and_touches_only_the_version(self):
        for toml, table in ((WORKSPACE_TOML, '[workspace.package]'), (PACKAGE_TOML, '[package]')):
            root = self.make_tree({'Cargo.toml': toml, 'Cargo.lock': ''})
            result = self.run_script(root, 'set', '2.0.0')
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('2.0.0', self.run_script(root, 'get').stdout.strip())
            with open(os.path.join(root, 'Cargo.toml')) as f:
                after = f.read()
            changed = [(a, b) for a, b in zip(toml.splitlines(), after.splitlines()) if a != b]
            self.assertEqual(1, len(changed), table)
            self.assertIn('2.0.0', changed[0][1])

    def test_set_refuses_a_malformed_version(self):
        root = self.make_tree({'Cargo.toml': PACKAGE_TOML, 'Cargo.lock': ''})
        result = self.run_script(root, 'set', 'v2.0')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('must be X.Y.Z', result.stderr)


if __name__ == '__main__':
    unittest.main()
