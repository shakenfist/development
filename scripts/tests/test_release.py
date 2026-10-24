#!/usr/bin/env python3

"""Tests for audit/checks/release.py.

Run with: python3 scripts/tests/test_release.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import release  # noqa: E402
from audit.text import workflows  # noqa: E402
from tests.base import REPO_ROOT, CheckTestCase  # noqa: E402


#: The CI gate every release.yml fixture carries, so that a fixture
#: written to exercise one rule is not failed by the gate rules for
#: lacking one. Jobs naming an environment need it for the same reason.
GATE_JOB = (
    '  ci-gate:\n'
    "    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')\n"
    '    runs-on: [self-hosted, static]\n'
    '    steps:\n'
    '      - uses: shakenfist/actions/require-ci-success@main\n'
    '        with:\n'
    '          workflows: ci.yml\n'
)

#: The workflow GATE_JOB names, triggered so that the gate can count it.
CI_WORKFLOW = (
    'on:\n'
    '  pull_request:\n'
    '  merge_group:\n'
    'jobs:\n'
    '  test:\n'
    '    runs-on: [self-hosted, static]\n'
    '    steps:\n'
    '      - run: make test\n'
)


def shipped_template():
    """The Python template's release.yml, as a repository fills it in.

    Its gate names {{CI_WORKFLOWS}}, a placeholder each repository
    replaces with its own CI files. Left in, the gate rules would read
    it as a workflow that does not exist, which is the right verdict on
    a copy nobody filled in and the wrong one on the template.
    """
    with open(os.path.join(REPO_ROOT, 'templates', 'release-automation', 'release.yml')) as f:
        return f.read().replace('{{CI_WORKFLOWS}}', 'ci.yml')


class ReleaseProcessTest(CheckTestCase):
    check_class = release.ReleaseProcess

    def compliant(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.workflow('release.yml', 'on: push\njobs:\n' + GATE_JOB)
        self.fixture.workflow('ci.yml', CI_WORKFLOW)
        self.fixture.write('RELEASE-SETUP.md', '# Setup\n')

    def test_with_neither_pyproject_nor_release_workflow_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No pyproject.toml or .github/workflows/release.yml')

    def test_a_release_workflow_alone_makes_it_apply(self):
        """A Rust or TypeScript release publishes as a Python one does.

        Such a repository gets the workflow findings and none of the
        packaging ones: it has no pyproject.toml to want a
        RELEASE-SETUP.md beside, and its requirements.txt, if any, is
        not this criterion's business.
        """
        self.fixture.write('requirements.txt', 'six\n')
        self.fixture.workflow('release.yml', 'on: push\njobs:\n  build:\n    runs-on: x\n')
        result = self.assert_fail(self.check(), containing='no job running')
        self.assertNotIn('requirements.txt', result['details'])
        self.assertNotIn('RELEASE-SETUP.md', result['details'])

    def test_a_gated_release_workflow_alone_passes(self):
        self.fixture.workflow('release.yml', 'on: push\njobs:\n' + GATE_JOB)
        self.fixture.workflow('ci.yml', CI_WORKFLOW)
        self.assert_pass(self.check())

    def test_a_compliant_project_passes(self):
        self.compliant()
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_leftover_release_script_fails(self):
        self.compliant()
        self.fixture.write('release.sh', '#!/bin/bash\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='release.sh still exists')

    def test_a_leftover_requirements_file_fails(self):
        self.compliant()
        self.fixture.write('requirements.txt', 'six\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='requirements.txt still exists')

    def test_a_missing_release_workflow_fails(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.write('RELEASE-SETUP.md', '# Setup\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='release.yml')

    def test_a_missing_setup_document_fails(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.workflow('release.yml', 'on: push\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='RELEASE-SETUP.md')

    def test_every_problem_is_reported_not_just_the_first(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.write('release.sh', '#!/bin/bash\n')
        result = self.assert_fail(self.check(has_pyproject_toml=True))
        self.assertIn('release.sh', result['details'])
        self.assertIn('RELEASE-SETUP.md', result['details'])

    # The asset-attaching half of the criterion. A release.yml can
    # satisfy every existence check above and still publish releases
    # with nothing attached to them, which is how this went unnoticed
    # across five repositories: the job reports success either way.

    RELEASE_JOB = (
        'name: Release\n'
        'jobs:\n' + GATE_JOB +
        '  github-release:\n'
        '    runs-on: [self-hosted, static]\n'
        '    steps:\n'
        '      - name: Download artifacts\n'
        '        uses: actions/download-artifact@v8\n'
        '%s'
        '      - name: Create GitHub Release\n'
        '        uses: softprops/action-gh-release@v3\n'
        '        with:\n'
        '%s'
    )

    NAMED_DOWNLOAD = ('        with:\n'
                      '          name: dist\n'
                      '          path: ${{ runner.temp }}/dist/\n')
    GUARDED_FILES = ('          files: ${{ runner.temp }}/dist/*\n'
                     '          fail_on_unmatched_files: true\n')
    PLAIN_FILES = '          files: ${{ runner.temp }}/dist/*\n'

    def release_workflow(self, download='', release=PLAIN_FILES):
        self.compliant()
        self.fixture.workflow('release.yml', self.RELEASE_JOB % (download, release))

    def test_a_correctly_wired_release_passes(self):
        self.release_workflow(self.NAMED_DOWNLOAD, self.GUARDED_FILES)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_bare_download_fails(self):
        self.release_workflow(release=self.GUARDED_FILES)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='downloads artifacts without "name:"')

    def test_an_unguarded_glob_fails(self):
        self.release_workflow(self.NAMED_DOWNLOAD)
        self.assert_fail(
            self.check(has_pyproject_toml=True),
            containing='without "fail_on_unmatched_files: true"')

    def test_fail_on_unmatched_files_must_be_true_not_merely_present(self):
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            '          files: ${{ runner.temp }}/dist/*\n'
            '          fail_on_unmatched_files: false\n')
        self.assert_fail(
            self.check(has_pyproject_toml=True),
            containing='without "fail_on_unmatched_files: true"')

    def test_merge_multiple_also_names_the_destination(self):
        """Downloading every artifact is fine when they are merged flat.

        A project which builds more than one distribution cannot name a
        single artifact, so the criterion is that the destination is
        determined -- not that it is spelled one particular way.
        """
        self.release_workflow(
            '        with:\n'
            '          merge-multiple: true\n'
            '          path: ${{ runner.temp }}/dist/\n',
            self.GUARDED_FILES)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_quoted_true_is_true(self):
        """An action receives "true" and true as the same string."""
        for quote in ('"', "'"):
            self.release_workflow(
                '        with:\n'
                f'          merge-multiple: {quote}true{quote}\n'
                '          path: ${{ runner.temp }}/dist/\n',
                '          files: ${{ runner.temp }}/dist/*\n'
                f'          fail_on_unmatched_files: {quote}true{quote}\n')
            self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_release_attaching_no_files_is_not_asked_about_globs(self):
        """No "files:" means no assets by choice, not a broken glob."""
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            '          generate_release_notes: true\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_commented_out_defect_does_not_count(self):
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            self.GUARDED_FILES
            + '        # uses: actions/download-artifact@v8 bare\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_an_inline_comment_does_not_hide_the_value(self):
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            '          files: ${{ runner.temp }}/dist/*\n'
            '          fail_on_unmatched_files: true  # else empty\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_the_shipped_template_satisfies_the_criterion(self):
        """The template we tell projects to copy must itself pass.

        This is the regression that mattered: the template carried the
        bare download for as long as the criterion did not look at it.
        """
        self.compliant()
        self.fixture.workflow('release.yml', shipped_template())
        self.assert_pass(self.check(has_pyproject_toml=True))

    # The dispatch-guard half of the criterion. release.yml offers
    # workflow_dispatch as a build-and-check smoke test, which is only
    # true while the publishing jobs decline to run on the branch ref a
    # dispatch arrives on. Seven of nine repositories never received
    # the guards the template grew.

    GUARD = "    if: startsWith(github.ref, 'refs/tags/v')\n"

    def dispatch_workflow(self, sign_guard='', release_guard='',
                          dispatch=True, build_guard=''):
        """A release.yml with a build job and two publishing jobs."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  push:\n'
            "    tags: ['v*']\n"
            + ('  workflow_dispatch:\n' if dispatch else '')
            + 'jobs:\n' + GATE_JOB +
            '  build:\n'
            + build_guard +
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n'
            '          name: dist\n'
            '  sign-tag:\n'
            + sign_guard +
            '    needs: ci-gate\n'
            '    environment: release\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - name: Create signed tag\n'
            '        run: git push origin "${TAG_NAME}" --force\n'
            '  github-release:\n'
            + release_guard +
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: softprops/action-gh-release@v3\n'
            '        with:\n'
            '          files: dist/*\n'
            '          fail_on_unmatched_files: true\n')

    def test_unguarded_publishing_jobs_fail_when_dispatchable(self):
        self.dispatch_workflow()
        result = self.assert_fail(self.check(has_pyproject_toml=True),
                                  containing='not confined to tags')
        self.assertIn('sign-tag', result['details'])
        self.assertIn('github-release', result['details'])

    def test_guarded_publishing_jobs_pass(self):
        self.dispatch_workflow(sign_guard=self.PUSH_GUARD,
                               release_guard=self.PUSH_GUARD)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_one_unguarded_job_is_named_on_its_own(self):
        """The finding says which job, not merely that one exists."""
        self.dispatch_workflow(sign_guard=self.PUSH_GUARD)
        result = self.assert_fail(self.check(has_pyproject_toml=True),
                                  containing='github-release')
        self.assertNotIn('sign-tag', result['details'])

    def test_without_a_dispatch_trigger_the_guards_are_not_required(self):
        """No manual trigger means no branch ref to defend against."""
        self.dispatch_workflow(dispatch=False)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_build_job_is_never_required_to_be_guarded(self):
        """The false positive that would break the smoke test.

        Building on a dispatch is the whole point of offering one, so a
        criterion which demanded the guard everywhere would remove the
        feature rather than make it safe. The build job here is
        unguarded in every other case above too; this states it.
        """
        self.dispatch_workflow(sign_guard=self.PUSH_GUARD,
                               release_guard=self.PUSH_GUARD)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_uploading_an_artifact_is_not_publishing(self):
        """A job whose only output is visible to later jobs is a build."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  workflow_dispatch:\n'
            'jobs:\n' + GATE_JOB +
            '  build:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n'
            '          name: dist\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_ref_type_is_an_accepted_spelling_of_the_guard(self):
        """The criterion is the property, not one way of writing it."""
        guard = ("    if: github.event_name == 'push' && "
                 "github.ref_type == 'tag'\n")
        self.dispatch_workflow(sign_guard=guard, release_guard=guard)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_step_level_if_does_not_guard_the_job(self):
        """An `if:` on a step leaves the job itself free to run."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  workflow_dispatch:\n'
            'jobs:\n' + GATE_JOB +
            '  sign-tag:\n'
            '    needs: ci-gate\n'
            '    environment: release\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - name: Create signed tag\n'
            "        if: startsWith(github.ref, 'refs/tags/v')\n"
            '        run: git tag -s\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='sign-tag')

    def test_the_shipped_template_satisfies_every_criterion(self):
        """One test for the whole file, over every criterion here.

        This is the regression that keeps mattering: each defect lived
        in the template for exactly as long as nothing measured it.
        """
        self.compliant()
        self.fixture.workflow('release.yml', shipped_template())
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_the_shipped_template_gates_its_build(self):
        """The template's build must wait for ci-gate on a tag push.

        A dispatch dry run skips the gate, so build's guard lets a
        skipped gate through, but only when the event is not a push.
        Dropping the needs, or the event clause, would let an ungated
        build reach the publishing jobs.
        """
        with open(os.path.join(
                REPO_ROOT, 'templates', 'release-automation',
                'release.yml')) as f:
            text = f.read()
        self.assertIn('uses: shakenfist/actions/require-ci-success@', text)
        start = text.index('\n  build:\n')
        end = text.index('\n  sign-tag:\n', start)
        build = text[start:end]
        self.assertIn('needs: ci-gate', build)
        self.assertIn("needs.ci-gate.result == 'success'", build)
        # always() would run build after a cancelled gate, and a bare
        # leading ! is a YAML tag rather than an expression.
        self.assertIn('${{ !cancelled() &&', build)
        self.assertNotIn('always()', build)
        self.assertIn("github.event_name != 'push' && needs.ci-gate.result == 'skipped'",
                      ' '.join(build.split()))
        self.assertIn('uses: shakenfist/actions/check-dist@', build)

        # The gate is skipped on a dispatch, which is what keeps the
        # dry run working; it reads runs and nothing else.
        start = text.index('\n  ci-gate:\n')
        gate = text[start:text.index('\n  build:\n', start)]
        self.assertIn("if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')", gate)
        permissions = gate[gate.index('    permissions:\n'):gate.index('    steps:\n')]
        self.assertEqual(['actions: read', 'contents: read'],
                         sorted(line.strip() for line in permissions.splitlines()[1:] if line.strip()))

    # The workspace half of the criterion. A job which skips checkout
    # inherits whatever the previous job left on that runner, and
    # download-artifact adds to a directory rather than replacing it,
    # so such a job must download into the per-job runner.temp.
    # Checkout cleans, so a job which checks out is exempt -- which is
    # how the real publish-pypi qualifies, since gh-action-pypi-publish
    # runs in a container that can only see the workspace.

    TEMP = '${{ runner.temp }}/dist/'

    def publishing_workflow(self, path=TEMP, subject=None, checkout='',
                            packages=None, publish=True):
        """A publish job, parameterised on where it reads and cleans.

        `publish` controls whether the container action is present,
        because that decides which of the two path criteria owns the
        job: with it, the container rule; without it, the workspace
        rule. A fixture carrying it unconditionally would leave the
        workspace rule untested.

        A fresh fixture per call, via CheckTestCase.fresh_fixture,
        because the case proving the two path criteria never both
        fire runs this eight times in a nested loop. Every caller
        starts here, so nothing is written before the rebuild.
        """
        subject = self.TEMP + '*' if subject is None else subject
        self.fresh_fixture()
        self.compliant()
        steps = checkout
        steps += (
            '      - uses: actions/download-artifact@v8\n'
            '        with:\n'
            '          name: dist\n'
            f'          path: {path}\n'
            '      - uses: actions/attest-build-provenance@v4\n'
            '        with:\n'
            f"          subject-path: '{subject}'\n")
        if publish:
            steps += '      - uses: pypa/gh-action-pypi-publish@release/v1\n'
            if packages is not None:
                steps += ('        with:\n'
                          f'          packages-dir: {packages}\n')
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  push:\n'
            "    tags: ['v*']\n"
            'jobs:\n' + GATE_JOB +
            '  publish-pypi:\n'
            '    needs: ci-gate\n'
            '    environment: release\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n' + steps)

    CHECKOUT = '      - uses: actions/checkout@v7\n'
    DIRTY_CHECKOUT = ('      - uses: actions/checkout@v7\n'
                      '        with:\n'
                      '          clean: false\n')

    def test_downloading_into_the_shared_workspace_fails(self):
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 publish=False)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='does not start from a cleaned checkout')

    def test_a_checkout_out_workspace_may_be_used(self):
        """Checkout runs git clean -ffdx, which removes a stale dist/.

        This is the shape the real publish-pypi has to take: the PyPI
        publish delegates to a Docker container action, which sees the
        workspace at /github/workspace and nothing else of the runner's
        filesystem, so its paths must be relative to a workspace that
        something has cleaned.
        """
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 checkout=self.CHECKOUT, packages='dist/')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_checkout_which_does_not_clean_earns_nothing(self):
        """clean: false hands back the very workspace at issue."""
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 checkout=self.DIRTY_CHECKOUT, publish=False)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='does not start from a cleaned checkout')

    def test_a_quoted_clean_false_earns_nothing_either(self):
        """YAML 'false' is the string, which Actions still reads as off."""
        self.publishing_workflow(
            path='dist/', subject='dist/*', publish=False,
            checkout=('      - uses: actions/checkout@v7\n'
                      '        with:\n'
                      "          clean: 'false'\n"))
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='does not start from a cleaned checkout')

    def test_an_explicit_clean_true_is_fine(self):
        self.publishing_workflow(
            path='dist/', subject='dist/*',
            checkout=('      - uses: actions/checkout@v7\n'
                      '        with:\n'
                      '          clean: true\n'))
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_temp_scoped_job_without_checkout_passes(self):
        """The github-release shape: no checkout, everything in temp."""
        self.publishing_workflow(publish=False)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_packages_dir_is_not_required_to_be_absolute(self):
        """The criterion must not ask for what the container forbids.

        packages-dir has to be relative to the workspace, so a job
        which is otherwise correct is not faulted for it -- nor for
        omitting it, since the action's own default is a relative
        ./dist.
        """
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 checkout=self.CHECKOUT, packages=None)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_build_job_uploading_to_dist_is_not_judged(self):
        """Uploading is not downloading, and build jobs check out."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  workflow_dispatch:\n'
            'jobs:\n' + GATE_JOB +
            '  build:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/checkout@v7\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n'
            '          name: dist\n'
            '          path: dist/\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    # The container constraint, and the reason it takes precedence over
    # the rule above. gh-action-pypi-publish delegates to a Docker
    # container action; the runner mounts RUNNER_TEMP at
    # /github/runner_temp while "${{ runner.temp }}" expands to the host
    # path, so a job can satisfy every other criterion here and still
    # fail every upload. That configuration shipped and had to be found
    # by running it.

    def test_a_container_action_may_not_be_given_runner_temp(self):
        self.publishing_workflow(checkout=self.CHECKOUT, packages=self.TEMP)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='Docker container')

    def test_a_container_action_may_not_be_given_an_absolute_path(self):
        """Absolute is wrong even when it points into the workspace.

        The host and the container disagree about where the workspace
        is, so an absolute path is right in at most one of them.
        """
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 checkout=self.CHECKOUT,
                                 packages='/github/workspace/dist/')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='relative to the workspace')

    def test_a_container_job_downloading_to_temp_fails(self):
        """Not just the input: what feeds it has to be readable too."""
        self.publishing_workflow(checkout=self.CHECKOUT, packages='dist/')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='cannot read')

    def test_a_container_job_must_check_out(self):
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 packages='dist/')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='does not check out')

    def test_the_proven_good_container_shape_passes(self):
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 checkout=self.CHECKOUT, packages='dist/')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_the_two_path_criteria_never_both_fire(self):
        """They ask for opposite things, so they must not overlap.

        A job feeding a container action is governed by the container
        rule; the workspace rule steps aside for it. Otherwise the
        report would tell a reader to move the download into
        runner.temp and out of it in the same breath.
        """
        for checkout in ('', self.CHECKOUT):
            for path in ('dist/', self.TEMP):
                for publish in (True, False):
                    self.publishing_workflow(
                        path=path, subject=path + '*', checkout=checkout,
                        packages=path if publish else None, publish=publish)
                    workspace = release.release_workspace_issues(
                        self.repo())
                    container = release.release_container_path_issues(
                        self.repo())
                    self.assertFalse(
                        workspace and container,
                        'both fired for checkout=%r path=%r publish=%r: %s / %s'
                        % (bool(checkout), path, publish, workspace, container))

    def test_a_javascript_action_is_not_subject_to_the_rule(self):
        """action-gh-release runs on the host; runner.temp is fine."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  push:\n'
            "    tags: ['v*']\n"
            'jobs:\n' + GATE_JOB +
            '  github-release:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/download-artifact@v8\n'
            '        with:\n'
            '          name: dist\n'
            f'          path: {self.TEMP}\n'
            '      - uses: softprops/action-gh-release@v3\n'
            '        with:\n'
            f'          files: {self.TEMP}*\n'
            '          fail_on_unmatched_files: true\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    # Where the download goes and where the consumers read are separate
    # questions. The second is asked of every job that downloads,
    # because moving one and not the other is how this breaks.

    def test_a_consumer_left_behind_by_the_download_fails(self):
        self.publishing_workflow(subject='dist/*', publish=False)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='reads somewhere else')

    def test_a_consumer_agreeing_with_the_download_passes(self):
        self.publishing_workflow(path='dist/', subject='dist/*',
                                 checkout=self.CHECKOUT)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_quoting_and_the_glob_are_not_disagreement(self):
        """'dist/*' and dist/ name the same directory."""
        self.publishing_workflow(path='dist', subject='dist/*',
                                 checkout=self.CHECKOUT)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_glob_is_compared_by_its_directory(self):
        """dist/*.tar.gz reads dist/, which is where the download went."""
        for subject in ('dist/*.tar.gz', 'dist/*.whl', 'dist/**/*.whl'):
            self.publishing_workflow(path='dist/', subject=subject,
                                     checkout=self.CHECKOUT)
            self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_glob_in_another_directory_still_disagrees(self):
        self.publishing_workflow(path='dist/', subject='build/*.whl',
                                 checkout=self.CHECKOUT)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing="\"subject-path: 'build/*.whl'\" reads somewhere else")

    def test_a_block_scalar_is_not_read_as_a_path(self):
        """'files: |' introduces the paths; it is not one itself."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  push:\n'
            "    tags: ['v*']\n"
            'jobs:\n' + GATE_JOB +
            '  github-release:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/download-artifact@v8\n'
            '        with:\n'
            '          name: dist\n'
            f'          path: {self.TEMP}\n'
            '      - uses: softprops/action-gh-release@v3\n'
            '        with:\n'
            '          files: |\n'
            f'            {self.TEMP}*.tar.gz\n'
            f'            {self.TEMP}*.whl\n'
            '          fail_on_unmatched_files: true\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_files_input_on_an_unrelated_step_is_not_judged(self):
        """"files:" only means the distribution on the release action."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  push:\n'
            "    tags: ['v*']\n"
            'jobs:\n' + GATE_JOB +
            '  github-release:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/download-artifact@v8\n'
            '        with:\n'
            '          name: dist\n'
            f'          path: {self.TEMP}\n'
            '      - uses: some/other-action@v1\n'
            '        with:\n'
            '          files: coverage/*\n'
            '      - uses: softprops/action-gh-release@v3\n'
            '        with:\n'
            f'          files: {self.TEMP}*\n'
            '          fail_on_unmatched_files: true\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    # The event half of the guard. A dispatch can be aimed at a tag, so
    # the ref test alone still lets a manual run re-sign and force-push
    # a tag that already exists.

    PUSH_GUARD = ("    if: github.event_name == 'push' && "
                  "startsWith(github.ref, 'refs/tags/v')\n")

    def test_a_ref_only_guard_fails(self):
        self.dispatch_workflow(sign_guard=self.GUARD,
                               release_guard=self.GUARD)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='the ref but not the event')

    def test_a_push_and_ref_guard_passes(self):
        self.dispatch_workflow(sign_guard=self.PUSH_GUARD,
                               release_guard=self.PUSH_GUARD)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_the_ref_only_finding_names_only_the_ref_only_jobs(self):
        self.dispatch_workflow(sign_guard=self.PUSH_GUARD,
                               release_guard=self.GUARD)
        result = self.assert_fail(self.check(has_pyproject_toml=True),
                                  containing='the ref but not the event')
        self.assertIn('github-release', result['details'])
        self.assertNotIn('sign-tag', result['details'])

    def test_an_unguarded_job_is_not_also_reported_as_ref_only(self):
        """The two findings partition the jobs; they do not overlap."""
        self.dispatch_workflow(sign_guard='', release_guard=self.PUSH_GUARD)
        result = self.assert_fail(self.check(has_pyproject_toml=True),
                                  containing='not confined to tags')
        self.assertNotIn('the ref but not the event', result['details'])

    def test_a_folded_guard_is_read_as_a_guard(self):
        """A condition written over several lines is still a condition.

        Read as absent, a folded "if:" would make the guard criterion
        report a correctly guarded repository as unguarded -- and, worse
        for a check the fleet is being measured against, would let a
        repository pass criterion two by writing its guard in a way the
        parser could not see.
        """
        folded = ('    if: >-\n'
                  "      github.event_name == 'push' &&\n"
                  "      startsWith(github.ref, 'refs/tags/v')\n")
        self.dispatch_workflow(sign_guard=folded, release_guard=folded)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_folded_ref_only_guard_is_still_caught(self):
        """Folding must not hide a missing clause either."""
        folded = ('    if: >-\n'
                  "      startsWith(github.ref, 'refs/tags/v')\n")
        self.dispatch_workflow(sign_guard=folded, release_guard=folded)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='the ref but not the event')

    def test_a_ref_only_guard_passes_without_a_dispatch_trigger(self):
        """No manual trigger means no event to disambiguate."""
        self.dispatch_workflow(sign_guard=self.GUARD,
                               release_guard=self.GUARD, dispatch=False)
        self.assert_pass(self.check(has_pyproject_toml=True))


PUSH_TAG_GUARD = "    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')\n"

#: The template's build guard: a skipped gate is let through, but only
#: off push, so that a workflow_dispatch dry run still builds.
TEMPLATE_BUILD_GUARD = (
    '    if: >-\n'
    "      ${{ !cancelled() && (needs.ci-gate.result == 'success' ||\n"
    "      (github.event_name != 'push' && needs.ci-gate.result == 'skipped')) }}\n"
)


def job(name, needs=None, condition='', environment=False, steps=None):
    """One job of a fixture release.yml."""
    text = f'  {name}:\n'
    if needs:
        text += f'    needs: {needs}\n'
    text += condition
    if environment:
        text += '    environment: release\n'
    text += '    runs-on: [self-hosted, static]\n    steps:\n'
    text += steps or '      - run: make\n'
    return text


class CiGateTest(CheckTestCase):
    """Every job naming an environment waits for the CI gate."""

    check_class = release.ReleaseProcess

    def release(self, *jobs):
        self.fixture.workflow('ci.yml', CI_WORKFLOW)
        self.fixture.workflow('release.yml', 'on:\n  push:\n    tags: [v*]\njobs:\n' + ''.join(jobs))

    def issues(self):
        return release.release_ci_gate_issues(self.repo())

    def test_the_shipped_template_passes(self):
        self.fixture.workflow('ci.yml', CI_WORKFLOW)
        self.fixture.workflow('release.yml', shipped_template())
        self.assertEqual([], self.issues())

    def test_an_environment_job_downstream_of_the_gate_passes(self):
        self.release(GATE_JOB, job('build', needs='ci-gate'),
                     job('publish', needs='[build]', condition=PUSH_TAG_GUARD, environment=True))
        self.assertEqual([], self.issues())

    def test_a_release_without_a_gate_fails_the_check(self):
        self.release(job('build'), job('publish', needs='build', environment=True))
        self.assert_fail(self.check(), containing='no job running shakenfist/actions/require-ci-success')

    def test_a_locally_vendored_gate_is_not_the_gate(self):
        self.release(job('ci-gate', steps='      - uses: ./.github/actions/require-ci-success\n'))
        self.assertIn('no job running', ' '.join(self.issues()))

    def test_two_gates_fail(self):
        self.release(GATE_JOB, GATE_JOB.replace('ci-gate', 'second-gate'))
        self.assertIn('in 2 jobs (ci-gate, second-gate)', ' '.join(self.issues()))

    def test_an_environment_job_off_the_gate_fails(self):
        self.release(GATE_JOB, job('build'),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        result = self.assert_fail(self.check(), containing='the publish job names an environment but is not '
                                                           'downstream of ci-gate')
        self.assertNotIn('the build job', result['details'])

    def test_every_spelling_of_needs_reaches_the_gate(self):
        for needs in ('ci-gate', '[ci-gate]', "['ci-gate']", '\n      - ci-gate'):
            self.fresh_fixture()
            self.release(GATE_JOB, job('publish', needs=needs, condition=PUSH_TAG_GUARD, environment=True))
            self.assertEqual([], self.issues(), needs)

    def test_every_spelling_of_environment_is_an_environment(self):
        for environment in ('release', '{name: release}', '\n      name: release'):
            self.fresh_fixture()
            publish = job('publish').replace('    runs-on', f'    environment: {environment}\n    runs-on')
            self.release(GATE_JOB, publish)
            self.assertIn('names an environment', ' '.join(self.issues()), environment)

    def test_a_cycle_is_reported_rather_than_raised(self):
        self.release(GATE_JOB, job('a', needs='[ci-gate, b]'), job('b', needs='a', environment=True))
        self.assertEqual(['the jobs a -> b -> a need each other in a cycle, so GitHub will not run the workflow '
                          'and which jobs wait for CI cannot be read'], self.issues())

    def test_a_job_needing_itself_is_a_cycle(self):
        self.release(GATE_JOB, job('a', needs='[ci-gate, a]'))
        self.assertIn('a -> a', ' '.join(self.issues()))

    def test_an_unknown_need_is_reported_rather_than_raised(self):
        self.release(GATE_JOB, job('publish', needs='[ci-gate, bulid]', environment=True))
        self.assertEqual(['the publish job needs bulid, which is not a job in release.yml, so GitHub will not '
                          'run the workflow and which jobs wait for CI cannot be read'], self.issues())

    def test_not_cancelled_with_the_success_clause_passes(self):
        self.release(GATE_JOB,
                     job('build', needs='ci-gate',
                         condition="    if: ${{ !cancelled() && needs.ci-gate.result == 'success' }}\n"),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertEqual([], self.issues())

    def test_not_cancelled_without_the_success_clause_fails(self):
        self.release(GATE_JOB,
                     job('build', needs='ci-gate', condition='    if: ${{ !cancelled() }}\n'),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assert_fail(self.check(), containing="the build job runs under always() or !cancelled() without "
                                                  "requiring needs.ci-gate.result == 'success'")

    def test_always_without_the_success_clause_fails(self):
        self.release(GATE_JOB,
                     job('build', needs='ci-gate', condition='    if: always()\n'),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertIn('the build job runs under always()', ' '.join(self.issues()))

    def test_always_with_the_success_clause_passes(self):
        self.release(GATE_JOB,
                     job('build', needs='ci-gate',
                         condition="    if: always() && needs.ci-gate.result == 'success'\n"),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertEqual([], self.issues())

    def test_an_environment_job_is_itself_on_the_path(self):
        self.release(GATE_JOB, job('publish', needs='ci-gate', condition='    if: always()\n', environment=True))
        self.assertIn('the publish job runs under always()', ' '.join(self.issues()))

    def test_each_need_wants_its_own_clause(self):
        """A clause for one need says nothing about the other."""
        self.release(GATE_JOB, job('build', needs='ci-gate'), job('docs', needs='ci-gate'),
                     job('publish', needs='[build, docs]', environment=True,
                         condition="    if: ${{ !cancelled() && needs.build.result == 'success' }}\n"))
        issues = ' '.join(self.issues())
        self.assertIn("needs.docs.result == 'success'", issues)
        self.assertNotIn("needs.build.result == 'success'", issues)

    def test_the_bracket_spelling_is_the_clause(self):
        self.release(GATE_JOB,
                     job('build', needs='ci-gate',
                         condition="    if: ${{ !cancelled() && needs['ci-gate'].result == 'success' }}\n"),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertEqual([], self.issues())

    def test_a_clause_for_a_longer_name_is_not_the_clause(self):
        """needs.ci-gate-two.result is not needs.ci-gate.result."""
        self.release(GATE_JOB, GATE_JOB.replace('ci-gate', 'other').replace('require-ci-success', 'x'),
                     job('build', needs='[ci-gate, other]',
                         condition="    if: ${{ !cancelled() && needs.other.result == 'success' && "
                                   "needs.ci-gate-two.result == 'success' }}\n"),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertIn("needs.ci-gate.result == 'success'", ' '.join(self.issues()))

    def test_a_job_off_the_path_to_an_environment_is_not_judged(self):
        """A build nothing publishes from carries no failed gate anywhere."""
        self.release(GATE_JOB, job('smoke', needs='ci-gate', condition='    if: always()\n'),
                     job('publish', needs='ci-gate', condition=PUSH_TAG_GUARD, environment=True))
        self.assertEqual([], self.issues())

    def test_the_template_admission_of_a_skipped_gate_passes(self):
        self.release(GATE_JOB, job('build', needs='ci-gate', condition=TEMPLATE_BUILD_GUARD),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertEqual([], self.issues())

    def test_a_skipped_gate_admitted_on_a_push_fails(self):
        self.release(GATE_JOB,
                     job('build', needs='ci-gate',
                         condition="    if: ${{ !cancelled() && (needs.ci-gate.result == 'success' || "
                                   "needs.ci-gate.result == 'skipped') }}\n"),
                     job('publish', needs='build', condition=PUSH_TAG_GUARD, environment=True))
        self.assertIn('the build job lets ci-gate through on a result other than success without confining '
                      "that to \"github.event_name != 'push'\"", ' '.join(self.issues()))

    def test_an_admission_needs_the_environment_jobs_below_confined_to_a_pushed_tag(self):
        ref_only = "    if: startsWith(github.ref, 'refs/tags/v')\n"
        self.release(GATE_JOB, job('build', needs='ci-gate', condition=TEMPLATE_BUILD_GUARD),
                     job('sign', needs='build', condition=PUSH_TAG_GUARD, environment=True),
                     job('publish', needs='build', condition=ref_only, environment=True))
        issues = self.issues()
        self.assertEqual(1, len(issues), issues)
        self.assertIn('and publish is not confined to one', issues[0])

    def test_an_environment_job_admitting_a_skip_must_itself_be_confined(self):
        """Off push is not enough where the admitting job publishes.

        A workflow_dispatch aimed at a tag skips the gate, so build is
        skipped, and an environment job that lets a skipped build
        through without a push guard of its own then runs.
        """
        self.release(GATE_JOB, job('build', needs='ci-gate'),
                     job('publish', needs='build', environment=True,
                         condition="    if: >-\n"
                                   "      ${{ !cancelled() && (needs.build.result == 'success' || "
                                   "(github.event_name != 'push' && needs.build.result == 'skipped')) }}\n"))
        issues = self.issues()
        self.assertEqual(1, len(issues), issues)
        self.assertIn('and publish is not confined to one', issues[0])

    def test_an_environment_job_admitting_a_skipped_build_fails(self):
        """The admission is judged wherever it is, not only on build.

        Here the publish job lets a skipped build through. On a tag push
        whose gate failed, build is skipped, and publish would run.
        """
        self.release(GATE_JOB, job('build', needs='ci-gate'),
                     job('publish', needs='build', environment=True,
                         condition="    if: >-\n"
                                   "      ${{ !cancelled() && github.event_name == 'push' && "
                                   "startsWith(github.ref, 'refs/tags/v') && (needs.build.result == 'success' "
                                   "|| needs.build.result == 'skipped') }}\n"))
        self.assertIn('the publish job lets build through', ' '.join(self.issues()))


class CiTriggerTest(CheckTestCase):
    """The workflows the gate names can give it a run to count."""

    check_class = release.ReleaseProcess

    GROUP_ON_REF = ('concurrency:\n'
                    '  group: ${{ github.workflow }}-${{ github.ref }}\n'
                    '  cancel-in-progress: true\n')

    def gated(self, ci, workflows='ci.yml', name='ci.yml'):
        self.fixture.workflow('release.yml', 'on: push\njobs:\n'
                              + GATE_JOB.replace('workflows: ci.yml', f'workflows: {workflows}'))
        if ci is not None:
            self.fixture.workflow(name, ci)

    def issues(self):
        return release.release_ci_trigger_issues(self.repo())

    def ci(self, on, concurrency='', job_extra='', steps='      - run: make test\n'):
        return (on + concurrency + 'jobs:\n  test:\n' + job_extra
                + '    runs-on: [self-hosted, static]\n    steps:\n' + steps)

    def test_a_merge_group_workflow_passes(self):
        self.gated(CI_WORKFLOW)
        self.assertEqual([], self.issues())

    def test_a_flow_style_merge_group_trigger_passes(self):
        self.gated(self.ci('on: [pull_request, merge_group]\n'))
        self.assertEqual([], self.issues())

    def test_a_missing_workflow_fails_the_check(self):
        self.gated(None)
        self.assert_fail(self.check(), containing="release.yml's gate names ci.yml, which is not a workflow in "
                                                  '.github/workflows/')

    def test_a_path_is_not_a_bare_name(self):
        self.gated(CI_WORKFLOW, workflows='.github/workflows/ci.yml')
        self.assertIn('names .github/workflows/ci.yml, which is not a workflow', ' '.join(self.issues()))

    def test_every_named_workflow_is_read(self):
        self.gated(CI_WORKFLOW, workflows='|\n            ci.yml\n            "functional.yml"\n')
        self.assertEqual(["release.yml's gate names functional.yml, which is not a workflow in "
                          '.github/workflows/, so the gate finds no run of it and every release fails; name '
                          'each workflow by its bare file name'], self.issues())

    def test_a_pull_request_only_workflow_fails(self):
        self.gated(self.ci('on:\n  pull_request:\n'))
        self.assertIn('runs neither on merge_group nor on a push to develop', ' '.join(self.issues()))

    def test_a_commented_merge_group_is_no_trigger(self):
        self.gated(self.ci('on:\n  pull_request:\n  # merge_group:\n'))
        self.assertIn('runs neither', ' '.join(self.issues()))

    def test_a_push_without_a_branches_filter_passes(self):
        self.gated(self.ci('on:\n  push:\n'))
        self.assertEqual([], self.issues())

    def test_a_flow_style_push_passes(self):
        self.gated(self.ci('on: [push]\n'))
        self.assertEqual([], self.issues())

    def test_a_push_filtered_to_the_default_branch_passes(self):
        for branches in ('[develop]', 'develop', "\n      - 'develop'", '[main, "dev*"]'):
            self.fresh_fixture()
            self.gated(self.ci(f'on:\n  push:\n    branches: {branches}\n'))
            self.assertEqual([], self.issues(), branches)

    def test_a_push_filtered_to_another_branch_fails(self):
        for trigger in ('    branches: [main]\n', '    branches-ignore: [develop]\n',
                        "    tags: ['v*']\n", "    branches: ['*', '!develop']\n"):
            self.fresh_fixture()
            self.gated(self.ci('on:\n  push:\n' + trigger))
            self.assertIn('runs neither', ' '.join(self.issues()), trigger)

    def test_a_negated_branch_can_be_readmitted(self):
        self.gated(self.ci("on:\n  push:\n    branches: ['!develop', develop]\n"))
        self.assertEqual([], self.issues())

    def test_a_ref_keyed_group_cancelling_push_runs_fails_the_check(self):
        self.gated(self.ci('on:\n  push:\n    branches: [develop]\n', concurrency=self.GROUP_ON_REF))
        self.assert_fail(self.check(), containing='ci.yml, which the gate counts on a push to develop, keys the '
                                                  'concurrency group of the workflow on github.ref, so '
                                                  'successive commits share one group')

    def test_a_job_level_ref_keyed_group_fails(self):
        self.gated(self.ci('on:\n  push:\n', job_extra='    ' + self.GROUP_ON_REF.replace('\n  ', '\n      ')))
        self.assertIn('the concurrency group of the test job', ' '.join(self.issues()))

    def test_the_merge_group_key_still_cancels_push_runs(self):
        """The queue's key falls back to the ref, which a push shares."""
        self.gated(self.ci(
            'on:\n  push:\n  merge_group:\n',
            concurrency='concurrency:\n  group: >-\n'
                        "    ${{ github.workflow }}-${{ github.event_name == 'merge_group' && "
                        "format('merge_group-{0}', github.event.merge_group.base_ref) || github.ref }}\n"
                        '  cancel-in-progress: true\n'))
        self.assertIn('keys the concurrency group of the workflow on github.ref', ' '.join(self.issues()))

    def test_a_group_keyed_by_sha_on_push_passes(self):
        for group in ("${{ github.workflow }}-${{ github.event_name == 'push' && github.sha || github.ref }}",
                      "${{ github.workflow }}-${{ github.event_name != 'push' && github.ref || github.sha }}",
                      '${{ github.workflow }}-${{ github.sha }}'):
            self.fresh_fixture()
            self.gated(self.ci('on:\n  push:\n', concurrency=f'concurrency:\n  group: {group}\n'
                                                             '  cancel-in-progress: true\n'))
            self.assertEqual([], self.issues(), group)

    def test_a_ref_keyed_group_which_does_not_cancel_in_progress_still_fails(self):
        """A third arrival cancels the pending run whatever the flag says."""
        self.gated(self.ci('on:\n  push:\n', concurrency=self.GROUP_ON_REF.replace('true', 'false')))
        self.assertIn('keys the concurrency group of the workflow on github.ref', ' '.join(self.issues()))

    def test_a_ref_keyed_group_without_cancel_in_progress_still_fails(self):
        self.gated(self.ci('on:\n  push:\n', concurrency=self.GROUP_ON_REF.replace('  cancel-in-progress: true\n', '')))
        self.assertIn('keys the concurrency group of the workflow on github.ref', ' '.join(self.issues()))

    def test_an_inline_ref_keyed_group_fails(self):
        """`concurrency: <group>` names the group alone, and still groups."""
        self.gated(self.ci('on:\n  push:\n', job_extra='    concurrency: ci-${{ github.ref }}\n'))
        self.assertIn('the concurrency group of the test job', ' '.join(self.issues()))

    def test_an_inline_group_keyed_by_sha_passes(self):
        self.gated(self.ci('on:\n  push:\n', concurrency='concurrency: ci-${{ github.sha }}\n'))
        self.assertEqual([], self.issues())

    def test_merge_group_concurrency_is_left_to_its_own_criterion(self):
        self.gated(self.ci('on:\n  pull_request:\n  merge_group:\n', concurrency=self.GROUP_ON_REF))
        self.assertEqual([], self.issues())

    def test_a_push_to_tags_alone_is_not_judged_for_concurrency(self):
        self.gated(self.ci("on:\n  merge_group:\n  push:\n    tags: ['v*']\n", concurrency=self.GROUP_ON_REF))
        self.assertEqual([], self.issues())

    # How a gated workflow's path filter reads a merge group.

    FILTER = ('      - uses: dorny/paths-filter@v4\n'
              '        id: filter\n'
              '%s'
              '        with:\n'
              '%s'
              '          filters: |\n'
              '            code:\n'
              "              - 'src/**'\n")
    BASE = "          base: ${{ github.event.merge_group.base_sha || 'develop' }}\n"
    ON_PULL_REQUEST = "        if: github.event_name == 'pull_request'\n"

    def filtered(self, step_if='', base='', job_if='', on='on:\n  pull_request:\n  merge_group:\n'):
        self.gated(self.ci(on, job_extra=job_if, steps=self.FILTER % (step_if, base)))

    def test_a_filter_diffing_against_the_merge_group_base_passes(self):
        self.filtered(step_if="        if: github.event_name != 'workflow_dispatch'\n", base=self.BASE)
        self.assertEqual([], self.issues())

    def test_a_filter_diffing_against_the_branch_fails_the_check(self):
        self.filtered(base='          base: develop\n')
        self.assert_fail(self.check(), containing='ci.yml, which the gate counts, runs dorny/paths-filter in '
                                                  'the test job on merge groups without diffing against '
                                                  'github.event.merge_group.base_sha')

    def test_a_filter_with_no_base_fails(self):
        self.filtered()
        self.assertIn("pass \"base: ${{ github.event.merge_group.base_sha || 'develop' }}\"",
                      ' '.join(self.issues()))

    def test_a_filter_confined_to_pull_request_by_its_step_passes(self):
        self.filtered(step_if=self.ON_PULL_REQUEST)
        self.assertEqual([], self.issues())

    def test_a_filter_confined_to_pull_request_by_its_job_passes(self):
        self.filtered(job_if="    if: github.event_name == 'pull_request'\n")
        self.assertEqual([], self.issues())

    def test_a_condition_admitting_merge_group_confines_nothing(self):
        self.filtered(step_if="        if: github.event_name == 'pull_request' || "
                              "github.event_name == 'merge_group'\n")
        self.assertIn('without diffing against', ' '.join(self.issues()))

    def test_a_filter_in_a_workflow_without_merge_group_is_not_judged(self):
        self.filtered(on='on:\n  push:\n')
        self.assertEqual([], self.issues())


class CheckDistTest(CheckTestCase):
    """A release runs check-dist on every wheel it uploads."""

    check_class = release.ReleaseProcess

    BUILD = '      - run: release-venv/bin/python3 -m build\n'
    SCRIPT_BUILD = '      - run: tools/build-wheel.sh\n'
    MATURIN = '      - uses: PyO3/maturin-action@v1\n        with:\n          command: build\n'

    def check_dist(self, path=None):
        return ('      - uses: shakenfist/actions/check-dist@main\n'
                + (f'        with:\n          path: {path}\n' if path else ''))

    def upload(self, path='dist/'):
        return ('      - uses: actions/upload-artifact@v7\n        with:\n'
                f'          name: dist\n          path: {path}\n')

    def build(self, *steps):
        self.fixture.workflow('ci.yml', CI_WORKFLOW)
        self.fixture.workflow('release.yml', 'on: push\njobs:\n' + GATE_JOB
                              + job('build', needs='ci-gate', steps=''.join(steps)))

    def issues(self):
        return release.release_check_dist_issues(self.repo())

    def test_the_shipped_template_passes(self):
        self.fixture.workflow('release.yml', shipped_template())
        self.assertEqual([], self.issues())

    def test_check_dist_between_build_and_upload_passes(self):
        self.build(self.BUILD, self.check_dist(), self.upload())
        self.assertEqual([], self.issues())

    def test_no_check_dist_fails_the_check(self):
        self.build(self.BUILD, self.upload())
        self.assert_fail(self.check(), containing='the build job builds or uploads wheels without running '
                                                  'shakenfist/actions/check-dist')

    def test_check_dist_after_the_upload_fails(self):
        self.build(self.BUILD, self.upload(), self.check_dist())
        self.assertIn('the build job uploads dist/ before check-dist runs', ' '.join(self.issues()))

    def test_check_dist_before_the_build_fails(self):
        self.build(self.check_dist(), self.BUILD, self.upload())
        self.assertIn('runs check-dist before it builds', ' '.join(self.issues()))

    def test_a_mismatched_path_fails(self):
        self.build(self.SCRIPT_BUILD, self.check_dist(), self.upload('target/wheels/*.whl'))
        self.assertEqual(["the build job runs check-dist on 'dist/' but uploads \"target/wheels/*.whl\", so "
                          'the wheels it publishes are not the ones it checked; point check-dist\'s "path:" at '
                          '"target/wheels/"'], self.issues())

    def test_a_matching_path_however_spelled_passes(self):
        self.build(self.SCRIPT_BUILD, self.check_dist("'target/wheels'"), self.upload('target/wheels/*.whl'))
        self.assertEqual([], self.issues())

    def test_a_maturin_action_job_is_a_wheel_job(self):
        self.build(self.MATURIN, self.upload('target/wheels/'))
        self.assertIn('without running', ' '.join(self.issues()))
        self.fresh_fixture()
        self.build(self.MATURIN, self.check_dist('target/wheels/'), self.upload('target/wheels/'))
        self.assertEqual([], self.issues())

    def test_a_script_build_uploading_wheels_is_a_wheel_job(self):
        self.build(self.SCRIPT_BUILD, self.upload('target/wheels/*.whl'))
        self.assertIn('without running', ' '.join(self.issues()))

    def test_a_job_which_only_uploads_wheels_is_judged(self):
        self.build(self.upload("|\n            dist/*.tar.gz\n            dist/*.whl"))
        self.assertIn('without running', ' '.join(self.issues()))
        self.fresh_fixture()
        self.build(self.check_dist(), self.upload('dist/*.whl'))
        self.assertEqual([], self.issues())

    def test_a_script_build_uploading_a_directory_is_not_seen(self):
        """The documented blind spot: neither signal is present."""
        self.build(self.SCRIPT_BUILD, self.upload('dist/'))
        self.assertEqual([], self.issues())

    def test_a_commented_build_is_not_a_build(self):
        self.build('      - run: make  # python3 -m build\n', self.upload())
        self.assertEqual([], self.issues())

    def test_a_pip_install_of_build_is_not_a_build(self):
        self.build('      - run: pip install build twine\n', self.upload())
        self.assertEqual([], self.issues())


class MappingValueLinesTest(unittest.TestCase):
    """An input's value, inline or as a block scalar, read as lines."""

    WITH = ('          workflows: |\n'
            '            ci.yml\n'
            '\n'
            '            functional-tests.yml\n'
            '          path: dist/  # the default\n'
            '          nested:\n'
            '            path: elsewhere/\n')

    def test_a_block_scalar_is_its_lines(self):
        self.assertEqual(['ci.yml', 'functional-tests.yml'],
                         workflows.mapping_value_lines(self.WITH, 'workflows'))

    def test_an_inline_value_is_one_line_without_its_comment(self):
        self.assertEqual(['dist/'], workflows.mapping_value_lines(self.WITH, 'path'))

    def test_a_nested_key_is_not_an_entry(self):
        self.assertEqual([], workflows.mapping_value_lines('          nested:\n            path: x/\n', 'path'))

    def test_an_absent_key_or_block_is_empty(self):
        self.assertEqual([], workflows.mapping_value_lines(self.WITH, 'missing'))
        self.assertEqual([], workflows.mapping_value_lines(None, 'path'))


if __name__ == '__main__':
    unittest.main()
