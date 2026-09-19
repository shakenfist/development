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
    reaching a morning run.
    """
    return {check.id: check.issue_title for check in CHECKS}


AUDIT_METADATA = _metadata()

ISSUE_TITLES = _issue_titles()


_CANONICAL_CACHE = {}


def gh_canonical_repo(org, repo):
    """Resolve a repository to its canonical org and name.

    When a repository is renamed on GitHub, REST calls (issue create,
    clone) follow the rename redirect but issue listing and search do
    not: they silently return no results for the old name. Operating
    on a stale name therefore creates a fresh issue on every run
    while never finding the previous one. Resolving the canonical
    name first makes every code path agree on which repo is meant.

    Returns an (org, repo) tuple. Falls back to the input values if
    the API call fails.
    """
    key = f'{org}/{repo}'
    if key not in _CANONICAL_CACHE:
        canonical = key
        try:
            result = subprocess.run(
                [
                    'gh', 'api', f'repos/{org}/{repo}',
                    '--jq', '.full_name',
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                canonical = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        _CANONICAL_CACHE[key] = canonical
    return tuple(_CANONICAL_CACHE[key].split('/', 1))


def gh_search_issues(org, repo, title_prefix, label='consistency'):
    """Search for open issues matching a title prefix and label."""
    org, repo = gh_canonical_repo(org, repo)
    try:
        result = subprocess.run(
            [
                'gh', 'issue', 'list',
                '--repo', f'{org}/{repo}',
                '--label', label,
                '--state', 'open',
                '--search', f'"{title_prefix}" in:title',
                '--json', 'number,title',
                '--limit', '10',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        issues = json.loads(result.stdout)
        # Filter to exact title match to avoid prefix collisions
        # (e.g. "Workflow standards" matching
        # "Workflow standards (flake8wrap)"). Sort oldest first so
        # callers treat the original issue as the survivor and any
        # later duplicates as disposable.
        return sorted(
            (i for i in issues if i['title'] == title_prefix),
            key=lambda i: i['number'],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError):
        return []


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
