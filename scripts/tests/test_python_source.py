#!/usr/bin/env python3

"""Tests for audit/text/python_source.py and the dependency name helper.

Run with: python3 scripts/tests/test_python_source.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks.packaging import canonical_dependency_name  # noqa: E402
from audit.text.python_source import (  # noqa: E402
    mask_comments_and_strings, mask_strings,
)


class CanonicalNameTest(unittest.TestCase):
    def test_collapses_separators_and_case(self):
        canonical = canonical_dependency_name
        self.assertEqual(canonical('typing_extensions'), 'typing-extensions')
        self.assertEqual(canonical('typing-extensions'), 'typing-extensions')
        self.assertEqual(canonical('Zope.Interface'), 'zope-interface')
        self.assertEqual(canonical('prometheus__client'), 'prometheus-client')


class MaskCommentsAndStringsTest(unittest.TestCase):
    """Masking is only usable if it preserves every offset.

    Class positions, reported line numbers and the window an
    audit-ok marker is read from are all offsets taken against the
    masked text and used against the original. A mask that changed a
    single length would keep working on every fixture here -- the
    class is still found -- while reporting the wrong line and
    reading the marker window off the wrong part of the file.
    """

    SOURCES = (
        'a = 1  # class Handler(X):\n',
        'S = """\nclass H(Base):\n    pass\n"""\nc = 3\n',
        'D = \'\'\'\nclass H(Base):\n    pass\n\'\'\'\n',
        'class H(make("#"), Base):\n    pass\n',
        "x = 'a\\'b' + 1\n",
        'y = f"{a}"  # tail\n',
        'z = "never closed\n',
        'w = "trailing backslash \\\\"\n',
    )

    def test_masking_preserves_length_and_line_breaks(self):
        for source in self.SOURCES:
            with self.subTest(source=source):
                masked = mask_comments_and_strings(source)
                self.assertEqual(len(masked), len(source))
                self.assertEqual(
                    [i for i, c in enumerate(masked) if c == '\n'],
                    [i for i, c in enumerate(source) if c == '\n'],
                )

    def test_code_outside_comments_and_strings_is_untouched(self):
        source = 'import os  # noqa\nPATH = "/tmp/x"\nclass A(B):\n'
        masked = mask_comments_and_strings(source)
        self.assertIn('import os', masked)
        self.assertIn('class A(B):', masked)
        self.assertNotIn('noqa', masked)
        self.assertNotIn('/tmp/x', masked)


class MaskStringsTest(unittest.TestCase):
    """The view an audit-ok marker is read from.

    The complement of the code view: string bodies are blanked and
    comments survive, because a marker is a comment. Reading it from
    the file instead let `DOC = "audit-ok: header-sanitization"`
    exempt the class below it from a CWE-113 check.
    """

    def test_comments_survive_and_string_bodies_do_not(self):
        source = 'X = "audit-ok: x"  # audit-ok: y\n'
        masked = mask_strings(source)
        self.assertIn('# audit-ok: y', masked)
        self.assertNotIn('audit-ok: x', masked)

    def test_a_hash_inside_a_string_does_not_start_a_comment(self):
        source = 'X = "# audit-ok: x"\nY = 1\n'
        masked = mask_strings(source)
        self.assertNotIn('audit-ok', masked)
        self.assertIn('Y = 1', masked)

    def test_masking_preserves_length_and_line_breaks(self):
        for source in MaskCommentsAndStringsTest.SOURCES:
            with self.subTest(source=source):
                masked = mask_strings(source)
                self.assertEqual(len(masked), len(source))
                self.assertEqual(
                    [i for i, c in enumerate(masked) if c == '\n'],
                    [i for i, c in enumerate(source) if c == '\n'],
                )


if __name__ == '__main__':
    unittest.main()
