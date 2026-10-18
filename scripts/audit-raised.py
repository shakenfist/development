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

One kind of error does not fail the leg: a network check that could
not ask GitHub (audit_snapshot.github_did_not_answer). A failed leg
holds back the compliance page for the whole fleet, and one transient
502 or rate limit in one repository is not worth that. The error
already leaves that criterion's issue alone, and the page shows the
cell as a check error saying what could not be asked. Each one is
printed as a workflow warning so the run still shows it. A token that
has expired or lost access does not get here: the leg's *Check the read
token* step fails before anything is measured.

Exits 0 when every result is a verdict or a network check GitHub did
not answer, and 1 after naming each check that raised.
"""

import argparse
import json
import sys

from audit import registry
from audit_snapshot import github_did_not_answer


def workflow_command_data(text):
    """Escape text for the message of a GitHub Actions workflow command.

    The runner decodes these three, so a literal one would otherwise
    truncate or mangle the annotation.
    """
    return text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def main():
    parser = argparse.ArgumentParser(
        description='Fail if any check in an audit results file raised'
    )
    parser.add_argument('results', help='A JSON file audit-check.py wrote')
    args = parser.parse_args()

    with open(args.results) as f:
        results = json.load(f)

    errors = registry.errored(results)
    for check in errors:
        if github_did_not_answer(check):
            print(f'::warning::{workflow_command_data(results["repo"])}: '
                  f'{check["id"]}: {workflow_command_data(check["details"])}')

    raised = [c for c in errors if not github_did_not_answer(c)]
    for check in raised:
        print(f'{results["repo"]}: {check["id"]}: {check["details"]}',
              file=sys.stderr)
    if raised:
        print(f'{len(raised)} check(s) raised; the tracebacks are in the '
              f'audit step\'s log above.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
