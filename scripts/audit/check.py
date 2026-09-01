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


PASS = 'pass'
FAIL = 'fail'
NOT_APPLICABLE = 'not_applicable'

STATUSES = (PASS, FAIL, NOT_APPLICABLE)


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
