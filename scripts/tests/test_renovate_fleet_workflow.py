#!/usr/bin/env python3

"""The fleet Renovate runner's permanent properties.

Run with: python3 -m unittest tests.test_renovate_fleet_workflow

`.github/workflows/renovate-fleet.yml` is the one workflow here that
holds a token which can push to every repository in the organisation.
What is asserted below is the set of properties that make that
tolerable, or that keep the workflow from colliding with the
per-repository copies still running, and every one of them is a line a
later edit can relax with nothing else noticing: a permissions block
widened to make something else work, a step reordered so that the
token-holding step installs its own tooling, a runner label changed to
the shared pool. `docs/audits/renovate.md` is the specification; this
is the part of it that can be checked by a test rather than by hand.

Line-based, like the rest of this tree's YAML reading: the audit ships
without PyYAML on purpose and the shape here is flat. Comments are
stripped before anything is matched, because this workflow argues with
itself in its comments -- it quotes the `schedule:` block to add at
cutover, and names `renovatebot/github-action` to explain why it is not
used -- and matching those would be asserting prose.
"""

import os
import re
import unittest

from tests.base import REPO_ROOT

WORKFLOW = os.path.join(
    REPO_ROOT, '.github', 'workflows', 'renovate-fleet.yml')

#: A step header, which is how the body below is cut into steps. Every
#: step in this workflow is named, and the one that stops being named
#: would drop out of the token-and-install assertion silently, so the
#: count is guarded rather than trusted.
STEP = re.compile(r'^      - name: (.*)$')


def body_lines():
    """The workflow without its comments or its blank lines."""
    with open(WORKFLOW) as f:
        return [line for line in f.read().splitlines()
                if line.strip() and not line.strip().startswith('#')]


def steps():
    """The job's steps, as {name: the lines under it}."""
    found = {}
    current = None
    for line in body_lines():
        match = STEP.match(line)
        if match:
            current = []
            found[match.group(1).strip()] = current
        elif current is not None:
            current.append(line)
    return found


class FleetRunnerWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.lines = body_lines()
        self.steps = steps()
        # Guard the parse: a read that found no steps would satisfy
        # every assertion below about what a step does not do.
        self.assertGreaterEqual(len(self.steps), 6, self.steps)

    def test_the_workflow_token_can_only_read(self):
        # Top level, so it is the floor for every job rather than one
        # job's override, and `contents: read` alone: nothing here uses
        # the job's own token, and a token with more than this is one
        # an injected step could spend.
        permissions = [i for i, line in enumerate(self.lines)
                       if line == 'permissions:']
        self.assertEqual(len(permissions), 1, 'expected one permissions block')
        self.assertEqual(self.lines[permissions[0] + 1].strip(),
                         'contents: read')
        self.assertFalse(self.lines[permissions[0] + 2].startswith(' '),
                         'the permissions block grants more than contents: read')

    def test_the_job_runs_in_the_write_environment(self):
        # Whether that environment restricts deployments to main is a
        # repository setting this file cannot assert, and referencing an
        # environment that does not exist creates an unprotected one.
        # What a test can hold is that the job still names it: an edit
        # that dropped the key would take the workflow outside whatever
        # protection was configured there, silently.
        self.assertIn('    environment: renovate-write', self.lines)

    def test_the_job_runs_on_an_ephemeral_vm(self):
        # Not the static pool. Those runners share one user and one home
        # directory between jobs, so code another job left behind would
        # run here with a token that writes every repository in the
        # organisation.
        self.assertIn('    runs-on: [self-hosted, vm, debian-13, s]',
                      self.lines)

    def test_the_workflow_is_dispatch_only_for_now(self):
        # The schedule arrives at cutover, when the per-repository
        # copies are disabled, so this assertion is the thing that has
        # to be changed deliberately then rather than a property that
        # holds for ever. Until then a schedule here would run Renovate
        # over the same repositories as their own hourly copies, which
        # races on branch creation and produces duplicate or
        # half-written pull requests -- and the concurrency group below
        # cannot prevent that, because the racing runs are different
        # workflows in different repositories.
        self.assertIn('  workflow_dispatch:', self.lines)
        scheduled = [line for line in self.lines
                     if line.strip().startswith(('schedule:', '- cron:'))]
        self.assertEqual(scheduled, [],
                         'the fleet runner has a schedule while the '
                         'per-repository copies are still running')

    def test_renovate_is_not_run_as_a_container_action(self):
        # renovatebot/github-action runs Renovate in a container, and
        # this runner has no Docker daemon, so the action fails on every
        # run. The npm install below is what replaced it.
        used = [line for line in self.lines if 'uses:' in line]
        self.assertTrue(used, 'no action is used at all, which cannot be right')
        for line in used:
            self.assertNotIn('renovatebot/github-action', line)

    def test_two_passes_never_overlap_and_neither_is_killed(self):
        # timeout-minutes does not cover queue time, so a run can sit
        # waiting for a VM while the next one starts. Not
        # cancel-in-progress: killing Renovate mid-push is worse than
        # skipping a pass.
        self.assertIn('      group: renovate-fleet', self.lines)
        self.assertIn('      cancel-in-progress: false', self.lines)

    def test_third_party_actions_are_pinned_to_a_commit(self):
        # A tag or a branch is whatever it points at on the day, and
        # everything in this job runs beside a step holding a credential
        # that can push to every repository in the organisation: an
        # action is free to put a shim on PATH or in GITHUB_ENV for the
        # token-holding step to run. Pinning is not required of the
        # fleet's other workflows, and first-party actions/* are left on
        # their tags here as they are everywhere else; this asserts only
        # that a third party cannot change what runs in this file
        # without the pin changing too. Derived from the uses: lines
        # rather than listed, so a new third-party action is covered
        # without anyone remembering this test.
        unpinned = []
        for line in self.lines:
            if 'uses:' not in line:
                continue
            # The version comment a pin carries sits on the same line,
            # and this file keeps its whole-line comments, so strip it
            # here rather than reading it as part of the ref.
            ref = line.split('uses:', 1)[1].split('#', 1)[0].strip()
            if ref.startswith('actions/') or '@' not in ref:
                continue
            if not re.fullmatch(r'[0-9a-f]{40}', ref.rsplit('@', 1)[1]):
                unpinned.append(ref)
        self.assertEqual(
            unpinned, [],
            'these third-party actions run beside the fleet push token '
            'on a ref that can move under them: ' + ', '.join(unpinned))

    def test_the_step_that_holds_the_token_installs_nothing(self):
        # The one property here that is about more than configuration
        # drift. npm resolves Renovate's dependency tree from the
        # registry on every run, so an install script in that tree runs
        # with whatever the step's environment holds: splitting the
        # install into a step without the token is what keeps it from
        # being an organisation-wide push credential handed to the
        # registry.
        holders = {name for name, lines in self.steps.items()
                   if any('RENOVATE_TOKEN' in line for line in lines)}
        installers = {name for name, lines in self.steps.items()
                      if any('npm install' in line for line in lines)}
        self.assertTrue(holders, 'no step is given RENOVATE_TOKEN')
        self.assertTrue(installers, 'no step installs Renovate')
        self.assertEqual(
            sorted(holders & installers), [],
            'these steps both install from the registry and hold the '
            'fleet push token: ' + ', '.join(sorted(holders & installers)))


if __name__ == '__main__':
    unittest.main()
