#!/usr/bin/env python3

"""Tests for the Renovate checks in audit/checks/packaging.py.

The rest of that module's checks are tested in test_packaging.py.

Run with: python3 scripts/tests/test_packaging_renovate.py
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import packaging  # noqa: E402
from tests.base import (  # noqa: E402
    REPO_ROOT, CheckTestCase, current_ref, repo_text, uses_clause,
)

RENOVATE_WORKFLOW = repo_text('templates', 'renovate', 'renovate.yml')


class RenovatePreCommitManagerTest(CheckTestCase):
    """The pre-commit manager is opt-in, so its absence is a finding."""

    check_class = packaging.Renovate

    REMOTE_HOOKS = (
        'repos:\n'
        '  - repo: https://github.com/rhysd/actionlint\n'
        '    rev: v1.7.12\n'
        '    hooks:\n'
        '      - id: actionlint\n'
    )

    LOCAL_HOOKS = (
        'repos:\n'
        '  - repo: local\n'
        '    hooks:\n'
        '      - id: rust-check\n'
        '        entry: ./scripts/check-rust.sh\n'
        '        language: script\n'
    )

    def _repo(self, renovate, pre_commit=None):
        # The version comment preset and the workflow are separate
        # findings, tested below; every repository here carries both.
        renovate = dict(renovate)
        renovate['extends'] = renovate.get('extends', []) + [
            'customManagers:githubActionsVersions']
        self.fixture.workflow('renovate.yml', RENOVATE_WORKFLOW)
        self.fixture.write('renovate.json', json.dumps(renovate))
        if pre_commit is not None:
            self.fixture.write('.pre-commit-config.yaml', pre_commit)
        return self.check()

    def test_remote_hooks_without_the_manager_fail(self):
        self.assert_fail(self._repo({}, self.REMOTE_HOOKS),
                         containing='pre-commit manager')

    def test_explicit_manager_block_passes(self):
        self.assert_pass(self._repo(
            {'pre-commit': {'enabled': True}}, self.REMOTE_HOOKS))

    def test_enabled_managers_list_passes(self):
        self.assert_pass(self._repo(
            {'enabledManagers': ['cargo', 'pre-commit']},
            self.REMOTE_HOOKS,
        ))

    def test_preset_passes(self):
        self.assert_pass(self._repo(
            {'extends': [':enablePreCommit']}, self.REMOTE_HOOKS))

    def test_manager_disabled_explicitly_fails(self):
        self.assert_fail(self._repo(
            {'pre-commit': {'enabled': False}}, self.REMOTE_HOOKS))

    def test_no_pre_commit_config_passes(self):
        self.assert_pass(self._repo({}))

    def test_local_only_hooks_pass(self):
        # A repo: local hook runs a script from the tree and carries no
        # revision, so there is nothing for renovate to bump.
        self.assert_pass(self._repo({}, self.LOCAL_HOOKS))

    def test_missing_files_still_reported_first(self):
        # The manager check must not mask the more basic finding.
        self.fixture.write('.pre-commit-config.yaml', self.REMOTE_HOOKS)
        result = self.check()
        self.assert_fail(result)
        self.assertIn('renovate.json', result['missing'])


class RenovateFleetPresetTest(CheckTestCase):
    """A repository that extends the fleet preset inherits what it holds.

    The audit trusts the preset by name, so what it vouches for is proved
    here against the real file: a local> preset takes effect fleet-wide the
    moment an edit to it merges, and an edit that dropped the pre-commit
    manager or the version comment extend would otherwise leave every
    repository passing on a promise the preset no longer keeps.
    """

    check_class = packaging.Renovate

    PRESET_NAME = 'local>shakenfist/development:renovate-fleet'

    REMOTE_HOOKS = RenovatePreCommitManagerTest.REMOTE_HOOKS

    def _preset(self):
        with open(os.path.join(REPO_ROOT, 'renovate-fleet.json')) as f:
            return json.load(f)

    def _repo(self, renovate):
        self.fixture.workflow('renovate.yml', RENOVATE_WORKFLOW)
        self.fixture.write('renovate.json', json.dumps(renovate))
        self.fixture.write('.pre-commit-config.yaml', self.REMOTE_HOOKS)
        return self.check()

    def test_the_real_preset_enables_the_pre_commit_manager(self):
        # The helper, not the public function: no name is trusted here.
        self.assertTrue(
            packaging.renovate_config_manages_pre_commit(self._preset()))

    def test_the_real_preset_reads_version_comments(self):
        self.assertTrue(
            packaging.renovate_config_reads_version_comments(self._preset()))

    def test_extending_the_preset_passes(self):
        self.assert_pass(self._repo({'extends': [self.PRESET_NAME]}))

    def test_extending_the_preset_satisfies_each_check_alone(self):
        config = {'extends': [self.PRESET_NAME]}
        self.assertTrue(packaging.extends_fleet_preset(config))
        self.assertFalse(packaging.renovate_config_manages_pre_commit(config))
        self.assertFalse(
            packaging.renovate_config_reads_version_comments(config))

    def test_a_similar_name_is_not_the_preset(self):
        self.assert_fail(self._repo(
            {'extends': ['local>shakenfist/development:renovate']}))

    def test_a_longer_name_is_not_the_preset(self):
        self.assertFalse(packaging.extends_fleet_preset(
            {'extends': [self.PRESET_NAME + '-other']}))

    def test_a_non_list_extends_is_not_the_preset(self):
        self.assertFalse(
            packaging.extends_fleet_preset({'extends': self.PRESET_NAME}))
        self.assertFalse(packaging.extends_fleet_preset({}))

    def _rule(self, **match):
        """The one packageRules entry whose keys are exactly `match`."""
        found = [rule for rule in self._preset()['packageRules']
                 if all(rule.get(key) == value
                        for key, value in match.items())]
        self.assertEqual(
            len(found), 1,
            'expected exactly one packageRules entry matching %r, found %d'
            % (match, len(found)))
        return found[0]

    def test_the_real_preset_keeps_the_shellcheck_versioning_rule(self):
        # Without this the github-tags lookup behind the pre-commit hook
        # fails with "Can't find version matching" and no update is ever
        # proposed -- visible on a dashboard and nowhere else. The build
        # group name and the four-component anchor are both load-bearing;
        # see the rule's own description.
        rule = self._rule(matchPackageNames=['shellcheck-py/shellcheck-py'])
        self.assertEqual(
            rule['versioning'],
            'regex:^v?(?<major>\\d+)\\.(?<minor>\\d+)'
            '\\.(?<patch>\\d+)\\.(?<build>\\d+)$')

    def test_the_real_preset_disables_node_majors_in_the_workflows(self):
        # The one update type the immediate security path does not reach,
        # so its scope is checked rather than only its existence.
        rule = self._rule(matchDepNames=['node'], enabled=False)
        self.assertEqual(rule['matchUpdateTypes'], ['major'])
        self.assertEqual(rule['matchManagers'], ['github-actions'])
        # The whole set, not membership: each file is a workflow that sets
        # up Node, and one left out is one a major Node pull request can
        # reach, which stops Renovate for every repository at once.
        self.assertEqual(sorted(rule['matchFileNames']),
                         ['.github/workflows/renovate-fleet.yml',
                          '.github/workflows/renovate.yml',
                          'templates/renovate/renovate.yml'])

    def test_the_real_preset_merges_nothing_automatically(self):
        rule = self._rule(automerge=False)
        self.assertEqual(sorted(rule['matchUpdateTypes']),
                         ['digest', 'minor', 'patch'])

    def test_the_real_preset_groups_ci_tooling_monthly(self):
        rule = self._rule(groupName='CI tooling')
        self.assertEqual(sorted(rule['matchManagers']),
                         ['github-actions', 'pre-commit'])
        self.assertEqual(rule['schedule'], ['on the first day of the month'])
        # No matchUpdateTypes: the group covers every one, digests
        # included, which is what both pages promise.
        self.assertNotIn('matchUpdateTypes', rule)

    def test_the_real_preset_pins_third_party_action_digests(self):
        # The fleet-wide promise docs/audits/renovate.md makes: every
        # third-party uses: becomes a sha. An edit that dropped this
        # entry would leave both pages describing a policy nothing
        # applies.
        self.assertIn('helpers:pinGitHubActionDigests',
                      self._preset()['extends'])

    def test_the_real_preset_exempts_the_fleets_own_actions(self):
        # Pinning these would freeze each repository on the sha it last
        # merged and defeat the moving ref, which a review ruleset on
        # shakenfist/actions protects instead of a sha. The dep type
        # names are Renovate's own, from extractRepositoryAction in
        # lib/modules/manager/github-actions/extract.ts at 44.227.4
        # (docs/audits/renovate.md links it): a uses: whose sub-path
        # matches reusableWorkflowPathRe is a 'workflow' and every
        # other reference an 'action'.
        rule = self._rule(pinDigests=False)
        self.assertEqual(rule['matchPackageNames'], ['shakenfist/**'])
        self.assertEqual(sorted(rule['matchDepTypes']),
                         ['action', 'workflow'])

    def test_no_other_rule_sets_the_pinning_flag(self):
        # The exemption holds by rule order: Renovate resolves the rules
        # an extends entry carries before the config's own, so the last
        # rule to set pinDigests for a reference wins. A rule added
        # after this one with pinDigests true would silently pin the
        # fleet's own moving refs, and nothing else here would fail.
        rules = self._preset()['packageRules']
        setters = [rule for rule in rules if 'pinDigests' in rule]
        self.assertEqual(len(setters), 1)
        self.assertFalse(setters[0]['pinDigests'])

    def test_the_real_preset_keeps_the_renovate_major_note(self):
        rule = self._rule(matchDepNames=['renovate'])
        self.assertEqual(rule['matchUpdateTypes'], ['major'])
        self.assertIn('engines.node', rule['prBodyNotes'][0])

    def test_the_renovate_major_note_names_each_workflow_to_move(self):
        # The note is the only thing that tells a reviewer which files hold
        # a node-version that has to move with the pin.
        note = self._rule(matchDepNames=['renovate'])['prBodyNotes'][0]
        for name in ('.github/workflows/renovate-fleet.yml',
                     '.github/workflows/renovate.yml',
                     'templates/renovate/renovate.yml'):
            self.assertIn(name, note)

    def test_the_real_preset_sets_the_documented_cadence(self):
        preset = self._preset()
        self.assertEqual(preset['schedule'], ['on saturday'])
        self.assertEqual(preset['timezone'], 'Australia/Sydney')
        self.assertEqual(preset['prHourlyLimit'], 2)
        self.assertEqual(preset['prConcurrentLimit'], 10)
        self.assertEqual(preset['minimumReleaseAge'], '3 days')
        # The all-majors rule is the one not narrowed to a dependency;
        # the other two major rules name renovate and node.
        majors = [rule for rule in preset['packageRules']
                  if rule.get('matchUpdateTypes') == ['major']
                  and 'matchDepNames' not in rule]
        self.assertEqual(len(majors), 1)
        self.assertEqual(majors[0]['schedule'],
                         ['on the first day of the month'])

    def test_the_real_preset_leaves_security_fixes_on_the_defaults(self):
        # Both pages say a security fix ignores the schedule, the release
        # age and grouping because the preset overrides none of that. An
        # override added here would make that claim false fleet-wide.
        preset = self._preset()
        self.assertNotIn('vulnerabilityAlerts', preset)
        self.assertNotIn('osvVulnerabilityAlerts', preset)

    def test_the_real_preset_sets_the_fleet_identity(self):
        preset = self._preset()
        self.assertEqual(preset['gitAuthor'],
                         'shakenfist-bot <bot@shakenfist.com>')
        self.assertEqual(preset['assignees'], ['mikalstill'])
        self.assertTrue(preset['dependencyDashboard'])
        self.assertTrue(preset['rollbackPrs'])


class RenovateReducedConfigTest(unittest.TestCase):
    """Neither shipped renovate.json restates what the preset carries.

    `templates/renovate/renovate.json` is what the fleet copies, so a
    fleet key re-introduced here is a per-repository override of fleet
    policy shipped to every repository that takes the template -- and a
    local `schedule` or `prHourlyLimit` silently wins over the preset's.
    The definition of done checked this by reading the two files once;
    this keeps it true.

    This repository's own root config is allowed the sections that exist
    only here: two that reach the templates/ directory the rest of the
    fleet does not have, and the packageRules entries for the central
    runner workflow. packageRules concatenates with the preset's rather
    than replacing it, so a local rule adds to fleet policy; the test
    below pins what the root config may hold in it.
    """

    PRESET_NAME = 'local>shakenfist/development:renovate-fleet'

    #: Sections that apply only to this repository, which ships the
    #: workflow templates. Everything else in the preset is fleet-wide.
    ROOT_LOCAL_KEYS = frozenset(
        {'github-actions', 'customManagers', 'packageRules'})

    STRUCTURAL_KEYS = frozenset({'$schema', 'extends'})

    def _config(self, *parts):
        with open(os.path.join(REPO_ROOT, *parts)) as f:
            return json.load(f)

    def _preset(self):
        return self._config('renovate-fleet.json')

    def _fleet_keys(self):
        keys = set(self._preset()) - self.STRUCTURAL_KEYS
        # Guard the derivation: an empty set would pass everything.
        self.assertIn('schedule', keys)
        self.assertIn('packageRules', keys)
        return keys

    def test_the_template_extends_the_preset(self):
        config = self._config('templates', 'renovate', 'renovate.json')
        self.assertEqual(config['extends'], [self.PRESET_NAME])

    def test_the_root_config_extends_the_preset(self):
        self.assertEqual(self._config('renovate.json')['extends'],
                         [self.PRESET_NAME])

    def test_the_template_holds_nothing_but_the_extend(self):
        config = self._config('templates', 'renovate', 'renovate.json')
        self.assertEqual(set(config), self.STRUCTURAL_KEYS)

    def test_neither_config_restates_a_fleet_key(self):
        fleet = self._fleet_keys()
        for parts, allowed in (
                (('renovate.json',), self.ROOT_LOCAL_KEYS),
                (('templates', 'renovate', 'renovate.json'), frozenset())):
            config = self._config(*parts)
            restated = sorted((set(config) & fleet) - allowed)
            self.assertEqual(
                restated, [],
                os.path.join(*parts) + ' restates fleet policy the preset '
                'already carries, which overrides it locally: '
                + ', '.join(restated))

    def _root_rule(self, file_names):
        rules = [rule for rule in self._config('renovate.json')['packageRules']
                 if sorted(rule.get('matchFileNames') or []) == sorted(file_names)]
        self.assertEqual(len(rules), 1,
                         f'renovate.json should hold exactly one packageRule whose '
                         f'matchFileNames is {sorted(file_names)}, and holds {len(rules)}')
        return rules[0]

    def test_the_root_config_holds_two_local_rules(self):
        # Each is local because it names files only this repository has;
        # a third is a decision somebody should have to make here.
        self.assertEqual(len(self._config('renovate.json')['packageRules']), 2)

    def test_the_root_config_gives_the_runner_workflow_actions_a_weekly_schedule(self):
        rule = self._root_rule(['.github/workflows/renovate-fleet.yml'])
        self.assertEqual(sorted(rule['matchPackageNames']),
                         ['actions/checkout', 'actions/setup-node'])
        # The preset's own weekly window, not a second spelling of it.
        self.assertEqual(rule['schedule'], self._preset()['schedule'])
        self.assertNotIn('groupName', rule)

    def test_only_the_doomed_renovate_copies_keep_rust_toolchain_unpinned(self):
        # The two files renovate-cadence deletes, and nothing else: the
        # fleet runner's own reference holds the organisation's push token
        # and stays pinned, so a rule that widened to it, or to every
        # action in these files, would unpin what the preset is there to
        # pin.
        rule = self._root_rule(['.github/workflows/renovate.yml',
                                'templates/renovate/renovate.yml'])
        self.assertEqual(rule['matchPackageNames'], ['dtolnay/rust-toolchain'])
        self.assertIs(rule['pinDigests'], False)
        self.assertEqual(set(rule), {'description', 'matchFileNames',
                                     'matchPackageNames', 'pinDigests'})
        # The rule and the files are one invariant: the exemption is
        # true only while both files exist and both references are still
        # unpinned. The pull request deleting either file must delete the
        # rule too, and a pin that reaches either file anyway is reverted
        # rather than given a toolchain input, which would make every
        # fleet copy of the template drift from it.
        for path in rule['matchFileNames']:
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, path)), path)
            ref = current_ref(repo_text(*path.split('/')), 'dtolnay/rust-toolchain')
            self.assertTrue(
                ref.endswith('@stable'),
                f'{path} has {ref}: this repository declines that pin, so put '
                f'the reference back to dtolnay/rust-toolchain@stable with no '
                f'with: block rather than adding a toolchain input')


class RenovateRunnerConfigTest(unittest.TestCase):
    """The fleet runner's global configuration, pinned key by key.

    This is the file that decides which repositories one
    organisation-wide push token is pointed at, and nothing else checks
    what is in it: the validator hook reads it against the schema, so a
    widened `autodiscoverFilter`, a lost `requireConfig`, `onboarding`
    turned back on, or a temporary rule that lost its `matchFileNames`
    -- and so disabled every dependency in every repository in the
    fleet -- all pass `pre-commit run --all-files` and would be noticed
    by the fleet quietly stopping.

    Whole sets rather than membership, in the same shape as the
    preset's Node rule: a key added here is then a key somebody decided
    to add.
    """

    RUNNER = 'renovate-runner.json'

    def _runner(self):
        with open(os.path.join(REPO_ROOT, self.RUNNER)) as f:
            return json.load(f)

    def test_the_runner_config_holds_exactly_these_keys(self):
        self.assertEqual(
            sorted(self._runner()),
            ['$schema', 'autodiscover', 'autodiscoverFilter', 'onboarding',
             'packageRules', 'requireConfig'])

    def test_the_runner_discovers_this_organisation_and_no_other(self):
        config = self._runner()
        self.assertIs(config['autodiscover'], True)
        # The whole list, not membership: a second pattern here is a
        # second organisation this token would be pointed at, and a
        # bare '*' is every repository on GitHub it can see.
        self.assertEqual(config['autodiscoverFilter'], ['shakenfist/*'])

    def test_the_runner_manages_only_repositories_that_asked_for_it(self):
        config = self._runner()
        # Without requireConfig the runner manages every repository it
        # discovers, configured or not; with onboarding it would open
        # an onboarding pull request on each of those.
        self.assertEqual(config['requireConfig'], 'required')
        self.assertIs(config['onboarding'], False)

    def test_the_runner_suppresses_the_renovate_workflows_and_nothing_else(self):
        rules = self._runner()['packageRules']
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertIs(rule['enabled'], False)
        # The whole set again, and the reason it matters most here: a
        # rule that lost its matchFileNames disables every dependency
        # in the fleet, and the only symptom is Renovate proposing
        # nothing to anybody.
        self.assertEqual(sorted(rule['matchFileNames']),
                         ['.github/workflows/renovate.yml',
                          'templates/renovate/renovate.yml'])


class RenovateVersionCommentsTest(CheckTestCase):
    """Renovate can only bump the pinned Renovate if it reads the comments."""

    check_class = packaging.Renovate

    def _repo(self, renovate):
        self.fixture.workflow('renovate.yml', RENOVATE_WORKFLOW)
        self.fixture.write('renovate.json', json.dumps(renovate))
        return self.check()

    def test_the_local_preset_passes(self):
        self.assert_pass(self._repo(
            {'extends': ['customManagers:githubActionsVersions']}))

    def test_a_config_without_it_fails(self):
        self.assert_fail(self._repo({'extends': [':enablePreCommit']}),
                         containing='customManagers:githubActionsVersions')

    def test_the_fleet_preset_passes(self):
        self.assert_pass(self._repo(
            {'extends': ['local>shakenfist/development:renovate-fleet']}))


class RenovateWithoutDockerTest(CheckTestCase):
    """renovate.yml runs Renovate from npm, and Renovate can see the pin.

    The fixtures are the template's own steps, before and after the
    change: the drift these findings exist to report is a copy of the
    old template.
    """

    check_class = packaging.Renovate

    CONTAINER_WORKFLOW = (
        'jobs:\n'
        '  renovate:\n'
        '    runs-on: [self-hosted, static]\n'
        '    steps:\n'
        '      - name: Checkout\n'
        '        uses: actions/checkout@v7\n'
        '\n'
        '      - name: Self-hosted Renovate\n'
        '        uses: renovatebot/github-action@v46.3.7\n'
        '        with:\n'
        '          token: ${{ secrets.RENOVATE_TOKEN }}\n'
    )

    # The template is the npm form, and the one copy that passes the
    # workflow finding RenovateWorkflowDriftTest measures.
    NPM_WORKFLOW = RENOVATE_WORKFLOW

    PRESET = {'extends': ['customManagers:githubActionsVersions']}

    def _repo(self, workflow, renovate):
        self.fixture.workflow('renovate.yml', workflow)
        self.fixture.write('renovate.json', json.dumps(renovate))
        return self.check()

    def _template(self, name):
        with open(os.path.join(REPO_ROOT, 'templates', 'renovate', name)) as f:
            return f.read()

    def test_the_template_itself_passes(self):
        # The template is what an issue tells a repository to copy, so
        # a copy of it has to close the issue.
        self.assert_pass(self._repo(
            self._template('renovate.yml'),
            json.loads(self._template('renovate.json'))))

    def test_the_npm_form_passes(self):
        # Its comment names the action; only a step running it counts.
        self.assert_pass(self._repo(self.NPM_WORKFLOW, self.PRESET))

    def test_the_container_action_fails(self):
        self.assert_fail(self._repo(self.CONTAINER_WORKFLOW, self.PRESET),
                         containing='needs a Docker daemon')

    def test_a_config_without_the_preset_fails(self):
        self.assert_fail(self._repo(self.NPM_WORKFLOW, {}),
                         containing='customManagers:githubActionsVersions')

    def test_the_old_preset_name_passes(self):
        self.assert_pass(self._repo(
            self.NPM_WORKFLOW,
            {'extends': ['regexManagers:githubActionsVersions']}))

    def test_a_malformed_config_fails_rather_than_raising(self):
        self.fixture.workflow('renovate.yml', self.NPM_WORKFLOW)
        for renovate in ('{not json\n', '[]', '{"extends": "x"}'):
            with self.subTest(renovate=renovate):
                self.fixture.write('renovate.json', renovate)
                self.assert_fail(self.check(),
                                 containing='githubActionsVersions')

    def test_an_old_copy_reports_both_findings(self):
        # The copy the fleet has today: one issue names both changes.
        result = self._repo(self.CONTAINER_WORKFLOW, {})
        self.assert_fail(result, containing='needs a Docker daemon')
        self.assertIn('githubActionsVersions', result['details'])


class RenovateWorkflowDriftTest(CheckTestCase):
    """renovate.yml is the template's, apart from the Renovate it pins."""

    check_class = packaging.Renovate

    PRESET = {'extends': ['customManagers:githubActionsVersions']}

    def _repo(self, workflow):
        self.fixture.workflow('renovate.yml', workflow)
        self.fixture.write('renovate.json', json.dumps(self.PRESET))
        return self.check()

    def test_the_template_passes(self):
        self.assert_pass(self._repo(RENOVATE_WORKFLOW))

    def test_a_different_renovate_pin_passes(self):
        """Renovate bumps the pin in each copy on its own schedule."""
        pinned = packaging.RENOVATE_PIN_RE.sub(r'\1 1.0.0', RENOVATE_WORKFLOW)
        self.assertNotEqual(pinned, RENOVATE_WORKFLOW)
        self.assert_pass(self._repo(pinned))

    def test_a_different_hashin_pin_passes(self):
        """hashin is pinned for Renovate to bump like Renovate itself."""
        self.assertIn('PINNED_HASHIN_VERSION: 1.0.5\n', RENOVATE_WORKFLOW)
        bumped = RENOVATE_WORKFLOW.replace(
            'PINNED_HASHIN_VERSION: 1.0.5\n', 'PINNED_HASHIN_VERSION: 1.1.0\n')
        self.assert_pass(self._repo(bumped))

    def test_a_pin_is_not_a_line_it_does_not_name(self):
        """Only the value of a PINNED_*_VERSION key is blanked."""
        self.assert_fail(
            self._repo(RENOVATE_WORKFLOW.replace(
                'PINNED_HASHIN_VERSION: 1.0.5\n', 'HASHIN_VERSION: 1.0.5\n')),
            containing='`HASHIN_VERSION: 1.0.5`')

    def test_other_action_refs_pass(self):
        """Renovate bumps the actions in each copy on its own schedule."""
        bumped = RENOVATE_WORKFLOW
        for action, moved in (('actions/checkout', 'v8'),
                              ('actions/setup-node', 'v6.1.0'),
                              ('dtolnay/rust-toolchain', '1.92.0')):
            # The clause is read out rather than written here: the
            # fleet preset pins these, so a literal would be whichever
            # sha Renovate last chose. The whole clause, so that a
            # digest comment in the shipped file does not survive the
            # substitution and leave `@v8 # v7` behind.
            bumped = bumped.replace(
                uses_clause(RENOVATE_WORKFLOW, action),
                f'uses: {action}@{moved}')
        self.assertNotEqual(bumped, RENOVATE_WORKFLOW)
        self.assert_pass(self._repo(bumped))

    def test_a_digest_pinned_action_passes(self):
        """Renovate's pinDigests leaves the version as a comment."""
        # The comment is this fixture's, not the shipped file's, so the
        # case covers RENOVATE_USES_RE's trailing-comment branch
        # whether or not the template is pinned today.
        pinned = RENOVATE_WORKFLOW.replace(
            uses_clause(RENOVATE_WORKFLOW, 'actions/checkout'),
            'uses: actions/checkout'
            '@08c6903cd8c0fde910a37f88322edcfb5dd907a8 # v7.0.0')
        self.assertIn('# v7.0.0', pinned)
        self.assertNotEqual(pinned, RENOVATE_WORKFLOW)
        self.assert_pass(self._repo(pinned))

    def test_a_comment_after_a_uses_line_is_compared(self):
        """The digest comment is on the uses: line, not the lines after."""
        comment = '      # A requirements file pinned with --hash lines needs hashin, which\n'
        # Derived, and no longer asserting adjacency: the rust-toolchain
        # pin held by minimumReleaseAge lands about 2026-11-18 and owes
        # a with: toolchain: stable block, either of which would break a
        # literal or a \n\n. The property is that the next comment after
        # a uses: line belongs to no uses: line of its own.
        after = RENOVATE_WORKFLOW.split(
            uses_clause(RENOVATE_WORKFLOW, 'dtolnay/rust-toolchain'), 1)[1]
        self.assertIn(comment, after)
        self.assertNotIn('uses:', after[:after.index(comment)])
        line = RENOVATE_WORKFLOW.splitlines().index(comment.rstrip('\n')) + 1
        self.assert_fail(
            self._repo(RENOVATE_WORKFLOW.replace(comment, '      # A local comment\n')),
            containing=f'line {line} is `# A local comment`')

    def test_another_action_fails(self):
        """Only the ref is Renovate's; the action named is the template's."""
        ref = current_ref(RENOVATE_WORKFLOW, 'actions/checkout')
        result = self._repo(RENOVATE_WORKFLOW.replace(
            ref, ref.replace('actions/', 'someone/', 1)))
        self.assert_fail(
            result,
            containing='is `uses: someone/checkout` where the template '
                       'has `uses: actions/checkout`')

    def test_another_node_fails(self):
        """kerbside-patches and visual-digest-rust after their Node 26 bump."""
        self.assertIn('node-version: 24\n', RENOVATE_WORKFLOW)
        result = self._repo(RENOVATE_WORKFLOW.replace(
            'node-version: 24\n', 'node-version: 26\n'))
        self.assert_fail(
            result,
            containing='is `node-version: 26` where the template has '
                       '`node-version: 24`')

    def test_line_endings_and_a_missing_final_newline_pass(self):
        self.assertTrue(RENOVATE_WORKFLOW.endswith('\n'))
        self.assert_pass(self._repo(RENOVATE_WORKFLOW.replace('\n', '\r\n')))
        self.assert_pass(self._repo(RENOVATE_WORKFLOW.rstrip('\n')))

    def test_an_added_line_fails(self):
        self.assert_fail(self._repo(RENOVATE_WORKFLOW + '# local tweak\n'),
                         containing='`# local tweak`, is not in the template')

    def test_a_removed_line_fails(self):
        comment = '          # renovate: datasource=npm depName=renovate\n'
        self.assertIn(comment, RENOVATE_WORKFLOW)
        line = RENOVATE_WORKFLOW.splitlines().index(comment.rstrip('\n')) + 1
        self.assert_fail(
            self._repo(RENOVATE_WORKFLOW.replace(comment, '')),
            containing='`# renovate: datasource=npm depName=renovate` is '
                       f'missing before line {line}')

    def test_a_removed_last_line_is_missing_at_the_end(self):
        """No line number past the copy's last."""
        lines = RENOVATE_WORKFLOW.splitlines(keepends=True)
        self.assertIn('node_modules/.bin/renovate', lines[-1])
        self.assert_fail(self._repo(''.join(lines[:-1])),
                         containing='is missing at the end')

    def test_every_place_is_named_with_the_copys_line(self):
        result = self._repo('# header\n' + RENOVATE_WORKFLOW.replace(
            'node-version: 24\n', 'node-version: 26\n'))
        line = RENOVATE_WORKFLOW.splitlines().index('          node-version: 24') + 2
        self.assertIn('line 1, `# header`, is not in the template', result['details'])
        self.assertIn(f'line {line} is `node-version: 26`', result['details'])

    def test_places_past_the_limit_are_counted(self):
        lines = RENOVATE_WORKFLOW.splitlines(keepends=True)
        # Five lines apart from one another make five places.
        for i in (0, 10, 20, 30, 40):
            lines[i] = lines[i].rstrip('\n') + '  # changed\n'
        details = self._repo(''.join(lines))['details']
        self.assertEqual(details.count('# changed'), packaging.RENOVATE_DRIFT_SHOWN)
        self.assertIn(f'and {5 - packaging.RENOVATE_DRIFT_SHOWN} more', details)

    def test_a_container_copy_is_told_once_to_copy_the_template(self):
        result = self._repo(RenovateWithoutDockerTest.CONTAINER_WORKFLOW)
        self.assert_fail(result, containing='needs a Docker daemon')
        self.assertNotIn('differs from', result['details'])

    def test_an_unreadable_template_is_a_tool_failure(self):
        with mock.patch.object(packaging, 'RENOVATE_WORKFLOW_TEMPLATE',
                               'templates/renovate/no-such.yml'):
            self.assert_tool_failure(
                lambda: self._repo(RENOVATE_WORKFLOW),
                containing='Could not read templates/renovate/no-such.yml')

    def test_developments_own_copy_passes(self):
        """This repository runs the template too."""
        self.assertIsNone(packaging.renovate_workflow_drift(
            repo_text('.github', 'workflows', 'renovate.yml'), RENOVATE_WORKFLOW))


class RenovateLockstepGroupsTest(CheckTestCase):
    check_class = packaging.RenovateLockstepGroups

    OSLO = ['"oslo.concurrency==7.6.1",', '"oslo.config==10.7.0",']

    def pyproject(self, dependencies, extra=''):
        body = '[project]\nname = "x"\ndependencies = [\n'
        body += ''.join('    %s\n' % line for line in dependencies)
        body += ']\n' + extra
        self.fixture.write('pyproject.toml', body)

    def renovate(self, *rules):
        self.fixture.write(
            'renovate.json',
            json.dumps({'packageRules': list(rules)}, indent=2) + '\n')

    def test_without_pyproject_it_does_not_apply(self):
        self.renovate()
        self.assert_skip(self.check(has_pyproject_toml=False),
                         containing='No pyproject.toml')

    def test_without_renovate_json_it_does_not_apply(self):
        self.pyproject(self.OSLO)
        self.assert_skip(self.check(), containing='No readable renovate.json')

    def test_malformed_renovate_json_does_not_apply(self):
        self.pyproject(self.OSLO)
        self.fixture.write('renovate.json', '{not json\n')
        self.assert_skip(self.check(), containing='No readable renovate.json')

    def test_a_single_family_member_does_not_apply(self):
        self.pyproject(['"oslo.concurrency==7.6.1",'])
        self.renovate()
        self.assert_skip(self.check(), containing='more than one member')

    def test_no_family_members_does_not_apply(self):
        self.pyproject(['"click==8.4.2",'])
        self.renovate()
        self.assert_skip(self.check(), containing='more than one member')

    def test_two_ungrouped_members_fail_and_are_named(self):
        self.pyproject(self.OSLO)
        self.renovate()
        result = self.assert_fail(self.check(), containing='oslo')
        self.assertIn('oslo.concurrency', result['details'])
        self.assertIn('oslo.config', result['details'])
        self.assertEqual(1, len(result['findings']))
        self.assertIn('oslo.config', result['findings'][0])

    def test_a_regex_group_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/']})
        self.assert_pass(self.check())

    def test_a_glob_group_in_the_declared_spelling_passes(self):
        """Renovate matches the manifest spelling, so "oslo.*" is correct."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['oslo.*']})
        self.assert_pass(self.check())

    def test_an_exact_name_group_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['oslo.concurrency',
                                             'oslo.config']})
        self.assert_pass(self.check())

    def test_the_deprecated_pattern_field_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackagePatterns': ['^oslo']})
        self.assert_pass(self.check())

    def test_a_group_missing_one_member_fails(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['oslo.concurrency']})
        self.assert_fail(self.check(), containing='oslo')

    def test_an_excluded_member_fails(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/', '!oslo.config']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_group_restricted_by_update_type_fails(self):
        """Grouping minor and patch leaves the majors arriving one by one."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchUpdateTypes': ['minor', 'patch'],
                       'matchPackageNames': ['/^oslo/']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_rule_without_a_group_name_is_not_a_group(self):
        self.pyproject(self.OSLO)
        self.renovate({'matchPackageNames': ['/^oslo/'],
                       'automerge': False})
        self.assert_fail(self.check(), containing='oslo')

    def test_members_in_an_optional_group_are_counted(self):
        self.pyproject(
            ['"click==8.4.2",'],
            extra=('\n[project.optional-dependencies]\n'
                   'test = [\n    "oslo.concurrency==7.6.1",\n'
                   '    "oslo.config==10.7.0",\n]\n'))
        self.renovate()
        self.assert_fail(self.check(), containing='oslo')

    def test_oslotest_is_not_an_oslo_library(self):
        """The family is "oslo.*", not everything starting with oslo."""
        self.pyproject(['"oslo.concurrency==7.6.1",', '"oslotest==5.0.0",'])
        self.renovate()
        self.assert_skip(self.check(), containing='more than one member')

    def test_an_exclusion_listed_first_still_excludes(self):
        """Renovate ignores order, so scoring by order is a false pass."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['!oslo.config', '/^oslo/']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_dep_name_group_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchDepNames': ['oslo.concurrency', 'oslo.config']})
        self.assert_pass(self.check())

    def test_the_deprecated_dep_pattern_field_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchDepPatterns': ['^oslo']})
        self.assert_pass(self.check())

    def test_a_case_insensitive_regex_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^OSLO/i']})
        self.assert_pass(self.check())

    def test_a_case_sensitive_regex_that_does_not_match_fails(self):
        """The flag is read, rather than every regex being folded."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^OSLO/']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_group_with_no_selector_fails(self):
        """Renovate rejects the rule, so the family is grouped nowhere."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'everything'})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_deprecated_exclusion_of_a_member_fails(self):
        """Migrated, this reads "!oslo.config" -- it splits the family."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'excludePackageNames': ['oslo.config']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_deprecated_exclusion_pattern_of_the_family_fails(self):
        """A rule excluding the whole family covers none of it."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'excludePackagePatterns': ['^oslo']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_deprecated_exclusion_of_a_non_member_passes(self):
        """Excluding something else leaves the family covered."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'excludePackageNames': ['click']})
        self.assert_pass(self.check())

    def test_a_list_of_only_exclusions_covers_everything_else(self):
        """Renovate constrains nothing when no entry is positive."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['!click']})
        self.assert_pass(self.check())

    def test_a_deprecated_prefix_matcher_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackagePrefixes': ['oslo']})
        self.assert_pass(self.check())

    def test_a_package_matcher_survives_a_matcher_we_do_not_read(self):
        """Only a rule with no package matcher at all covers nothing."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/'],
                       'matchManagers': ['pep621']})
        self.assert_pass(self.check())

    def test_both_matchers_must_accept_a_member(self):
        """The two are separate conditions, so the rule covers one."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/'],
                       'matchDepNames': ['oslo.config']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_group_narrowed_by_a_matcher_we_do_not_read_fails(self):
        """Whether it reaches these packages is not knowable here."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'npm things',
                       'matchManagers': ['npm']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_malformed_pyproject_does_not_crash_the_run(self):
        """A mid-edit manifest must not lose the whole repository's run."""
        self.fixture.write(
            'pyproject.toml', '[project]\nname = "x"\ndependencies = [\n')
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/']})
        self.assert_skip(self.check(), containing='more than one member')


if __name__ == '__main__':
    unittest.main()
