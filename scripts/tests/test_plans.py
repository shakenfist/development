#!/usr/bin/env python3

"""Tests for audit/checks/plans.py.

Run with: python3 scripts/tests/test_plans.py
"""

# audit-ok: plan-reference-file
#
# The PLAN- paths below are fixtures, not pointers. These
# criteria are tested by naming plans that do not resolve,
# so the marker belongs to the file rather than to any one
# line of it.

import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import plans  # noqa: E402
from audit.text import shared_blocks  # noqa: E402
from tests.base import REPO_ROOT, run_check  # noqa: E402


PLAN_STATUSES = plans.PLAN_STATUSES
PLAN_TEMPLATE_BLOCKS = plans.PLAN_TEMPLATE_BLOCKS
PUSH_AUDIT_BLOCKS = plans.PUSH_AUDIT_BLOCKS
SHARED_BLOCKS_DIR = shared_blocks.SHARED_BLOCKS_DIR
load_canonical_block = shared_blocks.load_canonical_block


def check_plan_phase_references(path, props=None):
    return run_check(plans.PlanPhaseReferences(), path, props)


def check_plan_source_references(path, props=None):
    return run_check(plans.PlanSourceReferences(), path, props)


def check_plan_index(path, props=None):
    return run_check(plans.PlanIndex(), path, props)


def check_push_audit(path, props=None, blocks_dir=None):
    return run_check(plans.PushAudit(blocks_dir=blocks_dir), path, props)


def check_plan_template(path, props=None, blocks_dir=None):
    return run_check(plans.PlanTemplate(blocks_dir=blocks_dir), path, props)


class PlanPhaseReferencesTest(unittest.TestCase):
    def _check(self, files, props=None):
        """files maps repo-relative paths to content."""
        with tempfile.TemporaryDirectory() as tmp:
            for rel, content in files.items():
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path) or tmp,
                            exist_ok=True)
                with open(path, 'w') as f:
                    f.write(content)
            return check_plan_phase_references(
                tmp, props or {}
            )

    def test_not_applicable_without_readme_or_docs(self):
        self.assertEqual(
            self._check({})['status'], 'not_applicable'
        )

    def test_clean_docs_pass(self):
        result = self._check({
            'README.md': '# Project\n\nA pitch.\n',
            'docs/usage.md': 'The frobnicator frobs on demand.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_phase_reference_in_docs_fails_with_location(self):
        result = self._check({
            'docs/usage.md': (
                'Frobbing.\n\n'
                'Frobnication was implemented in phase 5.\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/usage.md:3', result['details'])

    def test_phase_reference_in_readme_fails(self):
        result = self._check({
            'README.md': 'Since Phase 3, frobbing is default.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('README.md:1', result['details'])

    def test_generated_compliance_block_is_not_scanned(self):
        """A harvested detail must not fail this repository's own audit.

        The compliance tables on docs/audits/compliance.md are written
        by audit-update-docs.py from detail strings collected in other
        repositories, and rendered as bare prose. The plan-index check
        quotes an offending status cell verbatim, and the canonical
        example of one is 'Complete (phases 1-5 and 2b, 2026-08-15)'.
        Once the audits tree moved under docs/ those files entered this
        check's scope, so without the exclusion the bot writes that
        phrase into docs/audits/plan-index.md one morning and this
        repository fails its own audit the next, having committed
        nothing.
        """
        result = self._check({
            'docs/audits/plan-index.md': (
                '# Audit: plan index\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '| Project | Status |\n'
                '|---------|--------|\n'
                '| ryll | FAIL |\n'
                '\n'
                'Details for non-compliant projects:\n'
                '\n'
                '- ryll: 1 plan has a freeform status cell '
                '(PLAN-x.md ("Complete (phases 1-5 and 2b, '
                '2026-08-15): shipped"))\n'
                '<!-- consistency-audit:end -->\n'
            ),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_prose_naming_the_markers_exempts_nothing(self):
        """Naming a marker in prose must not exempt anything.

        The first version of this exclusion matched
        'consistency-audit:begin' as a bare substring, so a sentence
        describing the markers triggered it. Documents that write the
        pair as one token -- '<!-- consistency-audit:begin/end -->' --
        matched the begin and never the end, blanking the rest of the
        file: 148 of 309 lines of one plan and 96 of 159 of another
        silently left docs-external-links. Both spellings below are
        taken from the real files that did it.
        """
        for prose in (
            'The `<!-- consistency-audit:begin/end -->` marker block so',
            'empty `<!-- consistency-audit:begin/end -->` block. Pairs with',
            'rewrites the table between the `<!-- consistency-audit:begin',
        ):
            result = self._check({
                'docs/notes.md': (
                    '# Notes\n'
                    '\n'
                    + prose + '\n'
                    '\n'
                    'This was wired up in phase 6.\n'
                ),
            })
            self.assertEqual(
                result['status'], 'fail',
                f'prose naming the markers exempted the rest of the '
                f'file: {prose!r}',
            )
            self.assertIn('docs/notes.md:5', result['details'])

    def test_prose_naming_both_markers_exempts_nothing_between_them(self):
        """Exact whole-line matching, pinned by the case that needs it.

        This is the one the substring test really got wrong and that
        the unterminated case cannot prove: docs/consistency-audits.md
        names the begin marker on one line and the end marker two
        lines later while explaining them, so a substring test found a
        matched pair and blanked the prose between. Only a whole-line
        match in the exact spelling audit-update-docs.py emits
        distinguishes that from a real block.
        """
        result = self._check({
            'docs/notes.md': (
                '# Notes\n'
                '\n'
                'audit-update-docs.py rewrites the table between the\n'
                '`<!-- consistency-audit:begin -->` and\n'
                'This part was wired up in phase 6.\n'
                '`<!-- consistency-audit:end -->` markers.\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'prose naming both markers exempted the lines between them',
        )
        self.assertIn('docs/notes.md:5', result['details'])

    def test_prose_naming_a_marker_does_not_open_a_real_block(self):
        """Prose and a real table in one file is the shape that bites.

        The earlier tests here put prose naming the markers in a file
        with no real block, so nothing could ever close what the prose
        loosely opened and the exemption stayed empty. A spec page that
        both explains the markers and carries a table has a real end
        marker further down, which closes the block the prose opened --
        blanking every line between the sentence and the table. That is
        a larger and more plausible exemption than the one measured in
        the plan files, and it is invisible in exactly the same way.
        """
        result = self._check({
            'docs/audits/plan-index.md': (
                '# Audit: plan index\n'
                '\n'
                'The `<!-- consistency-audit:begin -->` marker opens '
                'the table.\n'
                '\n'
                'This was wired up in phase 6.\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '| ryll | PASS |\n'
                '<!-- consistency-audit:end -->\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'prose naming the begin marker was closed by the real end '
            'marker further down, exempting everything between',
        )
        self.assertIn('docs/audits/plan-index.md:5', result['details'])

    def test_an_unterminated_block_exempts_nothing(self):
        # Failing towards more scanning: a hidden exemption is
        # invisible, a false positive is not.
        result = self._check({
            'docs/notes.md': (
                '<!-- consistency-audit:begin -->\n'
                'Wired up in phase 6.\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/notes.md:2', result['details'])

    def test_a_phase_reference_after_a_generated_block_still_fails(self):
        # The exclusion must end at the end marker, and must not shift
        # the reported line numbers: blanked lines are kept, not
        # dropped. Without both, this is a hole rather than a filter.
        result = self._check({
            'docs/audits/plan-index.md': (
                '# Audit: plan index\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '- ryll: shipped in phase 4\n'
                '<!-- consistency-audit:end -->\n'
                '\n'
                'This was wired up in phase 6.\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/audits/plan-index.md:7', result['details'])

    def test_markers_shown_inside_a_fence_do_not_open_a_block(self):
        # The same hazard as the docs-external-links case, and worth
        # pinning per caller: each one runs its own fence pass, and
        # both ran it after blanking. A closing fence blanked from
        # between a marker pair leaves the fence open for the rest of
        # the file, so the phase reference below escapes.
        result = self._check({
            'docs/audits/README.md': (
                '# Audit index\n'
                '\n'
                '```markdown\n'
                '<!-- consistency-audit:begin -->\n'
                '```\n'
                '<!-- consistency-audit:end -->\n'
                '\n'
                'This was wired up in phase 6.\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'a fence delimiter blanked from inside a marker pair left '
            'the rest of the file unscanned',
        )
        self.assertIn('docs/audits/README.md:8', result['details'])

    def test_plural_phases_fails(self):
        result = self._check({
            'docs/usage.md': 'Delivered across phases 2 and 3.\n',
        })
        self.assertEqual(result['status'], 'fail')

    def test_plans_directory_is_ignored(self):
        result = self._check({
            'docs/plans/PLAN-frob.md': '## Phase 1: frob\n',
            'docs/usage.md': 'Frobbing.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_nested_plans_directory_is_ignored(self):
        result = self._check({
            'docs/parts/plans/PLAN-frob.md': '## Phase 2: frob\n',
            'docs/usage.md': 'Frobbing.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_doc_content_excludes_are_skipped(self):
        # shakenfist's docs/components/ is an automated import of
        # other repositories' documentation; findings there must be
        # fixed at the source, not double-reported.
        files = {
            'docs/components/ryll/notes.md': 'As of phase 2.\n',
            'docs/usage.md': 'Frobbing.\n',
        }
        result = self._check(
            files,
            props={'doc_content_excludes': ['docs/components/']},
        )
        self.assertEqual(result['status'], 'pass')
        # Without the override the same tree fails, so the exclude
        # is doing the work.
        self.assertEqual(self._check(files)['status'], 'fail')

    def test_code_blocks_do_not_count(self):
        result = self._check({
            'docs/usage.md': (
                'Frobbing.\n\n'
                '```\nlog line: entering phase 3\n```\n\n'
                'A `phase 3` inline span does not count either.\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_audit_ok_marker_suppresses_the_line(self):
        result = self._check({
            'docs/electrics.md': (
                'A phase 3 supply feeds the rack. '
                '<!-- audit-ok: phase-reference -->\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_phase_without_a_number_passes(self):
        result = self._check({
            'docs/usage.md': (
                'Two-phase commit is used for the frob step.\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_non_markdown_files_are_ignored(self):
        result = self._check({
            'docs/notes.txt': 'Implemented in phase 4.\n',
            'docs/usage.md': 'Frobbing.\n',
        })
        self.assertEqual(result['status'], 'pass')


class PlanSourceReferenceTest(unittest.TestCase):
    """Plan pointers written into source and configuration."""

    def _repo(self, tmp, files):
        for relative, content in files.items():
            path = os.path.join(tmp, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        subprocess.run(['git', 'init', '--quiet', '-b', 'main'], cwd=tmp,
                       check=True)
        subprocess.run(['git', 'add', '-A'], cwd=tmp, check=True)
        return check_plan_source_references(tmp, {})

    def test_a_resolving_reference_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'docs/plans/PLAN-frob.md': '# Frob\n',
                'src/frob.py': '# See docs/plans/PLAN-frob.md.\n',
            })
        self.assertEqual(result['status'], 'pass')

    def test_a_rotted_reference_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'docs/plans/PLAN-frob.md': '# Frob\n',
                'src/frob.py': '# See docs/plans/PLAN-gone.md.\n',
            })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-gone.md', result['details'])

    def test_a_rotted_reference_in_a_test_still_fails(self):
        # Test files carry prose pointers like any other source, and
        # they rot the same way -- instar's tests/test_adversarial.py
        # cites a plan that no longer exists in its module docstring.
        # Skipping a file because its name looks like a test would
        # hide exactly that.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'tests/test_frob.py': '"""See PLAN-gone.md."""\n',
            })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-gone.md', result['details'])

    def test_the_file_marker_exempts_a_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'tests/test_frob.py': (
                    '# audit-ok: plan-reference-file\n'
                    '"""See PLAN-gone.md and PLAN-also-gone.md."""\n'
                ),
            })
        self.assertEqual(result['status'], 'not_applicable')

    def test_the_line_marker_exempts_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'docs/plans/PLAN-frob.md': '# Frob\n',
                'src/frob.py': (
                    "PATTERN = 'PLAN-*.md'  # audit-ok: plan-reference\n"
                    '# See docs/plans/PLAN-gone.md.\n'
                ),
            })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-gone.md', result['details'])
        self.assertNotIn('PATTERN', result['details'])

    def test_plan_template_is_not_a_plan_reference(self):
        # PLAN-TEMPLATE.md lives at the repository root, not in
        # docs/plans/, and the plan-template audit is what holds it
        # there. Naming it is not a pointer that can rot.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'tools/check.sh': 'grep x PLAN-TEMPLATE.md\n',
            })
        self.assertEqual(result['status'], 'not_applicable')

    def test_an_absolute_url_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'src/frob.py': (
                    '# See https://github.com/shakenfist/ryll/blob/'
                    'develop/docs/plans/PLAN-gone.md.\n'
                ),
            })
        self.assertEqual(result['status'], 'not_applicable')


class PlanIndexTest(unittest.TestCase):
    """docs/plans/index.md layout, ordering, statuses and coverage."""

    HEADER = (
        '| Date | Plan | Intent | Status |\n'
        '|------|------|--------|--------|\n'
    )

    def _check(self, plans=None, index=None):
        """Run the check over a docs/plans/ built from the arguments.

        plans is a list of file names to create; index is the content
        of index.md, or None to leave it out entirely.
        """
        with tempfile.TemporaryDirectory() as tmp:
            plans_dir = os.path.join(tmp, 'docs', 'plans')
            os.makedirs(plans_dir)
            for name in plans or []:
                with open(os.path.join(plans_dir, name), 'w') as f:
                    f.write('# A plan\n')
            if index is not None:
                with open(os.path.join(plans_dir, 'index.md'), 'w') as f:
                    f.write(index)
            return check_plan_index(tmp, {})

    def test_not_applicable_without_plans_directory(self):
        result = check_plan_index('/nonexistent', {})
        self.assertEqual(result['status'], 'not_applicable')

    def test_not_applicable_with_no_plans_and_no_index(self):
        self.assertEqual(self._check()['status'], 'not_applicable')

    def test_missing_index_with_plans_fails(self):
        result = self._check(plans=['PLAN-thing.md'], index=None)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('index.md is missing', result['details'])

    def test_well_formed_index_passes(self):
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-two.md'],
            index=(
                '# Plans index\n\n' + self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
                '| 2026-02-01 | [Two](PLAN-two.md) | Do two | In progress |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_phase_plans_need_no_index_row(self):
        # Phase files are named after their master plan and tracked in
        # it, so the index carries the master plan alone.
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-one-phase-01-start.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_plan_first_columns_fail(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Plan | Phase | Status |\n'
                '|------|-------|--------|\n'
                '| [One](PLAN-one.md) | 1. Start | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('not led by Date then Plan', result['details'])

    def test_wrong_columns_suppress_date_and_status_findings(self):
        # Reading a date out of a column that holds something else
        # would bury the finding that actually needs fixing.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Plan | Phase | Status |\n'
                '|------|-------|--------|\n'
                '| [One](PLAN-one.md) | 1. Start | Whenever |\n'
            ),
        )
        self.assertNotIn('date', result['details'])
        self.assertNotIn('vocabulary', result['details'])

    def test_rows_out_of_date_order_fail(self):
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-two.md'],
            index=(
                self.HEADER +
                '| 2026-02-01 | [Two](PLAN-two.md) | Do two | Complete |\n'
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('out of date order', result['details'])
        self.assertIn('One', result['details'])

    def test_dates_are_not_compared_across_tables(self):
        # Each table is its own chronological run, so a second table
        # starting earlier than the first ended is not a finding.
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-two.md'],
            index=(
                '## Master plans\n\n' + self.HEADER +
                '| 2026-02-01 | [Two](PLAN-two.md) | Do two | Complete |\n'
                '\n## Standalone plans\n\n' + self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_malformed_date_fails(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| April 2026 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('YYYY-MM-DD', result['details'])

    def test_status_outside_the_vocabulary_fails(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                'Code landed; awaiting verification |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('vocabulary', result['details'])

    def test_qualified_status_fails(self):
        # The whole point of the vocabulary: "Complete (phases 1-5,
        # 2026-08-15): every merge to develop..." is how a status
        # column turns into prose.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                'Complete (phases 1-5) |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('vocabulary', result['details'])

    def test_status_matching_is_case_insensitive(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                'In Progress |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_decorated_status_passes(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                '**Complete** |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_table_without_a_status_column_is_fine(self):
        # Standalone plan listings carry no status, and that is not a
        # defect: they are registered, just not tracked.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Date | Plan | Intent |\n'
                '|------|------|--------|\n'
                '| 2026-01-01 | [One](PLAN-one.md) | Do one |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_unregistered_master_plan_fails(self):
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-orphan.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-orphan.md', result['details'])

    def test_bullet_list_index_fails(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index='# Plans\n\n* [One](PLAN-one.md).\n',
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no plan table', result['details'])
        # Linked plans still count as registered, so the only finding
        # is the missing table.
        self.assertNotIn('not listed in the index', result['details'])

    def test_table_without_plan_rows_is_ignored(self):
        # An index may explain itself with a table that lists no
        # plans. Judging its columns would be a finding nobody could
        # act on.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Term | Meaning |\n'
                '|------|---------|\n'
                '| Blocked | Waiting on something else |\n'
                '\n' + self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_link_free_row_is_not_read_as_a_header(self):
        # A header is the row the separator underlines. A data row
        # with no link must not reset the column mapping, or the rows
        # after it go unchecked.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | Not yet written up | Do it | Complete |\n'
                '| 2026-02-01 | [One](PLAN-one.md) | Do one | Whenever |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('vocabulary', result['details'])
        self.assertNotIn('not led by Date then Plan', result['details'])


class PushAuditTest(unittest.TestCase):
    def setUp(self):
        # A private canonical blocks directory so the tests do not
        # depend on the real templates/shared-blocks/ content.
        self._blocks = tempfile.TemporaryDirectory()
        self.addCleanup(self._blocks.cleanup)
        self.readme_block = (
            '<!-- shared-block: readme-discipline v2 -->\n'
            'Canonical wording.\n'
            '<!-- shared-block-end -->\n'
        )
        # A different version number, so the tests prove versions are
        # tracked per block rather than globally.
        self.comment_block = (
            '<!-- shared-block: comment-proportion v3 -->\n'
            'Comment wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.phase_block = (
            '<!-- shared-block: plan-phase-references v1 -->\n'
            'Phase wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.llm_doc_block = (
            '<!-- shared-block: llm-doc-discipline v1 -->\n'
            'Agent doc wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.path_block = (
            '<!-- shared-block: path-traversal-review v1 -->\n'
            'Path wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.python_block = (
            '<!-- shared-block: python-version-discipline v1 -->\n'
            'Python wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.tests_block = (
            '<!-- shared-block: functional-test-coverage v1 -->\n'
            'Testing wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.diagram_block = (
            '<!-- shared-block: diagram-discipline v1 -->\n'
            'Diagram wording.\n'
            '<!-- shared-block-end -->\n'
        )
        for name, block in (
            ('readme-discipline', self.readme_block),
            ('llm-doc-discipline', self.llm_doc_block),
            ('diagram-discipline', self.diagram_block),
            ('comment-proportion', self.comment_block),
            ('plan-phase-references', self.phase_block),
            ('path-traversal-review', self.path_block),
            ('python-version-discipline', self.python_block),
            ('functional-test-coverage', self.tests_block),
        ):
            with open(
                os.path.join(self._blocks.name, f'{name}.md'), 'w'
            ) as f:
                f.write(block)
        self.canonical = (
            f'{self.readme_block}\n{self.llm_doc_block}\n'
            f'{self.diagram_block}\n{self.comment_block}\n'
            f'{self.phase_block}\n{self.path_block}\n'
            f'{self.python_block}\n{self.tests_block}'
        )

    def _check(self, files):
        # A referencing AGENTS.md is supplied unless the case brings
        # its own, so the block-validation cases below keep testing
        # block validation instead of all failing on the reference
        # check. The reference check has its own cases.
        files = dict(files)
        named = None
        for candidate in ('PUSH-AUDIT.md', 'PUSH-TEMPLATE.md'):
            # `is not None` rather than `in`, so a case using the
            # absent-file sentinel selects the same filename the
            # check will.
            if files.get(candidate) is not None:
                named = candidate
                break
        if named and 'AGENTS.md' not in files:
            files['AGENTS.md'] = f'# Agents\n\nSee {named}.\n'
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
                # None means "this file is absent", which is how a
                # case opts out of the default AGENTS.md above.
                if content is None:
                    continue
                with open(os.path.join(tmp, name), 'w') as f:
                    f.write(content)
            return check_push_audit(
                tmp, {}, blocks_dir=self._blocks.name
            )

    def test_not_applicable_without_file(self):
        self.assertEqual(self._check({})['status'], 'not_applicable')

    def test_current_block_passes(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_legacy_filename_fails(self):
        result = self._check({
            'PUSH-TEMPLATE.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])

    def test_missing_block_fails(self):
        result = self._check({'PUSH-AUDIT.md': '# Audit\n'})
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block readme-discipline',
            result['details'],
        )
        self.assertIn(
            'missing shared block comment-proportion',
            result['details'],
        )

    def test_missing_comment_proportion_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.readme_block}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block comment-proportion',
            result['details'],
        )

    def test_missing_llm_doc_discipline_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': (
                f'# Audit\n\n{self.readme_block}\n'
                f'{self.comment_block}\n{self.phase_block}\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block llm-doc-discipline',
            result['details'],
        )

    def test_missing_plan_phase_references_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': (
                f'# Audit\n\n{self.readme_block}\n'
                f'{self.comment_block}\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block plan-phase-references',
            result['details'],
        )

    def test_stale_version_fails(self):
        stale = self.canonical.replace('v2', 'v1')
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{stale}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('stale (v1 embedded, v2 current)',
                      result['details'])

    def test_drifted_content_fails(self):
        drifted = self.canonical.replace(
            'Canonical wording.', 'Mutated wording.'
        )
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{drifted}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('drifted', result['details'])

    def test_trailing_whitespace_is_ignored(self):
        padded = self.canonical.replace(
            'Canonical wording.', 'Canonical wording.   '
        )
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{padded}\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_unknown_block_fails(self):
        content = (
            f'# Audit\n\n{self.canonical}\n'
            '<!-- shared-block: no-such-block v1 -->\n'
            'Words.\n'
            '<!-- shared-block-end -->\n'
        )
        result = self._check({'PUSH-AUDIT.md': content})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('unknown shared block no-such-block',
                      result['details'])

    def test_missing_end_marker_fails(self):
        content = (
            '# Audit\n\n'
            '<!-- shared-block: readme-discipline v2 -->\n'
            'Canonical wording.\n'
        )
        result = self._check({'PUSH-AUDIT.md': content})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no <!-- shared-block-end -->',
                      result['details'])

    def test_both_files_fails_even_with_current_block(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'PUSH-TEMPLATE.md': '# Old\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])

    def test_unreferenced_audit_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': '# Agents\n\nNothing about the audit here.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'AGENTS.md does not reference PUSH-AUDIT.md',
            result['details'],
        )

    def test_missing_agents_file_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': None,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no AGENTS.md to reference', result['details'])

    def test_legacy_name_reference_names_the_legacy_file(self):
        # A repository still on the old name gets told to rename it,
        # not told twice about a file it does not have.
        result = self._check({
            'PUSH-TEMPLATE.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': '# Agents\n\nSee PUSH-TEMPLATE.md.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])
        self.assertNotIn('does not reference', result['details'])

    def test_reference_is_reported_alongside_block_problems(self):
        # Both failures at once, so a repository fixing one is not
        # surprised by the other on the next daily run.
        result = self._check({
            'PUSH-AUDIT.md': '# Audit\n',
            'AGENTS.md': '# Agents\n\nNothing here.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('missing shared block', result['details'])
        self.assertIn('does not reference', result['details'])

    def test_both_files_with_only_the_legacy_name_referenced(self):
        # The code resolves filename to the new name when both are
        # present, so an AGENTS.md naming only the legacy file gets
        # both a rename message and a reference message. Pinning the
        # behaviour rather than asserting it is desirable.
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'PUSH-TEMPLATE.md': '# Old\n',
            'AGENTS.md': '# Agents\n\nSee PUSH-TEMPLATE.md.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])
        self.assertIn(
            'AGENTS.md does not reference PUSH-AUDIT.md',
            result['details'],
        )

    def test_shallowness_is_deliberate(self):
        # The spec says the check looks for the filename and not for
        # particular wording, so a mention inside a fenced code block
        # counts. That is a known false positive, kept on purpose:
        # this test exists so a later tightening is a deliberate
        # decision rather than a quiet one.
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': (
                '# Agents\n\n```\ncat PUSH-AUDIT.md\n```\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_pass_details_mention_the_reference(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'pass')
        self.assertIn('referenced from AGENTS.md', result['details'])

    def test_every_required_block_has_a_canonical_copy(self):
        # A name in the list with no file under
        # templates/shared-blocks would report every repository as
        # carrying an unknown block.
        for name in PUSH_AUDIT_BLOCKS:
            with self.subTest(block=name):
                self.assertTrue(os.path.exists(os.path.join(
                    REPO_ROOT, 'templates', 'shared-blocks',
                    f'{name}.md')))

    def test_every_required_block_is_named_in_the_spec(self):
        # A criterion spans four files that must stay in sync. A
        # block required here but absent from the spec page files a
        # fleet issue naming something that page never mentions,
        # which is exactly the cross-file drift this suite exists to
        # catch.
        with open(os.path.join(
                REPO_ROOT, 'docs', 'audits', 'push-audit.md')) as f:
            spec = f.read()
        for name in PUSH_AUDIT_BLOCKS:
            with self.subTest(block=name):
                self.assertIn(name, spec)

    def test_the_fixture_covers_every_required_block(self):
        # Otherwise a block added to the list is never exercised
        # here: self.canonical would simply be missing it and every
        # case in this class would fail for the same reason.
        self.assertEqual(
            sorted(PUSH_AUDIT_BLOCKS),
            sorted(os.path.splitext(name)[0]
                   for name in os.listdir(self._blocks.name)),
        )


class PlanTemplateTest(unittest.TestCase):
    """Tests for check_plan_template.

    The check had no direct coverage at all, which matters once
    PLAN_TEMPLATE_BLOCKS gains an entry: adding a name to that list
    marks every repository carrying the previous set non-compliant
    on the next daily run, and nothing asserted either the list's
    contents or that the check reads it.
    """

    def setUp(self):
        self._blocks = tempfile.TemporaryDirectory()
        self.addCleanup(self._blocks.cleanup)
        self.blocks = {}
        for name in PLAN_TEMPLATE_BLOCKS:
            block = (
                f'<!-- shared-block: {name} v1 -->\n'
                f'Canonical {name} wording.\n'
                '<!-- shared-block-end -->\n'
            )
            self.blocks[name] = block
            with open(
                os.path.join(self._blocks.name, f'{name}.md'), 'w'
            ) as f:
                f.write(block)

    def _check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
                with open(os.path.join(tmp, name), 'w') as f:
                    f.write(content)
            return check_plan_template(
                tmp, {}, blocks_dir=self._blocks.name
            )

    def _template(self, omit=None):
        return '# Plan template\n\n' + '\n'.join(
            block for name, block in self.blocks.items()
            if name != omit
        )

    def test_not_applicable_without_template(self):
        self.assertEqual(self._check({})['status'], 'not_applicable')

    def test_all_blocks_passes(self):
        result = self._check({'PLAN-TEMPLATE.md': self._template()})
        self.assertEqual(result['status'], 'pass')

    def test_missing_push_audit_phase_block_fails(self):
        result = self._check({
            'PLAN-TEMPLATE.md': self._template(
                omit='plan-push-audit-phase'),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block plan-push-audit-phase',
            result['details'],
        )

    def test_stale_push_audit_phase_block_fails(self):
        # Take the marker from the fixture setUp built rather than
        # naming a version. These fixtures are deliberately
        # independent of templates/shared-blocks/, so a literal here
        # would be a version this test does not otherwise track.
        marker = self.blocks['plan-push-audit-phase'].splitlines()[0]
        stale = self._template().replace(
            marker,
            '<!-- shared-block: plan-push-audit-phase v0 -->',
        )
        result = self._check({'PLAN-TEMPLATE.md': stale})
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'shared block plan-push-audit-phase is stale',
            result['details'],
        )

    def test_push_audit_phase_is_required(self):
        # The line of this change with the widest fleet consequence:
        # naming the block here is what marks four currently
        # compliant repositories non-compliant.
        self.assertIn(
            'plan-push-audit-phase', PLAN_TEMPLATE_BLOCKS
        )

    def test_every_required_block_has_a_canonical_copy(self):
        # A name in the list with no file under
        # templates/shared-blocks would report every repository as
        # carrying an unknown block.
        for name in PLAN_TEMPLATE_BLOCKS:
            with self.subTest(block=name):
                self.assertTrue(os.path.exists(os.path.join(
                    REPO_ROOT, 'templates', 'shared-blocks',
                    f'{name}.md')))


class CanonicalSharedBlocksTest(unittest.TestCase):
    def test_real_canonical_blocks_parse(self):
        # Every canonical file in templates/shared-blocks/ must
        # contain a block whose name matches the filename.
        blocks_dir = SHARED_BLOCKS_DIR
        names = [
            f[:-3] for f in os.listdir(blocks_dir)
            if f.endswith('.md') and f != 'README.md'
        ]
        self.assertIn('readme-discipline', names)
        self.assertIn('comment-proportion', names)
        for name in names:
            canonical = load_canonical_block(name)
            self.assertIsNotNone(
                canonical,
                f'templates/shared-blocks/{name}.md has no '
                f'shared-block marker matching its filename',
            )


class PlanStatusVocabularyBlockTest(unittest.TestCase):
    def test_canonical_block_lists_exactly_the_enforced_statuses(self):
        # The block is the wording repositories are handed and
        # PLAN_STATUSES is what the audit enforces. If they drift,
        # projects get told one thing and measured against another.
        canonical = load_canonical_block(
            'plan-status-vocabulary'
        )
        self.assertIsNotNone(canonical)
        _, text = canonical
        documented = re.findall(r'^- `([^`]+)`', text, re.MULTILINE)
        self.assertEqual(
            sorted(documented), sorted(PLAN_STATUSES)
        )

    def test_plan_templates_must_carry_the_block(self):
        self.assertIn(
            'plan-status-vocabulary', PLAN_TEMPLATE_BLOCKS
        )


# The rules of plan-push-audit-phase that this repository reads by
# phrase, per the "a named constant and an assertion" rule in
# AGENTS.md. Each is the shortest fragment that cannot survive the
# rule being dropped: a reword should keep them, a retraction should
# not. Grep for this list before rewording the canonical block.
PUSH_AUDIT_BLOCK_RULES = {
    'where a table records it': '`Merged` column',
    'where prose records it': '`Merged:` line',
    'not in the status cell': '`Status` column keeps',
    'one commit is not a range': 'only ever enough when it is a merge commit',
    'complete plans are not reopened': 'not reopened to acquire',
}


class PushAuditPhaseBlockTest(unittest.TestCase):
    """The canonical block is the only statement of where a phase's
    landing commit is recorded, and of what counts as a range.

    Nothing mechanical reads a plan's Execution table, so if a later
    revision renames the column or shortens away one of these rules,
    the first thing to notice would be a sweep already halfway
    through thirty-six plans. That the block is required of every
    PLAN-TEMPLATE.md is asserted by PlanTemplateTest, which owns that
    invariant.
    """

    def test_canonical_block_still_states_each_rule(self):
        canonical = load_canonical_block(
            'plan-push-audit-phase'
        )
        self.assertIsNotNone(canonical)
        _, text = canonical
        # Collapse whitespace first. These are prose wrapped at
        # seventy columns, so matching raw text would fail on a
        # reflow that changed no meaning -- and a test that fails on
        # cosmetic reflow teaches people to edit the test, which is
        # the reflex this tripwire exists to prevent.
        flat = ' '.join(text.split())
        for rule, phrase in PUSH_AUDIT_BLOCK_RULES.items():
            with self.subTest(rule=rule):
                self.assertIn(phrase, flat)


if __name__ == '__main__':
    unittest.main()
