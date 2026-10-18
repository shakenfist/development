#!/usr/bin/env python3

"""Assert that an audit-check.py run actually measured something.

The consistency audit's failure modes are not all loud. A bare
`pip install` meeting PEP 668 fails the job outright, which is how the
fleet-wide breakage of August 2026 announced itself. A skillsaw that
goes missing is quieter: `llm-context-lint` reports `error`, which
fails nothing until the next scheduled run fails every leg at once.

So the smoke job asserts the audit reached verdicts, not that the
verdicts are any particular value. A check that raised has not reached
one: `registry.run_check()` reports it as `error` so that the other
checks still run, and the scheduled workflow fails the leg on it with
audit-raised.py. Accepting it here would let a pull request that
introduces a raising check pass CI and fail the next scheduled run
instead, which is the failure this job exists to move earlier.

With one exception. This job runs without a GitHub token, so the
checks that ask GitHub cannot, and say so: an `error` marked
`tool_failure` from a check in NETWORK_CHECKS is expected here and is
listed rather than failed. Only that combination is excused -- an
unmarked error is a check that broke, and a tool failure anywhere
else is this runner's git or skillsaw not answering, which the
scheduled run would meet too.

Which checks fail against this repository is not this script's
business: development is exempt from the audits and several checks
fail here by design.

Usage: check-audit-smoke.py RESULTS.json
"""

import json
import sys

from audit_snapshot import NETWORK_CHECKS


# Checks which must reach a real verdict for the run to count as having
# measured anything. llm-context-lint is here because it is the one that
# depends on a tool installed for the audit rather than one every runner
# has; it reports not_applicable only for want of agent context, which
# this repository has.
MUST_BE_MEASURED = ('llm-context-lint',)


def main(argv):
    if len(argv) != 2:
        print('Usage: check-audit-smoke.py RESULTS.json', file=sys.stderr)
        return 2

    try:
        with open(argv[1]) as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print('The audit did not produce parseable JSON: %s' % e,
              file=sys.stderr)
        return 1

    checks = report.get('checks')
    if not checks:
        print('The audit produced no checks at all.', file=sys.stderr)
        return 1

    problems = []
    unasked = []

    for check in checks:
        if not check.get('id'):
            problems.append('a check was reported with no id: %r' % (check,))
        elif not check.get('status'):
            problems.append(
                '%s returned no status, so it neither passed nor failed'
                % check['id'])
        elif (check['status'] == 'error' and check.get('tool_failure')
                and check['id'] in NETWORK_CHECKS):
            unasked.append(check['id'])
        elif check['status'] == 'error':
            problems.append(
                '%s raised rather than reaching a verdict: %s'
                % (check['id'], check.get('details', 'no details')))

    by_id = {c.get('id'): c for c in checks}
    for check_id in MUST_BE_MEASURED:
        check = by_id.get(check_id)
        if check is None:
            problems.append(
                '%s did not run at all; it is meant to be measured on '
                'every audit' % check_id)
        elif check.get('status') == 'not_applicable':
            problems.append(
                '%s reported not_applicable (%s). This repository has '
                'the agent context it lints, so the check should have '
                'run.'
                % (check_id, check.get('details', 'no details')))

    if problems:
        print('The audit ran but did not measure what it should have:',
              file=sys.stderr)
        for problem in problems:
            print('  - %s' % problem, file=sys.stderr)
        return 1

    print('Audit smoke run measured %d checks, %s among them.'
          % (len(checks) - len(unasked), ', '.join(MUST_BE_MEASURED)))
    if unasked:
        print('Not measured without a GitHub token, as expected: %s.'
              % ', '.join(sorted(unasked)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
