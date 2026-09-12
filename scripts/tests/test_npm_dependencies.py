#!/usr/bin/env python3

"""Tests for audit/checks/npm_dependencies.py.

Run with: python3 -m unittest discover -s scripts/tests -t scripts
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import npm_dependencies  # noqa: E402
from tests.base import CheckTestCase  # noqa: E402


class NpmFixtureMixin:
    """Writing the two files every one of these criteria reads."""

    def manifest(self, **fields):
        self.fixture.write('package.json',
                           json.dumps(fields, indent=2) + '\n')

    def lockfile(self, packages=(), version=3, **extra):
        """A lockfileVersion 2/3 style lockfile.

        `packages` is a list of names, or of (name, bin names) pairs,
        written as the flat install-path map npm 7 and later produce.
        """
        entries = {'': {'name': 'x'}}
        for package in packages:
            binaries = ()
            if isinstance(package, tuple):
                package, binaries = package
            entry = {'version': '1.0.0'}
            if binaries:
                entry['bin'] = {name: 'bin/%s' % name for name in binaries}
            entries['node_modules/%s' % package] = entry
        document = {'name': 'x', 'lockfileVersion': version,
                    'packages': entries}
        document.update(extra)
        self.fixture.write('package-lock.json',
                           json.dumps(document, indent=2) + '\n')

    def source(self, content, path='src/main.ts'):
        self.fixture.write(path, content)


class NpmPinIndirectDependenciesTest(NpmFixtureMixin, CheckTestCase):
    check_class = npm_dependencies.NpmPinIndirectDependencies

    def test_without_a_package_json_it_does_not_apply(self):
        """Every repository in the fleet but one, today."""
        self.assert_skip(self.check(), containing='No package.json')

    def test_an_unreadable_manifest_does_not_apply(self):
        self.fixture.write('package.json', '{ this is not json\n')
        self.assert_skip(self.check(), containing='not readable JSON')

    def test_a_manifest_declaring_nothing_does_not_apply(self):
        self.manifest(name='x')
        self.assert_skip(self.check(), containing='no transitive tree')

    def test_another_package_manager_does_not_apply(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.fixture.write('pnpm-lock.yaml', 'lockfileVersion: 9.0\n')
        self.assert_skip(self.check(), containing='pnpm-lock.yaml')

    def test_a_missing_lockfile_fails(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.assert_fail(self.check(), containing='No package-lock.json')

    def test_a_committed_current_lockfile_passes(self):
        self.manifest(name='x', devDependencies={'typescript': '^5.3.0'})
        self.lockfile(['typescript'])
        result = self.assert_pass(self.check())
        self.assertIn('pins all 1 resolved package', result['details'])

    def test_an_unparseable_lockfile_fails(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.fixture.write('package-lock.json', '{ nope\n')
        self.assert_fail(self.check(), containing='not readable JSON')

    def test_a_version_one_lockfile_fails(self):
        """npm 7 rewrites it on contact, so its pins are a snapshot."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(version=1)
        self.assert_fail(self.check(), containing='lockfileVersion 1')

    def test_an_uncommitted_lockfile_fails(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.init_git()
        self.fixture.write('.gitignore', 'package-lock.json\n')
        self.fixture.commit()
        self.assert_fail(self.check(), containing='is not committed')

    def test_a_committed_lockfile_in_a_checkout_passes(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.init_git()
        self.fixture.commit()
        self.assert_pass(self.check())

    def test_a_directory_that_is_not_a_checkout_is_not_reported(self):
        """git saying nothing is not git saying the lockfile is absent."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.assert_pass(self.check())

    def test_a_workflow_running_npm_install_fails(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.workflow('ci.yml', 'jobs:\n  b:\n    steps:\n'
                                        '      - run: npm install\n')
        self.assert_fail(self.check(), containing='npm install')

    def test_a_workflow_running_npm_ci_passes(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.workflow('ci.yml', 'jobs:\n  b:\n    steps:\n'
                                        '      - run: npm ci\n'
                                        '      - run: npm run build\n')
        self.assert_pass(self.check())

    def test_a_global_install_is_not_a_project_install(self):
        """Installing a tool beside the project touches no lockfile."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.workflow(
            'agent.yml',
            'jobs:\n  b:\n    steps:\n'
            '      - run: npm install -g @anthropic-ai/claude-code\n')
        self.assert_pass(self.check())

    def test_a_lockfile_refresh_is_not_an_install(self):
        """--package-lock-only writes the lockfile and installs nothing."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.workflow(
            'bump.yml',
            'jobs:\n  b:\n    steps:\n'
            '      - run: npm install --package-lock-only\n')
        self.assert_pass(self.check())

    def test_a_commented_out_install_is_not_an_install(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.workflow('ci.yml', 'jobs:\n  b:\n    steps:\n'
                                        '      # was: npm install\n'
                                        '      - run: npm ci\n')
        self.assert_pass(self.check())


class NpmUnusedDeclaredDependencyTest(NpmFixtureMixin, CheckTestCase):
    check_class = npm_dependencies.NpmUnusedDeclaredDependency

    def test_without_a_package_json_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No package.json')

    def test_a_manifest_declaring_nothing_does_not_apply(self):
        self.manifest(name='x')
        self.lockfile()
        self.assert_skip(self.check(), containing='nothing')

    def test_a_workspace_root_does_not_apply(self):
        self.manifest(name='x', workspaces=['packages/*'],
                      dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.assert_skip(self.check(), containing='workspaces')

    def test_without_a_lockfile_it_does_not_apply(self):
        """Without it, every tool run by its command name looks unused."""
        self.manifest(name='x', devDependencies={'typescript': '^5.3.0'},
                      scripts={'build': 'tsc -p .'})
        self.assert_skip(self.check(), containing='package-lock.json')

    def test_an_imported_dependency_passes(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("import leftPad from 'left-pad';\n")
        self.assert_pass(self.check())

    def test_an_unused_dependency_fails_and_names_its_line(self):
        self.manifest(name='x',
                      dependencies={'left-pad': '^1.3.0',
                                    'semver': '^7.6.0'})
        self.lockfile(['left-pad', 'semver'])
        self.source("import leftPad from 'left-pad';\n")
        result = self.assert_fail(self.check(), containing='semver')
        self.assertIn('package.json:', result['details'])
        self.assertNotIn('left-pad', result['details'])

    def test_a_subpath_import_counts_as_a_use(self):
        self.manifest(name='x', dependencies={'lodash': '^4.17.21'})
        self.lockfile(['lodash'])
        self.source("import debounce from 'lodash/debounce';\n")
        self.assert_pass(self.check())

    def test_a_scoped_package_is_matched_whole(self):
        self.manifest(name='x', dependencies={'@scope/thing': '^1.0.0'})
        self.lockfile(['@scope/thing'])
        self.source("import { a } from '@scope/thing/deep';\n")
        self.assert_pass(self.check())

    def test_a_require_counts_as_a_use(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("const pad = require('left-pad');\n", path='tools/x.js')
        self.assert_pass(self.check())

    def test_a_type_only_import_counts_as_a_use(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("import type { Pad } from 'left-pad';\n")
        self.assert_pass(self.check())

    def test_a_types_package_is_read_by_the_compiler_not_imported(self):
        self.manifest(name='x', devDependencies={'@types/node': '^20.11.0'})
        self.lockfile(['@types/node'])
        self.source("export const a = 1;\n")
        result = self.assert_pass(self.check())
        self.assertIn('@types packages', result['details'])

    def test_a_binary_named_in_a_script_counts_as_a_use(self):
        """typescript is what puts tsc on the path, and nothing says so."""
        self.manifest(name='x', devDependencies={'typescript': '^5.3.0'},
                      scripts={'build': 'tsc -p .'})
        self.lockfile([('typescript', ['tsc', 'tsserver'])])
        self.source("export const a = 1;\n")
        self.assert_pass(self.check())

    def test_a_package_named_in_a_script_counts_as_a_use(self):
        self.manifest(name='x', devDependencies={'@biomejs/biome': '^2.5.13'},
                      scripts={'lint': 'npx @biomejs/biome check .'})
        self.lockfile(['@biomejs/biome'])
        self.source("export const a = 1;\n")
        self.assert_pass(self.check())

    def test_a_package_named_in_a_config_file_counts_as_a_use(self):
        """A postcss plugin is keyed by name and never imported."""
        self.manifest(name='x', devDependencies={'autoprefixer': '^10.4.0'})
        self.lockfile(['autoprefixer'])
        self.fixture.write(
            'postcss.config.js',
            "module.exports = { plugins: { autoprefixer: {} } };\n")
        self.source("export const a = 1;\n")
        self.assert_pass(self.check())

    def test_a_package_named_in_a_workflow_counts_as_a_use(self):
        self.manifest(name='x', devDependencies={'@vscode/vsce': '^3.0.0'})
        self.lockfile(['@vscode/vsce'])
        self.fixture.workflow('release.yml',
                              'jobs:\n  r:\n    steps:\n'
                              '      - run: npx @vscode/vsce publish\n')
        self.source("export const a = 1;\n")
        self.assert_pass(self.check())

    def test_the_declaration_itself_is_not_evidence_of_use(self):
        """package.json names every dependency; that is not a mention."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("export const a = 1;\n")
        self.assert_fail(self.check(), containing='left-pad')

    def test_the_lockfile_is_not_evidence_of_use(self):
        """It names every resolved package, which is all of them."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("export const a = 1;\n")
        self.assert_fail(self.check(), containing='left-pad')

    def test_a_commented_out_import_is_not_a_use(self):
        """The precise shape this criterion exists to find."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("// import leftPad from 'left-pad';\n"
                    "export const a = 1;\n")
        self.assert_fail(self.check(), containing='left-pad')

    def test_an_import_in_a_block_comment_is_not_a_use(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("/**\n * import leftPad from 'left-pad';\n */\n"
                    "export const a = 1;\n")
        self.assert_fail(self.check(), containing='left-pad')

    def test_a_copy_in_build_output_is_not_a_use(self):
        """out/ is a compiled copy of what the source said last build."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.fixture.write('tsconfig.json',
                           '{"compilerOptions": {"outDir": "out"}}\n')
        self.source("export const a = 1;\n")
        self.source("import leftPad from 'left-pad';\n",
                    path='out/src/main.js')
        self.assert_fail(self.check(), containing='left-pad')

    def test_a_copy_in_node_modules_is_not_a_use(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("export const a = 1;\n")
        self.source("import leftPad from 'left-pad';\n",
                    path='node_modules/other/index.js')
        self.assert_fail(self.check(), containing='left-pad')

    def test_peer_dependencies_are_not_read(self):
        """A peer dependency is a statement about the consumer."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'},
                      peerDependencies={'react': '^18.0.0'})
        self.lockfile(['left-pad'])
        self.source("import leftPad from 'left-pad';\n")
        self.assert_pass(self.check())

    def test_an_annotation_with_a_reason_exempts_it(self):
        self.manifest(
            name='x', dependencies={'left-pad': '^1.3.0'},
            shakenfistAudit={'notImported': {
                'left-pad': 'loaded by name through the plugin resolver'}})
        self.lockfile(['left-pad'])
        self.source("export const a = 1;\n")
        result = self.assert_pass(self.check())
        self.assertIn('annotated', result['details'])

    def test_an_annotation_without_a_reason_does_not_exempt(self):
        """An unexplained exception is a finding that was silenced."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'},
                      shakenfistAudit={'notImported': {'left-pad': ''}})
        self.lockfile(['left-pad'])
        self.source("export const a = 1;\n")
        self.assert_fail(self.check(), containing='left-pad')


class NpmUndeclaredDirectDependencyTest(NpmFixtureMixin, CheckTestCase):
    check_class = npm_dependencies.NpmUndeclaredDirectDependency

    def test_without_a_package_json_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No package.json')

    def test_without_a_lockfile_it_does_not_apply(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.source("import leftPad from 'left-pad';\n")
        self.assert_skip(self.check(), containing='no resolved tree')

    def test_a_lockfile_resolving_only_what_is_declared_does_not_apply(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad'])
        self.source("import leftPad from 'left-pad';\n")
        self.assert_skip(self.check(), containing='no transitive packages')

    def test_a_project_with_no_source_does_not_apply(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'wrapt'])
        self.assert_skip(self.check(), containing='No JavaScript')

    def test_a_workspace_root_does_not_apply(self):
        self.manifest(name='x', workspaces=['packages/*'],
                      dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'wrapt'])
        self.source("import wrapt from 'wrapt';\n")
        self.assert_skip(self.check(), containing='workspaces')

    def test_an_unimported_transitive_package_passes(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import leftPad from 'left-pad';\n")
        result = self.assert_pass(self.check())
        self.assertIn('one package', result['details'])

    def test_importing_a_transitive_package_fails(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import leftPad from 'left-pad';\n"
                    "import types from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')

    def test_a_type_only_import_of_a_transitive_package_fails(self):
        """The build resolves it, so the build breaks when it goes."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import type { Pool } from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')

    def test_an_export_from_a_transitive_package_fails(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("export { Pool } from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')

    def test_every_offender_is_reported_not_just_the_first(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types', 'semver'])
        self.source("import a from 'undici-types';\nimport b from 'semver';\n")
        result = self.assert_fail(self.check())
        self.assertIn('undici-types', result['details'])
        self.assertIn('semver', result['details'])

    def test_a_prefixed_builtin_is_not_a_package(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import fs from 'node:fs';\n"
                    "import assert from 'node:assert/strict';\n")
        self.assert_pass(self.check())

    def test_a_bare_builtin_is_not_a_package(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import fs from 'fs';\nimport path from 'path';\n")
        self.assert_pass(self.check())

    def test_a_declared_package_named_after_a_builtin_is_matched(self):
        """npm really does carry a package called path."""
        self.manifest(name='x', dependencies={'path': '^0.12.7'})
        self.lockfile(['path', 'undici-types'])
        self.source("import path from 'path';\n")
        self.assert_pass(self.check())

    def test_a_host_provided_module_is_not_a_package(self):
        """Declaring vscode installs a placeholder and breaks the build."""
        self.manifest(name='x', engines={'vscode': '^1.85.0'},
                      devDependencies={'typescript': '^5.3.0'})
        self.lockfile(['typescript', 'vscode'])
        self.source("import * as vscode from 'vscode';\n")
        self.assert_pass(self.check())

    def test_node_in_engines_does_not_exempt_a_package(self):
        """engines.node is a version floor, not a module the host injects."""
        self.manifest(name='x', engines={'node': '>=20'},
                      dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'node'])
        self.source("import node from 'node';\n")
        self.assert_fail(self.check(), containing='node')

    def test_relative_imports_are_not_packages(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import { a } from './diff';\n"
                    "import { b } from '../src/recount';\n"
                    "import { c } from '#internal/thing';\n")
        self.assert_pass(self.check())

    def test_a_commented_out_import_does_not_fail(self):
        """Here a comment read as code would be a false failure."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("// import types from 'undici-types';\n"
                    "export const a = 1;\n")
        self.assert_pass(self.check())

    def test_a_regular_expression_does_not_hide_a_later_import(self):
        """A regex holding // must not be read as a line comment."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("const url = /^https:\\/\\/x/;\n"
                    "import types from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')

    def test_a_division_does_not_hide_a_later_import(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("const half = width / 2;\n"
                    "import types from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')

    def test_a_quote_inside_a_comment_does_not_swallow_an_import(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("// don't read this\n"
                    "import types from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')

    def test_an_import_resolving_to_nothing_is_not_reported(self):
        """A tsconfig paths alias is resolver configuration we do not read."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.lockfile(['left-pad', 'undici-types'])
        self.source("import { helper } from '@app/helpers';\n")
        self.assert_pass(self.check())

    def test_a_workspace_link_is_not_a_transitive_package(self):
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.fixture.write('package-lock.json', json.dumps({
            'name': 'x', 'lockfileVersion': 3,
            'packages': {
                '': {'name': 'x'},
                'node_modules/left-pad': {'version': '1.3.0'},
                'node_modules/sibling': {'resolved': 'packages/sibling',
                                         'link': True},
            },
        }, indent=2))
        self.source("import s from 'sibling';\n")
        self.assert_skip(self.check(), containing='no transitive packages')

    def test_a_version_one_lockfile_is_still_read(self):
        """Answering "nothing is resolved" would disable the criterion."""
        self.manifest(name='x', dependencies={'left-pad': '^1.3.0'})
        self.fixture.write('package-lock.json', json.dumps({
            'name': 'x', 'lockfileVersion': 1,
            'dependencies': {
                'left-pad': {'version': '1.3.0'},
                'undici-types': {'version': '6.21.0'},
            },
        }, indent=2))
        self.source("import types from 'undici-types';\n")
        self.assert_fail(self.check(), containing='undici-types')


class SpecificationTest(unittest.TestCase):
    """The specifier reader, which is where the judgment calls live."""

    def test_a_relative_specifier_is_not_a_package(self):
        for specifier in ('./diff', '../src/recount', '/abs/path',
                          '#internal'):
            self.assertIsNone(
                npm_dependencies.specifier_package(specifier), specifier)

    def test_a_scheme_is_not_a_package(self):
        for specifier in ('node:fs', 'node:assert/strict', 'bun:test',
                          'data:text/javascript,0', 'https://x/y.js'):
            self.assertIsNone(
                npm_dependencies.specifier_package(specifier), specifier)

    def test_a_subpath_is_trimmed_to_the_package(self):
        self.assertEqual(
            npm_dependencies.specifier_package('lodash/debounce'), 'lodash')

    def test_a_scoped_package_keeps_its_scope(self):
        self.assertEqual(
            npm_dependencies.specifier_package('@scope/thing/deep'),
            '@scope/thing')

    def test_a_bare_builtin_comes_back_as_itself(self):
        """The caller subtracts the builtins, once it knows the manifest."""
        self.assertEqual(npm_dependencies.specifier_package('fs'), 'fs')


if __name__ == '__main__':
    unittest.main()
