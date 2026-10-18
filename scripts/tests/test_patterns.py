#!/usr/bin/env python3

"""Tests for audit/text/patterns.py, and the two criteria that use it.

Run with: python3 scripts/tests/test_patterns.py
"""

import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import packaging  # noqa: E402
from audit.text import patterns  # noqa: E402


#: Exponential in the length of a subject it does not match: each
#: character can be taken by either branch, and the missing Z makes
#: the engine try every way. Against a forty-character subject it does
#: not finish within any job limit.
PATHOLOGICAL = '^(.|.)*Z'


class BoundedSearchTest(unittest.TestCase):
    def setUp(self):
        # A short limit, so the pathological case costs the suite a
        # fraction of a second rather than the production bound.
        patterns._bounded_search.cache_clear()
        self.addCleanup(patterns._bounded_search.cache_clear)
        self.addCleanup(setattr, patterns, 'SEARCH_TIMEOUT', patterns.SEARCH_TIMEOUT)
        patterns.SEARCH_TIMEOUT = 0.5

    def test_each_subject_gets_its_own_answer(self):
        self.assertEqual((True, False), patterns.bounded_search(r'^\.vscode/', ['.vscode/x', 'src/x']))

    def test_flags_are_applied(self):
        self.assertEqual((True,), patterns.bounded_search('^OSLO', ['oslo'], re.IGNORECASE))

    def test_a_pattern_that_does_not_compile_has_no_answer(self):
        self.assertIsNone(patterns.bounded_search('^[unclosed', ['x']))

    def test_a_pattern_that_does_not_finish_has_no_answer(self):
        started = time.monotonic()
        self.assertIsNone(patterns.bounded_search(PATHOLOGICAL, ['x' * 40]))
        self.assertLess(time.monotonic() - started, 5)

    def test_a_subject_is_data_not_an_argument(self):
        """They travel on stdin, so a leading dash is just a character."""
        self.assertEqual((True,), patterns.bounded_search('^--', ['--version']))


class RenovateMatcherTest(unittest.TestCase):
    """A Renovate /regex/ matcher is the repository's pattern too."""

    def setUp(self):
        patterns._bounded_search.cache_clear()
        self.addCleanup(patterns._bounded_search.cache_clear)
        self.addCleanup(setattr, patterns, 'SEARCH_TIMEOUT', patterns.SEARCH_TIMEOUT)
        patterns.SEARCH_TIMEOUT = 0.5

    def test_a_regex_matcher_still_matches(self):
        self.assertTrue(packaging._match_pattern('/^oslo/', ['oslo.config']))

    def test_a_pathological_matcher_selects_nothing(self):
        self.assertFalse(packaging._match_pattern(f'/{PATHOLOGICAL}/', ['oslo-' + 'x' * 40]))


if __name__ == '__main__':
    unittest.main()
