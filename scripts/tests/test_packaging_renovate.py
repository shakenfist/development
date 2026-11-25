#!/usr/bin/env python3

"""Tests for the Renovate checks in audit/checks/packaging.py.

The rest of that module's checks are tested in test_packaging.py.

Run with: python3 scripts/tests/test_packaging_renovate.py
"""

import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import packaging  # noqa: E402
from tests.base import REPO_ROOT, CheckTestCase  # noqa: E402


class RenovateTest(CheckTestCase):
    """A repository reaches Renovate through the fleet preset and nothing else.

    Four findings: renovate.json is missing or unreadable, the
    per-repository workflow is present, renovate.json does not extend the
    preset, and renovate.json overrides a top-level key the preset sets.
    """

    check_class = packaging.Renovate

    PRESET_NAME = 'local>shakenfist/development:renovate-fleet'
    SCHEMA = 'https://docs.renovatebot.com/renovate-schema.json'
    WORKFLOW = 'name: Renovate\non: workflow_dispatch\n'

    def _preset(self):
        with open(os.path.join(REPO_ROOT, 'renovate-fleet.json')) as f:
            return json.load(f)

    def _repo(self, renovate, workflow=False):
        """Run the check over a renovate.json, given as a dict or raw text."""
        if renovate is not None:
            if not isinstance(renovate, str):
                renovate = json.dumps(renovate)
            self.fixture.write('renovate.json', renovate)
        if workflow:
            self.fixture.workflow('renovate.yml', self.WORKFLOW)
        return self.check()

    def _config(self, **keys):
        return dict({'$schema': self.SCHEMA, 'extends': [self.PRESET_NAME]},
                    **keys)

    def test_the_preset_alone_passes(self):
        self.assert_pass(self._repo(self._config()))

    def test_the_template_passes(self):
        # The template is what an issue points a repository at, so a
        # copy of it has to close the issue.
        with open(os.path.join(REPO_ROOT, 'templates', 'renovate',
                               'renovate.json')) as f:
            self.assert_pass(self._repo(f.read()))

    def test_the_preset_with_other_presets_passes(self):
        self.assert_pass(self._repo(self._config(
            extends=[self.PRESET_NAME, 'customManagers:dockerfileVersions'])))

    def test_the_workflow_fails(self):
        result = self._repo(self._config(), workflow=True)
        self.assert_fail(
            result, containing='.github/workflows/renovate.yml exists')
        self.assertIn('races it; delete the file', result['details'])

    def test_a_workflow_elsewhere_is_not_this(self):
        self.fixture.workflow('renovate-fleet.yml', self.WORKFLOW)
        self.assert_pass(self._repo(self._config()))

    def test_extends_missing_fails(self):
        for config in ({'$schema': self.SCHEMA},
                       {'extends': ['config:recommended']},
                       {'extends': []}):
            with self.subTest(config=config):
                self.fresh_fixture()
                result = self._repo(config)
                self.assert_fail(
                    result,
                    containing=f'does not extend {self.PRESET_NAME}')
                self.assertNotIn('has to be a list', result['details'])

    def test_extends_not_a_list_fails(self):
        for extends in (self.PRESET_NAME, {'0': self.PRESET_NAME}, None):
            with self.subTest(extends=extends):
                self.fresh_fixture()
                result = self._repo({'extends': extends})
                self.assert_fail(
                    result,
                    containing=f'does not extend {self.PRESET_NAME}')
                self.assertIn('(extends has to be a list)', result['details'])

    def test_one_overridden_key_fails_naming_it(self):
        # Every repository in the fleet carried this one: a local
        # schedule wins over the preset's, so the cadence never changes.
        result = self._repo(self._config(schedule=['after 9pm']))
        self.assert_fail(
            result, containing='renovate.json sets schedule at its top level')
        self.assertIn('delete it,', result['details'])

    def test_several_overridden_keys_are_named_in_one_finding(self):
        config = self._config(
            schedule=['after 9pm'], assignees=['mikalstill'],
            dependencyDashboard=True, minimumReleaseAge='3 days',
            rollbackPrs=True, gitAuthor='someone <someone@example.com>')
        config['pre-commit'] = {'enabled': True}
        result = self._repo(config)
        self.assert_fail(
            result,
            containing='renovate.json sets assignees, dependencyDashboard, '
                       'gitAuthor, minimumReleaseAge, pre-commit, '
                       'rollbackPrs, schedule at its top level')
        self.assertIn('delete them,', result['details'])
        self.assertEqual(result['details'].count('renovate.json sets'), 1)

    def test_every_key_the_preset_sets_is_reported(self):
        # Read from the preset, not listed here: a key the preset gains
        # is a key a repository may no longer set.
        preset = self._preset()
        expected = sorted(set(preset) - {'$schema', 'extends', 'packageRules'})
        # Guard the derivation: an empty set would pass everything.
        self.assertIn('schedule', expected)
        result = self._repo(dict(preset, extends=[self.PRESET_NAME]))
        self.assert_fail(
            result,
            containing=f'renovate.json sets {", ".join(expected)} at its '
                       f'top level')

    def test_the_shared_keys_are_never_reported(self):
        preset = self._preset()
        for key in ('$schema', 'extends', 'packageRules'):
            self.assertIn(key, preset)
        result = self._repo(self._config(packageRules=[
            {'matchPackageNames': ['oslo.*'], 'groupName': 'oslo'}]))
        self.assert_pass(result)

    def test_the_shared_keys_are_not_reported_beside_an_override(self):
        result = self._repo(self._config(
            schedule=['after 9pm'],
            packageRules=[{'matchPackageNames': ['oslo.*'],
                           'groupName': 'oslo'}]))
        # The list is joined just before "at its top level", so this
        # names schedule and nothing else.
        self.assert_fail(
            result, containing='renovate.json sets schedule at its top level')

    def test_keys_the_preset_does_not_set_pass(self):
        preset = self._preset()
        local = {
            'lockFileMaintenance': {'enabled': True,
                                    'schedule': ['before 4am on monday']},
            'constraints': {'python': '==3.12'},
            'customManagers': [{'customType': 'regex',
                                'managerFilePatterns': ['/^tox\\.ini$/'],
                                'matchStrings': ['x'],
                                'datasourceTemplate': 'pypi'}],
            'github-actions': {'managerFilePatterns': ['/^templates/.+$/']},
        }
        for key in local:
            self.assertNotIn(key, preset)
        self.assert_pass(self._repo(self._config(**local)))

    def test_every_finding_is_reported_together(self):
        result = self._repo({'extends': ['config:recommended'],
                             'schedule': ['after 9pm'],
                             'timezone': 'UTC'},
                            workflow=True)
        self.assert_fail(result)
        details = result['details']
        self.assertIn('.github/workflows/renovate.yml exists', details)
        self.assertIn(f'does not extend {self.PRESET_NAME}', details)
        self.assertIn('renovate.json sets schedule, timezone at its top level',
                      details)

    def test_renovate_json_missing_fails(self):
        result = self._repo(None)
        self.assert_fail(result, containing='Missing: renovate.json')
        self.assertEqual(result['missing'], ['renovate.json'])

    def test_renovate_json_missing_is_reported_with_the_workflow(self):
        result = self._repo(None, workflow=True)
        self.assert_fail(result, containing='Missing: renovate.json')
        self.assertIn('.github/workflows/renovate.yml exists',
                      result['details'])
        self.assertEqual(result['missing'], ['renovate.json'])

    def test_no_missing_list_when_renovate_json_is_there(self):
        result = self._repo({}, workflow=True)
        self.assert_fail(result)
        self.assertNotIn('missing', result)

    def test_a_malformed_renovate_json_fails_rather_than_raising(self):
        # renovate.json's top level can hold any JSON value, or none.
        for renovate in ('{not json', '[1, 2]', '"hi"', 'null', ''):
            with self.subTest(renovate=renovate):
                self.fresh_fixture()
                result = self._repo(renovate, workflow=True)
                self.assert_fail(result, containing='is not a JSON object')
                self.assertIn('.github/workflows/renovate.yml exists',
                              result['details'])
                self.assertNotIn('missing', result)

    def test_an_unreadable_preset_is_a_tool_failure(self):
        with mock.patch.object(packaging, 'RENOVATE_FLEET_PRESET_FILE',
                               'no-such-preset.json'):
            self.assert_tool_failure(
                lambda: self._repo(self._config()),
                containing='Could not read no-such-preset.json')

    def test_an_unparseable_preset_is_a_tool_failure(self):
        for content, containing in (
                ('{not json', 'Could not parse renovate-fleet.json'),
                ('[1, 2]', 'renovate-fleet.json in the audit\'s own '
                           'checkout is not a JSON object')):
            with self.subTest(content=content):
                self.fresh_fixture()
                self.fixture.write('renovate.json',
                                   json.dumps(self._config()))
                with mock.patch.object(packaging, 'read_within',
                                       return_value=content):
                    self.assert_tool_failure(self.check, containing=containing)

    def test_the_preset_is_read_only_to_compare_with_a_config(self):
        # With no renovate.json to compare, an unreadable preset changes
        # nothing about the verdict.
        with mock.patch.object(packaging, 'RENOVATE_FLEET_PRESET_FILE',
                               'no-such-preset.json'):
            self.assert_fail(self._repo(None), containing='Missing')
            self.fresh_fixture()
            self.assert_fail(self._repo('[]'),
                             containing='is not a JSON object')


class RenovateConfigPredicatesTest(unittest.TestCase):
    """The three properties RenovateFleetPresetTest holds the preset to.

    Each predicate is tested here on small configs, so that the preset
    tests passing means the preset has the property rather than that the
    predicate accepts anything.
    """

    VERSIONING = ('regex:^v?(?<major>\\d+)\\.(?<minor>\\d+)'
                  '\\.(?<patch>\\d+)\\.(?<build>\\d+)$')

    def test_each_way_of_enabling_the_pre_commit_manager_counts(self):
        for config in ({'pre-commit': {'enabled': True}},
                       {'enabledManagers': ['cargo', 'pre-commit']},
                       {'extends': [':enablePreCommit']}):
            with self.subTest(config=config):
                self.assertTrue(
                    packaging.renovate_config_manages_pre_commit(config))

    def test_the_pre_commit_manager_disabled_or_absent_does_not_count(self):
        for config in ({}, {'pre-commit': {'enabled': False}},
                       {'pre-commit': True}, {'enabledManagers': ['cargo']},
                       {'extends': ':enablePreCommit'}):
            with self.subTest(config=config):
                self.assertFalse(
                    packaging.renovate_config_manages_pre_commit(config))

    def test_the_version_comment_preset_counts_in_either_name(self):
        for preset in ('customManagers:githubActionsVersions',
                       'regexManagers:githubActionsVersions'):
            with self.subTest(preset=preset):
                self.assertTrue(
                    packaging.renovate_config_reads_version_comments(
                        {'extends': [':enablePreCommit', preset]}))

    def test_a_config_without_the_version_comment_preset_does_not_count(self):
        for config in ({}, {'extends': [':enablePreCommit']},
                       {'extends': 'customManagers:githubActionsVersions'}):
            with self.subTest(config=config):
                self.assertFalse(
                    packaging.renovate_config_reads_version_comments(config))

    def _rule(self, **match):
        return {'packageRules': [dict(match, versioning=self.VERSIONING)]}

    def _versions(self, config):
        return packaging.renovate_config_versions_shellcheck_py(config)

    def test_a_shellcheck_py_versioning_rule_counts(self):
        self.assertTrue(self._versions(
            self._rule(matchPackageNames=['shellcheck-py/shellcheck-py'])))

    def test_every_spelling_renovate_applies_counts(self):
        # Renovate applies each of these to the hook.
        for match in (
            {'matchDepNames': ['shellcheck-py/shellcheck-py']},
            {'matchPackageNames': ['shellcheck-py/**']},
            {'matchPackageNames': ['/^shellcheck-py//']},
            {'matchPackageNames': ['/SHELLCHECK/i']},
            {'matchPackagePatterns': ['^shellcheck-py/']},
            {'matchPackagePrefixes': ['shellcheck-py/']},
            {'matchPackageNames': ['!rhysd/actionlint']},
        ):
            with self.subTest(match=match):
                self.assertTrue(self._versions(self._rule(**match)))

    def test_a_rule_scoped_away_from_pre_commit_still_counts(self):
        # Chosen, not missed: see renovate_config_versions_shellcheck_py.
        self.assertTrue(self._versions(self._rule(
            matchPackageNames=['shellcheck-py/shellcheck-py'],
            matchManagers=['npm'])))

    def test_a_rule_that_does_not_select_the_hook_does_not_count(self):
        for match in (
            {'matchPackageNames': ['rhysd/actionlint']},
            {'matchPackageNames': ['shellcheck-py/**',
                                   '!shellcheck-py/shellcheck-py']},
            {'matchPackageNames': ['/^shellcheck/'],
             'excludePackageNames': ['shellcheck-py/shellcheck-py']},
            {'matchPackageNames': ['shellcheck-py/shellcheck-py'],
             'matchDepNames': ['rhysd/actionlint']},
            {'matchManagers': ['pre-commit']},
            # Renovate rejects a non-list matcher; it does not name the hook.
            {'matchPackageNames': 'shellcheck-py/shellcheck-py'},
        ):
            with self.subTest(match=match):
                self.assertFalse(self._versions(self._rule(**match)))

    def test_a_rule_without_a_versioning_does_not_count(self):
        for rule in ({'matchPackageNames': ['shellcheck-py/shellcheck-py'],
                      'groupName': 'linters'},
                     {'matchPackageNames': ['shellcheck-py/shellcheck-py'],
                      'versioning': ''}):
            with self.subTest(rule=rule):
                self.assertFalse(self._versions({'packageRules': [rule]}))

    def test_malformed_package_rules_do_not_raise(self):
        for rules in ('x', None, [1, 'x'], {'a': 1}):
            with self.subTest(rules=rules):
                self.assertFalse(self._versions({'packageRules': rules}))


class RenovateFleetPresetTest(CheckTestCase):
    """A repository that extends the fleet preset inherits what it holds.

    The audit trusts the preset by name, so what it vouches for is proved
    here against the real file: a local> preset takes effect fleet-wide the
    moment an edit to it merges, and an edit that dropped the pre-commit
    manager, the version comment extend or the shellcheck-py versioning
    rule would otherwise leave every repository passing on a promise the
    preset no longer keeps.
    """

    check_class = packaging.Renovate

    PRESET_NAME = 'local>shakenfist/development:renovate-fleet'

    def _preset(self):
        with open(os.path.join(REPO_ROOT, 'renovate-fleet.json')) as f:
            return json.load(f)

    def _repo(self, renovate):
        self.fixture.write('renovate.json', json.dumps(renovate))
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
        self.assertFalse(
            packaging.renovate_config_versions_shellcheck_py(config))

    def test_the_real_preset_versions_shellcheck_py(self):
        self.assertTrue(
            packaging.renovate_config_versions_shellcheck_py(
                self._preset()))

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
        self.assertEqual(rule['matchFileNames'],
                         ['.github/workflows/renovate-fleet.yml'])

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
        # The whole set, so a file named back here fails this.
        self.assertEqual(re.findall(r'[\w./-]+\.yml', note),
                         ['.github/workflows/renovate-fleet.yml'])

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
        {'github-actions', 'packageRules'})

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

    def test_the_root_config_holds_one_local_rule(self):
        # It is local because it names a file only this repository has;
        # a second is a decision somebody should have to make here.
        self.assertEqual(len(self._config('renovate.json')['packageRules']), 1)

    def test_the_root_config_gives_the_runner_workflow_actions_a_weekly_schedule(self):
        rule = self._root_rule(['.github/workflows/renovate-fleet.yml'])
        self.assertEqual(sorted(rule['matchPackageNames']),
                         ['actions/checkout', 'actions/setup-node'])
        # The preset's own weekly window, not a second spelling of it.
        self.assertEqual(rule['schedule'], self._preset()['schedule'])
        self.assertNotIn('groupName', rule)


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
        self.assertEqual(rule['matchFileNames'],
                         ['.github/workflows/renovate.yml'])


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
