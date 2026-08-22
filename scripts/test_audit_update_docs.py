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
    every audits/*.md had been rewritten but before any was
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
        self.assertIn('audits/workflow-standards.md', specs)
        self.assertIn(
            'review-marks-pre-commit', specs['audits/workflow-standards.md']
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
    """Documentation that names a test must name one that exists.

    docs/consistency-audits.md tells a contributor which test catches
    each cross-file breakage, and that pointer is the whole value of
    the paragraph: someone who opens the named file, does not find the
    named test, and concludes it was never written is worse off than
    if the sentence had said nothing. It has already happened once --
    the COLUMN_NAMES invariant was attributed to test_audit_check.py,
    which does not hold it -- and a moved or renamed test is silent
    about every prose reference to it.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Prose that explains the audit system. Not docs/plans/, which is a
    # record of what was decided at the time and is allowed to name
    # things that have since moved.
    DOC_FILES = (
        'AGENTS.md',
        'ARCHITECTURE.md',
        'audits/README.md',
        'docs/consistency-audits.md',
    )

    TEST_FILE_RE = re.compile(r'\b(scripts/)?(test_[a-z0-9_]+\.py)\b')
    TEST_FUNC_RE = re.compile(r'\b(test_[a-z0-9_]+)\b(?!\.py)')

    def _docs(self):
        for name in self.DOC_FILES:
            path = os.path.join(self.REPO, name)
            self.assertTrue(
                os.path.exists(path), '%s is named here but missing' % name
            )
            with open(path) as f:
                yield name, f.read()

    def test_named_test_files_exist(self):
        missing = []
        for name, body in self._docs():
            for _, filename in self.TEST_FILE_RE.findall(body):
                if not os.path.exists(
                    os.path.join(self.REPO, 'scripts', filename)
                ):
                    missing.append('%s names %s' % (name, filename))
        self.assertEqual(missing, [])

    def test_named_tests_exist_in_the_file_they_are_attributed_to(self):
        # The failure this is really for: the test exists, but not
        # where the prose says it does, so following the pointer finds
        # nothing. Every test_* function named in a paragraph must be
        # defined in a test file that paragraph also names.
        sources = {}
        for filename in os.listdir(os.path.join(self.REPO, 'scripts')):
            if filename.startswith('test_') and filename.endswith('.py'):
                with open(os.path.join(self.REPO, 'scripts', filename)) as f:
                    sources[filename] = f.read()
        self.assertTrue(sources)

        wrong = []
        for name, body in self._docs():
            for paragraph in re.split(r'\n\s*\n', body):
                named_files = {
                    filename
                    for _, filename in self.TEST_FILE_RE.findall(paragraph)
                }
                if not named_files:
                    continue
                for func in set(self.TEST_FUNC_RE.findall(paragraph)):
                    if not any(
                        re.search(r'def %s\(' % func, sources.get(f, ''))
                        for f in named_files
                    ):
                        wrong.append(
                            '%s attributes %s to %s'
                            % (name, func, ', '.join(sorted(named_files)))
                        )
        self.assertEqual(wrong, [])


if __name__ == '__main__':
    unittest.main()
