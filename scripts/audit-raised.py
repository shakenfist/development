#!/usr/bin/env python3
"""Fail if any check in an audit results file raised rather than returned.

Usage:
    python audit-raised.py /tmp/audit-result-occystrap.json

`registry.run_check()` turns a check that raises into an `error` result,
so one broken criterion no longer costs a repository all the others.
That boundary would also make the bug quiet, so the consistency audit
workflow runs this after the results are uploaded: the leg fails, which
is what `report-failure` watches for, while `manage-issues` still gets
the artifact and manages every other criterion's issues from it.

Exits 0 when every result is a verdict, and 1 after naming each check
that raised.
"""

import argparse
import json
import sys

from audit import registry


def main():
    parser = argparse.ArgumentParser(
        description='Fail if any check in an audit results file raised'
    )
    parser.add_argument('results', help='A JSON file audit-check.py wrote')
    args = parser.parse_args()

    with open(args.results) as f:
        results = json.load(f)

    raised = registry.errored(results)
    for check in raised:
        print(f'{results["repo"]}: {check["id"]}: {check["details"]}',
              file=sys.stderr)
    if raised:
        print(f'{len(raised)} check(s) raised; the tracebacks are in the '
              f'audit step\'s log above.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
