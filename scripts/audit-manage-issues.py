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


DEV_REPO_URL = (
    'https://github.com/shakenfist/development/blob/main'
)

# Map from check ID to the audit spec file and optional template.
AUDIT_METADATA = {
    'llm-tooling': {
        'spec': 'audits/llm-tooling.md',
        'template': None,
    },
    'release-process': {
        'spec': 'audits/release-process.md',
        'template': 'templates/release-automation/',
    },
    'ci-review-automation': {
        'spec': 'audits/ci-review-automation.md',
        'template': 'templates/ci-review-automation/',
    },
    'renovate': {
        'spec': 'audits/renovate.md',
        'template': 'templates/renovate/',
    },
    'pin-indirect-dependencies': {
        'spec': 'audits/pin-indirect-dependencies.md',
        'template': 'templates/pin-indirect-dependencies/',
    },
    'export-repo-config': {
        'spec': 'audits/export-repo-config.md',
        'template': 'templates/export-repo-config/',
    },
    'default-branch-naming': {
        'spec': 'audits/default-branch-naming.md',
        'template': None,
    },
    'github-security': {
        'spec': 'audits/github-security.md',
        'template': 'templates/codeql/',
    },
    'workflow-permissions': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'pre-commit-config': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'flake8wrap': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
}

# Map from check ID to issue title suffix. Must match existing
# manually created issues where possible.
ISSUE_TITLES = {
    'llm-tooling': 'LLM tooling',
    'release-process': 'Release process',
    'ci-review-automation': 'CI review automation',
    'renovate': 'Renovate',
    'pin-indirect-dependencies': 'Pin indirect dependencies',
    'export-repo-config': 'Export repo config',
    'default-branch-naming': 'Default branch naming',
    'github-security': 'GitHub security settings',
    'workflow-permissions': 'Workflow standards',
    'pre-commit-config': 'Workflow standards (linting)',
    'flake8wrap': 'Workflow standards (flake8wrap)',
}


def gh_search_issues(org, repo, title_prefix, label='consistency'):
    """Search for open issues matching a title prefix and label."""
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
        # "Workflow standards (flake8wrap)")
        return [
            i for i in issues
            if i['title'] == title_prefix
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError):
        return []


def gh_create_issue(org, repo, title, body, label='consistency'):
    """Create a new issue on the target repo."""
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


def process_results(results, dry_run=False):
    """Process audit results and manage issues."""
    org = results['org']
    repo = results['repo']

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
            else:
                print(f'  [{check_id}] FAIL -- creating issue')
                if not dry_run:
                    body = build_issue_body(check_id, check)
                    gh_create_issue(
                        org, repo, title_prefix, body,
                    )
                    time.sleep(1)  # Rate limiting

        elif status == 'pass':
            # Close any existing open issue
            existing = gh_search_issues(org, repo, title_prefix)
            if existing:
                print(
                    f'  [{check_id}] PASS -- closing issue '
                    f'#{existing[0]["number"]}'
                )
                if not dry_run:
                    gh_close_issue(
                        org, repo, existing[0]['number'],
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
            # Close any existing issue if one was opened
            existing = gh_search_issues(org, repo, title_prefix)
            if existing:
                print(
                    f'  [{check_id}] N/A -- closing issue '
                    f'#{existing[0]["number"]}'
                )
                if not dry_run:
                    gh_close_issue(
                        org, repo, existing[0]['number'],
                        comment=(
                            'This check is not applicable to '
                            'this project. Closing automatically.'
                        ),
                    )
                    time.sleep(1)
            else:
                print(f'  [{check_id}] N/A')


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

    for filename in result_files:
        filepath = os.path.join(args.results_dir, filename)
        with open(filepath, 'r') as f:
            results = json.load(f)
        process_results(results, dry_run=args.dry_run)

    print('\nDone.')


if __name__ == '__main__':
    main()
