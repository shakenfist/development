#!/usr/bin/env python3
"""Manage GitHub issues based on consistency audit results.

For each failed check, creates or updates an issue on the target repo.
For each passing check, closes any existing issue.

Usage:
    python audit-manage-issues.py --results-dir ./audit-results/

Requires `gh` CLI to be authenticated with permissions to create and
close issues on all target repos.
"""

import argparse
import json
import os
import subprocess
import sys
import time

from audit.github import GhCli
from audit_common import (
    AUDIT_METADATA,
    ISSUE_TITLES,
    IssueListingError,
    gh_canonical_repo,
    gh_open_issues,
    issue_title_for,
    issues_titled,
    retired_issue_titles,
)


DEV_REPO_URL = (
    'https://github.com/shakenfist/development/blob/main'
)


def gh_write(github, args, failure):
    """Run one `gh` write, returning its result, or None if it failed.

    A write that fails is reported here and counted by the caller, which
    is what lets the run exit non-zero once every repository has been
    managed. A timeout or a missing `gh` is a failed write like any
    other rather than a traceback that abandons the rest of the fleet.
    """
    try:
        result = github.run(args)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f'  ERROR {failure}: {e}', file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f'  ERROR {failure}: {result.stderr.strip()}', file=sys.stderr)
        return None
    return result


def gh_ensure_label(github, org, repo, label='consistency'):
    """Ensure the label exists on the target repo.

    gh issue create refuses to create an issue if the requested label
    does not exist, so create it first. --force makes this idempotent.
    A failure here is not counted: if the label really is missing, the
    create that follows fails and is counted instead.
    """
    gh_write(github, [
        'label', 'create', label,
        '--repo', f'{org}/{repo}',
        '--description', 'Project consistency audit item',
        '--color', '0075ca',
        '--force',
    ], f'ensuring label {label} exists')


def gh_create_issue(github, org, repo, title, body, label='consistency'):
    """Create a new issue on the target repo. Returns whether it did."""
    gh_ensure_label(github, org, repo, label)
    result = gh_write(github, [
        'issue', 'create',
        '--repo', f'{org}/{repo}',
        '--title', title,
        '--label', label,
        '--body', body,
    ], 'creating issue')
    if result is None:
        return False
    print(f'  Created issue: {result.stdout.strip()}')
    return True


def gh_edit_issue_body(github, org, repo, issue_number, body):
    """Replace the body of an existing issue. Returns whether it did."""
    result = gh_write(github, [
        'issue', 'edit',
        '--repo', f'{org}/{repo}',
        str(issue_number),
        '--body', body,
    ], f'updating issue #{issue_number}')
    if result is None:
        return False
    print(f'  Updated body of issue #{issue_number}')
    return True


def body_is_current(existing_body, body):
    """Whether an open issue's body already says what `body` says.

    GitHub hands a body back with CRLF line endings once anyone has
    touched it in the web editor, and is free to trim the trailing
    newline. Neither is a change in content, and treating either as one
    would rewrite the issue on every run.
    """
    def normalise(text):
        return (text or '').replace('\r\n', '\n').strip()
    return normalise(existing_body) == normalise(body)


def gh_close_issue(github, org, repo, issue_number, comment=None):
    """Close an issue, then explain why. Returns whether it closed.

    Closed first and commented on only once closed. Commenting first
    meant a close that failed left the explanation behind on an issue
    that was still open, and the next run, finding it open, posted the
    same explanation again -- once a run for as long as the close kept
    failing. A comment that fails after the close is reported but not
    counted: the issue is closed, which was the point, and no later run
    can retry it.
    """
    result = gh_write(github, [
        'issue', 'close',
        '--repo', f'{org}/{repo}',
        str(issue_number),
    ], f'closing issue #{issue_number}')
    if result is None:
        return False
    print(f'  Closed issue #{issue_number}')
    if comment:
        gh_write(github, [
            'issue', 'comment',
            '--repo', f'{org}/{repo}',
            str(issue_number),
            '--body', comment,
        ], f'commenting on closed issue #{issue_number}')
    return True


# GitHub rejects an issue body over 65536 characters outright, so a
# repository with a pathological number of items would file no issue
# at all rather than a long one. The budget sits under that with room
# for the rest of the body, and only bites where the alternative is
# silence.
ISSUE_BODY_BUDGET = 60000

# `details` is one field among several competing for that budget, and
# the only one with no bound at all. Given the whole budget it starves
# the per-item lists, which are the part a maintainer acts on: the
# issue still files, it just carries no actionable items. Half leaves
# both sides room in every realistic case, and where it does bite the
# trailer says where the rest is.
DETAILS_BUDGET = ISSUE_BODY_BUDGET // 2

# Module level so a test can compute the exact room `render_details`
# has, and so pin the truncation boundary rather than approach it.
DETAILS_HEADING = '\n### Automated check details\n\n'
DETAILS_TRAILER = (
    '\n\n*...truncated to stay under GitHub\'s issue body limit. '
    'Run `scripts/audit-check.py` for the full details.*\n'
)


def defuse_item(item):
    """Make one harvested item safe to render inside a code span.

    An item is a path out of an audited repository -- `missing` and
    `findings` are built from `git ls-files`, so the repository chooses
    the bytes. A git path may hold anything but NUL and `/`, and two
    of those characters escape the backtick-wrapped rendering below.

    A newline ends the list item, so the rest of the path lands in the
    body as raw markdown: a heading, a fake footer, a link, or an
    `@org/team` mention that notifies. The body is authored by
    shakenfist-bot, so injected content arrives with the bot's
    authority. Whitespace is collapsed the way defuse() does it in
    audit-update-docs.py, which is the same problem on the page path.

    A backtick closes the code span early and lets the tail render as
    markdown. It cannot be escaped inside a span, so the span is
    widened instead: CommonMark ends a span at the first run of
    backticks matching the opener, so an opener longer than any run in
    the item cannot be closed by the item. The padding spaces are what
    let a value start or end with a backtick, and CommonMark strips
    one leading and one trailing space when both are present.

    A fence of three or more backticks is the shape of a fenced code
    block opener rather than a span, which would be a different bug.
    It cannot happen: the fence only reaches that width when the item
    itself contains a run of two or more backticks, and it is emitted
    on the same line as them, so the would-be info string contains a
    backtick -- which CommonMark forbids. The opener is never valid,
    and the parser falls back to span rules. This is what makes the
    ```` a```b`c ```` case render as intended.

    Nothing here rejects an item, but something is transformed: the
    whitespace collapse means a path whose name contains a newline or
    a tab is published with those rendered as single spaces, so it no
    longer names a file that can be copied straight out of the issue.
    That is the same trade defuse() makes for details in
    audit_common.py, and it is preferred here for symmetry -- the
    alternative, escaping losslessly, would make ordinary paths the
    only thing the two functions disagree about. What matters is that
    the item is still there: a path that needs defusing is still the
    path somebody has to go and review, and dropping it would make the
    work queue lie about what is outstanding.
    """
    flat = ' '.join(str(item).split())
    longest = 0
    run = 0
    for char in flat:
        run = run + 1 if char == '`' else 0
        longest = max(longest, run)
    if not longest:
        return f'`{flat}`'
    fence = '`' * (longest + 1)
    return f'{fence} {flat} {fence}'


def render_issue_items(heading, items, used):
    """Render one per-item list, stopping before the body gets too big.

    `used` is the length of the body so far. The lists are deliberately
    not truncated in any realistic case: the fix is per item, and a
    short list leaves somebody re-running the audit to learn what the
    issue meant. Where the budget does run out, the reader is told how
    many items are missing and how to see them, which is the one thing
    a silently short list cannot do.
    """
    rendered = f'\n**{heading}:**\n'
    for index, item in enumerate(items):
        line = f'- {defuse_item(item)}\n'
        if used + len(rendered) + len(line) > ISSUE_BODY_BUDGET:
            return rendered + (
                f'- ...and {len(items) - index} more, omitted to stay '
                f'under GitHub\'s issue body limit. Run '
                f'`scripts/audit-check.py` for the full list.\n'
            )
        rendered += line
    return rendered


#: The narrowest code span defuse_item() wraps a string in: one
#: backtick either side, for a string holding none.
SPAN_OVERHEAD = 2


def details_room(used):
    """How many characters of `details` fit, given a body of `used`.

    Counted for a string holding no backtick, whose code span costs
    SPAN_OVERHEAD; a wider span costs its own width out of the same
    room. Can go negative where the rest of the body has already spent
    the budget; the caller clamps. Kept separate from the rendering so
    the boundary is a value a test can ask for rather than one it has
    to find by bisection.
    """
    return (
        min(ISSUE_BODY_BUDGET - used, DETAILS_BUDGET)
        - len(DETAILS_HEADING) - len(DETAILS_TRAILER) - SPAN_OVERHEAD
    )


def render_details(details, used):
    """Render the check's own details, under the same budget.

    `details` is written by the criterion and is not bounded by
    anything the criterion has to think about: several route a per-item
    list through it, and the npm ones quote a workflow's `run:` line
    verbatim. Left unaccounted it can push the body past GitHub's
    limit on its own, at which point the create call returns nothing
    and the criterion silently stops filing while the audit reports
    success -- the failure mode the per-item budget already exists to
    prevent, reached through the one field that was not measured.

    Capped at `DETAILS_BUDGET` rather than at whatever is left, so
    that a pathological `details` cannot spend the room the per-item
    lists need.

    Rendered as a code span by defuse_item(), for the reason it gives:
    the string is written out of what a check found in another
    repository -- filenames, heading text, a tool's output -- and in
    prose its `@org/team` would notify and its markdown would render,
    under the bot's identity. Truncated before it is wrapped rather
    than after, so that the cut can never fall inside the span's
    closing fence and let the tail render after all.
    """
    flat = ' '.join(details.split())
    wrapped = defuse_item(flat)
    room = details_room(used) + SPAN_OVERHEAD
    if len(wrapped) + 1 <= room:
        return f'{DETAILS_HEADING}{wrapped}\n'
    kept = flat[:max(room - (len(wrapped) - len(flat)), 0)]
    return (
        f'{DETAILS_HEADING}{defuse_item(kept) if kept else ""}'
        f'{DETAILS_TRAILER}')


def build_issue_body(check_id, check_result):
    """Build the issue body for a failed check."""
    meta = AUDIT_METADATA.get(check_id, {})
    spec_file = meta.get('spec', f'docs/audits/{check_id}.md')
    template_dir = meta.get('template')

    body = (
        f'This project is not yet compliant with the '
        f'**{ISSUE_TITLES.get(check_id, check_id)}** '
        f'consistency audit.\n\n'
    )

    body += (
        f'**Audit spec:** '
        f'[development/{spec_file}]'
        f'({DEV_REPO_URL}/{spec_file})\n'
    )

    if template_dir:
        body += (
            f'**Template:** '
            f'[development/{template_dir}README.md]'
            f'({DEV_REPO_URL}/{template_dir}README.md)\n'
        )

    # render_details() puts the string in a code span, and measures
    # the span rather than the raw string, so the budget counts what
    # actually lands.
    body += render_details(check_result['details'], len(body))

    if 'missing' in check_result:
        body += render_issue_items(
            'Missing items', check_result['missing'], len(body))

    # The sibling of 'missing', for a check whose findings are things
    # present that should not be rather than things absent. Both carry
    # a per-item list because the fix is per item, and a truncated one
    # leaves somebody re-running the audit to learn what the issue
    # meant; only the heading differs, and calling a list of offending
    # lines "missing items" sends the reader looking for the wrong
    # thing.
    if 'findings' in check_result:
        body += render_issue_items(
            'Findings', check_result['findings'], len(body))

    body += (
        '\n---\n'
        '*This issue was created automatically by the '
        'consistency audit workflow, which rewrites this description '
        'on every run to match the latest result.*\n'
    )
    return body


def close_duplicates(github, org, repo, check_id, duplicates, original,
                     dry_run, tally):
    """Close duplicate issues, pointing at the surviving original."""
    for dup in duplicates:
        print(
            f'  [{check_id}] closing duplicate issue '
            f'#{dup["number"]} (original is #{original["number"]})'
        )
        if not dry_run:
            closed = gh_close_issue(
                github, org, repo, dup['number'],
                comment=(
                    f'Duplicate of #{original["number"]}. '
                    'Closing automatically.'
                ),
            )
            tally.wrote(closed, org, repo, f'close #{dup["number"]}')
            time.sleep(1)


class Tally:
    """What went wrong across a run, so that main() can fail on it.

    Each is a reason to exit non-zero once every repository has been
    processed, rather than to stop at the first: one repository whose
    issues cannot be listed is no reason to leave the rest unmanaged.
    """

    def __init__(self):
        # Repositories whose configured name is stale (renamed on
        # GitHub). Their issues were managed under the canonical name.
        self.renamed = []
        # Repositories whose open issues could not be listed, and so
        # whose issues were not managed at all.
        self.unlisted = []
        # Creates, edits and closes that GitHub refused, as
        # 'org/repo: what' strings.
        self.write_failures = []

    def wrote(self, succeeded, org, repo, what):
        """Record the outcome of one write."""
        if not succeeded:
            self.write_failures.append(f'{org}/{repo}: {what}')

    def failed(self):
        return bool(self.renamed or self.unlisted or self.write_failures)


def process_results(results, dry_run=False, github=None, tally=None):
    """Process audit results and manage issues.

    `github` is the audit.github.GitHubClient every `gh` invocation
    goes through; tests pass a fake one. Problems the caller should
    fail the run on are recorded in `tally`, a Tally, which is
    returned.
    """
    if github is None:
        github = GhCli()
    if tally is None:
        tally = Tally()
    org = results['org']
    repo = results['repo']

    # A renamed repo must be handled under its canonical name:
    # issue search silently returns nothing for the old name, while
    # issue creation follows the rename redirect, so a stale name
    # files a fresh duplicate on every run.
    canonical_org, canonical_repo = gh_canonical_repo(github, org, repo)
    if (canonical_org, canonical_repo) != (org, repo):
        print(
            f'WARNING: {org}/{repo} has been renamed to '
            f'{canonical_org}/{canonical_repo}; update the matrix in '
            f'.github/workflows/consistency-audit.yml',
            file=sys.stderr,
        )
        tally.renamed.append(f'{org}/{repo}')
        org, repo = canonical_org, canonical_repo

    print(f'\n=== {repo} ===')
    print(
        f'Results: {results["summary"]["pass"]} pass, '
        f'{results["summary"]["fail"]} fail, '
        f'{results["summary"]["not_applicable"]} N/A, '
        f'{results["summary"].get("error", 0)} error'
    )

    # Listed once, and before anything is written. A listing that
    # failed is not an empty one: treated as one, every failing check
    # would file a duplicate of the issue it already has. So nothing
    # in this repository is touched, and the run fails at the end.
    try:
        open_issues = gh_open_issues(github, org, repo)
    except IssueListingError as e:
        print(
            f'  ERROR: could not list open issues, so none were '
            f'managed: {e}',
            file=sys.stderr,
        )
        tally.unlisted.append(f'{org}/{repo}')
        return tally

    for check in results['checks']:
        check_id = check['id']
        status = check['status']
        title_prefix = issue_title_for(check_id)

        if status == 'fail':
            body = build_issue_body(check_id, check)
            existing = issues_titled(open_issues, title_prefix)
            if existing:
                original = existing[0]
                # The body is the work queue, so it has to follow the
                # numbers: an issue left as the snapshot it was filed
                # with goes on listing files already reviewed and
                # omitting ones added since, and on linking specs that
                # have moved.
                if body_is_current(original.get('body'), body):
                    print(
                        f'  [{check_id}] FAIL -- issue '
                        f'#{original["number"]} already open and current'
                    )
                else:
                    print(
                        f'  [{check_id}] FAIL -- issue '
                        f'#{original["number"]} already open, updating body'
                    )
                    if not dry_run:
                        edited = gh_edit_issue_body(
                            github, org, repo, original['number'], body)
                        tally.wrote(edited, org, repo,
                                    f'edit #{original["number"]}')
                        time.sleep(1)  # Rate limiting
                close_duplicates(
                    github, org, repo, check_id, existing[1:], original,
                    dry_run, tally,
                )
            else:
                print(f'  [{check_id}] FAIL -- creating issue')
                if not dry_run:
                    created = gh_create_issue(
                        github, org, repo, title_prefix, body,
                    )
                    tally.wrote(created, org, repo,
                                f'create "{title_prefix}"')
                    time.sleep(1)  # Rate limiting

        elif status == 'pass':
            # Close any existing open issues (all of them, in case
            # duplicates have accumulated)
            existing = issues_titled(open_issues, title_prefix)
            if existing:
                for issue in existing:
                    print(
                        f'  [{check_id}] PASS -- closing issue '
                        f'#{issue["number"]}'
                    )
                    if not dry_run:
                        closed = gh_close_issue(
                            github, org, repo, issue['number'],
                            comment=(
                                'This check is now passing in the '
                                'automated consistency audit. '
                                'Closing automatically.'
                            ),
                        )
                        tally.wrote(closed, org, repo,
                                    f'close #{issue["number"]}')
                        time.sleep(1)
            else:
                print(f'  [{check_id}] PASS')

        elif status == 'not_applicable':
            # Close any existing issues if any were opened
            existing = issues_titled(open_issues, title_prefix)
            if existing:
                for issue in existing:
                    print(
                        f'  [{check_id}] N/A -- closing issue '
                        f'#{issue["number"]}'
                    )
                    if not dry_run:
                        closed = gh_close_issue(
                            github, org, repo, issue['number'],
                            comment=(
                                'This check is not applicable to '
                                'this project. Closing automatically.'
                            ),
                        )
                        tally.wrote(closed, org, repo,
                                    f'close #{issue["number"]}')
                        time.sleep(1)
            else:
                print(f'  [{check_id}] N/A')

        else:
            # 'error': the check raised, so nothing is known about the
            # repository either way. Its issues are left exactly as they
            # are -- closing one because the audit broke would read as
            # the repository having fixed it. Any other status is a
            # results file this script does not understand, which gets
            # the same answer for the same reason.
            print(f'  [{check_id}] {status.upper()} -- issues left alone')

    close_retired(github, org, repo, open_issues, dry_run, tally)
    return tally


def close_retired(github, org, repo, open_issues, dry_run, tally):
    """Close the issues of criteria the audit no longer measures.

    Run for every repository with results, on every run, rather than
    once: a repository whose audit leg failed on the run after a
    retirement still has its issues closed on a later one.
    """
    for title in retired_issue_titles():
        for issue in issues_titled(open_issues, title):
            print(f'  [retired] closing issue #{issue["number"]} ({title})')
            if not dry_run:
                closed = gh_close_issue(
                    github, org, repo, issue['number'],
                    comment=(
                        'The consistency audit no longer measures this '
                        'criterion: it has been retired. Closing '
                        'automatically.'
                    ),
                )
                tally.wrote(closed, org, repo, f'close #{issue["number"]}')
                time.sleep(1)


def main():
    parser = argparse.ArgumentParser(
        description='Manage GitHub issues from audit results',
    )
    parser.add_argument(
        '--results-dir', required=True,
        help='Directory containing JSON result files',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print actions without executing them',
    )
    args = parser.parse_args()

    if not os.path.isdir(args.results_dir):
        print(
            f'Error: {args.results_dir} is not a directory',
            file=sys.stderr,
        )
        sys.exit(1)

    # Load all result files
    result_files = sorted([
        f for f in os.listdir(args.results_dir)
        if f.endswith('.json')
    ])

    if not result_files:
        print('No JSON result files found.')
        sys.exit(0)

    github = GhCli()
    tally = Tally()
    for filename in result_files:
        filepath = os.path.join(args.results_dir, filename)
        with open(filepath, 'r') as f:
            results = json.load(f)
        process_results(results, dry_run=args.dry_run, github=github,
                        tally=tally)

    print('\nDone.')

    if tally.renamed:
        print(
            f'\n{len(tally.renamed)} repo(s) have been renamed on GitHub '
            f'but the audit still uses the old name (see warnings '
            f'above): {", ".join(tally.renamed)}. Issues were managed '
            f'under the canonical names, but the workflow matrix needs '
            f'updating. Failing so this gets fixed.',
            file=sys.stderr,
        )
    if tally.unlisted:
        print(
            f'\nThe open issues of {len(tally.unlisted)} repo(s) could '
            f'not be listed, so their issues were not managed this run: '
            f'{", ".join(tally.unlisted)}.',
            file=sys.stderr,
        )
    if tally.write_failures:
        print(
            f'\n{len(tally.write_failures)} issue write(s) failed: '
            f'{"; ".join(tally.write_failures)}.',
            file=sys.stderr,
        )
    if tally.failed():
        sys.exit(1)


if __name__ == '__main__':
    main()
