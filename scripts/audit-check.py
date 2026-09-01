#!/usr/bin/env python3
"""Check a cloned repository against Shaken Fist consistency audit criteria.

Usage:
    python audit-check.py --repo-path /tmp/clone --repo-name occystrap

Outputs JSON results to stdout.

The criteria themselves live in the audit package beside this file:
audit/checks/ holds them grouped the way their specifications are, and
audit/registry.py is the schedule. See docs/consistency-audits.md for
what a criterion is and how to add one.
"""

import argparse
import json
import os
import sys

from audit import registry
from audit.repo import REPO_OVERRIDES, Repo, detect_repo_properties

# REPO_OVERRIDES and detect_repo_properties live in audit/repo.py and
# are re-exported here because callers and tests reach for them on this
# module. Kept until there is a reason to move the name as well as the
# code.
__all__ = ['REPO_OVERRIDES', 'detect_repo_properties', 'run_all_checks']


def run_all_checks(repo_path, repo_name, org, github=None):
    """Run every check against a clone and return the results document.

    Keeps its name and signature because check-audit-smoke.py, ci.yml
    and the tests all drive it.
    """
    return registry.run_all(Repo(repo_path, repo_name, org, github=github))


def main():
    parser = argparse.ArgumentParser(
        description='Check a repo against consistency audit criteria'
    )
    parser.add_argument(
        '--repo-path', required=True,
        help='Path to the cloned repository',
    )
    parser.add_argument(
        '--repo-name', required=True,
        help='Repository name (e.g. occystrap)',
    )
    parser.add_argument(
        '--github-org', default='shakenfist',
        help='GitHub organization (default: shakenfist)',
    )
    args = parser.parse_args()

    if not os.path.isdir(args.repo_path):
        print(
            f'Error: {args.repo_path} is not a directory',
            file=sys.stderr,
        )
        sys.exit(1)

    results = run_all_checks(
        args.repo_path, args.repo_name, args.github_org,
    )
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
