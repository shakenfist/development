#!/usr/bin/env python3

"""Tests for audit-update-docs.py.

Run with: python3 scripts/test_audit_update_docs.py
"""

import importlib.util
import io
import os
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


if __name__ == '__main__':
    unittest.main()
