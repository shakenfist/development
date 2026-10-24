#!/usr/bin/env python3

"""Tests for audit/checks/release.py.

Run with: python3 scripts/tests/test_release.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import release  # noqa: E402
from tests.base import REPO_ROOT, CheckTestCase  # noqa: E402


class ReleaseProcessTest(CheckTestCase):
    check_class = release.ReleaseProcess

    def compliant(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.workflow('release.yml', 'on: push\n')
        self.fixture.write('RELEASE-SETUP.md', '# Setup\n')

    def test_without_pyproject_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No pyproject.toml')

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
        'jobs:\n'
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
        with open(os.path.join(
                REPO_ROOT, 'templates', 'release-automation',
                'release.yml')) as f:
            self.fixture.workflow('release.yml', f.read())
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
            + 'jobs:\n'
            '  build:\n'
            + build_guard +
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n'
            '          name: dist\n'
            '  sign-tag:\n'
            + sign_guard +
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
            'jobs:\n'
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
            'jobs:\n'
            '  sign-tag:\n'
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
        with open(os.path.join(
                REPO_ROOT, 'templates', 'release-automation',
                'release.yml')) as f:
            self.fixture.workflow('release.yml', f.read())
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
            'jobs:\n'
            '  publish-pypi:\n'
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
            'jobs:\n'
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
            'jobs:\n'
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
            'jobs:\n'
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
            'jobs:\n'
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


if __name__ == '__main__':
    unittest.main()
