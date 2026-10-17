"""Shared metadata and helpers for the consistency audit scripts.

Imported by audit-manage-issues.py and audit-update-docs.py so that
the check-to-spec mapping and issue title conventions live in one
place.
"""

import json
import subprocess


# The markers delimiting the generated compliance tables. Re-exported
# from audit.markers so that this module can import the registry
# without a cycle: audit/text/markdown.py needs the markers too, and
# it is reached from the registry.
from audit.markers import BEGIN_MARKER, END_MARKER  # noqa: F401
from audit.registry import CHECKS


def _metadata():
    """The check-to-spec mapping, derived from the registry.

    Was a hand-written table that had to be kept in step with
    check_calls() by a test. A criterion now declares its
    specification and template as class attributes, so the mapping is
    a view of the registry rather than a second copy of it. Adding a
    criterion no longer means remembering this file.
    """
    return {
        check.id: {'spec': check.spec, 'template': check.template}
        for check in CHECKS
    }


def _issue_titles():
    """The check-to-issue-title mapping, derived from the registry.

    This is an interface, not a label: audit-manage-issues.py finds a
    criterion's open issue by title, so a renamed entry orphans every
    open issue for that criterion across the fleet. Deriving it means
    the title lives beside the check that files it; a frozen snapshot
    in scripts/tests/test_metadata.py is what stops a careless edit
    reaching a scheduled run.
    """
    return {check.id: check.issue_title for check in CHECKS}


AUDIT_METADATA = _metadata()

ISSUE_TITLES = _issue_titles()


# The `issue_title` of each criterion that has been retired from the
# audit, spelled exactly as it was on the check and without the
# "Consistency: " prefix. Deleting a check would otherwise orphan its
# open issues the way renaming one does: no later run measures it, so
# none would ever close them. audit-manage-issues.py closes any open
# issue carrying one of these titles, with a comment saying why.
# docs/consistency-audits.md has the procedure, and test_metadata.py
# fails on a title that is retired and live at once.
RETIRED_ISSUE_TITLES = ()

ISSUE_TITLE_PREFIX = 'Consistency: '


def issue_title_for(check_id):
    """The title of the issue a check files, and is matched by.

    One function rather than an f-string in each caller, because the
    two callers have to agree to the character: audit-manage-issues.py
    files and closes by this title, and audit-update-docs.py links the
    issues it finds by it. A check id missing from the registry falls
    back to the id, which is what an issue for it was filed under.
    """
    return ISSUE_TITLE_PREFIX + ISSUE_TITLES.get(check_id, check_id)


def retired_issue_titles():
    """The full issue titles of the retired criteria."""
    return [ISSUE_TITLE_PREFIX + title for title in RETIRED_ISSUE_TITLES]


_CANONICAL_CACHE = {}


def gh_canonical_repo(github, org, repo):
    """Resolve a repository to its canonical org and name.

    When a repository is renamed on GitHub, REST calls (issue create,
    clone) follow the rename redirect but issue listing and search do
    not: they silently return no results for the old name. Operating
    on a stale name therefore creates a fresh issue on every run
    while never finding the previous one. Resolving the canonical
    name first makes every code path agree on which repo is meant.

    `github` is an audit.github.GitHubClient. Returns an (org, repo)
    tuple. Falls back to the input values if the API call fails.
    """
    key = f'{org}/{repo}'
    if key not in _CANONICAL_CACHE:
        canonical = key
        try:
            result = github.api(f'repos/{org}/{repo}', jq='.full_name')
            if result.returncode == 0 and result.stdout.strip():
                canonical = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        _CANONICAL_CACHE[key] = canonical
    return tuple(_CANONICAL_CACHE[key].split('/', 1))


#: The most open issues one listing asks for. A repository has one per
#: criterion it fails plus whatever duplicates have accumulated, so a
#: listing that comes back this full is one that may have been cut
#: short, and is treated as a failure rather than as the whole answer.
OPEN_ISSUE_LIMIT = 1000


class IssueListingError(Exception):
    """A repository's open issues could not be listed.

    Raised rather than answered with an empty list, because the two
    callers act on an empty list: audit-manage-issues.py files a fresh
    issue for every failing check that appears to have none, and
    audit-update-docs.py publishes "no issue" for each. A listing that
    failed says nothing about which issues are open.
    """


def gh_open_issues(github, org, repo, label='consistency'):
    """Every open issue on a repository carrying `label`.

    One listing per repository, matched against titles by the caller
    with issues_titled(). It used to be one search per check, which
    made a failure in any one of them read as "no issue" for that
    check, and searched with a limit of ten that a family of nine
    titles sharing a prefix came within one of filling. A label
    listing is not a search, either: it reads the repository's issues
    directly rather than through the search index, which lags behind
    an issue created moments earlier.

    Each issue carries its body as well as its number and title, so
    that a caller can tell whether an open issue still says what the
    latest audit result would have it say. Raises IssueListingError
    if the listing fails, cannot be parsed, or may be incomplete.
    """
    org, repo = gh_canonical_repo(github, org, repo)
    where = f'{org}/{repo}'
    try:
        result = github.run([
            'issue', 'list',
            '--repo', where,
            '--label', label,
            '--state', 'open',
            '--json', 'number,title,body',
            '--limit', str(OPEN_ISSUE_LIMIT),
        ])
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise IssueListingError(f'{where}: {e}') from e
    if result.returncode != 0:
        raise IssueListingError(
            f'{where}: gh exited {result.returncode}: '
            f'{result.stderr.strip()}')
    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise IssueListingError(f'{where}: unparseable listing: {e}') from e
    if not isinstance(issues, list):
        raise IssueListingError(f'{where}: listing is not a list')
    if len(issues) >= OPEN_ISSUE_LIMIT:
        raise IssueListingError(
            f'{where}: {len(issues)} open issues, which is the listing '
            f'limit, so the list may be incomplete')
    return issues


def issues_titled(open_issues, title):
    """The issues among `open_issues` whose title is exactly `title`.

    Exactly, because several titles are prefixes of others ("Workflow
    standards" of every "Workflow standards (...)" criterion), and a
    looser match lets one criterion's verdict close or rewrite a
    sibling's issue in every repository at once. Sorted oldest first so
    callers treat the original issue as the survivor and any later
    duplicates as disposable.
    """
    return sorted(
        (i for i in open_issues if i['title'] == title),
        key=lambda i: i['number'],
    )


def defuse(details):
    """Make a harvested detail string safe to publish.

    A detail string is written by a check in audit-check.py out of what
    it found in an audited repository, so it can carry that
    repository's filenames and a tool's output verbatim. It is then
    rendered as bare prose inside the generated block, which means two
    things have to be taken away from it before it lands.

    Newlines, because the block's structure is line-based: a detail
    spanning lines can emit a table row, a `## ` heading, or a bare
    marker, none of which the renderer intended. A subprocess
    traceback reaching a detail string is the way this happens by
    accident.

    The HTML comment opener, because update_compliance_page finds the
    end marker in order to replace the block. A detail containing the
    literal end marker would terminate the next run's splice early,
    leaving the rest of that run's tables outside the block -- where
    they are preserved, and preserved again by every later run, so the
    page grows without bound and publishes stale verdicts that
    blank_generated_blocks no longer exempts from this repository's own
    docs-external-links and plan-phase-references checks. It takes
    commit access to an audited repository to do deliberately (a
    workflow file named for the marker is enough), which is why the
    marker is defused here rather than the whole string escaped.
    """
    return ' '.join(details.split()).replace('<!--', '&lt;!--')
