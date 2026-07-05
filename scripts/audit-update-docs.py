#!/usr/bin/env python3
"""Regenerate the per-project compliance tables in audits/*.md.

Reads the JSON results produced by audit-check.py and rewrites the
section between the consistency-audit markers in each audit spec
file that has an automated check. The tables were previously
maintained by hand and drifted; this makes the daily audit run the
single source of truth.

Usage:
    python audit-update-docs.py --results-dir ./audit-results/

With --no-issues the GitHub issue column is omitted from lookups
(cells show '-'), which allows offline testing without `gh`.
"""

import argparse
import json
import os
import sys

from audit_common import (
    AUDIT_METADATA,
    ISSUE_TITLES,
    gh_search_issues,
)


BEGIN_MARKER = '<!-- consistency-audit:begin -->'
END_MARKER = '<!-- consistency-audit:end -->'

# Column headings for specs covered by more than one check. Specs
# with a single check get a plain 'Status' column.
COLUMN_NAMES = {
    'workflow-permissions': 'Permissions',
    'pre-commit-config': 'Linting',
    'flake8wrap': 'flake8wrap',
}

STATUS_LABELS = {
    'pass': 'compliant',
    'fail': 'non-compliant',
    'not_applicable': 'N/A',
}


def checks_by_spec():
    """Group check IDs by the audit spec file they belong to."""
    spec_map = {}
    for check_id, meta in AUDIT_METADATA.items():
        spec_map.setdefault(meta['spec'], []).append(check_id)
    return spec_map


def load_results(results_dir):
    """Load all per-repo result files, sorted by repo name."""
    results = []
    for filename in sorted(os.listdir(results_dir)):
        if not filename.endswith('.json'):
            continue
        with open(os.path.join(results_dir, filename)) as f:
            results.append(json.load(f))
    return sorted(results, key=lambda r: r['repo'])


def issue_cell(org, repo, check_ids, no_issues):
    """Build the issue-links cell for one repo row."""
    if no_issues:
        return '-'
    links = set()
    for check_id in check_ids:
        title = f'Consistency: {ISSUE_TITLES[check_id]}'
        for issue in gh_search_issues(org, repo, title):
            links.add(f'{org}/{repo}#{issue["number"]}')
    return ', '.join(sorted(links)) if links else '-'


def render_section(spec, check_ids, results, no_issues):
    """Render the generated block for one audit spec file."""
    columns = (
        ['Status'] if len(check_ids) == 1
        else [COLUMN_NAMES[c] for c in check_ids]
    )

    lines = [
        BEGIN_MARKER,
        '*This table is regenerated daily by the consistency audit',
        'workflow from `scripts/audit-check.py` results; do not edit',
        'it by hand.*',
        '',
    ]

    timestamps = sorted(r['timestamp'] for r in results)
    if timestamps:
        lines.append(f'Last regenerated: {timestamps[-1]}')
        lines.append('')

    lines.append('| Project | ' + ' | '.join(columns) + ' | Issue |')
    lines.append('|---------|' + '--------|' * (len(columns) + 1))

    failures = []
    for result in results:
        repo = result['repo']
        by_id = {c['id']: c for c in result['checks']}
        cells = []
        for check_id in check_ids:
            check = by_id.get(check_id)
            if check is None:
                cells.append('unknown')
                continue
            cells.append(STATUS_LABELS.get(check['status'], 'unknown'))
            if check['status'] == 'fail':
                column = (
                    'Status' if len(check_ids) == 1
                    else COLUMN_NAMES[check_id]
                )
                failures.append(
                    f'- **{repo}** ({column}): {check["details"]}'
                )
        issue = issue_cell(result['org'], repo, check_ids, no_issues)
        lines.append(
            f'| {repo} | ' + ' | '.join(cells) + f' | {issue} |'
        )

    if failures:
        lines.append('')
        lines.append('Details for non-compliant projects:')
        lines.append('')
        lines.extend(failures)

    lines.append(END_MARKER)
    return '\n'.join(lines)


def update_spec_file(spec, section):
    """Replace the marker block in one audit spec file.

    Returns True if the file was updated, False if the markers were
    not found.
    """
    with open(spec) as f:
        content = f.read()

    begin = content.find(BEGIN_MARKER)
    end = content.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        return False

    updated = (
        content[:begin] + section + content[end + len(END_MARKER):]
    )
    if updated != content:
        with open(spec, 'w') as f:
            f.write(updated)
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Regenerate audit compliance tables from results',
    )
    parser.add_argument(
        '--results-dir', required=True,
        help='Directory containing JSON result files',
    )
    parser.add_argument(
        '--no-issues', action='store_true',
        help='Skip GitHub issue lookups (for offline testing)',
    )
    args = parser.parse_args()

    if not os.path.isdir(args.results_dir):
        print(
            f'Error: {args.results_dir} is not a directory',
            file=sys.stderr,
        )
        sys.exit(1)

    results = load_results(args.results_dir)
    if not results:
        print('No JSON result files found.')
        sys.exit(0)

    missing_markers = []
    for spec, check_ids in sorted(checks_by_spec().items()):
        section = render_section(
            spec, check_ids, results, args.no_issues,
        )
        if update_spec_file(spec, section):
            print(f'Updated {spec}')
        else:
            missing_markers.append(spec)

    if missing_markers:
        for spec in missing_markers:
            print(
                f'Error: no consistency-audit markers in {spec}',
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == '__main__':
    main()
