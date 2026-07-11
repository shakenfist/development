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

from audit_common import (
    AUDIT_METADATA,
    ISSUE_TITLES,
    gh_canonical_repo,
    gh_search_issues,
)


DEV_REPO_URL = (
    'https://github.com/shakenfist/development/blob/main'
)


def gh_ensure_label(org, repo, label='consistency'):
    """Ensure the label exists on the target repo.

    gh issue create refuses to create an issue if the requested label
    does not exist, so create it first. --force makes this idempotent.
    """
    result = subprocess.run(
        [
            'gh', 'label', 'create', label,
            '--repo', f'{org}/{repo}',
            '--description', 'Project consistency audit item',
            '--color', '0075ca',
            '--force',
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(
            f'  WARNING: could not ensure label {label} exists: '
            f'{result.stderr.strip()}',
            file=sys.stderr,
        )


def gh_create_issue(org, repo, title, body, label='consistency'):
    """Create a new issue on the target repo."""
    gh_ensure_label(org, repo, label)
    result = subprocess.run(
        [
            'gh', 'issue', 'create',
            '--repo', f'{org}/{repo}',
            '--title', title,
            '--label', label,
            '--body', body,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        print(f'  Created issue: {url}')
        return url
    else:
        print(
            f'  ERROR creating issue: {result.stderr.strip()}',
            file=sys.stderr,
        )
        return None


def gh_close_issue(org, repo, issue_number, comment=None):
    """Close an issue with an optional comment."""
    if comment:
        subprocess.run(
            [
                'gh', 'issue', 'comment',
                '--repo', f'{org}/{repo}',
                str(issue_number),
                '--body', comment,
            ],
            capture_output=True, text=True, timeout=30,
        )
    result = subprocess.run(
        [
            'gh', 'issue', 'close',
            '--repo', f'{org}/{repo}',
            str(issue_number),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        print(f'  Closed issue #{issue_number}')
    else:
        print(
            f'  ERROR closing issue #{issue_number}: '
            f'{result.stderr.strip()}',
            file=sys.stderr,
        )


def build_issue_body(check_id, check_result):
    """Build the issue body for a failed check."""
    meta = AUDIT_METADATA.get(check_id, {})
    spec_file = meta.get('spec', f'audits/{check_id}.md')
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

    body += f'\n### Automated check details\n\n{check_result["details"]}\n'

    if 'missing' in check_result:
        body += '\n**Missing items:**\n'
        for item in check_result['missing']:
            body += f'- `{item}`\n'

    body += (
        '\n---\n'
        '*This issue was created automatically by the '
        'consistency audit workflow.*\n'
    )
    return body


def close_duplicates(org, repo, check_id, duplicates, original,
                     dry_run):
    """Close duplicate issues, pointing at the surviving original."""
    for dup in duplicates:
        print(
            f'  [{check_id}] closing duplicate issue '
            f'#{dup["number"]} (original is #{original["number"]})'
        )
        if not dry_run:
            gh_close_issue(
                org, repo, dup['number'],
                comment=(
                    f'Duplicate of #{original["number"]}. '
                    'Closing automatically.'
                ),
            )
            time.sleep(1)


def process_results(results, dry_run=False):
    """Process audit results and manage issues.

    Returns True if the configured repo name is stale (the repo has
    been renamed on GitHub) so the caller can fail the run visibly.
    """
    org = results['org']
    repo = results['repo']

    # A renamed repo must be handled under its canonical name:
    # issue search silently returns nothing for the old name, while
    # issue creation follows the rename redirect, so a stale name
    # files a fresh duplicate on every run.
    canonical_org, canonical_repo = gh_canonical_repo(org, repo)
    renamed = (canonical_org, canonical_repo) != (org, repo)
    if renamed:
        print(
            f'WARNING: {org}/{repo} has been renamed to '
            f'{canonical_org}/{canonical_repo}; update the matrix in '
            f'.github/workflows/consistency-audit.yml',
            file=sys.stderr,
        )
        org, repo = canonical_org, canonical_repo

    print(f'\n=== {repo} ===')
    print(
        f'Results: {results["summary"]["pass"]} pass, '
        f'{results["summary"]["fail"]} fail, '
        f'{results["summary"]["not_applicable"]} N/A'
    )

    for check in results['checks']:
        check_id = check['id']
        status = check['status']
        title_prefix = f'Consistency: {ISSUE_TITLES.get(check_id, check_id)}'

        if status == 'fail':
            # Search for existing open issue
            existing = gh_search_issues(org, repo, title_prefix)
            if existing:
                print(
                    f'  [{check_id}] FAIL -- issue '
                    f'#{existing[0]["number"]} already open'
                )
                close_duplicates(
                    org, repo, check_id, existing[1:], existing[0],
                    dry_run,
                )
            else:
                print(f'  [{check_id}] FAIL -- creating issue')
                if not dry_run:
                    body = build_issue_body(check_id, check)
                    gh_create_issue(
                        org, repo, title_prefix, body,
                    )
                    time.sleep(1)  # Rate limiting

        elif status == 'pass':
            # Close any existing open issues (all of them, in case
            # duplicates have accumulated)
            existing = gh_search_issues(org, repo, title_prefix)
            if existing:
                for issue in existing:
                    print(
                        f'  [{check_id}] PASS -- closing issue '
                        f'#{issue["number"]}'
                    )
                    if not dry_run:
                        gh_close_issue(
                            org, repo, issue['number'],
                            comment=(
                                'This check is now passing in the '
                                'automated consistency audit. '
                                'Closing automatically.'
                            ),
                        )
                        time.sleep(1)
            else:
                print(f'  [{check_id}] PASS')

        else:  # not_applicable
            # Close any existing issues if any were opened
            existing = gh_search_issues(org, repo, title_prefix)
            if existing:
                for issue in existing:
                    print(
                        f'  [{check_id}] N/A -- closing issue '
                        f'#{issue["number"]}'
                    )
                    if not dry_run:
                        gh_close_issue(
                            org, repo, issue['number'],
                            comment=(
                                'This check is not applicable to '
                                'this project. Closing automatically.'
                            ),
                        )
                        time.sleep(1)
            else:
                print(f'  [{check_id}] N/A')

    return renamed


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

    stale_names = False
    for filename in result_files:
        filepath = os.path.join(args.results_dir, filename)
        with open(filepath, 'r') as f:
            results = json.load(f)
        if process_results(results, dry_run=args.dry_run):
            stale_names = True

    print('\nDone.')

    if stale_names:
        print(
            '\nOne or more repos have been renamed on GitHub but the '
            'audit still uses the old name (see warnings above). '
            'Issues were managed under the canonical names, but the '
            'workflow matrix needs updating. Failing so this gets '
            'fixed.',
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
