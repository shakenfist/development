#!/usr/bin/env python3

"""Assert that an audit-check.py run actually measured something.

The consistency audit's failure modes are not all loud. A bare
`pip install` meeting PEP 668 fails the job outright, which is how the
fleet-wide breakage of August 2026 announced itself -- but a skillsaw
that goes missing more quietly does not fail anything at all.
`skillsaw_errors()` catches `FileNotFoundError` and returns `None`, and
`check_llm_context_lint()` renders that as `not_applicable`, which is
the same word the audit uses for "we decided this check does not apply
here". The compliance table then reports a considered exemption where
what actually happened is that the tool was not installed.

So the smoke job asserts the audit reached verdicts, not that the
verdicts are any particular value. Which checks fail against this
repository is not this script's business: development is exempt from
the audits and several checks fail here by design.

Usage: check-audit-smoke.py RESULTS.json
"""

import json
import sys


# Checks which must reach a real verdict for the run to count as having
# measured anything. llm-context-lint is here because it is the one that
# degrades silently when its external tool is missing; the others report
# not_applicable only for reasons visible in the repository itself.
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

    for check in checks:
        if not check.get('id'):
            problems.append('a check was reported with no id: %r' % (check,))
        elif not check.get('status'):
            problems.append(
                '%s returned no status, so it neither passed nor failed'
                % check['id'])

    by_id = {c.get('id'): c for c in checks}
    for check_id in MUST_BE_MEASURED:
        check = by_id.get(check_id)
        if check is None:
            problems.append(
                '%s did not run at all; it is meant to be measured on '
                'every audit' % check_id)
        elif check.get('status') == 'not_applicable':
            problems.append(
                '%s reported not_applicable (%s). In this smoke run that '
                'means its tooling is missing rather than that the check '
                'does not apply, and a missing tool reads as a deliberate '
                'exemption in the compliance table.'
                % (check_id, check.get('details', 'no details')))

    if problems:
        print('The audit ran but did not measure what it should have:',
              file=sys.stderr)
        for problem in problems:
            print('  - %s' % problem, file=sys.stderr)
        return 1

    print('Audit smoke run measured %d checks, %s among them.'
          % (len(checks), ', '.join(MUST_BE_MEASURED)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
