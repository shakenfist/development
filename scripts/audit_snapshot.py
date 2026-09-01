#!/usr/bin/env python3

"""Normalise and compare consistency audit result files.

The audit's own regression net. `audit-check.py` prints a verdict and a
details string per criterion, and both are published: the details reach
`docs/audits/compliance.md` and the bodies of issues filed fleet-wide.
So the test that a change to the checker is safe is not that the tests
pass -- it is that the checker says exactly what it said before about
the same repositories, byte for byte.

Snapshots are captured by `tools/audit-snapshot.sh` and are not
committed; see the plan in docs/plans/ for why.
"""

import argparse
import json
import os
import sys


# Checks that reach the network, and so may legitimately differ between
# two runs over the same tree: a repository setting can change, or a
# token can expire, without anything in this repository moving. They are
# reported separately rather than ignored -- a difference here is not
# evidence of a regression, but it is not nothing either.
#
# Derived by hand from the enclosing function of every 'gh' and 'git
# clone' invocation in scripts/audit-check.py. test_audit_snapshot.py
# re-derives it from the source and fails if the two disagree, so this
# list cannot quietly go stale as checks are added.
NETWORK_CHECKS = frozenset({
    'default-branch-naming',
    'github-security',
    'delete-branch-on-merge',
    'merge-queue-config',
    'merge-group-cancellation',
    'sfui-vendor',
})

# Fields that move on every run regardless of what the checker does.
VOLATILE_FIELDS = ('timestamp',)


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
        elif before.get('details') != after.get('details'):
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
