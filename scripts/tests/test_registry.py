#!/usr/bin/env python3

"""Repository-wide invariants of the audit: the schedule and its scope.

These are the tests that are about the audit as a whole rather than
about any one criterion: that everything scheduled has a specification
and an issue title, that a scoped repository still reports every check,
that the overrides are documented, and that the workflows checking out
pull request code neuter core.hooksPath.

Run with: python3 -m unittest tests.test_registry
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit import registry, scope  # noqa: E402
from audit.repo import REPO_OVERRIDES, detect_repo_properties  # noqa: E402
from audit_common import AUDIT_METADATA, ISSUE_TITLES  # noqa: E402
from tests.base import REPO_ROOT  # noqa: E402

sys.path.insert(0, REPO_ROOT)


def run_all_checks(repo_path, repo_name, org, github=None):
    """The entry point's scheduler, without importing the hyphenated file."""
    from audit.repo import Repo
    return registry.run_all(Repo(repo_path, repo_name, org, github=github))


class AuditScopeIsStatedOnceTest(unittest.TestCase):
    """The three places that say who is audited must agree.

    Scope is written down three times: the matrix in
    .github/workflows/consistency-audit.yml is what actually runs,
    and the in-scope and excluded lists in docs/audits/README.md are
    what a reader is told. Nothing else ties them together, so a
    repository added to the matrix alone is audited while the
    documentation says it is not -- and one dropped from the matrix
    alone silently stops being measured while the documentation says
    it is.

    The parse itself lives in audit.scope, because the scope-coverage
    check needs the same one: this class holds the three lists to each
    other, that check holds them to the organisation, and a second
    copy of the parse would let the two disagree about what the lists
    say. What is tested here is that the anchors still delimit their
    lists, which is what the comparisons below are worth.
    """

    root = REPO_ROOT

    def matrix_repos(self):
        return scope.matrix_repos(self.root)

    def documented_in_scope(self):
        return scope.documented_in_scope(self.root)

    def documented_excluded(self):
        return scope.documented_excluded(self.root)

    def partially_scoped(self):
        return {
            name for name, overrides
            in REPO_OVERRIDES.items()
            if overrides.get('only_checks')
        }

    def test_a_parse_that_overruns_its_list_is_rejected(self):
        """The REPO_NAME guard must fire, not merely exist.

        Reading a guard cannot distinguish one that holds from one
        that cannot fail, so this hands bulleted_block() the failure it
        was written for. The loud cases are already covered by the
        count checks: a start or end phrase that vanishes raises
        naming the phrase. The quiet case is an end phrase that has
        drifted further down the page, so the block still terminates
        but now spans a prose list on the way -- with no heading
        crossed, REPO_NAME is the only thing left to notice.

        The bullet used here is the example named in the comment above
        REPO_NAME, which an end-anchored pattern accepted: re.search
        found 'covered' at the end of it and passed.
        """
        overrun = (
            'Two repositories are **excluded** from the conventions:\n'
            '\n'
            '* imago\n'
            '* ryll\n'
            '\n'
            'Some criterion, described in a paragraph that grew a list:\n'
            '\n'
            '* The configured version file path must be covered\n'
            '\n'
            'The `actions` repository is a library of composite actions.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'drifted.md'), 'w') as f:
                f.write(overrun)
            with self.assertRaisesRegex(
                    scope.ScopeParseError,
                    'The configured version file path must be covered'):
                scope.bulleted_block(
                    tmp, 'drifted.md', scope.EXCLUDED_START,
                    scope.EXCLUDED_END, scope.EXCLUDED_BULLET,
                )

    def test_repo_name_rejects_a_sentence_ending_in_a_word(self):
        # re.search is what bulleted_block() uses, so this is the whole
        # point of the leading anchor. Kept separate from the parse
        # above because it is the property, not the plumbing: if
        # REPO_NAME ever loses its '^' again, this is the test that
        # says so in one line.
        self.assertIsNone(
            scope.REPO_NAME.search(
                'The configured version file path must be covered'),
            'REPO_NAME matched a sentence, so it is not anchored at '
            'the start and cannot notice a parse collecting prose',
        )
        for name in ['shakenfist', 'client-python', 'kerbside-patches']:
            self.assertIsNotNone(
                scope.REPO_NAME.search(name),
                f'REPO_NAME no longer matches the repository name '
                f'"{name}"',
            )

    def test_a_subsection_heading_inside_the_block_is_caught(self):
        # The list this guards sits under a '### ', so a guard that
        # only knew '## ' would not have noticed a '###' subsection
        # appearing inside the parsed span.
        drifted = (
            'Two repositories are **excluded** from the conventions:\n'
            '\n'
            '* imago\n'
            '\n'
            '### Some new subsection\n'
            '\n'
            '* ryll\n'
            '\n'
            'The `actions` repository is a library of composite actions.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'drifted.md'), 'w') as f:
                f.write(drifted)
            with self.assertRaisesRegex(scope.ScopeParseError,
                                        'runs past a heading'):
                scope.bulleted_block(
                    tmp, 'drifted.md', scope.EXCLUDED_START,
                    scope.EXCLUDED_END, scope.EXCLUDED_BULLET,
                )

    def test_the_parse_anchors_still_delimit_their_lists(self):
        # The comparisons below are worth no more than the parses that
        # feed them, and all three parses are anchored to phrases in
        # documents that get rewritten for reasons that have nothing
        # to do with this suite -- the page holding both lists was
        # rewritten wholesale more than once already. Run
        # them here on their own so that a reworded anchor fails as a
        # reworded anchor, naming the phrase and the file, rather than
        # as a mysterious disagreement about which repositories are
        # audited. Each parse raises on its own delimiting; this test
        # is what makes sure all three are exercised even if a
        # comparison below is one day rewritten not to call them.
        self.matrix_repos()
        self.documented_in_scope()
        self.documented_excluded()

    def test_matrix_matches_the_documented_scope(self):
        matrix = set(self.matrix_repos())
        self.assertIn('development', matrix)
        self.assertEqual(
            matrix - self.partially_scoped(),
            set(self.documented_in_scope()),
            'the audit matrix and the in-scope list in '
            'docs/audits/README.md disagree',
        )

    def test_no_audited_repo_is_also_documented_as_excluded(self):
        # A repository scoped to a subset of the checks is the one
        # exception: private-ci is excluded from the conventions but
        # audited for sfui-vendor, and both statements are true.
        overlap = (
            set(self.matrix_repos())
            & set(self.documented_excluded())
            - self.partially_scoped()
        )
        self.assertEqual(
            overlap, set(),
            'docs/audits/README.md lists these as excluded but '
            'the audit matrix runs every check against them',
        )


class RepoOverridesTest(unittest.TestCase):
    def test_actions_repo_properties(self):
        # The actions repository carries Python helper scripts but has
        # nothing to package, and keeps "main" because every consumer
        # pins to @main.
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'actions'
        )
        self.assertTrue(props['not_python'])
        self.assertIn('@main', props['default_branch_exception'])

    def test_development_audits_itself(self):
        # development holds the audit tooling and is audited by it.
        # Its Python is never packaged, and it publishes no releases,
        # so it has no release branch for "develop" to be distinct
        # from -- but the exemption has to be a stated reason, not an
        # absence from the matrix.
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'development'
        )
        self.assertTrue(props['not_python'])
        self.assertIn('releases', props['default_branch_exception'])

    def test_ordinary_repo_has_no_default_branch_exception(self):
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['default_branch_exception'], '')

    def test_shakenfist_excludes_imported_docs(self):
        # docs/components/ is an automated import of the other
        # repositories' documentation directories.
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'shakenfist'
        )
        self.assertEqual(
            props['doc_content_excludes'], ['docs/components/']
        )

    def test_ordinary_repo_has_no_doc_content_excludes(self):
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['doc_content_excludes'], [])

    def test_ordinary_repo_is_scoped_to_no_checks(self):
        # An empty only_checks means the whole audit applies, so the
        # override cannot narrow a repository by accident.
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['only_checks'], [])

    def test_private_ci_is_scoped_to_the_sfui_check(self):
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'private-ci'
        )
        self.assertEqual(props['only_checks'], ['sfui-vendor'])


class CheckScopeTest(unittest.TestCase):
    """The only_checks scoping in run_all_checks."""

    def _ids(self):
        # The schedule itself. Every criterion is a registered Check
        # now, so this reads registry.CHECKS -- but it reads it through
        # scheduled(), which is what run_all() calls, rather than the
        # list directly.
        return [
            check_id for check_id, _ in registry.scheduled()
        ]

    def test_every_scheduled_id_is_a_known_check(self):
        # A typo in the id table would make a check unschedulable
        # while still reporting a plausible looking result, so the
        # table has to agree with the issue title map. ISSUE_TITLES is
        # that map itself rather than a copy of it: audit-manage-issues
        # reads it as .get(check_id, check_id), so an id missing from it
        # files under the bare check id and orphans every open issue for
        # that check across the fleet, and audit-update-docs subscripts
        # it directly, so the same omission raises KeyError during docs
        # regeneration.
        #
        # AUDIT_METADATA is the third corner of the same triangle:
        # audit-update-docs iterates it to emit one compliance section
        # per check, and audit-manage-issues reads it for the spec link
        # in each filed issue. Asserting both closes the loop, so a new
        # check cannot be scheduled while missing from either map.
        ids = self._ids()
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertEqual(
            sorted(ids), sorted(ISSUE_TITLES.keys())
        )
        self.assertEqual(
            sorted(ids), sorted(AUDIT_METADATA.keys())
        )

    def test_scoped_repo_runs_only_its_check(self):
        # private-ci is scoped to sfui-vendor. Every other check must
        # be reported not_applicable with the scoping reason, and must
        # not have run: a check that ran would have written its own
        # details, and several of them would reach for the network.
        with tempfile.TemporaryDirectory() as tmp:
            results = run_all_checks(
                tmp, 'private-ci', 'shakenfist'
            )

        reason = 'private-ci is audited for sfui-vendor only'
        by_id = {c['id']: c for c in results['checks']}
        self.assertEqual(len(by_id), len(ISSUE_TITLES))

        for check_id, check in by_id.items():
            if check_id == 'sfui-vendor':
                self.assertNotEqual(check['details'], reason)
                continue
            self.assertEqual(check['status'], 'not_applicable')
            self.assertEqual(check['details'], reason)

        # Nothing is dropped from the results, because a check missing
        # from the JSON renders as "unknown" in the docs/audits/ tables.
        self.assertEqual(
            results['summary']['total'], len(ISSUE_TITLES)
        )
        self.assertEqual(results['summary']['fail'], 0)

    def test_unscoped_repo_schedules_everything(self):
        # The scoping is opt in: with no override, no check is
        # replaced by the not_applicable stand-in.
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertFalse(props['only_checks'])


class GitHooksDisabledTest(unittest.TestCase):
    """The workflows that check out PR code must neuter core.hooksPath.

    Layer 4 of the security model in docs/ci-review-automation.md
    names these three files and asserts the control is set in them.
    Nothing in check_ci_review_automation inspects checkout steps, so
    without this a template edit could drop the step and leave the
    document claiming a control that is not there -- which is the
    defect this test's own pull request existed to fix. The assertion
    is on this repository's files rather than on a synthetic tree
    because the templates are the fleet's source of truth: a repo that
    copies them inherits whatever is here.
    """

    WORKFLOWS = [
        os.path.join('.github', 'workflows', 'pr-re-review.yml'),
        os.path.join(
            'templates', 'ci-review-automation', 'pr-re-review.yml'),
        os.path.join(
            'templates', 'test-drift-fix', 'test-drift-fix.yml'),
    ]

    # Matched as a pattern rather than as one exact spelling. This
    # test is the fleet's guard, and its failures are read by people
    # who did not write it: `git config --local core.hooksPath` sets
    # the same thing, and reporting it as a missing line would send
    # them to delete a correct one.
    HOOKS_PATH = re.compile(r'git config (--local )?core\.hooksPath')

    def test_hooks_path_is_set_after_the_checkout(self):
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    lines = f.read().splitlines()

                config = [
                    i for i, line in enumerate(lines)
                    if self.HOOKS_PATH.search(line)
                    and not line.lstrip().startswith('#')
                ]
                self.assertEqual(
                    len(config), 1,
                    f'{name} must set core.hooksPath exactly once')

                # Ordering matters as much as presence: "git config"
                # outside a work tree fails, and hooks set before the
                # checkout would be overwritten by it. Against the
                # last checkout rather than the first, because a
                # second one added after the config step would
                # re-clone the tree and discard .git/config.
                checkout = [
                    i for i, line in enumerate(lines)
                    if 'actions/checkout@' in line
                ]
                self.assertTrue(
                    checkout, f'{name} has no checkout step')
                self.assertGreater(
                    config[0], checkout[-1],
                    f'{name} sets core.hooksPath before its last '
                    'checkout, which would discard the setting')

    def test_the_setting_is_repository_local(self):
        # --global would outlive the job on the shared claude-code
        # pool and disable hooks for every later job on that machine.
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    body = f.read()
                self.assertNotIn(
                    'git config --global core.hooksPath', body)

    def test_the_document_still_names_these_workflows(self):
        # The test and the claim have to move together: a workflow
        # dropped from the list here but left in the document is the
        # same unbacked claim in the other direction.
        #
        # Scoped to layer 4 rather than the whole document on purpose.
        # "test-drift-fix.yml" also appears under Workflow Templates,
        # so a document-wide assertIn would stay green after the name
        # was struck from the security model -- a guard that passes
        # for a reason unrelated to what it defends.
        layer = self._security_model_layer_four()
        self.assertIn('core.hooksPath=/dev/null', layer)
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertIn(os.path.basename(name), layer)

    # AGENTS.md: a document parsed by phrase gets named constants
    # and an assertion, not a bare index() that raises ValueError
    # without naming the phrase that stopped matching. Same treatment
    # as AuditScopeIsStatedOnceTest.bulleted_block(), including the
    # count assertions -- they report the phrase rather than dumping
    # the document the way assertIn would.
    DOC = os.path.join('docs', 'ci-review-automation.md')
    LAYER_FOUR = '4. **Git hooks disabled**'
    LAYER_FIVE = '5. **'

    def _security_model_layer_four(self):
        with open(os.path.join(REPO_ROOT, self.DOC)) as f:
            doc = f.read()
        self.assertEqual(
            doc.count(self.LAYER_FOUR), 1,
            f'{self.DOC} must contain "{self.LAYER_FOUR}" exactly '
            f'once: it is where this test starts reading the layer, '
            f'and a renumbered or reworded security model has to fail '
            f'as that rather than as a missing control')
        after = doc.split(self.LAYER_FOUR, 1)[1]
        self.assertEqual(
            after.count(self.LAYER_FIVE), 1,
            f'{self.DOC} must contain "{self.LAYER_FIVE}" exactly '
            f'once after "{self.LAYER_FOUR}": it is where this test '
            f'stops reading, and without it the parse runs to the end '
            f'of the file')
        return self.LAYER_FOUR + after.split(self.LAYER_FIVE, 1)[0]


if __name__ == '__main__':
    unittest.main()
