#!/usr/bin/env python3

"""Normalise and compare consistency audit result files.

The audit's own regression net. `audit-check.py` prints a verdict and a
details string per criterion, and both are published: the details reach
`docs/audits/compliance.md` and the bodies of issues filed fleet-wide.
So the test that a change to the checker is safe is not that the tests
pass -- it is that the checker says exactly what it said before about
the same repositories, byte for byte.

Snapshots are captured by `tools/audit-snapshot.sh` and are not
committed: generated JSON under scripts/ or docs/ would land in review
scope and sit permanently stale in the review queue.
"""

import argparse
import json
import os
import re
import sys


# Checks whose *verdict* may legitimately differ between two runs over
# the same tree, because they ask GitHub rather than the tree: a
# repository setting can change, or a token can expire, without
# anything in this repository moving. They are reported separately
# rather than ignored -- a difference here is not evidence of a
# regression, but it is not nothing either.
#
# This is not the same question as whether a *tool failure* in a check
# is transient; TRANSIENT_FAILURE_CHECKS below is that one, and it is
# the wider set. A criterion can read a remote without its verdict
# depending on one.
#
# Written by hand. NetworkCheckListTest in test_audit_snapshot.py
# re-derives it from the checks under audit/ -- every check that
# reaches the GitHub client (repo.github) or runs a git clone, directly
# or through a helper or base class -- and fails if the two disagree,
# so this list cannot quietly go stale as checks are added.
NETWORK_CHECKS = frozenset({
    'default-branch-naming',
    'github-security',
    'delete-branch-on-merge',
    'merge-queue-config',
    'release-environment-protection',
    'release-merge-queue',
    # Reads the review-marks environment and its branch policy, which
    # an operator can change between runs.
    'bypass-actor-identity',
    # Reads the default branch's effective rules and the enforcement
    # of each ruleset that contributed one, both of which an
    # operator can change between runs.
    'moving-ref-source-protection',
    'merge-group-cancellation',
    'sfui-vendor',
    # Reads run history, which moves every time a workflow fires.
    'scheduled-workflow-health',
    # The one that differs for a reason outside this repository
    # entirely: it lists the organisation, so somebody creating a
    # repository moves its verdict without a line here changing.
    'scope-coverage',
    # Reads each remote hook's manifest at its pinned rev, to learn
    # whether the hook is built with Node.
    'pre-commit-node-version',
})


# Checks whose tooling reaches a remote, so an `error` marked
# `tool_failure` from one is an outage rather than a broken check.
# Every NETWORK_CHECK qualifies, because asking GitHub is how it
# reaches its verdict at all.
#
# plan-retirement is here without being a NETWORK_CHECK, which is the
# distinction this set exists to draw. Its verdict comes from the tree
# and from this repository's own history, so it is held to the
# byte-for-byte comparison like any other local check. But its
# `git show` of a historical docs/plans/index.md is the audit
# package's only read of a blob out of history -- every other git call
# under audit/ is `ls-files`, `check-ignore`, `rev-parse`, a metadata
# `log`, or sfui-vendor's own clone -- and the consistency audit clones
# with `--filter=blob:none`, where reading such a blob fetches it from
# the promisor remote. A 502 or a reset between two legs of one run is
# therefore a failure here, and failing the leg for it holds back the
# compliance page for the whole fleet.
TRANSIENT_FAILURE_CHECKS = NETWORK_CHECKS | frozenset({'plan-retirement'})


def github_did_not_answer(check):
    """Is this result a network check whose tooling could not ask?

    An `error` marked `tool_failure` from a check in NETWORK_CHECKS is
    GitHub, or a clone from it, not answering: an outage, a rate limit,
    or no token at all. It is not a bug in the audit.

    This is the narrow question check-audit-smoke.py asks, because it
    runs without a token and its clone is an ordinary one: nothing it
    measures can want a blob a promisor remote has to supply, so a
    tool failure outside this set is a check that broke and the smoke
    run should say so. audit-raised.py asks the wider question below.
    """
    return (check.get('status') == 'error'
            and bool(check.get('tool_failure'))
            and check.get('id') in NETWORK_CHECKS)


def transient_tool_failure(check):
    """Is this result a tool failure a remote could have caused?

    The question audit-raised.py asks, which must not let one
    transient 502 hold back the compliance page for every repository
    in the fleet. A tool failure from a check outside this set is the
    runner's own git, skillsaw or review-tracking.py not answering,
    which the next run would meet too, and an unmarked error is a
    check that broke: both fail the leg.
    """
    return (check.get('status') == 'error'
            and bool(check.get('tool_failure'))
            and check.get('id') in TRANSIENT_FAILURE_CHECKS)


# Fields that move on every run regardless of what the checker does.
VOLATILE_FIELDS = ('timestamp',)


# An age in a details string, counted from the clock rather than from
# anything in the tree. plan-retirement says how long each plan's row
# has been terminal -- "at least 97 days" -- which is the number a
# person reading the issue needs in order to decide whether the
# finding is right, so it stays in what the check says. But it is a
# different number tomorrow for the same repository and the same code,
# and a comparison that counted that as a difference would report one
# for every repository with an eligible plan, which is the whole of
# the net this tool is. Normalised on both sides instead, so that what
# is compared is what the audit said and not when it said it.
CLOCK_AGE_RE = re.compile(r'at least \d+ days')
CLOCK_AGE_PLACEHOLDER = 'at least N days'
CLOCK_AGE_CHECK = 'plan-retirement'


def comparable_details(check):
    """A check's details with the ages that move with the clock fixed.

    Only for the check that emits them: the same words in another
    check's details would be text the comparison is meant to see.
    """
    details = check.get('details', '')
    if check.get('id') != CLOCK_AGE_CHECK:
        return details
    return CLOCK_AGE_RE.sub(CLOCK_AGE_PLACEHOLDER, details)


def normalise(results):
    """Strip the fields that move on every run.

    Returns a new dict; the input is not modified.
    """
    return {k: v for k, v in results.items() if k not in VOLATILE_FIELDS}


def load_snapshot(directory):
    """Read a snapshot directory into {repo_name: results}."""
    snapshot = {}
    for name in sorted(os.listdir(directory)):
        if not name.startswith('audit-result-') or not name.endswith('.json'):
            continue
        path = os.path.join(directory, name)
        with open(path, 'r') as f:
            results = json.load(f)
        snapshot[results.get('repo', name)] = results
    return snapshot


def checks_by_id(results):
    """Index a result document's checks by their id."""
    return {c['id']: c for c in results.get('checks', [])}


def compare_repo(old, new):
    """Compare one repository's results.

    Returns a list of (check_id, description) differences. A check that
    appears in only one of the two is itself a difference: the set of
    criteria is part of what the snapshot pins.
    """
    differences = []
    old_checks = checks_by_id(old)
    new_checks = checks_by_id(new)

    for check_id in sorted(set(old_checks) | set(new_checks)):
        before = old_checks.get(check_id)
        after = new_checks.get(check_id)

        if before is None:
            differences.append((check_id, 'not present before; now '
                                          f'{after["status"]}'))
            continue
        if after is None:
            differences.append((check_id, f'was {before["status"]}; '
                                          'no longer reported'))
            continue

        if before['status'] != after['status']:
            differences.append((
                check_id,
                f'status {before["status"]} -> {after["status"]}\n'
                f'      - {before.get("details", "")}\n'
                f'      + {after.get("details", "")}',
            ))
        elif comparable_details(before) != comparable_details(after):
            differences.append((
                check_id,
                f'details changed ({before["status"]} either way)\n'
                f'      - {before.get("details", "")}\n'
                f'      + {after.get("details", "")}',
            ))

    return differences


def report(old_dir, new_dir, stream=sys.stdout):
    """Print the differences between two snapshots.

    Returns the number of differences in checks that do not reach the
    network. Network checks are printed under their own heading and do
    not count, because a difference there is not evidence about the
    code.
    """
    old = load_snapshot(old_dir)
    new = load_snapshot(new_dir)

    hard = 0
    advisory = 0

    for repo in sorted(set(old) | set(new)):
        if repo not in old:
            print(f'{repo}: present only in {new_dir}', file=stream)
            hard += 1
            continue
        if repo not in new:
            print(f'{repo}: present only in {old_dir}', file=stream)
            hard += 1
            continue

        differences = compare_repo(old[repo], new[repo])
        firm = [d for d in differences if d[0] not in NETWORK_CHECKS]
        soft = [d for d in differences if d[0] in NETWORK_CHECKS]

        if firm:
            print(f'=== {repo} ===', file=stream)
            for check_id, description in firm:
                print(f'  {check_id}: {description}', file=stream)
            hard += len(firm)
        if soft:
            print(f'=== {repo} (advisory: reaches the network) ===',
                  file=stream)
            for check_id, description in soft:
                print(f'  {check_id}: {description}', file=stream)
            advisory += len(soft)

    print(file=stream)
    if hard:
        print(f'{hard} difference(s) in checks that do not reach the '
              'network.', file=stream)
    else:
        print('No differences in checks that do not reach the network.',
              file=stream)
    if advisory:
        print(f'{advisory} advisory difference(s); a repository setting '
              'may have changed between runs.', file=stream)

    return hard


def main():
    parser = argparse.ArgumentParser(
        description='Normalise or compare audit result snapshots',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    strip = subparsers.add_parser(
        'normalise', help='strip volatile fields from a result document',
    )
    strip.add_argument('path', help='audit-result-<repo>.json to rewrite')

    diff = subparsers.add_parser(
        'diff', help='compare two snapshot directories',
    )
    diff.add_argument('old', help='baseline snapshot directory')
    diff.add_argument('new', help='snapshot directory to compare')

    args = parser.parse_args()

    if args.command == 'normalise':
        with open(args.path, 'r') as f:
            results = json.load(f)
        with open(args.path, 'w') as f:
            json.dump(normalise(results), f, indent=2)
            f.write('\n')
        return 0

    return 1 if report(args.old, args.new) else 0


if __name__ == '__main__':
    sys.exit(main())
