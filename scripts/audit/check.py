"""The Check base class and the vocabulary of a result.

A criterion is a subclass of `Check`. It declares what it is -- its id,
the specification that defines it, the template that implements it, the
title its issues carry -- as class attributes, and implements `run()`.

The id is declared once. Before this existed it was a string literal
repeated two to nine times inside each check function, around 180 of
them, and only one of those agreements was under test.

`applies()` is separate from `run()` on purpose. Several checks query
the GitHub API, and on a private repository those queries fail for
reasons that have nothing to do with compliance, so a repository scoped
by `only_checks` has to be able to skip a check without paying for it
first. The scheduler asks the cheap question before the expensive one.
"""

import abc
import subprocess


PASS = 'pass'
FAIL = 'fail'
NOT_APPLICABLE = 'not_applicable'

STATUSES = (PASS, FAIL, NOT_APPLICABLE)

#: What the scheduler reports for a check that raised rather than
#: returning. Deliberately not in STATUSES: those are the verdicts a
#: check can reach about a repository, and this is a statement about
#: the audit instead. A check never returns it -- only
#: `registry.run_check()` does -- and the tests that call `run()`
#: directly assert a verdict, so a check that raises still fails them.
ERROR = 'error'


class ToolFailure(Exception):
    """The check's own tooling failed, so it has no verdict to give.

    Raised for a GitHub API error, a `git ls-files` that could not
    answer, a clone that failed and a subprocess that timed out:
    whatever the check asked could not reply, so it learned nothing
    about the repository. `registry.run_check()` reports it as `error`,
    which touches no issue. Returning `fail()` instead would file one
    on the repository for an outage on our side, and `skip()` would
    close the one it has. docs/consistency-audits.md, "What each status
    means", is the policy.

    The line is whether the tool answered. An answer that the thing
    asked about does not exist -- an exit status that means "no", an
    empty list -- is evidence, and a verdict may rest on it.

    The message is the `details` of the error result, so it says what
    could not be asked and why, in the terms a reader of the
    compliance page needs.

    `transient` says the failure could have been a remote declining to
    answer, which audit-raised.py excuses rather than failing the
    repository's leg of the scheduled run over. It defaults to false
    because most of what raises this is the runner's own tooling, and
    only the raise site knows which it was: a criterion reading
    history in a `--filter=blob:none` clone fetches a blob from the
    promisor remote for some of its reads and not for others, and
    nothing in the result can tell them apart afterwards. Marking it
    per check id instead would excuse a local failure -- a shallow
    checkout, say -- on the strength of a sibling read that reaches a
    remote, which is how a broken audit comes to look like a healthy
    one.
    """

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


def run_tool(args, what, timeout, transient=False, **kwargs):
    """subprocess.run() for a check, with a failure to run raised.

    A timeout, a missing binary and an unusable working directory all
    mean the tool never answered, so each becomes a ToolFailure naming
    `what` was being run. The exit status is the caller's to read:
    only it knows which statuses are answers.

    `transient` is passed through to the ToolFailure, for a call whose
    timeout is a remote not answering in time rather than local
    tooling hanging -- a read that a promisor remote has to satisfy.
    It covers the missing-binary case too, which is not transient; a
    caller whose first call is a local one finds that out there, with
    the leg failing as it should.
    """
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise ToolFailure(f'{what} timed out after {timeout} seconds',
                          transient=transient) from e
    except OSError as e:
        raise ToolFailure(f'{what} could not run: {e}',
                          transient=transient) from e


class Check(abc.ABC):
    """One consistency criterion.

    Subclasses set the class attributes and implement `run()`. They
    should return results through `ok()`, `fail()` and `skip()` rather
    than building dicts, so that the id cannot disagree with itself.
    """

    #: The criterion's id. Appears in the results JSON, in the
    #: compliance tables, and as the key that schedules the check.
    id = None

    #: Path to the specification that defines the criterion, relative
    #: to the repository root of this project.
    spec = None

    #: Path to the template that implements it, where there is one.
    template = None

    #: The title of the issue filed when the check fails. This is the
    #: idempotency key for filing and closing: renaming it orphans
    #: every open issue for the check across the fleet.
    issue_title = None

    #: Column heading on the compliance page, set only where the check
    #: shares a specification file with another and so cannot be
    #: labelled by its spec name alone.
    column = None

    def applies(self, repo):
        """Return a reason to skip, or None to run.

        The reason becomes the `details` of a `not_applicable` result.
        A check that does not apply must say so rather than be omitted:
        an omitted check renders as `unknown` on the compliance page,
        and out of scope is a decision we made rather than something we
        failed to measure.
        """
        return None

    @abc.abstractmethod
    def run(self, repo):
        """Measure the criterion. Returns a result dict."""

    def result(self, status, details, **extra):
        """Build a result dict.

        Key order matches what the check functions built by hand, so
        that the JSON is byte-identical: id, status, details, then
        anything else.
        """
        built = {'id': self.id, 'status': status, 'details': details}
        built.update(extra)
        return built

    def ok(self, details, **extra):
        return self.result(PASS, details, **extra)

    def fail(self, details, **extra):
        return self.result(FAIL, details, **extra)

    def skip(self, details, **extra):
        return self.result(NOT_APPLICABLE, details, **extra)
