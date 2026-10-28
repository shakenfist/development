#!/usr/bin/env python3

"""Tests for audit/checks/release_rust.py.

Run with: python3 scripts/tests/test_release_rust.py
"""

import os
import re
import shutil
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.check import ToolFailure  # noqa: E402
from audit.checks import release, release_rust  # noqa: E402
from tests.base import REPO_ROOT, CheckTestCase, repo_text  # noqa: E402


TEMPLATE_DIR = os.path.join(REPO_ROOT, 'templates', 'release-automation-rust')

#: The template's release.yml exactly as shipped, placeholders and all.
TEMPLATE = repo_text('templates', 'release-automation-rust', 'release.yml')

#: The same, as a repository fills it in. Only the gate's workflow list
#: is a placeholder, and the trigger rule reads a name with no workflow
#: behind it as a gate that can never pass.
FILLED = TEMPLATE.replace('{{CI_WORKFLOWS}}', 'ci.yml')

#: The workflow the filled-in gate names, triggered so the gate counts it.
CI_WORKFLOW = (
    'on:\n'
    '  pull_request:\n'
    '  merge_group:\n'
    'permissions: {}\n'
    'jobs:\n'
    '  test:\n'
    '    runs-on: [self-hosted, static]\n'
    '    permissions: {}\n'
    '    steps:\n'
    '      - run: make test\n'
)

#: What a repository writes into release-version.sh's settings.
SETTINGS = {
    '{{CARGO_TOML}}': 'Cargo.toml',
    '{{CARGO_LOCK}}': 'Cargo.lock',
    '{{CRATE_NAME}}': 'crate',
}

PUSH_TAG_IF = "    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')\n"
#: The push and tag guard with its clauses swapped: as safe, and not the guard.
RESPELLED_PUSH_TAG_IF = "    if: startsWith(github.ref, 'refs/tags/v') && github.event_name == 'push'\n"
BUILD_IF = "    if: ${{ !cancelled() && needs.check-version.result == 'success' }}\n"
CHECK_VERSION_IF = (
    '    if: >-\n'
    "      ${{ !cancelled() && (needs.ci-gate.result == 'success' ||\n"
    "      (github.event_name != 'push' && needs.ci-gate.result == 'skipped')) }}\n"
)
END_OF_BUILD_SECTION = '  # ---------------------------------------------------------------------\n  # END repository'


def in_job(text, job, old, new):
    """text with the first old inside job's block replaced by new."""
    start = text.index(f'\n  {job}:\n')
    position = text.index(old, start)
    return text[:position] + new + text[position + len(old):]


def add_job(text, block):
    """text with a job added at the end of the build section."""
    return text.replace(END_OF_BUILD_SECTION, block + '\n' + END_OF_BUILD_SECTION, 1)


def job(name, needs='check-version', condition=BUILD_IF, permissions='      contents: read\n', steps=None,
        extra=''):
    """A job in the template's style, by default a build job."""
    return (f'  {name}:\n'
            f'    needs: {needs}\n'
            f'{condition}'
            '    runs-on: [self-hosted, vm, debian-13, s]\n'
            '    timeout-minutes: 10\n'
            f'{extra}'
            '    permissions:\n'
            f'{permissions}'
            '    steps:\n'
            + (steps or '      - run: make thing\n'))


def template_script(name):
    with open(os.path.join(TEMPLATE_DIR, name), 'rb') as f:
        return f.read()


def filled_script(name):
    data = template_script(name)
    for placeholder, value in SETTINGS.items():
        data = data.replace(placeholder.encode(), value.encode())
    return data


class RustReleaseCase(CheckTestCase):
    """A Rust repository released by the template, as each test varies it."""

    check_class = release.ReleaseProcess

    def setUp(self):
        super().setUp()
        self.compliant()

    def compliant(self, workflow=FILLED):
        self.fixture.write('Cargo.toml', '[package]\nname = "crate"\n')
        self.fixture.workflow('ci.yml', CI_WORKFLOW)
        self.fixture.workflow('release.yml', workflow)
        for name in release_rust.RELEASE_SCRIPTS:
            self.script(name, filled_script(name))
        self.fixture.init_git()
        self.track()

    def track(self):
        self.fixture.git('add', '-A')

    def script(self, name, data, mode=0o755):
        path = os.path.join(self.fixture.path, 'tools', name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        os.chmod(path, mode)

    def release(self, text):
        self.fixture.workflow('release.yml', text)

    def issues(self, rule):
        return rule(self.repo())

    def details(self):
        result = self.check()
        return result['details']


class TemplateTest(RustReleaseCase):
    """The template's own files against its own invariants."""

    def test_every_workflow_rule_passes_the_template_with_its_placeholders(self):
        self.release(TEMPLATE)
        for rule in release_rust.RUST_RULES:
            if rule in (release_rust.rust_script_issues, release_rust.rust_git_tag_issues):
                continue
            self.assertEqual([], self.issues(rule), rule.__name__)

    def test_the_template_scripts_pass_once_their_settings_are_filled_in(self):
        self.assertEqual([], self.issues(release_rust.rust_script_issues))
        self.assertEqual([], self.issues(release_rust.rust_git_tag_issues))

    def test_the_template_scripts_unfilled_fail_only_on_their_placeholders(self):
        """release-version.sh ships with placeholders, which a copy must fill.

        That is the invariant working, not the template failing it: the
        placeholders sit inside the per-repository settings, so the
        comparison passes and only the "no {{" clause objects.
        """
        for name in release_rust.RELEASE_SCRIPTS:
            self.script(name, template_script(name))
        self.assertEqual(['tools/release-version.sh still holds a {{ placeholder from the template (invariant 14)'],
                         self.issues(release_rust.rust_script_issues))

    def test_a_filled_in_release_passes_the_whole_criterion(self):
        self.assert_pass(self.check())

    def test_the_rules_read_the_guards_the_readme_defines(self):
        """The README is the authority, so its strings are the constants."""
        text = ' '.join(repo_text('templates', 'release-automation-rust', 'README.md').split())
        push_tag = re.search(r'"The push and tag guard" is `if: ([^`]+)`', text).group(1)
        skipped = re.search(r'"The skipped-gate guard" is `([^`]+)`', text).group(1)
        build = re.search(r'for a job that needs only `check-version` it is `([^`]+)`', text).group(1)
        self.assertEqual(release_rust.PUSH_TAG_GUARD, push_tag)
        self.assertEqual(release_rust.SKIPPED_GATE_GUARD, skipped)
        self.assertEqual(release_rust.build_job_guard(['check-version']), build)


#: The heading the template README lists its invariants under, and the
#: one docs/audits/release-process.md maps them to rules under.
README_INVARIANTS_HEADING = '## Invariants'
SPEC_INVARIANTS_HEADING = '## The Rust invariants'


class ReleaseRustInvariantTableTest(unittest.TestCase):
    """docs/audits/release-process.md maps every README invariant to a rule."""

    def section(self, text, heading, source):
        """The text under a level-two heading, up to the next one.

        Fails, naming the file and the heading, when the heading is
        gone: a renamed heading would otherwise surface as an
        IndexError that says nothing about which document moved.
        """
        marker = f'\n{heading}\n'
        self.assertIn(marker, text, f'{source} has no "{heading}" heading; the invariant table tests read the '
                                    'section under it, so update the constant if the heading was renamed')
        return text.split(marker, 1)[1].split('\n## ', 1)[0]

    def readme_invariants(self):
        text = repo_text('templates', 'release-automation-rust', 'README.md')
        section = self.section(text, README_INVARIANTS_HEADING, 'templates/release-automation-rust/README.md')
        return {int(number) for number in re.findall(r'^(\d+)\. ', section, re.MULTILINE)}

    def table(self):
        text = repo_text('docs', 'audits', 'release-process.md')
        section = self.section(text, SPEC_INVARIANTS_HEADING, 'docs/audits/release-process.md')
        return {int(row.group(1)): row.group(2)
                for row in re.finditer(r'^\| (\d+) \| (.+) \|$', section, re.MULTILINE)}

    def test_a_missing_heading_is_named_in_the_failure(self):
        with self.assertRaises(AssertionError) as raised:
            self.section('# Title\n\n## Other\n\ntext\n', README_INVARIANTS_HEADING, 'README.md')
        self.assertIn('README.md has no "## Invariants" heading', str(raised.exception))

    def test_the_readme_lists_invariants(self):
        self.assertEqual(set(range(1, 16)), self.readme_invariants())

    def test_the_table_maps_every_invariant(self):
        missing = self.readme_invariants() - set(self.table())
        self.assertFalse(missing, f'docs/audits/release-process.md does not map invariants {sorted(missing)}')

    def test_the_table_maps_nothing_the_readme_does_not_list(self):
        self.assertFalse(set(self.table()) - self.readme_invariants())

    def test_every_rule_the_table_names_exists(self):
        for number, cell in self.table().items():
            names = re.findall(r'`((?:rust|release)_[a-z_]+)`', cell)
            self.assertTrue(names, number)
            for name in names:
                self.assertTrue(hasattr(release_rust, name) or hasattr(release, name), name)

    def test_every_rust_rule_is_in_the_table(self):
        named = set(re.findall(r'`(rust_[a-z_]+)`', ' '.join(self.table().values())))
        self.assertEqual({rule.__name__ for rule in release_rust.RUST_RULES}, named)


class ApplicabilityTest(RustReleaseCase):

    def test_without_a_cargo_toml_the_rust_rules_do_not_run(self):
        os.remove(os.path.join(self.fixture.path, 'Cargo.toml'))
        os.remove(os.path.join(self.fixture.path, 'tools', 'tag-release.sh'))
        self.assert_pass(self.check())

    def test_a_cargo_toml_under_src_is_a_rust_repository(self):
        os.remove(os.path.join(self.fixture.path, 'Cargo.toml'))
        self.fixture.write('src/Cargo.toml', '[package]\nname = "crate"\n')
        os.remove(os.path.join(self.fixture.path, 'tools', 'tag-release.sh'))
        self.assert_fail(self.check(), containing='tools/tag-release.sh is missing')

    def test_every_rule_passes_the_compliant_fixture(self):
        for rule in release_rust.RUST_RULES:
            self.assertEqual([], self.issues(rule), rule.__name__)

    def test_without_a_release_workflow_the_workflow_rules_say_nothing(self):
        os.remove(os.path.join(self.fixture.path, '.github', 'workflows', 'release.yml'))
        for rule in release_rust.RUST_RULES[:-2]:
            self.assertEqual([], self.issues(rule), rule.__name__)


class TriggerTest(RustReleaseCase):
    """Invariant 1."""

    ON = "on:\n  push:\n    tags:\n      - 'v*'\n"

    def trigger_issues(self, on):
        self.release(FILLED.replace(self.ON, on))
        return ' '.join(self.issues(release_rust.rust_trigger_issues))

    def test_every_spelling_of_the_tag_filter_passes(self):
        for tags in ("['v*']", '[v*]', '"v*"', "\n      - v*"):
            self.assertEqual('', self.trigger_issues(f'on:\n  push:\n    tags: {tags}\n'), tags)

    def test_an_extra_trigger_fails(self):
        self.assertIn('not on push and workflow_dispatch alone',
                      self.trigger_issues(self.ON + '  pull_request:\n'))

    def test_a_missing_dispatch_fails(self):
        self.release(FILLED.replace('  workflow_dispatch:\n', ''))
        self.assertIn('triggers on push, not on push and workflow_dispatch alone',
                      ' '.join(self.issues(release_rust.rust_trigger_issues)))

    def test_another_tag_pattern_fails(self):
        self.assertIn("filters tags on ['release-*']",
                      self.trigger_issues("on:\n  push:\n    tags:\n      - 'release-*'\n"))

    def test_a_push_without_a_tag_filter_fails(self):
        self.assertIn('filters tags on nothing', self.trigger_issues('on:\n  push:\n'))

    def test_a_branch_filter_fails(self):
        for key in ('branches', 'branches-ignore'):
            self.assertIn(f'push trigger has {key}', self.trigger_issues(self.ON + f'    {key}: [develop]\n'), key)

    def test_dispatch_inputs_fail(self):
        self.release(FILLED.replace('  workflow_dispatch:\n',
                                    '  workflow_dispatch:\n    inputs:\n      version:\n        type: string\n'))
        self.assertIn('takes inputs', ' '.join(self.issues(release_rust.rust_trigger_issues)))

    def test_an_inline_on_cannot_be_read_as_the_two_triggers(self):
        self.assertIn('is not a mapping', self.trigger_issues('on: [push, workflow_dispatch]\n'))


class PermissionsTest(RustReleaseCase):
    """Invariant 2."""

    def test_top_level_permissions_other_than_empty_fail(self):
        for top in ('permissions: read-all\n', 'permissions:\n  contents: read\n', ''):
            self.fresh_fixture()
            self.compliant(FILLED.replace('permissions: {}\n', top))
            self.assertIn('top-level permissions are', ' '.join(self.issues(release_rust.rust_permissions_issues)),
                          top)

    def test_a_job_without_its_own_block_fails(self):
        self.release(in_job(FILLED, 'build', '    permissions:\n      contents: read\n', ''))
        self.assertEqual(['build has no "permissions:" block of its own, so what the job may do is not stated '
                          'where it runs (invariant 2)'], self.issues(release_rust.rust_permissions_issues))


class ConcurrencyTest(RustReleaseCase):
    """Invariant 3."""

    BLOCK = 'concurrency:\n  group: release-${{ github.ref }}\n  cancel-in-progress: false\n'

    def concurrency_issues(self, block):
        self.release(FILLED.replace(self.BLOCK, block))
        return ' '.join(self.issues(release_rust.rust_concurrency_issues))

    def test_no_concurrency_fails(self):
        self.assertIn('sets no top-level concurrency', self.concurrency_issues(''))

    def test_a_group_on_another_key_fails(self):
        issues = self.concurrency_issues(self.BLOCK.replace('github.ref', 'github.ref_name'))
        self.assertIn('does not contain github.ref', issues)

    def test_cancelling_fails(self):
        self.assertIn('does not set cancel-in-progress: false',
                      self.concurrency_issues(self.BLOCK.replace('false', 'true')))

    def test_an_inline_group_does_not_say_it_will_not_cancel(self):
        issues = self.concurrency_issues('concurrency: release-${{ github.ref }}\n')
        self.assertIn('cancel-in-progress', issues)
        self.assertNotIn('does not contain github.ref', issues)


class CiGateShapeTest(RustReleaseCase):
    """Invariant 4."""

    def gate_issues(self, old, new):
        self.release(in_job(FILLED, 'ci-gate', old, new))
        return ' '.join(self.issues(release_rust.rust_ci_gate_issues))

    def test_a_gate_under_another_name_fails(self):
        self.release(FILLED.replace('  ci-gate:\n', '  gate:\n'))
        self.assertEqual(['the gate runs in gate, not in a job named ci-gate, which is the name check-version\'s '
                          'guard and needs are written against (invariant 4)'],
                         self.issues(release_rust.rust_ci_gate_issues))

    def test_no_gate_at_all_is_the_gate_rule_s_finding(self):
        self.release(FILLED.replace('shakenfist/actions/require-ci-success@main', 'actions/checkout@v7'))
        self.assertEqual([], self.issues(release_rust.rust_ci_gate_issues))
        self.assertIn('no job running shakenfist/actions/require-ci-success', self.details())

    def test_a_respelled_guard_fails(self):
        self.assertIn('not the push and tag guard', self.gate_issues(PUSH_TAG_IF, RESPELLED_PUSH_TAG_IF))

    def test_needs_fail(self):
        self.assertIn('the ci-gate job needs build', self.gate_issues(PUSH_TAG_IF, '    needs: build\n' + PUSH_TAG_IF))

    def test_another_pool_fails(self):
        self.assertIn('runs on [self-hosted, vm, s]', self.gate_issues('[self-hosted, static]', '[self-hosted, vm, s]'))

    def test_more_permissions_fail(self):
        self.assertIn("the ci-gate job's permissions are {actions: read, contents: write}",
                      self.gate_issues('      contents: read\n', '      contents: write\n'))

    def test_a_second_step_fails(self):
        self.assertIn('has 2 steps', self.gate_issues('    steps:\n', '    steps:\n      - run: echo hi\n'))

    def test_a_pinned_gate_fails(self):
        self.assertIn('uses shakenfist/actions/require-ci-success@v1', self.gate_issues(
            'require-ci-success@main', 'require-ci-success@v1'))

    def test_no_timeout_fails(self):
        self.assertIn('sets no timeout-minutes', self.gate_issues('        timeout-minutes: 5\n', ''))

    def test_no_workflows_fail(self):
        self.assertIn('names no workflows', self.gate_issues('workflows: |\n            ci.yml\n', "workflows: ''\n"))

    def test_a_workflow_named_by_a_path_fails(self):
        self.assertIn('names ./ci.yml by a path', self.gate_issues('            ci.yml\n', '            ./ci.yml\n'))

    def test_a_path_with_no_workflow_behind_it_is_the_trigger_rule_s_finding(self):
        self.release(FILLED.replace('            ci.yml\n', '            .github/workflows/ci.yml\n'))
        self.assertEqual([], self.issues(release_rust.rust_ci_gate_issues))
        self.assertIn('names .github/workflows/ci.yml, which is not a workflow', self.details())


class GateBypassTest(RustReleaseCase):
    """The gate rule's refusal of a gate that can pass without holding, on the Rust template."""

    def test_the_template_s_gate_passes(self):
        self.assert_pass(self.check())

    def test_a_gate_job_that_continues_on_error_fails(self):
        self.release(in_job(FILLED, 'ci-gate', PUSH_TAG_IF, PUSH_TAG_IF + '    continue-on-error: true\n'))
        self.assertIn('the ci-gate job sets continue-on-error, so a gate that fails is recorded as a success',
                      self.details())

    def test_a_gate_step_that_continues_on_error_fails(self):
        self.release(in_job(FILLED, 'ci-gate', '        timeout-minutes: 5\n',
                            '        timeout-minutes: 5\n        continue-on-error: true\n'))
        self.assertIn("the ci-gate job's gate step sets continue-on-error", self.details())

    def test_a_gate_step_with_a_condition_fails(self):
        self.release(in_job(FILLED, 'ci-gate', '        timeout-minutes: 5\n',
                            '        timeout-minutes: 5\n        if: ${{ false }}\n'))
        self.assertIn('the ci-gate job\'s gate step has "if: ${{ false }}", so the gate can be skipped',
                      self.details())

    def test_a_gate_step_naming_a_commit_fails(self):
        self.release(in_job(FILLED, 'ci-gate', '        with:\n',
                            '        with:\n          sha: ${{ github.event.before }}\n'))
        self.assertIn("the ci-gate job's gate step passes sha:", self.details())

    def test_a_gate_reference_with_a_trailing_comment_is_still_at_main(self):
        self.release(in_job(FILLED, 'ci-gate', 'require-ci-success@main\n',
                            'require-ci-success@main  # newest, always\n'))
        self.assertEqual([], self.issues(release_rust.rust_ci_gate_issues))
        self.assert_pass(self.check())


class CheckVersionTest(RustReleaseCase):
    """Invariant 5, for check-version itself."""

    def version_issues(self, old, new):
        self.release(in_job(FILLED, 'check-version', old, new))
        return ' '.join(self.issues(release_rust.rust_check_version_issues))

    def test_a_missing_job_fails(self):
        self.release(FILLED.replace('  check-version:\n', '  version:\n'))
        self.assertIn('has no check-version job', ' '.join(self.issues(release_rust.rust_check_version_issues)))

    def test_needing_more_than_the_gate_fails(self):
        self.assertIn('needs ci-gate, other, not ci-gate alone',
                      self.version_issues('    needs: ci-gate\n', '    needs: [ci-gate, other]\n'))

    def test_a_safe_respelling_of_the_guard_fails_once(self):
        """The gate rule passes this guard, so the finding is this rule's alone."""
        self.release(in_job(FILLED, 'check-version', CHECK_VERSION_IF, (
            "    if: ${{ !cancelled() && (needs.ci-gate.result == 'success' || "
            "(needs.ci-gate.result == 'skipped' && github.event_name != 'push')) }}\n")))
        details = self.details()
        self.assertIn('not the skipped-gate guard', details)
        self.assertEqual(1, details.count('; ') + 1, details)

    def test_an_unsafe_guard_is_the_gate_rule_s_finding(self):
        self.release(in_job(FILLED, 'check-version', CHECK_VERSION_IF, '    if: always()\n'))
        details = self.details()
        self.assertIn("the check-version job runs under always(), which replaces GitHub's implicit success(), "
                      'without requiring', details)
        self.assertNotIn('skipped-gate guard', details)

    def test_an_admission_not_confined_off_push_is_the_gate_rule_s_finding(self):
        self.release(in_job(FILLED, 'check-version', CHECK_VERSION_IF, (
            "    if: ${{ !cancelled() && (needs.ci-gate.result == 'success' || "
            "needs.ci-gate.result == 'skipped') }}\n")))
        details = self.details()
        self.assertIn('the check-version job lets ci-gate through on a result other than success without', details)
        self.assertNotIn('skipped-gate guard', details)

    def test_an_unguarded_environment_job_an_admission_reaches_is_the_gate_rule_s_finding(self):
        """Without a dispatch trigger the dispatch rule is silent, and the gate rule says it."""
        self.release(in_job(FILLED.replace('  workflow_dispatch:\n', ''), 'sign-tag', PUSH_TAG_IF, ''))
        details = self.details()
        self.assertIn('safe only while the jobs below it that sign, publish or write run on a pushed tag alone, '
                      'and sign-tag',
                      details)
        self.assertNotIn('not the push and tag guard', details)

    def test_more_permissions_fail(self):
        self.assertIn("the check-version job's permissions are {contents: write}",
                      self.version_issues('      contents: read\n', '      contents: write\n'))

    def test_not_checking_the_version_fails(self):
        self.assertIn('does not run tools/release-version.sh check', self.version_issues(
            'run: tools/release-version.sh check "${GITHUB_REF}"', 'run: tools/release-version.sh get'))


class BuildRootTest(RustReleaseCase):
    """Invariant 5, for every job below check-version."""

    def test_a_job_that_skips_check_version_fails(self):
        gated = "    if: ${{ !cancelled() && needs.ci-gate.result == 'success' }}\n"
        self.release(add_job(FILLED, job('lint', needs='ci-gate', condition=gated)))
        self.assertEqual(['lint does not need check-version, so it runs without the version having been checked and '
                          'without waiting for ci-gate; only ci-gate needs nothing (invariant 5)'],
                         self.issues(release_rust.rust_build_root_issues))

    def test_a_job_needing_check_version_through_another_passes(self):
        self.release(add_job(FILLED, job('package', needs='build',
                                         condition="    if: ${{ !cancelled() && needs.build.result == 'success' }}\n")))
        self.assertEqual([], self.issues(release_rust.rust_build_root_issues))

    def test_an_environment_job_off_the_gate_is_the_gate_rule_s_finding(self):
        self.release(add_job(FILLED, job('publish', needs='[]', condition=PUSH_TAG_IF,
                                         extra='    environment: release\n')))
        self.assertEqual([], self.issues(release_rust.rust_build_root_issues))
        self.assertEqual([], self.issues(release_rust.rust_sign_tag_needs_issues))
        self.assertIn('the publish job names an environment but is not downstream of ci-gate', self.details())

    def test_an_unreadable_graph_is_the_gate_rule_s_finding(self):
        self.release(in_job(FILLED, 'build', '    needs: check-version\n', '    needs: [check-version, ghost]\n'))
        self.assertEqual([], self.issues(release_rust.rust_build_root_issues))
        self.assertEqual([], self.issues(release_rust.rust_upload_needs_issues))
        self.assertEqual([], self.issues(release_rust.rust_sign_tag_needs_issues))
        self.assertIn('the build job needs ghost, which is not a job', self.details())

    def test_a_cycle_is_the_gate_rule_s_finding(self):
        """Each rule below would otherwise find the two jobs waiting for nothing above them."""
        upload = ('      - run: echo ${{ secrets.TOKEN }}\n'
                  '      - uses: actions/upload-artifact@v7\n        with:\n          name: x\n          path: out/\n')
        self.release(add_job(FILLED, job('loop-a', needs='loop-b', condition=PUSH_TAG_IF, steps=upload)
                             + job('loop-b', needs='loop-a', condition=PUSH_TAG_IF)))
        self.assertEqual([], self.issues(release_rust.rust_build_root_issues))
        self.assertEqual([], self.issues(release_rust.rust_upload_needs_issues))
        self.assertEqual([], self.issues(release_rust.rust_sign_tag_needs_issues))
        self.assertIn('need each other in a cycle', self.details())


class BuildGuardTest(RustReleaseCase):
    """Invariant 6."""

    def guard_issues(self, condition):
        self.release(in_job(FILLED, 'build', BUILD_IF, condition))
        return ' '.join(self.issues(release_rust.rust_build_guard_issues))

    def test_no_guard_fails(self):
        self.assertIn('the build job has no "if:"', self.guard_issues(''))

    def test_the_push_and_tag_guard_is_not_a_build_job_s_and_passes(self):
        self.assertEqual('', self.guard_issues(PUSH_TAG_IF))

    def test_always_with_the_success_clause_fails_here(self):
        """Safe by the gate rule's reading, but always() is ruled out."""
        self.assertIn('neither the build-job guard', self.guard_issues(
            "    if: ${{ always() && needs.check-version.result == 'success' }}\n"))

    def test_always_without_it_is_the_gate_rule_s_finding(self):
        self.release(in_job(FILLED, 'build', BUILD_IF, '    if: always()\n'))
        details = self.details()
        self.assertIn('the build job runs under always()', details)
        self.assertNotIn('neither the build-job guard', details)

    def test_the_clauses_may_come_in_any_order(self):
        self.release(add_job(FILLED, job('package', needs='[check-version, build]', condition=(
            "    if: ${{ !cancelled() && needs.build.result == 'success' && "
            "needs.check-version.result == 'success' }}\n"))))
        self.assertEqual([], self.issues(release_rust.rust_build_guard_issues))

    def test_a_missing_clause_off_the_gate_s_path_fails_here(self):
        """No environment job needs docs, so the gate rule does not read it."""
        self.release(add_job(FILLED, job('docs', needs='[check-version, build]', condition=BUILD_IF)))
        self.assertIn('the docs job has', ' '.join(self.issues(release_rust.rust_build_guard_issues)))
        self.assertIn('the docs job has', self.details())

    def test_cancelled_off_a_build_job_fails(self):
        condition = "    if: ${{ !cancelled() && github.event_name == 'push' }}\n"
        self.release(add_job(FILLED, job('docs', condition=condition)))
        self.assertIn('the docs job has', ' '.join(self.issues(release_rust.rust_build_guard_issues)))

    def test_cancelled_without_the_success_clause_on_the_gate_s_path_is_the_gate_rule_s_finding(self):
        self.release(in_job(FILLED, 'build', BUILD_IF, "    if: ${{ !cancelled() && github.event_name == 'push' }}\n"))
        details = self.details()
        self.assertIn('the build job runs under !cancelled(), which replaces', details)
        self.assertNotIn('neither the build-job guard', details)


class EffectGuardTest(RustReleaseCase):
    """Invariant 7."""

    def publisher(self, condition, needs='[build, sign-tag]', permissions='      contents: read\n',
                  steps='      - env:\n          TOKEN: ${{ secrets.CRATES_TOKEN }}\n        run: make publish\n'):
        return job('publish', needs=needs, condition=condition, permissions=permissions, steps=steps)

    def test_a_job_reading_a_secret_needs_the_guard(self):
        self.release(add_job(FILLED, self.publisher(BUILD_IF)))
        self.assertEqual(['the publish job reads secrets.CRATES_TOKEN, an effect outside the run, but has "if: '
                          "${{ !cancelled() && needs.check-version.result == 'success' }}\", not the push and tag "
                          f'guard "if: {release_rust.PUSH_TAG_GUARD}" (invariant 7)'],
                         self.issues(release_rust.rust_effect_guard_issues))

    def test_the_run_s_own_token_is_not_an_effect(self):
        self.release(add_job(FILLED, self.publisher(
            BUILD_IF, needs='check-version', steps='      - run: echo ${{ secrets.GITHUB_TOKEN }}\n')))
        self.assertEqual([], self.issues(release_rust.rust_effect_guard_issues))
        self.assertEqual([], self.issues(release_rust.rust_sign_tag_needs_issues))

    def test_every_effect_is_an_effect(self):
        for extra, permissions, effect in (
                ('    environment: release\n', '      contents: read\n', 'names an environment'),
                ('', '      contents: write\n', 'holds contents: write'),
                ('', '      id-token: write\n', 'holds id-token: write'),
                ('', '      contents: read\n      packages: write\n', 'holds packages: write'),
                ('', '      attestations: write\n', 'holds attestations: write'),
                ('', '      pull-requests: Write\n', 'holds pull-requests: Write')):
            self.fresh_fixture()
            self.compliant(add_job(FILLED, job('publish', needs='[build, sign-tag]', condition=RESPELLED_PUSH_TAG_IF,
                                               permissions=permissions, extra=extra)))
            self.assertIn(effect, ' '.join(self.issues(release_rust.rust_effect_guard_issues)), effect)

    def test_write_all_is_an_effect(self):
        publisher = job('publish', needs='[build, sign-tag]', condition=RESPELLED_PUSH_TAG_IF).replace(
            '    permissions:\n      contents: read\n', '    permissions: write-all\n')
        self.release(add_job(FILLED, publisher))
        self.assertIn('the publish job holds write-all, an effect outside the run',
                      ' '.join(self.issues(release_rust.rust_effect_guard_issues)))

    def test_read_access_is_no_effect(self):
        for permissions in ('      contents: read\n      packages: read\n', '      packages: none\n'):
            self.fresh_fixture()
            self.compliant(add_job(FILLED, job('lint', permissions=permissions)))
            self.assertEqual([], self.issues(release_rust.rust_effect_guard_issues), permissions)
            self.assertEqual([], self.issues(release_rust.rust_sign_tag_needs_issues), permissions)

    def test_a_package_push_under_the_build_job_guard_fails(self):
        """A build-section job pushing an image would run on a dispatch, whose gate is skipped."""
        self.release(add_job(FILLED, job(
            'publish-image', needs='build',
            condition="    if: ${{ !cancelled() && needs.build.result == 'success' }}\n",
            permissions='      contents: read\n      packages: write\n')))
        details = self.details()
        self.assertIn('and publish-image is not confined to one', details)
        self.assertIn('publish-image has an effect outside the run but does not need sign-tag', details)
        # The guard is the gate rule's finding, so the effect rule says nothing more about it.
        self.assertNotIn('not the push and tag guard', details)

    def test_a_safe_respelling_on_sign_tag_fails_once(self):
        self.release(in_job(FILLED, 'sign-tag', PUSH_TAG_IF, RESPELLED_PUSH_TAG_IF))
        details = self.details()
        self.assertIn('the sign-tag job names an environment and holds contents: write and holds id-token: write',
                      details)
        self.assertEqual(1, details.count('; ') + 1, details)

    def test_an_unguarded_publishing_job_is_the_dispatch_rule_s_finding(self):
        """github-release names no environment, so only the dispatch rule reads its guard."""
        for name in ('sign-tag', 'github-release'):
            self.release(in_job(FILLED, name, PUSH_TAG_IF, ''))
            details = self.details()
            self.assertIn(f'its publishing jobs are not confined to tags: {name} lacks', details)
            self.assertNotIn('not the push and tag guard', details)


class SignTagTest(RustReleaseCase):
    """Invariant 8."""

    RUNS_ON = '    runs-on: [self-hosted, vm, debian-13, s]\n'

    def sign_issues(self, old, new):
        self.release(in_job(FILLED, 'sign-tag', old, new))
        return ' '.join(self.issues(release_rust.rust_sign_tag_issues))

    def test_a_missing_job_fails(self):
        self.release(FILLED.replace('  sign-tag:\n', '  sign:\n'))
        self.assertIn('has no sign-tag job', ' '.join(self.issues(release_rust.rust_sign_tag_issues)))

    def test_another_environment_fails(self):
        self.assertIn("names environment 'staging', not release",
                      self.sign_issues('    environment: release\n', '    environment: staging\n'))

    def test_more_permissions_fail(self):
        self.assertIn("the sign-tag job's permissions are",
                      self.sign_issues('      id-token: write\n', '      id-token: write\n      packages: write\n'))

    def test_not_running_the_script_fails(self):
        self.assertIn('does not run tools/sign-release-tag.sh',
                      self.sign_issues('tools/sign-release-tag.sh', 'tools/sign.sh'))

    def test_the_static_pool_fails(self):
        self.assertIn('runs on [self-hosted, static]',
                      self.sign_issues(self.RUNS_ON, '    runs-on: [self-hosted, static]\n'))

    def test_a_marked_github_hosted_runner_passes(self):
        self.assertEqual('', self.sign_issues(
            self.RUNS_ON, '    # audit-ok: github-hosted-runner -- throwaway\n    runs-on: ubuntu-latest\n'))

    def test_an_unmarked_or_misplaced_mark_fails(self):
        for runs_on in ('    runs-on: ubuntu-latest\n',
                        '    runs-on: ubuntu-latest  # audit-ok: github-hosted-runner\n',
                        '    # audit-ok: github-hosted-runner\n    timeout-minutes: 5\n    runs-on: ubuntu-latest\n',
                        '    # audit-ok: github-hosted-runner\n    runs-on: [ubuntu-latest, self-hosted]\n',
                        '    # audit-ok: github-hosted-runner\n    runs-on: static\n'):
            self.fresh_fixture()
            self.compliant()
            self.assertIn('neither a vm runner', self.sign_issues(self.RUNS_ON, runs_on), runs_on)


class UploadNeedsTest(RustReleaseCase):
    """Invariant 9."""

    def test_an_upload_sign_tag_does_not_wait_for_fails(self):
        self.release(add_job(FILLED, job('wheels', steps=(
            '      - uses: actions/upload-artifact@v7\n        with:\n          name: x\n          path: out/\n'))))
        self.assertEqual(['wheels uploads an artifact but sign-tag does not need it, so the tag can be signed while '
                          'a build is running or after one has failed (invariant 9)'],
                         self.issues(release_rust.rust_upload_needs_issues))

    def test_an_upload_sign_tag_waits_for_through_another_job_passes(self):
        self.release(add_job(FILLED, job('wheels', steps=(
            '      - uses: actions/upload-artifact@v7\n        with:\n          name: x\n          path: out/\n')))
            .replace('    needs: check-version\n    if: ${{ !cancelled() && needs.check-version', (
                "    needs: [check-version, wheels]\n    if: ${{ !cancelled() && needs.wheels.result == 'success' "
                '&& needs.check-version'), 1))
        self.assertEqual([], self.issues(release_rust.rust_upload_needs_issues))


class SignTagNeedsTest(RustReleaseCase):
    """Invariant 10, and github-release's need from invariant 12."""

    def test_a_publisher_ahead_of_sign_tag_fails(self):
        self.release(add_job(FILLED, job('publish', needs='build', condition=PUSH_TAG_IF,
                                         steps='      - run: echo ${{ secrets.TOKEN }}\n')))
        self.assertEqual(['publish has an effect outside the run but does not need sign-tag, so it publishes before '
                          'a reviewer has approved the release and its tag is signed (invariant 10)'],
                         self.issues(release_rust.rust_sign_tag_needs_issues))

    def test_one_finding_for_a_publisher_that_needs_nothing_of_the_two(self):
        self.release(add_job(FILLED, job('publish', needs='ci-gate', condition=PUSH_TAG_IF,
                                         steps='      - run: echo ${{ secrets.TOKEN }}\n')))
        self.assertEqual([], self.issues(release_rust.rust_build_root_issues))
        self.assertEqual(1, len(self.issues(release_rust.rust_sign_tag_needs_issues)))

    def test_github_release_needs_sign_tag(self):
        self.release(in_job(FILLED, 'github-release', '    needs: [build, sign-tag]\n', '    needs: [build]\n'))
        self.assertIn('github-release has an effect', ' '.join(self.issues(release_rust.rust_sign_tag_needs_issues)))


class WritePermissionTest(RustReleaseCase):
    """Invariant 11."""

    def holder(self, permissions, extra=''):
        self.release(add_job(FILLED, job('publish', needs='[build, sign-tag]', condition=PUSH_TAG_IF,
                                         permissions=permissions, extra=extra)))
        return ' '.join(self.issues(release_rust.rust_write_permission_issues))

    def test_contents_write_elsewhere_fails(self):
        self.assertIn('publish holds contents: write', self.holder('      contents: write\n'))

    def test_a_write_spelt_with_capitals_still_counts(self):
        self.assertIn('publish holds contents: write', self.holder('      contents: Write\n'))
        self.fresh_fixture()
        self.compliant()
        self.assertIn('publish holds id-token: write without', self.holder('      id-token: WRITE\n'))

    def test_id_token_without_the_release_environment_fails(self):
        self.assertIn('publish holds id-token: write without', self.holder('      id-token: write\n'))
        self.fresh_fixture()
        self.compliant()
        self.assertIn('id-token', self.holder('      id-token: write\n', extra='    environment: staging\n'))

    def test_id_token_with_the_release_environment_passes(self):
        self.assertEqual('', self.holder('      id-token: write\n', extra='    environment: release\n'))

    def test_write_all_holds_both(self):
        self.release(add_job(FILLED, job('publish', needs='[build, sign-tag]', condition=PUSH_TAG_IF,
                                         permissions='')).replace('    permissions:\n    steps:',
                                                                  '    permissions: write-all\n    steps:'))
        issues = ' '.join(self.issues(release_rust.rust_write_permission_issues))
        self.assertIn('contents: write', issues)
        self.assertIn('id-token: write', issues)

    def test_the_fixed_jobs_are_judged_by_their_own_invariants(self):
        self.release(in_job(FILLED, 'github-release', '      contents: write\n',
                            '      contents: write\n      id-token: write\n'))
        self.assertEqual([], self.issues(release_rust.rust_write_permission_issues))
        self.assertIn("the github-release job's permissions are",
                      ' '.join(self.issues(release_rust.rust_github_release_issues)))


class GithubReleaseTest(RustReleaseCase):
    """Invariant 12."""

    DOWNLOAD = '          pattern: release-*\n          path: ${{ runner.temp }}/dist/\n'

    def release_issues(self, old, new, text=FILLED):
        self.release(in_job(text, 'github-release', old, new))
        return ' '.join(self.issues(release_rust.rust_github_release_issues))

    def test_a_missing_job_fails(self):
        self.release(FILLED.replace('  github-release:\n', '  publish-release:\n'))
        self.assertIn('has no github-release job', ' '.join(self.issues(release_rust.rust_github_release_issues)))

    def test_no_download_fails(self):
        self.assertIn('downloads no artifacts',
                      self.release_issues('uses: actions/download-artifact@v8', 'uses: actions/cache@v4'))

    def test_another_pattern_fails(self):
        self.assertIn('downloads pattern wheel-*', self.release_issues('pattern: release-*', 'pattern: wheel-*'))

    def test_a_download_outside_runner_temp_after_a_checkout_fails(self):
        issues = self.release_issues(
            '    steps:\n', '    steps:\n      - uses: actions/checkout@v7\n',
            text=FILLED.replace('path: ${{ runner.temp }}/dist/', 'path: dist/'))
        self.assertIn('downloads to "dist/", not to a path under', issues)

    def test_a_download_outside_runner_temp_without_a_checkout_is_the_workspace_rule_s_finding(self):
        self.release(in_job(FILLED, 'github-release', 'path: ${{ runner.temp }}/dist/', 'path: dist/'))
        self.assertEqual([], self.issues(release_rust.rust_github_release_issues))
        self.assertIn('the github-release job does not start from a cleaned checkout', self.details())

    def test_runner_temp_must_be_the_root_of_the_path(self):
        self.assertIn('not to a path under', self.release_issues(
            'path: ${{ runner.temp }}/dist/', 'path: out/${{ runner.temp }}/dist/'))

    def test_no_release_step_fails(self):
        self.assertIn('does not create the release with softprops/action-gh-release',
                      self.release_issues('softprops/action-gh-release@v3', 'actions/github-script@v7'))

    def test_a_release_attaching_nothing_must_still_fail_on_unmatched_files(self):
        self.assertIn('without fail_on_unmatched_files: true', self.release_issues(
            '          files: ${{ runner.temp }}/dist/*\n          fail_on_unmatched_files: true\n', ''))

    def test_a_release_attaching_files_is_the_asset_rule_s_finding(self):
        self.release(in_job(FILLED, 'github-release', '          fail_on_unmatched_files: true\n', ''))
        self.assertEqual([], self.issues(release_rust.rust_github_release_issues))
        self.assertIn('without "fail_on_unmatched_files: true"', self.details())


class CheckDistRefTest(RustReleaseCase):
    """Invariant 13, the part the check-dist rule does not read."""

    STEP = ('      - uses: shakenfist/actions/check-dist@{ref}\n        with:\n          path: target/wheels/\n'
            '      - uses: actions/upload-artifact@v7\n        with:\n          name: w\n'
            '          path: target/wheels/*.whl\n')

    def test_check_dist_at_main_passes(self):
        self.release(add_job(FILLED, job('wheels', steps=self.STEP.format(ref='main'))))
        self.assertEqual([], self.issues(release_rust.rust_check_dist_ref_issues))

    def test_a_pinned_check_dist_fails(self):
        self.release(add_job(FILLED, job('wheels', steps=self.STEP.format(ref='v1'))))
        self.assertEqual(['wheels (shakenfist/actions/check-dist@v1) runs check-dist at a version other than @main, '
                          'so the release misses fixes to what the check rejects (invariant 13)'],
                         self.issues(release_rust.rust_check_dist_ref_issues))


class ScriptTest(RustReleaseCase):
    """Invariant 14."""

    def script_issues(self):
        return ' '.join(self.issues(release_rust.rust_script_issues))

    def test_a_missing_script_fails(self):
        os.remove(os.path.join(self.fixture.path, 'tools', 'propose-release.sh'))
        self.assertIn('tools/propose-release.sh is missing', self.script_issues())

    def test_a_directory_in_its_place_fails(self):
        os.remove(os.path.join(self.fixture.path, 'tools', 'propose-release.sh'))
        os.makedirs(os.path.join(self.fixture.path, 'tools', 'propose-release.sh'))
        self.assertIn('tools/propose-release.sh is not a regular file', self.script_issues())

    def test_a_script_that_is_not_executable_fails(self):
        self.script('tag-release.sh', filled_script('tag-release.sh'), mode=0o644)
        self.assertIn('tools/tag-release.sh is not executable', self.script_issues())

    def test_a_verbatim_script_that_differs_fails(self):
        # Filling in the repository's name, which the template spells as
        # ${GITHUB_REPOSITORY} so that every copy can stay byte for byte.
        template = filled_script('sign-release-tag.sh')
        changed = template.replace(b'${GITHUB_REPOSITORY}', b'shakenfist/ryll')
        self.assertNotEqual(template, changed)
        self.script('sign-release-tag.sh', changed)
        self.assertEqual(['tools/sign-release-tag.sh differs from templates/release-automation-rust/'
                          'sign-release-tag.sh, which every Rust release copies byte for byte (invariant 14)'],
                         self.issues(release_rust.rust_script_issues))

    def test_a_marked_script_may_differ_inside_its_settings(self):
        script = filled_script('propose-release.sh')
        self.script('propose-release.sh', script.replace(b'    make test\n', b'    make test-all\n'))
        self.assertEqual('', self.script_issues())

    def test_a_marked_script_may_not_differ_outside_them(self):
        script = filled_script('propose-release.sh')
        self.script('propose-release.sh', script.replace(b'set -euo pipefail', b'set -eu'))
        self.assertIn('tools/propose-release.sh differs from templates/release-automation-rust/propose-release.sh '
                      'outside its per-repository settings', self.script_issues())

    def test_a_marked_script_without_its_markers_fails(self):
        self.script('release-version.sh', filled_script('release-version.sh').replace(
            release_rust.SETTINGS_END, b'end of settings'))
        self.assertIn('does not have its per-repository settings markers', self.script_issues())

    def test_a_rewritten_marker_line_fails(self):
        self.script('release-version.sh', filled_script('release-version.sh').replace(
            b'# --- end of per-repository settings ---', b'## --- end of per-repository settings ---'))
        self.assertIn('outside its per-repository settings', self.script_issues())

    def test_an_unreadable_template_is_a_tool_failure(self):
        with mock.patch.object(release_rust, 'DEVELOPMENT_ROOT', self.tempdir()):
            with self.assertRaisesRegex(ToolFailure, 'Could not read templates/release-automation-rust/'):
                self.issues(release_rust.rust_script_issues)

    def test_a_template_without_markers_is_a_tool_failure(self):
        root = self.tempdir()
        shutil.copytree(TEMPLATE_DIR, os.path.join(root, 'templates', 'release-automation-rust'))
        path = os.path.join(root, 'templates', 'release-automation-rust', 'propose-release.sh')
        with open(path, 'wb') as f:
            f.write(template_script('propose-release.sh').replace(release_rust.SETTINGS_START, b'settings'))
        with mock.patch.object(release_rust, 'DEVELOPMENT_ROOT', os.path.realpath(root)):
            with self.assertRaisesRegex(ToolFailure, 'propose-release.sh .* has no per-repository settings markers'):
                self.issues(release_rust.rust_script_issues)

    def test_the_template_scripts_are_executable(self):
        """The README says so, and a copy made with its mode inherits it."""
        for name in release_rust.RELEASE_SCRIPTS:
            self.assertTrue(os.stat(os.path.join(TEMPLATE_DIR, name)).st_mode & stat.S_IXUSR, name)


class GitTagTest(RustReleaseCase):
    """Invariant 15."""

    def tag_issues(self, path, content):
        self.fixture.write(path, content)
        self.track()
        return ' '.join(self.issues(release_rust.rust_git_tag_issues))

    def test_a_makefile_that_tags_fails(self):
        self.assertIn('Makefile:2 runs git tag', self.tag_issues('Makefile', 'release:\n\tgit tag -s v$(V)\n'))

    def test_a_nested_makefile_or_script_that_tags_fails(self):
        self.assertIn('sub/Makefile:1', self.tag_issues('sub/Makefile', '\tgit tag v1\n'))
        self.assertIn('scripts/cut.sh:1', self.tag_issues('scripts/cut.sh', 'git -C "$here" tag v1\n'))

    def test_a_workflow_that_tags_fails(self):
        self.assertIn('.github/workflows/tag.yml:1', self.tag_issues(
            '.github/workflows/tag.yml', 'run: git tag v1\n'))

    def test_a_comment_or_another_git_command_passes(self):
        self.assertEqual('', self.tag_issues('tools/x.sh', '# git tag v1\ngit config tag.gpgsign true  # git tag\n'))

    def test_the_tag_scripts_may_tag(self):
        self.assertEqual('', self.tag_issues('tools/tag-release.sh', 'git tag -a v1\n'))

    def test_an_untracked_makefile_is_not_read(self):
        self.fixture.write('Makefile', 'release:\n\tgit tag v1\n')
        self.assertEqual([], self.issues(release_rust.rust_git_tag_issues))

    def test_a_checkout_git_cannot_list_is_a_tool_failure(self):
        shutil.rmtree(os.path.join(self.fixture.path, '.git'))
        with self.assertRaises(ToolFailure):
            self.issues(release_rust.rust_git_tag_issues)


if __name__ == '__main__':
    unittest.main()
