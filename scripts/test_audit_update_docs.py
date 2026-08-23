#!/usr/bin/env python3

"""Tests for audit-update-docs.py.

Run with: python3 scripts/test_audit_update_docs.py
"""

import importlib.util
import io
import os
import re
import sys
import unittest


SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'audit-update-docs.py'
)

# audit-update-docs.py is not importable by name (the hyphens are not
# valid in a module identifier), so load it from its path. It imports
# audit_common, which lives beside it.
sys.path.insert(0, os.path.dirname(SCRIPT))
_spec = importlib.util.spec_from_file_location('audit_update_docs', SCRIPT)
audit_update_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_update_docs)


class ColumnNamesTest(unittest.TestCase):
    """COLUMN_NAMES has to cover every multi-check spec.

    This is the invariant that broke the 2026-08-12 audit run: the
    review-marks-pre-commit check joined the workflow-standards spec
    without a column heading, and rendering raised KeyError after
    every docs/audits/*.md had been rewritten but before any was
    committed, so no project's table published that day.
    """

    def _multi_check_specs(self):
        return {
            spec: ids
            for spec, ids in audit_update_docs.checks_by_spec().items()
            if len(ids) > 1
        }

    def test_multi_check_specs_have_a_heading_for_every_check(self):
        missing = []
        for spec, check_ids in sorted(self._multi_check_specs().items()):
            for check_id in check_ids:
                if check_id not in audit_update_docs.COLUMN_NAMES:
                    missing.append(f'{check_id} (in {spec})')
        self.assertEqual(
            missing, [],
            'checks sharing a spec file need a COLUMN_NAMES heading in '
            'audit-update-docs.py',
        )

    def test_the_workflow_standards_spec_is_still_multi_check(self):
        # The test above passes trivially if nothing shares a spec, so
        # assert the case it is meant to cover still exists.
        specs = self._multi_check_specs()
        self.assertIn('docs/audits/workflow-standards.md', specs)
        self.assertIn(
            'review-marks-pre-commit', specs['docs/audits/workflow-standards.md']
        )

    def test_no_heading_for_an_unknown_check(self):
        # A heading left behind after a check is renamed or removed is
        # dead weight that reads as coverage.
        known = set(audit_update_docs.AUDIT_METADATA)
        self.assertEqual(
            sorted(set(audit_update_docs.COLUMN_NAMES) - known), []
        )

    def test_single_check_specs_do_not_need_a_heading(self):
        # Specs with one check render a plain 'Status' column, so they
        # are deliberately absent from COLUMN_NAMES.
        singles = [
            ids[0] for ids in audit_update_docs.checks_by_spec().values()
            if len(ids) == 1
        ]
        self.assertTrue(singles)
        for check_id in singles:
            self.assertNotIn(check_id, audit_update_docs.COLUMN_NAMES)


class ColumnNameFallbackTest(unittest.TestCase):
    def test_known_check_uses_its_heading(self):
        self.assertEqual(
            audit_update_docs.column_name('workflow-permissions'),
            'Permissions',
        )

    def test_unknown_check_falls_back_and_warns(self):
        # Rendering must not take the whole fleet's tables down for a
        # missing label, but it must say so.
        stderr = io.StringIO()
        original = sys.stderr
        sys.stderr = stderr
        try:
            name = audit_update_docs.column_name('not-a-real-check')
        finally:
            sys.stderr = original
        self.assertEqual(name, 'not-a-real-check')
        self.assertIn('no COLUMN_NAMES heading', stderr.getvalue())


class DocumentedTestReferencesTest(unittest.TestCase):
    """Prose naming a test file must name one that exists, correctly.

    The guarantee is about attribution rather than existence: a
    paragraph that names a test file is checked, and any test function
    named in that paragraph must be defined in one of the files the
    paragraph names. A paragraph naming a function and no file is not
    checked, because there is nothing to check it against.

    That pointer is the whole value of the sentence carrying it:
    someone who opens the named file, does not find the named test,
    and concludes it was never written is worse off than if the
    sentence had said nothing. It has already happened once -- the
    COLUMN_NAMES invariant was attributed to test_audit_check.py,
    which does not hold it -- and a moved or renamed test is silent
    about every prose reference to it.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Prose that explains how this repository works. Everything in
    # docs/ except docs/plans/, which is a record of what was decided
    # at the time and is allowed to name things that have since moved.
    #
    # docs/audits/*.md is deliberately out, apart from the index. Those
    # files carry generated compliance tables whose findings quote file
    # paths harvested from the audited repositories -- docs/audits/
    # docs-external-links.md already names a test_amend.py that belongs
    # to another project entirely -- so scanning them would demand that
    # other people's test files exist under our scripts/.
    @property
    def doc_files(self):
        names = ['AGENTS.md', 'ARCHITECTURE.md', 'docs/audits/README.md']
        names.extend(
            sorted(
                os.path.join('docs', f)
                for f in os.listdir(os.path.join(self.REPO, 'docs'))
                if f.endswith('.md')
            )
        )
        return names

    TEST_FILE_RE = re.compile(r'\b(scripts/)?(test_[a-z0-9_]+\.py)\b')
    TEST_FUNC_RE = re.compile(r'\b(test_[a-z0-9_]+)\b(?!\.py)')

    def _docs(self):
        for name in self.doc_files:
            path = os.path.join(self.REPO, name)
            with open(path) as f:
                yield name, f.read()

    def _sources(self):
        """Every test file under scripts/, by basename."""
        sources = {}
        scripts = os.path.join(self.REPO, 'scripts')
        for filename in os.listdir(scripts):
            if filename.startswith('test_') and filename.endswith('.py'):
                with open(os.path.join(scripts, filename)) as f:
                    sources[filename] = f.read()
        self.assertTrue(sources)
        return sources

    def test_the_documentation_set_is_not_empty(self):
        # doc_files is computed, so an empty docs/ or a renamed file
        # would make every test in this class pass by vacuum.
        names = self.doc_files
        self.assertIn('docs/consistency-audits.md', names)
        for name in names:
            self.assertTrue(
                os.path.exists(os.path.join(self.REPO, name)),
                f'{name} is in the documentation set but missing',
            )

    def test_named_test_files_exist(self):
        missing = []
        for name, body in self._docs():
            for _, filename in self.TEST_FILE_RE.findall(body):
                if not os.path.exists(
                    os.path.join(self.REPO, 'scripts', filename)
                ):
                    missing.append(f'{name} names {filename}')
        self.assertEqual(missing, [])

    def test_named_tests_exist_in_the_file_they_are_attributed_to(self):
        # The failure this is really for: the test exists, but not
        # where the prose says it does, so following the pointer finds
        # nothing.
        #
        # Only functions that are defined *somewhere* under scripts/
        # are candidates. A test_-prefixed token that is defined
        # nowhere is prose, not a broken pointer -- `test_coverage` is
        # an audit criterion this very page discusses, and demanding it
        # be a function would set a trap on a phrase the next editor of
        # that page is likely to write.
        sources = self._sources()
        defined = {
            func
            for body in sources.values()
            for func in re.findall(r'def (test_[a-z0-9_]+)\(', body)
        }
        self.assertIn('test_named_test_files_exist', defined)

        wrong = []
        for name, body in self._docs():
            for paragraph in re.split(r'\n\s*\n', body):
                named_files = {
                    filename
                    for _, filename in self.TEST_FILE_RE.findall(paragraph)
                }
                if not named_files:
                    continue
                for func in sorted(set(self.TEST_FUNC_RE.findall(paragraph))):
                    if func not in defined:
                        continue
                    if not any(
                        re.search(rf'def {func}\(', sources.get(f, ''))
                        for f in named_files
                    ):
                        files = ', '.join(sorted(named_files))
                        wrong.append(f'{name} attributes {func} to {files}')
        self.assertEqual(wrong, [])


class UnmeasuredCriteriaTest(unittest.TestCase):
    """The criteria with no check are named in prose, so pin the list.

    docs/consistency-audits.md states the property that identifies
    them -- a criterion with no check has no consistency-audit marker
    block in its spec file -- and then names the current set. The
    property makes the set computable, so the list can be held to it
    rather than left to rot the first time one of them is automated.
    The plan's own future work proposes doing exactly that.

    Same shape as AuditScopeIsStatedOnceTest in test_audit_check.py,
    which ties the audit matrix to the two places that describe it.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DOC = 'docs/consistency-audits.md'
    MARKER = 'consistency-audit:begin'

    def _unmeasured(self):
        audits = os.path.join(self.REPO, 'docs', 'audits')
        found = set()
        for filename in os.listdir(audits):
            # README.md is the index, not a criterion. It is excluded
            # by name rather than by whether it happens to mention the
            # marker in its prose.
            if not filename.endswith('.md') or filename == 'README.md':
                continue
            with open(os.path.join(audits, filename)) as f:
                if self.MARKER not in f.read():
                    found.add(filename[:-len('.md')])
        return found

    def _documented(self):
        with open(os.path.join(self.REPO, self.DOC)) as f:
            body = f.read()
        # Whitespace is collapsed before matching: the anchor phrase
        # is prose and wraps across lines at whatever column the
        # paragraph happens to reflow to.
        paragraphs = [
            ' '.join(p.split())
            for p in re.split(r'\n\s*\n', body)
        ]
        paragraphs = [
            p for p in paragraphs if 'at the time of writing' in p
        ]
        self.assertEqual(
            len(paragraphs), 1,
            f'{self.DOC} should name the unmeasured criteria in exactly '
            f'one paragraph, anchored by "at the time of writing"',
        )
        return {
            token for token in re.findall(r'`([a-z0-9-]+)`', paragraphs[0])
            if os.path.exists(
                os.path.join(self.REPO, 'docs', 'audits', f'{token}.md')
            )
        }

    def test_the_documented_list_is_the_real_one(self):
        self.assertEqual(self._documented(), self._unmeasured())

    def test_there_is_something_to_measure(self):
        # The comparison above passes trivially if both sides are
        # empty, which is also what a renamed marker string would do.
        # The right response to this failing is usually deletion
        # rather than repair, and that is not guessable from
        # "False is not true".
        self.assertTrue(
            self._unmeasured(),
            f'no audit spec lacks a {self.MARKER} marker block. Either '
            f'every criterion is measured now -- in which case delete '
            f'this class and the "at the time of writing" paragraph in '
            f'{self.DOC} -- or MARKER no longer matches the marker '
            f'string.',
        )


if __name__ == '__main__':
    unittest.main()
