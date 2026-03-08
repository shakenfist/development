#!/usr/bin/env python3
"""Check a cloned repository against Shaken Fist consistency audit criteria.

Usage:
    python audit-check.py --repo-path /tmp/clone --repo-name occystrap

Outputs JSON results to stdout.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


# Minimal hardcoded overrides for properties that cannot be detected
# from a clone alone.
REPO_OVERRIDES = {
    'imago': {'is_private': True},
    'cloudgood': {'is_docs_only': True},
}

# Map from check ID to the human-readable name used in issue titles.
# Must match the titles used in the manually created issues from phase 1.
CHECK_NAMES = {
    'llm-tooling': 'LLM tooling',
    'release-process': 'Release process',
    'ci-review-automation': 'CI review automation',
    'renovate': 'Renovate',
    'pin-indirect-dependencies': 'Pin indirect dependencies',
    'export-repo-config': 'Export repo config',
    'default-branch-naming': 'Default branch naming',
    'github-security': 'GitHub security settings',
    'workflow-permissions': 'Workflow standards',
    'pre-commit-config': 'Workflow standards',
    'flake8wrap': 'Workflow standards (flake8wrap)',
}


def detect_repo_properties(repo_path, repo_name):
    """Auto-detect repo type from files present."""
    overrides = REPO_OVERRIDES.get(repo_name, {})
    return {
        'has_pyproject_toml': os.path.exists(
            os.path.join(repo_path, 'pyproject.toml')
        ),
        'has_cargo_toml': (
            os.path.exists(os.path.join(repo_path, 'Cargo.toml'))
            or os.path.exists(os.path.join(repo_path, 'src', 'Cargo.toml'))
        ),
        'has_workflows_dir': os.path.exists(
            os.path.join(repo_path, '.github', 'workflows')
        ),
        'has_flake8wrap': os.path.exists(
            os.path.join(repo_path, 'tools', 'flake8wrap.sh')
        ),
        'is_private': overrides.get('is_private', False),
        'is_docs_only': overrides.get('is_docs_only', False),
    }


def check_file_exists(repo_path, path):
    """Check if a file exists relative to repo root."""
    return os.path.exists(os.path.join(repo_path, path))


def check_file_contains(repo_path, path, pattern):
    """Check if a file contains a regex pattern."""
    filepath = os.path.join(repo_path, path)
    if not os.path.exists(filepath):
        return False
    with open(filepath, 'r', errors='replace') as f:
        return bool(re.search(pattern, f.read()))


def list_workflow_files(repo_path):
    """List all .yml files in .github/workflows/."""
    workflows_dir = os.path.join(repo_path, '.github', 'workflows')
    if not os.path.isdir(workflows_dir):
        return []
    return [
        f for f in os.listdir(workflows_dir)
        if f.endswith('.yml') or f.endswith('.yaml')
    ]


def workflow_has_permissions(repo_path, workflow_file):
    """Check if a workflow file has a top-level permissions block.

    We do a simple line-based check rather than full YAML parsing to
    avoid a PyYAML dependency. A top-level permissions block is a line
    starting with 'permissions:' (no leading whitespace).
    """
    filepath = os.path.join(
        repo_path, '.github', 'workflows', workflow_file
    )
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            if line.startswith('permissions:'):
                return True
    return False


def any_workflow_contains(repo_path, pattern):
    """Check if any workflow file contains a regex pattern."""
    for wf in list_workflow_files(repo_path):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            if re.search(pattern, f.read()):
                return True
    return False


# --- Check functions ---
# Each returns a dict with: id, status (pass/fail/not_applicable), details


def check_llm_tooling(repo_path, props):
    """Check for AGENTS.md and ARCHITECTURE.md."""
    missing = []
    if not check_file_exists(repo_path, 'AGENTS.md'):
        missing.append('AGENTS.md')
    if not check_file_exists(repo_path, 'ARCHITECTURE.md'):
        missing.append('ARCHITECTURE.md')

    if missing:
        return {
            'id': 'llm-tooling',
            'status': 'fail',
            'details': f'Missing: {", ".join(missing)}',
            'missing': missing,
        }
    return {
        'id': 'llm-tooling',
        'status': 'pass',
        'details': 'AGENTS.md and ARCHITECTURE.md both exist',
    }


def check_release_process(repo_path, props):
    """Check release process compliance."""
    if not props['has_pyproject_toml']:
        return {
            'id': 'release-process',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    issues = []
    if check_file_exists(repo_path, 'release.sh'):
        issues.append('release.sh still exists (should be removed)')
    if check_file_exists(repo_path, 'requirements.txt'):
        issues.append('requirements.txt still exists (use pyproject.toml)')
    if not check_file_exists(
        repo_path, '.github/workflows/release.yml'
    ):
        issues.append('Missing .github/workflows/release.yml')
    if not check_file_exists(repo_path, 'RELEASE-SETUP.md'):
        issues.append('Missing RELEASE-SETUP.md')

    if issues:
        return {
            'id': 'release-process',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'release-process',
        'status': 'pass',
        'details': 'Release process is compliant',
    }


def check_ci_review_automation(repo_path, props):
    """Check for automated review and developer automation workflows."""
    if props['is_docs_only']:
        # cloudgood: only check for pr-re-review and pr-address-comments
        missing = []
        if not check_file_exists(
            repo_path, '.github/workflows/pr-re-review.yml'
        ):
            missing.append('pr-re-review.yml')
        if not check_file_exists(
            repo_path, '.github/workflows/pr-address-comments.yml'
        ):
            missing.append('pr-address-comments.yml')
        if missing:
            return {
                'id': 'ci-review-automation',
                'status': 'fail',
                'details': f'Missing workflows: {", ".join(missing)}',
                'missing': missing,
            }
        return {
            'id': 'ci-review-automation',
            'status': 'pass',
            'details': 'Developer automation workflows exist',
        }

    issues = []
    # Check developer automation workflows
    for wf in [
        'pr-re-review.yml',
        'pr-address-comments.yml',
        'pr-retest.yml',
    ]:
        if not check_file_exists(
            repo_path, f'.github/workflows/{wf}'
        ):
            issues.append(f'Missing {wf}')

    # Check that at least one workflow uses the shared review action
    if not any_workflow_contains(
        repo_path, r'review-pr-with-claude@main'
    ):
        issues.append(
            'No workflow uses shared action '
            'review-pr-with-claude@main'
        )

    if issues:
        return {
            'id': 'ci-review-automation',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'ci-review-automation',
        'status': 'pass',
        'details': (
            'Automated review and developer automation '
            'workflows are compliant'
        ),
    }


def check_renovate(repo_path, props):
    """Check for renovate workflow and config."""
    missing = []
    if not check_file_exists(
        repo_path, '.github/workflows/renovate.yml'
    ):
        missing.append('.github/workflows/renovate.yml')
    if not check_file_exists(repo_path, 'renovate.json'):
        missing.append('renovate.json')

    if missing:
        return {
            'id': 'renovate',
            'status': 'fail',
            'details': f'Missing: {", ".join(missing)}',
            'missing': missing,
        }
    return {
        'id': 'renovate',
        'status': 'pass',
        'details': 'Renovate workflow and config exist',
    }


def check_pin_indirect_deps(repo_path, props):
    """Check for indirect dependency pinning."""
    if not props['has_pyproject_toml']:
        return {
            'id': 'pin-indirect-dependencies',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    issues = []
    if not check_file_exists(
        repo_path,
        '.github/workflows/pin-indirect-dependencies.yml',
    ):
        issues.append(
            'Missing .github/workflows/'
            'pin-indirect-dependencies.yml'
        )
    if not check_file_contains(
        repo_path, 'pyproject.toml', r'END_OF_INDIRECT_DEPS'
    ):
        issues.append(
            'Missing # END_OF_INDIRECT_DEPS marker in '
            'pyproject.toml'
        )

    if issues:
        return {
            'id': 'pin-indirect-dependencies',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'pin-indirect-dependencies',
        'status': 'pass',
        'details': 'Indirect dependency pinning is configured',
    }


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


def check_default_branch(repo_path, props, repo_name, org):
    """Check default branch is 'develop' via GitHub API."""
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq', '.default_branch',
            ],
            capture_output=True, text=True, timeout=30,
        )
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

        # Exceptions: docs-only repos and actions repos may use main
        if props['is_docs_only']:
            return {
                'id': 'default-branch-naming',
                'status': 'not_applicable',
                'details': (
                    f'Docs-only repo (current: {branch}, '
                    f'exception allowed)'
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


def check_github_security(repo_path, props, repo_name, org):
    """Check GitHub security settings and CodeQL workflow."""
    issues = []

    # Check CodeQL workflow (file-based, not API)
    if props['is_private']:
        pass  # Private repos can't use CodeQL without GHAS
    elif props['is_docs_only']:
        pass  # No code to scan
    elif not check_file_exists(
        repo_path, '.github/workflows/codeql-analysis.yml'
    ):
        issues.append('Missing .github/workflows/codeql-analysis.yml')

    # Check Dependabot and secret scanning via API
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq', '.security_and_analysis',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                security = json.loads(result.stdout.strip())
                if security:
                    secret_scanning = security.get(
                        'secret_scanning', {}
                    )
                    if secret_scanning.get('status') != 'enabled':
                        issues.append('Secret scanning not enabled')

                    push_protection = security.get(
                        'secret_scanning_push_protection', {}
                    )
                    if push_protection.get('status') != 'enabled':
                        issues.append(
                            'Secret scanning push protection '
                            'not enabled'
                        )
            except json.JSONDecodeError:
                issues.append(
                    'Could not parse security settings response'
                )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append('Could not query GitHub API for security settings')

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


def check_workflow_permissions(repo_path, props):
    """Check all workflows have top-level permissions blocks."""
    if not props['has_workflows_dir']:
        return {
            'id': 'workflow-permissions',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'workflow-permissions',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    missing = [
        wf for wf in workflows
        if not workflow_has_permissions(repo_path, wf)
    ]

    if missing:
        return {
            'id': 'workflow-permissions',
            'status': 'fail',
            'details': (
                f'{len(missing)} workflow(s) missing top-level '
                f'permissions: {", ".join(sorted(missing))}'
            ),
            'missing': sorted(missing),
        }
    return {
        'id': 'workflow-permissions',
        'status': 'pass',
        'details': (
            f'All {len(workflows)} workflows have '
            f'top-level permissions'
        ),
    }


def check_pre_commit_config(repo_path, props):
    """Check for .pre-commit-config.yaml."""
    if not check_file_exists(repo_path, '.pre-commit-config.yaml'):
        return {
            'id': 'pre-commit-config',
            'status': 'fail',
            'details': 'Missing .pre-commit-config.yaml',
        }
    return {
        'id': 'pre-commit-config',
        'status': 'pass',
        'details': '.pre-commit-config.yaml exists',
    }


def check_flake8wrap(repo_path, props):
    """Check flake8wrap.sh for correct SC2086 handling."""
    if not props['has_flake8wrap']:
        return {
            'id': 'flake8wrap',
            'status': 'not_applicable',
            'details': 'No tools/flake8wrap.sh',
        }

    filepath = os.path.join(repo_path, 'tools', 'flake8wrap.sh')
    with open(filepath, 'r', errors='replace') as f:
        content = f.read()

    issues = []
    if 'SC2086' not in content:
        issues.append('Missing shellcheck disable=SC2086 directive')

    # Check for quoted ${filtered_files} on diff/flake8 lines
    # (the variable must NOT be quoted)
    for line in content.splitlines():
        if ('filtered_files' in line
                and ('diff' in line or 'FLAKE' in line or 'flake' in line)):
            if '"${filtered_files}"' in line:
                issues.append(
                    'filtered_files is incorrectly quoted '
                    'on diff/flake8 line'
                )
                break

    if issues:
        return {
            'id': 'flake8wrap',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'flake8wrap',
        'status': 'pass',
        'details': 'flake8wrap.sh has correct SC2086 handling',
    }


def run_all_checks(repo_path, repo_name, org):
    """Run all checks and return results."""
    props = detect_repo_properties(repo_path, repo_name)

    checks = [
        check_llm_tooling(repo_path, props),
        check_release_process(repo_path, props),
        check_ci_review_automation(repo_path, props),
        check_renovate(repo_path, props),
        check_pin_indirect_deps(repo_path, props),
        check_export_repo_config(repo_path, props),
        check_default_branch(repo_path, props, repo_name, org),
        check_github_security(repo_path, props, repo_name, org),
        check_workflow_permissions(repo_path, props),
        check_pre_commit_config(repo_path, props),
        check_flake8wrap(repo_path, props),
    ]

    summary = {
        'total': len(checks),
        'pass': sum(1 for c in checks if c['status'] == 'pass'),
        'fail': sum(1 for c in checks if c['status'] == 'fail'),
        'not_applicable': sum(
            1 for c in checks if c['status'] == 'not_applicable'
        ),
    }

    return {
        'repo': repo_name,
        'org': org,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'checks': checks,
        'summary': summary,
    }


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
