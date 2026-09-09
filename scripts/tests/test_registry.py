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

    def documented_partial_scope(self):
        return scope.documented_partial_scope(self.root)

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
        self.documented_partial_scope()

    def test_matrix_matches_the_documented_scope(self):
        matrix = set(self.matrix_repos())
        self.assertIn('development', matrix)
        self.assertEqual(
            matrix - self.partially_scoped(),
            set(self.documented_in_scope()),
            'the audit matrix and the in-scope list in '
            'docs/audits/README.md disagree',
        )

    def test_the_partial_scope_paragraph_matches_the_overrides(self):
        """What a scoped repository is audited for, said once.

        The three lists above are held to each other; this sentence
        was not held to anything. only_checks was widened from
        ['sfui-vendor'] to five ids and the paragraph naming the one
        went on saying "and nothing else" -- with the full suite
        passing, because no test read it. This is that keeper.
        """
        documented = self.documented_partial_scope()
        overrides = {
            name: props['only_checks']
            for name, props in REPO_OVERRIDES.items()
            if props.get('only_checks')
        }
        self.assertEqual(
            set(documented), set(overrides),
            'the partial-scope paragraph in docs/audits/README.md and '
            'the only_checks overrides in scripts/audit/repo.py '
            'disagree about which repositories are scoped to a subset '
            'of the checks',
        )
        registered = {check.id for check in registry.CHECKS}
        for name, only in overrides.items():
            self.assertEqual(
                sorted(documented[name]), sorted(only),
                f'docs/audits/README.md says {name} is audited for '
                f'{sorted(documented[name])}, but only_checks in '
                f'scripts/audit/repo.py runs {sorted(only)}',
            )
            # An id in only_checks that no check answers to is a
            # criterion silently not running: run_all() tests
            # membership, so a typo scopes the repository to one fewer
            # check and reports every other one not_applicable with a
            # reason naming a check that does not exist.
            self.assertEqual(
                set(only) - registered, set(),
                f'only_checks for {name} names check ids that are not '
                f'in the registry',
            )

    def test_a_partial_scope_sentence_that_overruns_is_rejected(self):
        # The quiet failure: the end phrase is reworded, so the
        # non-greedy span runs on to the next sentence that does carry
        # it and collects the backticked tokens in between. Here that
        # is `pyproject.toml`, which CHECK_ID rejects -- the same role
        # REPO_NAME plays for the two list parses.
        overrun = (
            'One project is in scope for part of the audit only:\n'
            '\n'
            '- private-ci is audited for the `sfui-vendor` criteria. '
            'It is not expected to grow a `pyproject.toml` or the '
            'other `release-workflow` checks, and nothing else.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'docs', 'audits'))
            with open(os.path.join(tmp, scope.PARTIAL_SCOPE_DOC), 'w') as f:
                f.write(overrun)
            with self.assertRaisesRegex(scope.ScopeParseError,
                                        'pyproject.toml'):
                scope.documented_partial_scope(tmp)

    def test_a_partial_scope_span_crossing_a_paragraph_is_rejected(self):
        # The same drift, but where everything the overrun span picks
        # up happens to look like a check id, so CHECK_ID cannot
        # notice. One sentence never crosses a blank line, so that is
        # what says the parse has left its sentence.
        overrun = (
            'One project is in scope for part of the audit only:\n'
            '\n'
            '- private-ci is audited for the `sfui-vendor` criteria.\n'
            '\n'
            'Some later paragraph mentioning the `plan-index` checks, '
            'and nothing else.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'docs', 'audits'))
            with open(os.path.join(tmp, scope.PARTIAL_SCOPE_DOC), 'w') as f:
                f.write(overrun)
            with self.assertRaisesRegex(scope.ScopeParseError,
                                        'runs past the end of its sentence'):
                scope.documented_partial_scope(tmp)

    def test_the_partial_scope_sentence_survives_a_reflow(self):
        # The phrases are long enough to be split by a re-wrap that
        # changes not one word, and a split phrase does not match. So
        # the same sentence is parsed at two wrappings and must give
        # the same answer at both; without the unwrap the second one
        # raises "the phrase is missing" at somebody who only reflowed
        # a paragraph.
        wrappings = [
            '- private-ci is audited for the `sfui-vendor` and '
            '`plan-index` checks, and nothing else. It is internal '
            'tooling.\n',

            '- private-ci is audited for the `sfui-vendor`\n'
            '  and `plan-index` checks, and\n'
            '  nothing else. It is internal tooling.\n',
        ]
        for text in wrappings:
            with tempfile.TemporaryDirectory() as tmp:
                os.makedirs(os.path.join(tmp, 'docs', 'audits'))
                path = os.path.join(tmp, scope.PARTIAL_SCOPE_DOC)
                with open(path, 'w') as f:
                    f.write(text)
                self.assertEqual(
                    scope.documented_partial_scope(tmp),
                    {'private-ci': ['sfui-vendor', 'plan-index']},
                    'the partial-scope parse reads a different list '
                    'depending on where the lines were wrapped',
                )

    def test_a_partial_scope_paragraph_that_vanished_is_rejected(self):
        # The loud failure, kept as a test because the alternative to
        # raising is returning {}, which compares equal to an empty
        # set of overrides and would pass the comparison above on a
        # page that no longer states the scope at all.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'docs', 'audits'))
            with open(os.path.join(tmp, scope.PARTIAL_SCOPE_DOC), 'w') as f:
                f.write('The scope is described elsewhere now.\n')
            with self.assertRaisesRegex(scope.ScopeParseError,
                                        'is audited for the'):
                scope.documented_partial_scope(tmp)

    def test_no_audited_repo_is_also_documented_as_excluded(self):
        # A repository scoped to a subset of the checks is the one
        # exception: private-ci is excluded from the conventions but
        # audited for a subset of the criteria, and both statements
        # are true. Phrased for the subset rather than for today's
        # members so it does not need editing when the scope moves.
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

    def test_private_ci_is_scoped_to_sfui_and_the_plan_checks(self):
        props = detect_repo_properties(
            tempfile.mkdtemp(), 'private-ci'
        )
        self.assertEqual(
            props['only_checks'],
            ['plan-phase-references', 'plan-source-references',
             'plan-index', 'plan-template', 'sfui-vendor'])
        # plan-audit-phase and push-audit are deliberately absent: the
        # plans written there before it adopted the template do not
        # carry a push audit phase, so enabling plan-audit-phase would
        # file an issue for a retrofit nobody has decided to do, and
        # push-audit has no PUSH-AUDIT.md to read until that changes.
        # Asserted rather than left to the equality above so that the
        # reason survives the next time the list moves.
        self.assertNotIn('plan-audit-phase', props['only_checks'])
        self.assertNotIn('push-audit', props['only_checks'])


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
        # private-ci is scoped to sfui-vendor and the four plan
        # checks. Every other check must be reported not_applicable
        # with the scoping reason, and must not have run: a check that
        # ran would have written its own details, and several of them
        # would reach for the network.
        with tempfile.TemporaryDirectory() as tmp:
            results = run_all_checks(
                tmp, 'private-ci', 'shakenfist'
            )

        reason = ('private-ci is audited for plan-index, '
                  'plan-phase-references, plan-source-references, '
                  'plan-template, sfui-vendor only')
        scoped = {
            'plan-phase-references', 'plan-source-references',
            'plan-index', 'plan-template', 'sfui-vendor',
        }
        by_id = {c['id']: c for c in results['checks']}
        self.assertEqual(len(by_id), len(ISSUE_TITLES))

        for check_id, check in by_id.items():
            if check_id in scoped:
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


class MergeRefResolutionTest(unittest.TestCase):
    """The re-review workflows must resolve the merge ref, not name one.

    Naming `refs/pull/N/merge` is right only while GitHub's copy is
    both present and current, and it is not guaranteed to be either.
    The case that bites is a merge ref which is present but stale --
    GitHub recomputes the merge commit asynchronously after a push --
    because the review that produces is a real, careful review of
    superseded code. Nothing about the run looks wrong.

    The resolve step is a control the whole fleet inherits from
    templates/ci-review-automation/, and it is documented in
    docs/ci-review-automation.md, so it gets the same treatment as
    core.hooksPath in GitHooksDisabledTest above: a test on this
    repository's own files, because a template edit that dropped the
    step would silently restore reviews of the wrong commit in every
    repository that copies it afterwards.
    """

    DEPLOYED_RE_REVIEW = os.path.join(
        '.github', 'workflows', 'pr-re-review.yml')
    TEMPLATE_RE_REVIEW = os.path.join(
        'templates', 'ci-review-automation', 'pr-re-review.yml')

    # docs/ci-review-automation.md lists this template's customisation
    # as "None", which is what makes byte-identical the right
    # assertion here. pr-retest.yml is deliberately excluded from it:
    # its ci.yml / functional-tests.yml divergence is documented in
    # its own header.
    def test_the_two_copies_are_identical(self):
        with open(os.path.join(REPO_ROOT, self.DEPLOYED_RE_REVIEW)) as f:
            deployed = f.read()
        with open(os.path.join(REPO_ROOT, self.TEMPLATE_RE_REVIEW)) as f:
            template = f.read()
        self.assertEqual(
            deployed, template,
            f'{self.DEPLOYED_RE_REVIEW} and {self.TEMPLATE_RE_REVIEW} '
            f'must be byte-identical: docs/ci-review-automation.md '
            f'tells the fleet this template is copied unmodified')

    # Matched on the step name rather than on any line of its shell,
    # because the shell is the part expected to change. A rename is a
    # decision somebody makes; a deletion should not be.
    RESOLVE_STEP = '- name: Resolve the ref to review'
    CHECKOUT_REF = 'ref: ${{ steps.ref.outputs.ref }}'

    # A checkout naming the merge ref directly is the shape this
    # replaced, and re-introducing it would pass every other check in
    # the tree.
    NAMED_MERGE_REF = re.compile(r'ref:\s*refs/pull/.*/merge')

    def test_the_merge_ref_is_resolved_rather_than_named(self):
        for name in [self.DEPLOYED_RE_REVIEW, self.TEMPLATE_RE_REVIEW]:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    body = f.read()
                self.assertIn(
                    self.RESOLVE_STEP, body,
                    f'{name} must carry a "{self.RESOLVE_STEP}" step: '
                    f'without it a re-review can check out a merge '
                    f'commit for a superseded head')
                self.assertIn(
                    self.CHECKOUT_REF, body,
                    f'{name} must check out the ref that step '
                    f'resolved, or resolving it changes nothing')
                self.assertIsNone(
                    self.NAMED_MERGE_REF.search(body),
                    f'{name} names a merge ref in a checkout rather '
                    f'than resolving one')

    DEPLOYED_RETEST = os.path.join('.github', 'workflows', 'pr-retest.yml')
    TEMPLATE_RETEST = os.path.join(
        'templates', 'ci-review-automation', 'pr-retest.yml')

    GROUP = 'group: pr-retest-${{ github.event.issue.number }}'
    GATE = "if: needs.trigger-retest.outputs.authorized == 'true'"

    def test_retest_dispatch_is_grouped_and_gated(self):
        # The group has to sit on a job that unauthorised comments
        # never enter. trigger-retest decides authorisation inside
        # itself, so a group there lets anyone who can comment cancel
        # an authorised run mid-flight -- leaving a dispatched test
        # suite whose only trace on the pull request is a refusal.
        # Asserting both together because either alone is the bug:
        # a group with no gate is that cancellation, and a gate with
        # no group is the double dispatch this fixed.
        for name in [self.DEPLOYED_RETEST, self.TEMPLATE_RETEST]:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    body = f.read()
                self.assertIn(
                    self.GROUP, body,
                    f'{name} must carry a per-pull-request concurrency '
                    f'group, or two "please retest" comments dispatch '
                    f'the test suite twice')
                self.assertIn(
                    self.GATE, body,
                    f'{name} must gate the grouped job on '
                    f'pr-bot-trigger having authorised the request')
                self.assertLess(
                    body.index(self.GATE), body.index(self.GROUP),
                    f'{name} declares its concurrency group before the '
                    f'authorisation gate, so the group is not on the '
                    f'gated job')


if __name__ == '__main__':
    unittest.main()
