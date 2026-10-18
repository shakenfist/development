#!/usr/bin/env python3

"""Tests for the audit package's seams: Check, Repo, the client, the registry.

Run with: python3 scripts/tests/test_audit_seams.py
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.check import Check, ERROR, FAIL, NOT_APPLICABLE, PASS, ToolFailure, run_tool  # noqa: E402
from audit.github import (  # noqa: E402
    CompletedCommand, FakeGitHub, GhCli, RecordingGitHubClient,
)
from audit.registry import run_all, run_check  # noqa: E402
from audit.repo import Repo  # noqa: E402


class Sample(Check):
    """A check that reports whatever it is told to."""

    id = 'sample'
    spec = 'docs/audits/sample.md'
    issue_title = 'Consistency: sample'

    def __init__(self, outcome='ok', skip_reason=None):
        self.outcome = outcome
        self.skip_reason = skip_reason
        self.ran = False

    def applies(self, repo):
        return self.skip_reason

    def run(self, repo):
        self.ran = True
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.outcome == 'ok':
            return self.ok('all good')
        return self.fail('not good', missing=['a thing'])


def fixture_repo(tmp, name='testrepo', **props):
    """A Repo over an empty directory with props supplied directly."""
    base = {
        'only_checks': [],
        'has_workflows_dir': False,
    }
    base.update(props)
    return Repo(tmp, name, 'shakenfist', github=FakeGitHub(), props=base)


class CheckResultTest(unittest.TestCase):
    def test_the_id_comes_from_the_class(self):
        self.assertEqual(Sample().ok('fine')['id'], 'sample')

    def test_key_order_matches_the_handwritten_dicts(self):
        """The JSON is a contract; key order is part of the diff."""
        result = Sample().fail('bad', missing=['x'])
        self.assertEqual(list(result), ['id', 'status', 'details', 'missing'])

    def test_the_three_constructors_set_their_statuses(self):
        check = Sample()
        self.assertEqual(check.ok('a')['status'], PASS)
        self.assertEqual(check.fail('b')['status'], FAIL)
        self.assertEqual(check.skip('c')['status'], NOT_APPLICABLE)

    def test_applies_defaults_to_running(self):
        class Bare(Check):
            id = 'bare'

            def run(self, repo):
                return self.ok('ran')

        self.assertIsNone(Bare().applies(None))

    def test_run_is_abstract(self):
        class Incomplete(Check):
            id = 'incomplete'

        with self.assertRaises(TypeError):
            Incomplete()


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = fixture_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, content):
        full = os.path.join(self.tmp.name, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def test_exists_and_read(self):
        self.write('AGENTS.md', 'hello\n')
        self.assertTrue(self.repo.exists('AGENTS.md'))
        self.assertEqual(self.repo.read('AGENTS.md'), 'hello\n')

    def test_a_missing_file_reads_as_none(self):
        self.assertFalse(self.repo.exists('nope.md'))
        self.assertIsNone(self.repo.read('nope.md'))

    def test_reads_are_cached(self):
        """Forty-one open() sites became one, so the cache has to hold."""
        self.write('AGENTS.md', 'first\n')
        self.assertEqual(self.repo.read('AGENTS.md'), 'first\n')
        self.write('AGENTS.md', 'second\n')
        self.assertEqual(self.repo.read('AGENTS.md'), 'first\n')

    def test_a_missing_file_is_cached_too(self):
        self.assertIsNone(self.repo.read('later.md'))
        self.write('later.md', 'now here\n')
        self.assertIsNone(self.repo.read('later.md'))

    def test_undecodable_bytes_do_not_raise(self):
        full = os.path.join(self.tmp.name, 'binary.md')
        with open(full, 'wb') as f:
            f.write(b'\xff\xfe not utf-8')
        self.assertIn('not utf-8', self.repo.read('binary.md'))

    def test_workflows_lists_yml_and_yaml_only(self):
        self.write('.github/workflows/ci.yml', 'on: push\n')
        self.write('.github/workflows/release.yaml', 'on: push\n')
        self.write('.github/workflows/notes.txt', 'ignore me\n')
        self.assertEqual(sorted(self.repo.workflows()),
                         ['ci.yml', 'release.yaml'])

    def test_workflows_is_empty_without_the_directory(self):
        self.assertEqual(self.repo.workflows(), [])

    def test_workflows_is_cached(self):
        self.assertEqual(self.repo.workflows(), [])
        self.write('.github/workflows/ci.yml', 'on: push\n')
        self.assertEqual(self.repo.workflows(), [])

    def test_workflow_reads_by_name(self):
        self.write('.github/workflows/ci.yml', 'on: push\n')
        self.assertEqual(self.repo.workflow('ci.yml'), 'on: push\n')

    def test_properties_are_detected_when_not_supplied(self):
        repo = Repo(self.tmp.name, 'development', 'shakenfist')
        self.assertTrue(repo.props['not_python'])
        self.assertIn('publishes no releases',
                      repo.props['default_branch_exception'])


class GitHubClientTest(unittest.TestCase):
    def test_api_builds_the_gh_argument_list(self):
        seen = []

        class Recorder(GhCli):
            def run(self, args, timeout=30):
                seen.append((list(args), timeout))
                return CompletedCommand()

        Recorder().api('repos/shakenfist/ryll', jq='.default_branch')
        self.assertEqual(
            seen[0][0],
            ['api', 'repos/shakenfist/ryll', '--jq', '.default_branch'])

    def test_api_omits_the_jq_flag_when_there_is_no_expression(self):
        seen = []

        class Recorder(GhCli):
            def run(self, args, timeout=30):
                seen.append(list(args))
                return CompletedCommand()

        Recorder().api('repos/shakenfist/ryll/rules/branches/main')
        self.assertEqual(
            seen[0], ['api', 'repos/shakenfist/ryll/rules/branches/main'])

    def test_a_scripted_response_comes_back(self):
        client = FakeGitHub({
            'api repos/shakenfist/ryll --jq .default_branch':
                CompletedCommand(stdout='develop\n'),
        })
        result = client.api('repos/shakenfist/ryll', jq='.default_branch')
        self.assertEqual(result.stdout.strip(), 'develop')

    def test_an_unscripted_call_fails_loudly_rather_than_passing(self):
        """A forgotten stub must not read as a healthy repository."""
        result = FakeGitHub().api('repos/shakenfist/ryll')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no response for', result.stderr)

    def test_a_scripted_exception_is_raised(self):
        client = FakeGitHub({
            'api repos/shakenfist/ryll':
                subprocess.TimeoutExpired(cmd='gh', timeout=30),
        })
        with self.assertRaises(subprocess.TimeoutExpired):
            client.api('repos/shakenfist/ryll')

    def test_calls_are_recorded_for_assertions(self):
        client = FakeGitHub()
        client.api('repos/shakenfist/ryll', jq='.private')
        self.assertEqual(
            client.calls, ['api repos/shakenfist/ryll --jq .private'])

    def test_recording_then_replaying_gives_the_same_answer(self):
        inner = FakeGitHub({
            'api repos/shakenfist/ryll': CompletedCommand(stdout='yes'),
        })
        recorder = RecordingGitHubClient(inner=inner)
        first = recorder.api('repos/shakenfist/ryll')

        replay = RecordingGitHubClient(transcript=recorder.transcript)
        second = replay.api('repos/shakenfist/ryll')

        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(second.returncode, 0)

    def test_replaying_something_unrecorded_does_not_reach_the_network(self):
        replay = RecordingGitHubClient(transcript={})
        result = replay.api('repos/shakenfist/instar')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not recorded', result.stderr)


class AnswerTest(unittest.TestCase):
    """GitHubClient.answer(): a call whose every failure is GitHub not answering."""

    def test_an_answer_comes_back(self):
        client = FakeGitHub({'api repos/x --jq .y': CompletedCommand(stdout='z\n')})
        self.assertEqual(client.api_answer('repos/x', 'y', jq='.y').stdout, 'z\n')

    def test_a_non_zero_exit_raises_naming_what_was_asked(self):
        client = FakeGitHub({'api repos/x': CompletedCommand(
            returncode=1, stderr='gh: Bad credentials (HTTP 401)\n')})
        with self.assertRaises(ToolFailure) as caught:
            client.api_answer('repos/x', 'the thing')
        self.assertEqual(str(caught.exception),
                         'Could not query GitHub for the thing: gh: Bad credentials (HTTP 401)')

    def test_a_multi_line_reason_is_folded_onto_one_line(self):
        # The message is a details string, which renders in a table
        # cell, and gh's advice on a missing login runs to two lines.
        client = FakeGitHub({'api repos/x': CompletedCommand(
            returncode=4, stderr='To get started, run:  gh auth login\nOr set GH_TOKEN.\n')})
        with self.assertRaises(ToolFailure) as caught:
            client.api_answer('repos/x', 'the thing')
        self.assertEqual(str(caught.exception), 'Could not query GitHub for the thing: '
                         'To get started, run: gh auth login Or set GH_TOKEN.')

    def test_a_silent_failure_still_says_something(self):
        client = FakeGitHub({'api repos/x': CompletedCommand(returncode=1, stderr='  \n')})
        with self.assertRaises(ToolFailure) as caught:
            client.api_answer('repos/x', 'the thing')
        self.assertIn('no error reported', str(caught.exception))

    def test_a_timeout_and_a_missing_gh_raise(self):
        for raised, said in ((subprocess.TimeoutExpired('gh', 30), 'timed out after 30 seconds'),
                             (FileNotFoundError('gh'), 'gh')):
            with self.subTest(raised=type(raised).__name__):
                client = FakeGitHub({'repo list x': raised})
                with self.assertRaises(ToolFailure) as caught:
                    client.answer(['repo', 'list', 'x'], 'the listing')
                self.assertIn(said, str(caught.exception))
                self.assertIs(caught.exception.__cause__, raised)


class RunToolTest(unittest.TestCase):
    """run_tool(): a subprocess that never answered raises; one that did returns."""

    def test_a_non_zero_exit_is_returned_not_raised(self):
        # Only the caller knows which statuses are answers.
        result = run_tool([sys.executable, '-c', 'import sys; sys.exit(1)'], 'the tool', timeout=30)
        self.assertEqual(result.returncode, 1)

    def test_a_timeout_raises(self):
        with self.assertRaises(ToolFailure) as caught:
            run_tool([sys.executable, '-c', 'import time; time.sleep(5)'], 'the tool', timeout=0.1)
        self.assertEqual(str(caught.exception), 'the tool timed out after 0.1 seconds')

    def test_a_missing_binary_raises(self):
        with self.assertRaises(ToolFailure) as caught:
            run_tool(['/nonexistent/binary'], 'the tool', timeout=30)
        self.assertIn('the tool could not run', str(caught.exception))

    def test_an_unusable_working_directory_raises(self):
        with self.assertRaises(ToolFailure):
            run_tool([sys.executable, '-c', 'pass'], 'the tool', timeout=30, cwd='/nonexistent/dir')


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = fixture_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_check_that_applies_runs(self):
        check = Sample()
        result = run_check(check, self.repo)
        self.assertTrue(check.ran)
        self.assertEqual(result['status'], PASS)

    def test_a_check_that_does_not_apply_is_never_run(self):
        """The whole reason applies() is separate from run()."""
        check = Sample(skip_reason='No pyproject.toml')
        result = run_check(check, self.repo)
        self.assertFalse(check.ran)
        self.assertEqual(result['status'], NOT_APPLICABLE)
        self.assertEqual(result['details'], 'No pyproject.toml')
        self.assertEqual(result['id'], 'sample')

    def test_a_tool_failure_is_an_error_saying_what_could_not_be_asked(self):
        with contextlib.redirect_stderr(io.StringIO()):
            result = run_check(Sample(outcome=ToolFailure('Could not query GitHub for x: HTTP 502')),
                               self.repo)
        self.assertEqual(result['status'], ERROR)
        self.assertTrue(result['tool_failure'])
        self.assertEqual(
            result['details'],
            'Could not query GitHub for x: HTTP 502. The audit could not measure this '
            'criterion, so this is not a finding about testrepo.')

    def test_a_message_that_ends_in_a_stop_gets_no_second_one(self):
        # gh's stderr usually ends in one, and the details are a table
        # cell on the compliance page. An ellipsis is left alone rather
        # than trimmed and stopped again.
        for message, expected in (
                ('Could not query GitHub for x: Or set GH_TOKEN.', 'Or set GH_TOKEN. The audit'),
                ('scope-orphans failed: usage: ...', 'usage: ... The audit')):
            with self.subTest(message=message):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = run_check(Sample(outcome=ToolFailure(message)), self.repo)
                self.assertIn(expected, result['details'])
                self.assertNotIn('..', result['details'].replace('...', ''))

    def test_a_bug_whose_message_ends_in_a_stop_gets_no_second_one(self):
        with contextlib.redirect_stderr(io.StringIO()):
            result = run_check(Sample(outcome=ValueError('No such file.')), self.repo)
        self.assertIn('ValueError: No such file. This is a bug', result['details'])

    def test_any_other_exception_is_an_error_blaming_the_audit(self):
        with contextlib.redirect_stderr(io.StringIO()):
            result = run_check(Sample(outcome=KeyError('k')), self.repo)
        self.assertEqual(result['status'], ERROR)
        self.assertNotIn('tool_failure', result)
        self.assertIn('This is a bug in the audit', result['details'])

    def test_run_all_summarises(self):
        document = run_all(
            self.repo,
            checks=[Sample(), Sample(outcome='bad'),
                    Sample(skip_reason='n/a')],
        )
        self.assertEqual(document['repo'], 'testrepo')
        self.assertEqual(document['org'], 'shakenfist')
        self.assertEqual(document['summary'],
                         {'total': 3, 'pass': 1, 'fail': 1,
                          'not_applicable': 1, 'error': 0})

    def test_a_scoped_repository_skips_without_running(self):
        """Scoping must not pay for the check it is skipping.

        Several checks reach the network, and on a private repository
        those calls fail for reasons that say nothing about
        compliance.
        """
        repo = fixture_repo(self.tmp.name, name='private-ci',
                            only_checks=['sfui-vendor'])
        skipped = Sample()
        document = run_all(repo, checks=[skipped])

        self.assertFalse(skipped.ran)
        self.assertEqual(document['checks'][0]['status'], NOT_APPLICABLE)
        self.assertEqual(document['checks'][0]['details'],
                         'private-ci is audited for sfui-vendor only')

    def test_a_scoped_repository_still_reports_every_check(self):
        """A check missing from the JSON renders as "unknown"."""
        repo = fixture_repo(self.tmp.name, name='private-ci',
                            only_checks=['nothing-matches'])
        document = run_all(repo, checks=[Sample()])
        self.assertEqual(document['summary']['total'], 1)
        self.assertEqual(document['summary']['not_applicable'], 1)

    def test_the_timestamp_is_present_and_utc(self):
        document = run_all(self.repo, checks=[])
        self.assertIn('+00:00', document['timestamp'])


if __name__ == '__main__':
    unittest.main()
