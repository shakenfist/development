#!/usr/bin/env python3
"""Check a cloned repository against Shaken Fist consistency audit criteria.

Usage:
    python audit-check.py --repo-path /tmp/clone --repo-name occystrap

Outputs JSON results to stdout.
"""

import argparse
import json
import os
import subprocess
import sys

from audit import registry
from audit.files import check_file_exists
from audit.github import GhCli
from audit.repo import REPO_OVERRIDES, Repo, detect_repo_properties

# REPO_OVERRIDES and detect_repo_properties now live in audit/repo.py
# and are re-exported here: test_audit_check.py and several callers
# reach for them on this module, and moving the code is a separate
# question from moving the name.
__all__ = ['REPO_OVERRIDES', 'detect_repo_properties']


def _github(client):
    """The GitHub client to use, defaulting to the real one.

    Threaded through the checks that query the API rather than reached
    for globally, so a test can substitute audit.github.FakeGitHub.
    """
    return client if client is not None else GhCli()


def check_export_repo_config(repo_path, props):
    """Check for repo config export workflow."""
    if not check_file_exists(
        repo_path, '.github/workflows/export-repo-config.yml'
    ):
        return {
            'id': 'export-repo-config',
            'status': 'fail',
            'details': 'Missing .github/workflows/export-repo-config.yml',
        }
    return {
        'id': 'export-repo-config',
        'status': 'pass',
        'details': 'export-repo-config.yml exists',
    }


def check_default_branch(repo_path, props, repo_name, org, github=None):
    """Check default branch is 'develop' via GitHub API."""
    try:
        result = _github(github).api(
            f'repos/{org}/{repo_name}', jq='.default_branch')
        if result.returncode != 0:
            return {
                'id': 'default-branch-naming',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API: '
                    f'{result.stderr.strip()}'
                ),
            }
        branch = result.stdout.strip()

        # Exceptions: docs-only repos, and repositories carrying a
        # documented reason in REPO_OVERRIDES, may use main
        if props['is_docs_only']:
            return {
                'id': 'default-branch-naming',
                'status': 'not_applicable',
                'details': (
                    f'Docs-only repo (current: {branch}, '
                    f'exception allowed)'
                ),
            }

        if props['default_branch_exception']:
            return {
                'id': 'default-branch-naming',
                'status': 'not_applicable',
                'details': (
                    f'Exempt: {props["default_branch_exception"]} '
                    f'(current: {branch})'
                ),
            }

        if branch != 'develop':
            return {
                'id': 'default-branch-naming',
                'status': 'fail',
                'details': (
                    f'Default branch is "{branch}", '
                    f'expected "develop"'
                ),
            }
        return {
            'id': 'default-branch-naming',
            'status': 'pass',
            'details': 'Default branch is "develop"',
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'default-branch-naming',
            'status': 'fail',
            'details': f'Error checking default branch: {e}',
        }


def check_github_security(repo_path, props, repo_name, org, github=None):
    """Check GitHub security settings and CodeQL workflow."""
    issues = []

    # Fetch visibility and security settings in one API call.
    # Visibility is queried live rather than hardcoded because repos
    # change visibility over time and a stale override would silently
    # skip the CodeQL check.
    is_private = props['is_private']
    security = None
    try:
        result = _github(github).api(
            f'repos/{org}/{repo_name}',
            jq='{private: .private, security: .security_and_analysis}')
        if result.returncode == 0 and result.stdout.strip():
            try:
                repo_info = json.loads(result.stdout.strip())
                is_private = repo_info.get('private', is_private)
                security = repo_info.get('security')
            except json.JSONDecodeError:
                issues.append(
                    'Could not parse security settings response'
                )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append('Could not query GitHub API for security settings')

    # Check CodeQL workflow (file-based, not API)
    if is_private:
        pass  # Private repos can't use CodeQL without GHAS
    elif props['is_docs_only']:
        pass  # No code to scan
    elif not check_file_exists(
        repo_path, '.github/workflows/codeql-analysis.yml'
    ):
        issues.append('Missing .github/workflows/codeql-analysis.yml')

    if security:
        secret_scanning = security.get('secret_scanning', {})
        if secret_scanning.get('status') != 'enabled':
            issues.append('Secret scanning not enabled')

        push_protection = security.get(
            'secret_scanning_push_protection', {}
        )
        if push_protection.get('status') != 'enabled':
            issues.append(
                'Secret scanning push protection not enabled'
            )

    if issues:
        return {
            'id': 'github-security',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'github-security',
        'status': 'pass',
        'details': 'Security settings and CodeQL are compliant',
    }


def check_delete_branch_on_merge(repo_path, props, repo_name, org,
                                 github=None):
    """Check head branches are deleted automatically when a PR merges."""
    try:
        result = _github(github).api(
            f'repos/{org}/{repo_name}', jq='.delete_branch_on_merge')
        if result.returncode != 0:
            return {
                'id': 'delete-branch-on-merge',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API: '
                    f'{result.stderr.strip()}'
                ),
            }
        setting = result.stdout.strip()

        if setting == 'true':
            return {
                'id': 'delete-branch-on-merge',
                'status': 'pass',
                'details': 'Delete branch on merge is enabled',
            }
        if setting == 'false':
            return {
                'id': 'delete-branch-on-merge',
                'status': 'fail',
                'details': 'Delete branch on merge is not enabled',
            }
        # The API omits this field (returns null) when the token
        # lacks push access to the repository.
        return {
            'id': 'delete-branch-on-merge',
            'status': 'fail',
            'details': (
                f'Could not determine delete branch on merge setting '
                f'(API returned "{setting or "null"}"; the token may '
                f'lack push access)'
            ),
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'delete-branch-on-merge',
            'status': 'fail',
            'details': f'Error checking delete branch on merge: {e}',
        }


def evaluate_merge_queue_rules(rules):
    """Evaluate effective branch rules for merge queue reasonability.

    Takes the rule list returned by the
    /repos/{org}/{repo}/rules/branches/{branch} endpoint. Returns a
    list of problem strings (empty when compliant), or None when no
    merge queue rule is present.

    The expectations encode two mechanics that are easy to get wrong
    (learned on shakenfist/shakenfist, August 2026):

    * max_entries_to_build > 1 enables speculative stacking: entry
      N+1 builds on top of entry N, so any failure ahead of it
      ejects that work and rebuilds the group on a new SHA. On CI
      that fails under cluster load, the speculative builds both
      waste runs (entries observed rebuilding five times in a day)
      and add the load that causes the failures.
    * min_entries_to_merge > 1 makes the queue idle for up to
      min_entries_to_merge_wait_minutes hoping to batch merges, but
      batching saves no CI (the queue builds one merge group and one
      CI run per entry regardless of how merges are batched), so it
      is pure latency. With min_entries_to_merge = 1 the wait timer
      never engages.
    """
    merge_queue = [r for r in rules if r.get('type') == 'merge_queue']
    if not merge_queue:
        return None

    problems = []
    for rule in merge_queue:
        params = rule.get('parameters') or {}

        build = params.get('max_entries_to_build')
        if build != 1:
            problems.append(
                f'max_entries_to_build is {build}, expected 1: '
                f'speculative stacked builds are ejected and rebuilt '
                f'whenever an entry ahead of them fails, wasting CI '
                f'and adding load'
            )

        min_merge = params.get('min_entries_to_merge')
        if min_merge != 1:
            problems.append(
                f'min_entries_to_merge is {min_merge}, expected 1: '
                f'waiting to batch merges adds up to the configured '
                f'wait time to every merge and saves no CI, which '
                f'runs once per queue entry regardless'
            )
    return problems


def check_merge_queue_config(repo_path, props, repo_name, org,
                             github=None):
    """Check any merge queue on the default branch is serialized."""
    client = _github(github)
    try:
        result = client.api(
            f'repos/{org}/{repo_name}', jq='.default_branch')
        if result.returncode != 0:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API for the default '
                    f'branch: {result.stderr.strip()}'
                ),
            }
        branch = result.stdout.strip()

        result = client.api(
            f'repos/{org}/{repo_name}/rules/branches/{branch}')
        if result.returncode != 0:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API for branch rules: '
                    f'{result.stderr.strip()}'
                ),
            }
        try:
            rules = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': 'Could not parse branch rules response',
            }

        problems = evaluate_merge_queue_rules(rules)
        if problems is None:
            return {
                'id': 'merge-queue-config',
                'status': 'not_applicable',
                'details': (
                    f'No merge queue on default branch "{branch}"'
                ),
            }
        if problems:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': '; '.join(problems),
            }
        return {
            'id': 'merge-queue-config',
            'status': 'pass',
            'details': (
                f'Merge queue on "{branch}" is serialized '
                f'(max_entries_to_build 1, min_entries_to_merge 1)'
            ),
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'merge-queue-config',
            'status': 'fail',
            'details': f'Error checking merge queue config: {e}',
        }


# Diagram format: a picture of structure or flow belongs in a mermaid
# fence, where GitHub and mkdocs both render it. See
# docs/audits/diagram-format.md, and
# templates/shared-blocks/diagram-discipline.md for the policy the
# push-audit reviewer applies to a diff.


def check_calls(repo_path, props, repo_name, org, github=None):
    """Pair every check id with the call that produces it.

    The calls are deferred so that a repository scoped with
    only_checks can skip a check without paying for it first: several
    checks query the GitHub API, and on a private repository some of
    those queries fail for reasons that have nothing to do with the
    repository's compliance.

    The id written here must be the id the check returns.
    test_audit_check.py asserts that for every entry, so a check that
    renames its id cannot silently become unschedulable.
    """
    return [
        ('export-repo-config',
         lambda: check_export_repo_config(repo_path, props)),
        ('default-branch-naming',
         lambda: check_default_branch(
             repo_path, props, repo_name, org, github)),
        ('github-security',
         lambda: check_github_security(
             repo_path, props, repo_name, org, github)),
        ('delete-branch-on-merge',
         lambda: check_delete_branch_on_merge(
             repo_path, props, repo_name, org, github)),
        ('merge-queue-config',
         lambda: check_merge_queue_config(
             repo_path, props, repo_name, org, github)),
    ]


def run_all_checks(repo_path, repo_name, org, github=None):
    """Run every check against a clone and return the results document.

    The scheduling, the only_checks scoping and the summary now live in
    audit/registry.py. This keeps its name and signature because
    check-audit-smoke.py, ci.yml and the tests all drive it.
    """
    repo = Repo(repo_path, repo_name, org, github=github)
    return registry.run_all(
        repo,
        legacy=check_calls(repo_path, repo.props, repo_name, org,
                           repo.github),
    )


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
