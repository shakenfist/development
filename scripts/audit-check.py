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
    'cloudgood': {'is_docs_only': True},
    # kerbside-patches carries Python helper scripts but is a patch
    # archive, not a Python project.
    'kerbside-patches': {'not_python': True},
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
    'self-hosted-runners': 'Workflow standards (self-hosted runners)',
    'version-file-gitignore': 'Generated version file',
    'pyproject-usage': 'pyproject.toml usage',
    'rust-unwrap-lint': 'Rust unwrap lint',
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
        'not_python': overrides.get('not_python', False),
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


def toml_section_has_key(content, section, key_pattern):
    """Check a TOML section contains a key matching a regex.

    We do a simple line-based scan rather than full TOML parsing to
    avoid a dependency. A section is a line consisting of the exact
    header (e.g. '[lints]'); the section ends at the next header.
    """
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            in_section = (stripped == f'[{section}]')
            continue
        if in_section and re.match(key_pattern, stripped):
            return True
    return False


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

    # Fetch visibility and security settings in one API call.
    # Visibility is queried live rather than hardcoded because repos
    # change visibility over time and a stale override would silently
    # skip the CodeQL check.
    is_private = props['is_private']
    security = None
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq',
                '{private: .private, security: .security_and_analysis}',
            ],
            capture_output=True, text=True, timeout=30,
        )
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


# GitHub-hosted runner labels (e.g. ubuntu-latest, windows-2022,
# macos-15, ubuntu-24.04-arm). Self-hosted runner labels never use
# these names.
GITHUB_HOSTED_LABEL_RE = re.compile(
    r'\b(?:ubuntu|windows|macos)-(?:latest|\d+(?:\.\d+)?)'
    r'(?:-(?:arm|arm64|large|xlarge))?\b'
)

# Marker acknowledging a deliberate exception, placed on the
# offending line or the line immediately above it.
RUNNER_EXCEPTION_RE = re.compile(r'audit-ok:\s*github-hosted-runner')


def check_self_hosted_runners(repo_path, props):
    """Check workflows use self-hosted runners.

    GitHub-provided runner minutes are limited per month, so jobs
    must run on self-hosted runners except under exceptional
    circumstances (e.g. Windows or macOS builds needing hardware we
    don't own). Exceptions are marked with an
    'audit-ok: github-hosted-runner' comment on the offending line
    or the line immediately above it.

    We scan every workflow line for GitHub-hosted runner labels
    rather than just runs-on lines, so matrix values that feed
    'runs-on: ${{ matrix.os }}' are caught too.
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'self-hosted-runners',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'self-hosted-runners',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    offenders = []
    for wf in sorted(workflows):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            lines = f.read().splitlines()

        for i, line in enumerate(lines):
            match = GITHUB_HOSTED_LABEL_RE.search(line)
            if not match:
                continue
            if 'self-hosted' in line:
                continue
            if RUNNER_EXCEPTION_RE.search(line):
                continue
            if i > 0 and RUNNER_EXCEPTION_RE.search(lines[i - 1]):
                continue
            offenders.append(f'{wf}:{i + 1} ({match.group(0)})')

    if offenders:
        return {
            'id': 'self-hosted-runners',
            'status': 'fail',
            'details': (
                f'{len(offenders)} unmarked GitHub-hosted runner '
                f'reference(s): {", ".join(offenders)}. Move to a '
                f'self-hosted runner, or mark deliberate exceptions '
                f'with an "audit-ok: github-hosted-runner" comment'
            ),
        }
    return {
        'id': 'self-hosted-runners',
        'status': 'pass',
        'details': (
            f'No unmarked GitHub-hosted runner references in '
            f'{len(workflows)} workflow(s)'
        ),
    }


def check_pyproject_usage(repo_path, props):
    """Check Python projects use pyproject.toml for packaging.

    Any project with Python code must have a pyproject.toml, and
    must not carry legacy packaging files (setup.py, setup.cfg)
    alongside it.
    """
    if props['is_docs_only']:
        return {
            'id': 'pyproject-usage',
            'status': 'not_applicable',
            'details': 'Docs-only repo',
        }
    if props['has_cargo_toml']:
        return {
            'id': 'pyproject-usage',
            'status': 'not_applicable',
            'details': 'Rust project (any Python is helper scripts)',
        }
    if props['not_python']:
        return {
            'id': 'pyproject-usage',
            'status': 'not_applicable',
            'details': 'Not a Python project (per overrides)',
        }

    if props['has_pyproject_toml']:
        legacy = [
            f for f in ['setup.py', 'setup.cfg']
            if check_file_exists(repo_path, f)
        ]
        if legacy:
            return {
                'id': 'pyproject-usage',
                'status': 'fail',
                'details': (
                    f'Legacy packaging files exist alongside '
                    f'pyproject.toml: {", ".join(legacy)}'
                ),
            }
        return {
            'id': 'pyproject-usage',
            'status': 'pass',
            'details': (
                'pyproject.toml exists with no legacy '
                'packaging files'
            ),
        }

    # No pyproject.toml: only a problem if there is Python code.
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'ls-files', '--', '*.py'],
            capture_output=True, text=True, timeout=30,
        )
        python_files = [
            line for line in result.stdout.splitlines()
            if line.strip()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'pyproject-usage',
            'status': 'fail',
            'details': f'Could not run git ls-files: {e}',
        }

    if python_files:
        return {
            'id': 'pyproject-usage',
            'status': 'fail',
            'details': (
                f'{len(python_files)} Python file(s) but no '
                f'pyproject.toml'
            ),
        }
    return {
        'id': 'pyproject-usage',
        'status': 'not_applicable',
        'details': 'No Python code',
    }


def check_version_file(repo_path, props):
    """Check generated version files are gitignored and not tracked.

    setuptools_scm writes a version file (usually _version.py) at
    build time. That file must never be committed: it should be
    covered by .gitignore and must not be tracked by git.
    """
    if not props['has_pyproject_toml']:
        return {
            'id': 'version-file-gitignore',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    pyproject = os.path.join(repo_path, 'pyproject.toml')
    with open(pyproject, 'r', errors='replace') as f:
        content = f.read()

    match = re.search(
        r'^\s*(?:write_to|version_file|version-file)\s*=\s*'
        r'["\']([^"\']+)["\']',
        content, re.MULTILINE,
    )

    issues = []

    # A tracked generated version file is always wrong, whether or
    # not we can work out the configured path.
    try:
        result = subprocess.run(
            [
                'git', '-C', repo_path,
                'ls-files', '--', '*_version.py',
            ],
            capture_output=True, text=True, timeout=30,
        )
        tracked = [
            line for line in result.stdout.splitlines()
            if line.strip()
        ]
        if tracked:
            issues.append(
                f'Generated version file tracked in git '
                f'(use git rm --cached): {", ".join(sorted(tracked))}'
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        issues.append(f'Could not run git ls-files: {e}')

    if not match:
        if issues:
            return {
                'id': 'version-file-gitignore',
                'status': 'fail',
                'details': '; '.join(issues),
            }
        return {
            'id': 'version-file-gitignore',
            'status': 'not_applicable',
            'details': (
                'No generated version file configured in '
                'pyproject.toml'
            ),
        }

    version_file = match.group(1)
    try:
        result = subprocess.run(
            [
                'git', '-C', repo_path,
                # --no-index so a tracked copy doesn't mask the
                # .gitignore coverage answer.
                'check-ignore', '-q', '--no-index', version_file,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 1:
            issues.append(
                f'{version_file} is not covered by .gitignore'
            )
        elif result.returncode != 0:
            issues.append(
                f'Could not check .gitignore coverage: '
                f'{result.stderr.strip()}'
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        issues.append(f'Could not run git check-ignore: {e}')

    if issues:
        return {
            'id': 'version-file-gitignore',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'version-file-gitignore',
        'status': 'pass',
        'details': (
            f'{version_file} is gitignored and no generated '
            f'version file is tracked'
        ),
    }


def check_rust_unwrap_lint(repo_path, props):
    """Check Rust projects enable clippy's unwrap_used lint.

    The root Cargo.toml must set unwrap_used to warn or deny (under
    [workspace.lints.clippy], or [lints.clippy] for single-crate
    repos), a clippy.toml must exempt test code with
    allow-unwrap-in-tests, and every first-party crate manifest must
    either inherit the workspace lints or define the lint itself.
    Fuzz harness crates are exempt.
    """
    if not props['has_cargo_toml']:
        return {
            'id': 'rust-unwrap-lint',
            'status': 'not_applicable',
            'details': 'No Cargo.toml (not a Rust project)',
        }

    if check_file_exists(repo_path, 'Cargo.toml'):
        root_manifest = 'Cargo.toml'
    else:
        root_manifest = 'src/Cargo.toml'
    root_dir = os.path.dirname(root_manifest)

    # Accepts unwrap_used = "warn", "deny", or the table form
    # { level = "warn", priority = -1 }.
    lint_pattern = r'unwrap_used\s*=\s*.*"(warn|deny)"'

    issues = []

    with open(
        os.path.join(repo_path, root_manifest), 'r', errors='replace'
    ) as f:
        root_content = f.read()
    if not (
        toml_section_has_key(
            root_content, 'workspace.lints.clippy', lint_pattern
        )
        or toml_section_has_key(
            root_content, 'lints.clippy', lint_pattern
        )
    ):
        issues.append(
            f'clippy unwrap_used lint not set to warn or deny '
            f'in {root_manifest}'
        )

    clippy_toml = os.path.join(root_dir, 'clippy.toml')
    if not check_file_contains(
        repo_path, clippy_toml,
        r'(?m)^\s*allow-unwrap-in-tests\s*=\s*true',
    ):
        issues.append(
            f'{clippy_toml} missing allow-unwrap-in-tests = true'
        )

    # Every other first-party crate manifest must inherit the
    # workspace lints or define the lint itself.
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'ls-files', '--', '*Cargo.toml'],
            capture_output=True, text=True, timeout=30,
        )
        manifests = [
            line for line in result.stdout.splitlines()
            if line.strip()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'rust-unwrap-lint',
            'status': 'fail',
            'details': f'Could not run git ls-files: {e}',
        }

    for manifest in manifests:
        if manifest == root_manifest:
            continue
        if 'fuzz' in manifest.split('/'):
            continue
        with open(
            os.path.join(repo_path, manifest), 'r', errors='replace'
        ) as f:
            content = f.read()
        if '[package]' not in content:
            continue
        inherits = (
            toml_section_has_key(
                content, 'lints', r'workspace\s*=\s*true'
            )
            or re.search(
                r'^\s*lints\.workspace\s*=\s*true', content,
                re.MULTILINE,
            )
        )
        defines = toml_section_has_key(
            content, 'lints.clippy', lint_pattern
        )
        if not (inherits or defines):
            issues.append(
                f'{manifest} neither inherits workspace lints '
                f'([lints] workspace = true) nor defines '
                f'unwrap_used itself'
            )

    if issues:
        return {
            'id': 'rust-unwrap-lint',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'rust-unwrap-lint',
        'status': 'pass',
        'details': (
            'clippy unwrap_used lint is enabled with '
            'allow-unwrap-in-tests'
        ),
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
        check_self_hosted_runners(repo_path, props),
        check_pyproject_usage(repo_path, props),
        check_version_file(repo_path, props),
        check_rust_unwrap_lint(repo_path, props),
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
