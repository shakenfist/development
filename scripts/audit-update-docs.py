#!/usr/bin/env python3
"""Regenerate the per-project compliance page from the audit results.

Reads the JSON results produced by audit-check.py and rewrites the
section between the consistency-audit markers in
docs/audits/compliance.md: one table per audit spec that has an
automated check, plus a closing list of the criteria that have none.

The tables used to live in the spec files themselves, one generated
block per criterion. They were moved here because the block opens
with a timestamp that changes on every run, and a whole-file human
review mark attests to content by blob SHA -- so a spec carrying a
generated block could never hold one, and the prose defining what we
audit for was excluded from review coverage as a result. Everything
under docs/audits/ is now hand-written except this one page. See
docs/plans/PLAN-audit-compliance-split.md.

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
    BEGIN_MARKER,
    END_MARKER,
    ISSUE_TITLES,
    gh_search_issues,
)

# The one generated file under docs/audits/. It is the only path in
# that directory excluded from human review in
# .vscode/review-scope.toml, and the only one this script writes.
COMPLIANCE_PAGE = 'docs/audits/compliance.md'

# The audits directory, for finding criteria that have no check.
AUDITS_DIR = 'docs/audits'

# Pages in AUDITS_DIR that are not criterion specs.
NOT_A_SPEC = ('README.md', os.path.basename(COMPLIANCE_PAGE))

# Column headings for specs covered by more than one check. Specs
# with a single check get a plain 'Status' column.
COLUMN_NAMES = {
    'workflow-permissions': 'Permissions',
    'pre-commit-config': 'Linting',
    'review-marks-pre-commit': 'Review marks',
    'flake8wrap': 'flake8wrap',
    'self-hosted-runners': 'Runners',
    'static-runner-tags': 'Static tags',
    'devpi-fallback': 'devpi fallback',
    'devpi-stale-ip': 'devpi IP',
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


def spec_anchor(spec):
    """The compliance page anchor for a spec file.

    The basename without its extension, which is also the check id for
    single-check specs. Keeping it derivable from the path is what lets
    each spec carry a static link to its own section, and lets
    test_audit_update_docs.py assert every one of those links resolves.
    """
    return os.path.basename(spec)[:-len('.md')]


def unmeasured_specs():
    """Criterion specs with no automated check, by anchor.

    Marker-block absence used to be how a reader told a measured
    criterion from one judged by a person: a spec with no check had no
    generated block. Consolidating the blocks onto one page took that
    tell away, so the page states the set instead, and computes it from
    the same AUDIT_METADATA the runner uses rather than from prose that
    would rot the first time one of them was automated.
    """
    if not os.path.isdir(AUDITS_DIR):
        return []
    measured = {spec_anchor(spec) for spec in checks_by_spec()}
    found = []
    for filename in sorted(os.listdir(AUDITS_DIR)):
        if not filename.endswith('.md') or filename in NOT_A_SPEC:
            continue
        anchor = filename[:-len('.md')]
        if anchor not in measured:
            found.append(anchor)
    return found


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


def column_name(check_id):
    """Column heading for a check in a multi-check spec.

    A check whose id is missing from COLUMN_NAMES falls back to the id
    itself, loudly. Adding a check to a multi-check spec means adding
    its heading here, and forgetting once already cost the fleet a
    day of tables: the KeyError this replaces was raised after every
    docs/audits/*.md had been rewritten but before any of them was
    committed, so one missing label stopped every project's table
    from publishing. A run that prints an ugly heading and a warning
    is a better failure than a run that silently publishes nothing.

    Consolidating onto one page raised that stake rather than lowering
    it. The whole fleet's compliance output is now a single write, so a
    raise here takes out every table at once instead of the tail of an
    alphabetical walk. test_audit_update_docs.py fails on the omission,
    so the fallback should never be reached in a run from a tested
    tree.
    """
    if check_id not in COLUMN_NAMES:
        print(
            f'warning: no COLUMN_NAMES heading for {check_id}; '
            f'using the check id',
            file=sys.stderr,
        )
    return COLUMN_NAMES.get(check_id, check_id)


def render_table(check_ids, results, no_issues):
    """Render the compliance table for one audit spec.

    Returns the table lines, followed by the per-project failure
    details when there are any. The surrounding heading, markers and
    generation note belong to render_page.
    """
    columns = (
        ['Status'] if len(check_ids) == 1
        else [column_name(c) for c in check_ids]
    )

    lines = [
        '| Project | ' + ' | '.join(columns) + ' | Issue |',
        '|---------|' + '--------|' * (len(columns) + 1),
    ]

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
                    else column_name(check_id)
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

    return lines


def render_page(results, no_issues):
    """Render the generated block of the whole compliance page."""
    timestamps = sorted(r['timestamp'] for r in results)
    when = f' {timestamps[-1]}' if timestamps else ''

    lines = [
        BEGIN_MARKER,
        f'*Generated{when} from `scripts/audit-check.py`; do not edit.*',
    ]

    for spec, check_ids in sorted(checks_by_spec().items()):
        anchor = spec_anchor(spec)
        lines.append('')
        lines.append(f'## {anchor}')
        lines.append('')
        lines.append(f'Criterion: [{anchor}.md]({anchor}.md)')
        lines.append('')
        lines.extend(render_table(check_ids, results, no_issues))

    unmeasured = unmeasured_specs()
    if unmeasured:
        lines.append('')
        lines.append('## Criteria with no automated check')
        lines.append('')
        lines.append(
            'These criteria are written down and judged by a person, so '
            'they have no table above. Each says why in its own page:'
        )
        lines.append('')
        for anchor in unmeasured:
            lines.append(f'- [{anchor}.md]({anchor}.md)')

    lines.append(END_MARKER)
    return '\n'.join(lines)


def update_compliance_page(page, section):
    """Replace the marker block in the compliance page.

    Returns True if the file was updated, False if the markers were
    not found.
    """
    with open(page) as f:
        content = f.read()

    begin = content.find(BEGIN_MARKER)
    end = content.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        return False

    updated = (
        content[:begin] + section + content[end + len(END_MARKER):]
    )
    if updated != content:
        with open(page, 'w') as f:
            f.write(updated)
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Regenerate the audit compliance page from results',
    )
    parser.add_argument(
        '--results-dir', required=True,
        help='Directory containing JSON result files',
    )
    parser.add_argument(
        '--no-issues', action='store_true',
        help='Skip GitHub issue lookups (for offline testing)',
    )
    parser.add_argument(
        '--page', default=COMPLIANCE_PAGE,
        help=f'Compliance page to rewrite (default: {COMPLIANCE_PAGE})',
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

    section = render_page(results, args.no_issues)
    if not update_compliance_page(args.page, section):
        # The markers are the only thing tying the generated block to
        # its page. Losing them silently would leave yesterday's
        # verdicts in place looking current, which is the failure the
        # generation timestamp exists to make visible.
        print(
            f'Error: no consistency-audit markers in {args.page}',
            file=sys.stderr,
        )
        sys.exit(1)
    print(f'Updated {args.page}')


if __name__ == '__main__':
    main()
