"""The seam between the checks and the GitHub CLI.

Five criteria read repository settings that are not in a clone --
the default branch, the security features, the merge queue rules --
by shelling out to `gh`. Before this existed each of them built its
own `subprocess.run`, with its own copy of the timeout and the
`FileNotFoundError` handling, and none of the five had a test,
because there was nothing to substitute for the network.

`GhCli` is what runs in production. `FakeGitHub` is what the tests
use. `RecordingGitHubClient` sits in front of a real client and keeps
a transcript, so that a before-and-after snapshot over the same clones
can replay identical responses instead of asking GitHub twice and
comparing two different afternoons.

The contract is deliberately thin: `run()` returns whatever
`subprocess.run` returns and raises what it raises. The callers
already handle `returncode`, `stdout`, `stderr`, `TimeoutExpired` and
`FileNotFoundError`, and a richer interface would have meant
rewriting them at the same time as moving them.
"""

import subprocess


DEFAULT_TIMEOUT = 30


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
