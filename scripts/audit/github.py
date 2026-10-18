"""The seam between the checks and the GitHub CLI.

Some criteria read what is not in a clone -- the default branch, the
security features, the merge queue rules, run history, and the
membership of the organisation itself -- by shelling out to `gh`.
They go through one seam so that the timeout and the
`FileNotFoundError` handling live in one place, and so that a test has
something to substitute for the network.

The issue scripts go through it too: audit-manage-issues.py, which
files and closes issues across the fleet, and audit-update-docs.py,
which links them from the compliance page. Those are the calls with
the widest reach in the audit, and the seam is what lets a test watch
what they would do to a repository's issues.

`GhCli` is what runs in production. `FakeGitHub` is what the tests
use. `RecordingGitHubClient` sits in front of a real client and keeps
a transcript, so that a before-and-after snapshot over the same clones
can replay identical responses instead of asking GitHub twice and
comparing two different afternoons.

The contract is deliberately thin: `run()` returns whatever
`subprocess.run` returns and raises what it raises. `answer()` sits on
top of it for the common case, a call whose every failure is GitHub
not answering rather than answering "no": it raises ToolFailure, so the
check reports `error` instead of a verdict.
"""

import os
import subprocess

from audit.check import ToolFailure


DEFAULT_TIMEOUT = 30


# Variables that hand a GitHub credential to a child process. The audit
# step holds a GitHub token, and some checks run another program over
# the audited repository or out of another one -- skillsaw, sfui's
# tools/vendor.sh -- which needs no credential and must not inherit
# one. Only `gh` itself, through GhCli, is meant to see the token.
CREDENTIAL_ENV = ('GH_TOKEN', 'GITHUB_TOKEN')


def environment_without_credentials():
    """os.environ with every CREDENTIAL_ENV variable removed."""
    return {
        name: value for name, value in os.environ.items()
        if name not in CREDENTIAL_ENV
    }


class CompletedCommand:
    """What a fake or replayed invocation returns.

    Carries the three attributes the checks read from
    `subprocess.CompletedProcess`, and nothing else.
    """

    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GitHubClient:
    """Interface: run a `gh` invocation and return its result."""

    def run(self, args, timeout=DEFAULT_TIMEOUT):
        raise NotImplementedError

    def api(self, path, jq=None, timeout=DEFAULT_TIMEOUT):
        """Convenience for the `gh api <path> [--jq <expr>]` shape."""
        args = ['api', path]
        if jq is not None:
            args += ['--jq', jq]
        return self.run(args, timeout=timeout)

    def answer(self, args, what, timeout=DEFAULT_TIMEOUT):
        """Run an invocation that must succeed, and return its result.

        For the calls where any non-zero exit means GitHub did not
        answer -- an expired token, rate limiting, an outage -- rather
        than answering "no". A timeout and a missing `gh` are the same
        failure reached another way. Each raises ToolFailure naming
        `what` was being asked, so the check reports `error` rather than
        a verdict built on nothing (docs/consistency-audits.md, "What
        each status means"). A caller to whom some failing status is an
        answer -- a 404 meaning "no such repository" -- uses run()
        instead and decides for itself.
        """
        try:
            result = self.run(args, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise ToolFailure(
                f'Could not query GitHub for {what}: timed out after '
                f'{timeout} seconds') from e
        except OSError as e:
            raise ToolFailure(
                f'Could not query GitHub for {what}: {e}') from e
        if result.returncode != 0:
            # One line: the message is a details string, which renders
            # into a table cell, and gh's advice runs to several.
            reason = ' '.join((result.stderr or '').split())
            raise ToolFailure(
                f'Could not query GitHub for {what}: '
                f'{reason or "no error reported"}')
        return result

    def api_answer(self, path, what, jq=None, timeout=DEFAULT_TIMEOUT):
        """answer() for the `gh api <path> [--jq <expr>]` shape."""
        args = ['api', path]
        if jq is not None:
            args += ['--jq', jq]
        return self.answer(args, what, timeout=timeout)


class GhCli(GitHubClient):
    """The real thing."""

    def run(self, args, timeout=DEFAULT_TIMEOUT):
        return subprocess.run(
            ['gh'] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )


class FakeGitHub(GitHubClient):
    """A scripted client for tests.

    `responses` maps the argument list, joined with spaces, to either a
    `CompletedCommand` or an exception instance to raise. An
    unscripted invocation returns a non-zero result naming what was
    asked for, which is what a test wants to see when it has forgotten
    to script something.
    """

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def run(self, args, timeout=DEFAULT_TIMEOUT):
        key = ' '.join(args)
        self.calls.append(key)
        response = self.responses.get(key)
        if isinstance(response, BaseException):
            raise response
        if response is None:
            return CompletedCommand(
                returncode=1, stderr=f'FakeGitHub: no response for {key}')
        return response


class RecordingGitHubClient(GitHubClient):
    """Record a real client's answers, or replay a recording.

    Given a transcript it replays; given none it delegates and fills
    one in. That is what lets a snapshot taken before a change and one
    taken after it compare exactly rather than merely closely: the
    settings on GitHub can move between two runs, and a difference
    caused by somebody else's afternoon is noise in the one comparison
    this project relies on.
    """

    def __init__(self, inner=None, transcript=None):
        self.inner = inner if inner is not None else GhCli()
        self.transcript = dict(transcript or {})
        self.replaying = transcript is not None

    def run(self, args, timeout=DEFAULT_TIMEOUT):
        key = ' '.join(args)
        if self.replaying:
            recorded = self.transcript.get(key)
            if recorded is None:
                return CompletedCommand(
                    returncode=1,
                    stderr=f'RecordingGitHubClient: {key} not recorded')
            return CompletedCommand(**recorded)

        result = self.inner.run(args, timeout=timeout)
        self.transcript[key] = {
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
        }
        return result
