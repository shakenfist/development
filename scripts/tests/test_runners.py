#!/usr/bin/env python3

"""Tests for audit/checks/runners.py.

Run with: python3 scripts/tests/test_runners.py
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import runners  # noqa: E402
from audit.text import workflows  # noqa: E402
from tests.base import REPO_ROOT, run_check  # noqa: E402


VM_SIZE_LABELS = runners.VM_SIZE_LABELS
parse_runner_labels = workflows.parse_runner_labels
literal_runner_labels = workflows.literal_runner_labels


def check_vm_runner_size(path, props=None):
    return run_check(runners.VmRunnerSize(), path, props)


def check_self_hosted_runners(path, props=None):
    return run_check(runners.SelfHostedRunners(), path, props)


class RunnerLabelParsingTest(unittest.TestCase):
    """The two label parsers must agree about what an expression is.

    They answer different questions -- parse_runner_labels() gives up
    on a line it cannot fully resolve, literal_runner_labels() drops
    what it cannot resolve and keeps the rest -- but they must not
    disagree about which *elements* are unresolvable. Nothing tested
    these before, which is how a refactor changed one of them without
    a failure.
    """

    def test_an_expression_in_a_trailing_comment_is_not_a_label(self):
        # The comment is stripped before the expression test, so a
        # perfectly literal runs-on stays judgeable no matter what its
        # comment mentions. Testing the raw value here would skip the
        # line, and a skip in check_static_runner_tags() reports pass.
        value = '[self-hosted, static, s]  # was ${{ matrix.runner }}'
        self.assertEqual(
            ['self-hosted', 'static', 's'],
            parse_runner_labels(value))
        self.assertEqual(
            ['self-hosted', 'static', 's'],
            literal_runner_labels(value))

    def test_a_bare_expression_is_unjudgeable(self):
        self.assertIsNone(
            parse_runner_labels('${{ matrix.runner }}'))

    def test_one_expression_element_makes_the_line_unjudgeable(self):
        self.assertIsNone(parse_runner_labels(
            "[self-hosted, '${{ matrix.os }}', s]"))

    def test_a_comma_inside_an_expression_is_not_a_separator(self):
        # Splitting on it would leave fragments which no longer carry
        # the '${{' marking them unresolvable, so literal_runner_labels
        # would take them for labels -- and a fragment which happened
        # to read 'm' would excuse a sizeless job.
        value = ("[self-hosted, vm, "
                 "'${{ format('{0},{1}', matrix.a, matrix.b) }}']")
        self.assertEqual(
            ['self-hosted', 'vm'],
            literal_runner_labels(value))
        self.assertIsNone(parse_runner_labels(value))


class VmRunnerSizeTest(unittest.TestCase):
    """Every 'vm' runs-on has to name a size, and 'xs' is an answer."""

    # The phrases delimiting the size list in the specification. Named
    # rather than inlined so a reword fails with an explanation.
    SIZE_SENTENCE_START = '* Sizes are '
    SIZE_SENTENCE_END = 'variants.'

    def _repo(self, tmp, workflows):
        wdir = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(wdir)
        for name, content in workflows.items():
            with open(os.path.join(wdir, name), 'w') as f:
                f.write(content)
        return check_vm_runner_size(
            tmp, {'has_workflows_dir': True}
        )

    def _job(self, runs_on):
        return (
            'on:\n  pull_request:\njobs:\n  build:\n'
            '    runs-on: %s\n'
            '    steps:\n      - run: true\n' % runs_on
        )

    def test_a_sized_vm_job_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12, s]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_sizeless_vm_job_is_a_finding(self):
        # The defect this check exists for: no size element, so the
        # conductor falls back to xs and nobody chose it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])

    def test_a_vm_job_with_no_os_label_is_still_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm]')})
        self.assertEqual('fail', result['status'])

    def test_xs_counts_as_naming_a_size(self):
        # The rule is that the size is chosen, not that it is large.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12, xs]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_bigdisk_variants_count_as_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, 'debian-13', 'xl-bigdisk']")})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_literal_size_beside_a_matrix_expression_passes(self):
        # kerbside-patches' shape: the OS comes from the matrix, the
        # size is written literally beside it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, '${{ matrix.test.runs_on }}', 'xl']")})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_matrix_expression_alone_does_not_excuse_a_missing_size(self):
        # The sibling job in the same file writes the size literally,
        # so an expression here is not evidence a size arrives -- and
        # skipping the line would hide a real sizeless deploy job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, '${{ matrix.test.runs_on }}']")})
        self.assertEqual('fail', result['status'])

    def test_static_jobs_are_not_in_scope(self):
        # A static runner must name no size; that is the complementary
        # check's business, and this one must not contradict it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, static]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_the_offending_line_is_named(self):
        # A finding has to say where, because the fix is per-line.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'a.yml': self._job('[self-hosted, vm, debian-12]'),
                'b.yml': self._job('[self-hosted, vm, debian-12, m]'),
            })
        self.assertEqual('fail', result['status'])
        self.assertIn('a.yml:5', result['details'])
        self.assertNotIn('b.yml', result['details'])

    def test_a_repository_without_workflows_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = check_vm_runner_size(
                tmp, {'has_workflows_dir': False})
        self.assertEqual('not_applicable', result['status'])

    def test_a_trailing_comment_does_not_hide_the_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]  # sized later')})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])

    def test_every_offending_line_is_reported(self):
        # The fix is per-line, so a file with two sizeless jobs has to
        # name both -- stopping at the first would hide the second
        # until the next run.
        job = ('    runs-on: [self-hosted, vm, debian-12]\n'
               '    steps:\n      - run: true\n')
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n' + job +
                '  deploy:\n' + job)})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])
        self.assertIn('ci.yml:9', result['details'])

    def test_the_offender_names_the_labels_it_found(self):
        # Matching check_static_runner_tags(): the issue body is what
        # somebody fixing this reads, and the labels say which job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        self.assertIn('(self-hosted, vm, debian-12)', result['details'])

    def test_the_remediation_names_every_size_accepted(self):
        # A job which needs the disk must be able to find its answer
        # in the issue body, not only in the specification.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        for label in VM_SIZE_LABELS:
            self.assertIn(label, result['details'])

    def test_an_audit_ok_marker_exempts_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]  '
                '# audit-ok: vm-runner-size, sized by the caller')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_an_audit_ok_marker_on_the_line_above_exempts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n'
                '    # audit-ok: vm-runner-size, sized by the caller\n'
                '    runs-on: [self-hosted, vm, debian-12]\n'
                '    steps:\n      - run: true\n')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_block_sequence_runs_on_is_not_examined(self):
        # A documented limitation rather than a behaviour we want:
        # RUNS_ON_RE needs a value on the same line, so this shape is
        # invisible to the check. Nothing in scope writes one. If that
        # changes, this test is the place the decision is recorded.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n'
                '    runs-on:\n      - self-hosted\n      - vm\n'
                '      - debian-12\n'
                '    steps:\n      - run: true\n')})
        self.assertEqual('pass', result['status'])

    def test_the_size_vocabulary_matches_the_specification(self):
        # VM_SIZE_LABELS is a copy of CI_SIZES in shakenfist/private-ci
        # which this repository cannot reach, so the least it can do is
        # keep its own two copies in step.
        spec = os.path.join(
            REPO_ROOT, 'docs', 'audits', 'workflow-standards.md')
        with open(spec) as f:
            content = f.read()
        start = content.find(self.SIZE_SENTENCE_START)
        self.assertNotEqual(
            -1, start,
            f'{self.SIZE_SENTENCE_START!r} no longer introduces the '
            f'size list in workflow-standards.md')
        end = content.find(self.SIZE_SENTENCE_END, start)
        self.assertNotEqual(
            -1, end,
            f'{self.SIZE_SENTENCE_END!r} no longer ends the size list '
            f'in workflow-standards.md')
        documented = set(re.findall(r'`([^`]+)`', content[start:end]))
        self.assertEqual(set(VM_SIZE_LABELS), documented)


class SelfHostedRunnerLabelPositionTest(unittest.TestCase):
    """A GitHub-hosted label only counts where a runner can be named.

    The check scans every line rather than only 'runs-on:' lines, so
    that matrix values feeding 'runs-on: ${{ matrix.os }}' are caught.
    That breadth is what made it read image names, artifact names and
    job names as runner references.
    """

    def _check(self, workflow):
        with tempfile.TemporaryDirectory() as tmp:
            workflows = os.path.join(tmp, '.github', 'workflows')
            os.makedirs(workflows)
            with open(os.path.join(workflows, 'ci.yml'), 'w') as f:
                f.write(workflow)
            return check_self_hosted_runners(
                tmp, {'has_workflows_dir': True}
            )

    def test_a_bare_runs_on_is_reported(self):
        result = self._check('jobs:\n  a:\n    runs-on: ubuntu-latest\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ubuntu-latest', result['details'])

    def test_a_list_element_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: [foo, windows-latest]\n')
        self.assertEqual(result['status'], 'fail')

    def test_a_matrix_item_is_reported(self):
        # The reason the check scans every line: this feeds
        # runs-on: ${{ matrix.os }} somewhere else in the file.
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            '        os:\n          - ubuntu-24.04\n'
            '          - macos-latest\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ubuntu-24.04', result['details'])

    def test_a_quoted_matrix_item_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            "        os:\n          - 'windows-11-arm'\n")
        self.assertEqual(result['status'], 'fail')

    def test_a_matrix_include_mapping_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            '        include:\n          - os: ubuntu-24.04-arm\n')
        self.assertEqual(result['status'], 'fail')

    def test_self_hosted_on_the_same_line_is_not_reported(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: [self-hosted, ubuntu-24.04]\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_marked_exception_is_not_reported(self):
        result = self._check(
            'jobs:\n  a:\n'
            '    # audit-ok: github-hosted-runner\n'
            '    runs-on: macos-latest\n')
        self.assertEqual(result['status'], 'pass')

    def test_an_image_path_is_not_a_runner(self):
        # shakenfist's functional-tests.yml names Shaken Fist image
        # labels this way. The trailing path separator before the label
        # is what distinguishes it from a value position.
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            "      - run: echo 'sf://label/ci-images/ubuntu-2404'\n")
        self.assertEqual(result['status'], 'pass')

    def test_a_job_name_containing_a_label_is_not_a_runner(self):
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            "      - run: echo 'ubuntu-2404-slim-primary'\n")
        self.assertEqual(result['status'], 'pass')

    def test_a_label_inside_a_shell_command_is_not_a_runner(self):
        # shakenfist/actions uploads an artifact named ubuntu-2004 from
        # inside an ssh command. Reporting it asked for an audit-ok
        # marker on a line which never described a runner.
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            '      - run: |\n'
            '          ssh host "${setup} ubuntu-2004 /srv/ci/ubuntu:20.04"\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_name_ending_in_a_label_is_not_a_runner(self):
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n          name: build-ubuntu-latest\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_trailing_comment_does_not_hide_a_real_runner(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: ubuntu-latest  # why not\n')
        self.assertEqual(result['status'], 'fail')

    def test_no_workflows_directory_is_not_applicable(self):
        result = check_self_hosted_runners(
            '/nonexistent', {'has_workflows_dir': False}
        )
        self.assertEqual(result['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
