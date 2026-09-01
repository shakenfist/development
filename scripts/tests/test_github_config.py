#!/usr/bin/env python3

"""Tests for audit/checks/github_config.py.

Run with: python3 scripts/tests/test_github_config.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import github_config  # noqa: E402

evaluate_merge_queue_rules = github_config.evaluate_merge_queue_rules


class MergeQueueConfigTest(unittest.TestCase):
    def _rule(self, **params):
        return {'type': 'merge_queue', 'parameters': params}

    def test_no_merge_queue_rule_returns_none(self):
        self.assertIsNone(evaluate_merge_queue_rules([]))
        self.assertIsNone(evaluate_merge_queue_rules(
            [{'type': 'deletion'}, {'type': 'non_fast_forward'}]
        ))

    def test_serialized_queue_passes(self):
        problems = evaluate_merge_queue_rules([
            {'type': 'pull_request'},
            self._rule(
                max_entries_to_build=1, min_entries_to_merge=1,
                max_entries_to_merge=5,
                min_entries_to_merge_wait_minutes=5,
            ),
        ])
        self.assertEqual(problems, [])

    def test_speculative_stacking_fails(self):
        problems = evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=2, min_entries_to_merge=1),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('max_entries_to_build is 2', problems[0])

    def test_batched_merging_fails(self):
        problems = evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=1, min_entries_to_merge=2),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('min_entries_to_merge is 2', problems[0])

    def test_missing_parameters_flags_both(self):
        problems = evaluate_merge_queue_rules([
            {'type': 'merge_queue'},
        ])
        self.assertEqual(len(problems), 2)


if __name__ == '__main__':
    unittest.main()
