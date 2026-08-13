"""Shared metadata and helpers for the consistency audit scripts.

Imported by audit-manage-issues.py and audit-update-docs.py so that
the check-to-spec mapping and issue title conventions live in one
place.
"""

import json
import subprocess


# Map from check ID to the audit spec file and optional template.
AUDIT_METADATA = {
    'llm-tooling': {
        'spec': 'audits/llm-tooling.md',
        'template': None,
    },
    'release-process': {
        'spec': 'audits/release-process.md',
        'template': 'templates/release-automation/',
    },
    'ci-review-automation': {
        'spec': 'audits/ci-review-automation.md',
        'template': 'templates/ci-review-automation/',
    },
    'renovate': {
        'spec': 'audits/renovate.md',
        'template': 'templates/renovate/',
    },
    'pin-indirect-dependencies': {
        'spec': 'audits/pin-indirect-dependencies.md',
        'template': 'templates/pin-indirect-dependencies/',
    },
    'dependency-name-normalization': {
        'spec': 'audits/dependency-name-normalization.md',
        'template': None,
    },
    'export-repo-config': {
        'spec': 'audits/export-repo-config.md',
        'template': 'templates/export-repo-config/',
    },
    'default-branch-naming': {
        'spec': 'audits/default-branch-naming.md',
        'template': None,
    },
    'github-security': {
        'spec': 'audits/github-security.md',
        'template': 'templates/codeql/',
    },
    'delete-branch-on-merge': {
        'spec': 'audits/delete-branch-on-merge.md',
        'template': None,
    },
    'merge-queue-config': {
        'spec': 'audits/merge-queue-config.md',
        'template': None,
    },
    'workflow-permissions': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'pre-commit-config': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'review-marks-pre-commit': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'flake8wrap': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'self-hosted-runners': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'static-runner-tags': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'devpi-fallback': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'devpi-stale-ip': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'pyproject-usage': {
        'spec': 'audits/pyproject-usage.md',
        'template': None,
    },
    'version-file-gitignore': {
        'spec': 'audits/version-file-gitignore.md',
        'template': None,
    },
    'rust-unwrap-lint': {
        'spec': 'audits/rust-unwrap-lint.md',
        'template': None,
    },
    'readme-absolute-links': {
        'spec': 'audits/readme-absolute-links.md',
        'template': None,
    },
    'readme-structure': {
        'spec': 'audits/readme-structure.md',
        'template': None,
    },
    'plan-phase-references': {
        'spec': 'audits/plan-phase-references.md',
        'template': None,
    },
    'push-audit': {
        'spec': 'audits/push-audit.md',
        'template': 'templates/shared-blocks/',
    },
    'secret-scanning-ci': {
        'spec': 'audits/secret-handling.md',
        'template': None,
    },
    'review-coverage': {
        'spec': 'audits/review-coverage.md',
        'template': None,
    },
    'sfui-vendor': {
        'spec': 'audits/sfui-vendor.md',
        'template': None,
    },
}

# Map from check ID to issue title suffix. Must match existing
# manually created issues where possible.
ISSUE_TITLES = {
    'llm-tooling': 'LLM tooling',
    'release-process': 'Release process',
    'ci-review-automation': 'CI review automation',
    'renovate': 'Renovate',
    'pin-indirect-dependencies': 'Pin indirect dependencies',
    'dependency-name-normalization': 'Dependency name normalization',
    'export-repo-config': 'Export repo config',
    'default-branch-naming': 'Default branch naming',
    'github-security': 'GitHub security settings',
    'delete-branch-on-merge': 'Delete branch on merge',
    'merge-queue-config': 'Merge queue reasonability',
    'workflow-permissions': 'Workflow standards',
    'pre-commit-config': 'Workflow standards (linting)',
    'review-marks-pre-commit': 'Workflow standards (review marks)',
    'flake8wrap': 'Workflow standards (flake8wrap)',
    'self-hosted-runners': 'Workflow standards (self-hosted runners)',
    'static-runner-tags': 'Workflow standards (static runner tags)',
    'devpi-fallback': 'Workflow standards (devpi cache fallback)',
    'devpi-stale-ip': 'Workflow standards (devpi cache address)',
    'pyproject-usage': 'pyproject.toml usage',
    'version-file-gitignore': 'Generated version file',
    'rust-unwrap-lint': 'Rust unwrap lint',
    'readme-absolute-links': 'README absolute links',
    'readme-structure': 'README structure',
    'plan-phase-references': 'Plan phase references',
    'push-audit': 'Pre-push audit file',
    'secret-scanning-ci': 'Secret scanning in CI',
    'review-coverage': 'Human review coverage',
    'sfui-vendor': 'sfui vendored copy',
}


# Cache of configured-name to canonical-name lookups, so each repo
# costs at most one API call per run.
_CANONICAL_CACHE = {}


def gh_canonical_repo(org, repo):
    """Resolve a repository to its canonical org and name.

    When a repository is renamed on GitHub, REST calls (issue create,
    clone) follow the rename redirect but issue listing and search do
    not: they silently return no results for the old name. Operating
    on a stale name therefore creates a fresh issue on every run
    while never finding the previous one. Resolving the canonical
    name first makes every code path agree on which repo is meant.

    Returns an (org, repo) tuple. Falls back to the input values if
    the API call fails.
    """
    key = f'{org}/{repo}'
    if key not in _CANONICAL_CACHE:
        canonical = key
        try:
            result = subprocess.run(
                [
                    'gh', 'api', f'repos/{org}/{repo}',
                    '--jq', '.full_name',
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                canonical = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        _CANONICAL_CACHE[key] = canonical
    return tuple(_CANONICAL_CACHE[key].split('/', 1))


def gh_search_issues(org, repo, title_prefix, label='consistency'):
    """Search for open issues matching a title prefix and label."""
    org, repo = gh_canonical_repo(org, repo)
    try:
        result = subprocess.run(
            [
                'gh', 'issue', 'list',
                '--repo', f'{org}/{repo}',
                '--label', label,
                '--state', 'open',
                '--search', f'"{title_prefix}" in:title',
                '--json', 'number,title',
                '--limit', '10',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        issues = json.loads(result.stdout)
        # Filter to exact title match to avoid prefix collisions
        # (e.g. "Workflow standards" matching
        # "Workflow standards (flake8wrap)"). Sort oldest first so
        # callers treat the original issue as the survivor and any
        # later duplicates as disposable.
        return sorted(
            (i for i in issues if i['title'] == title_prefix),
            key=lambda i: i['number'],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError):
        return []
