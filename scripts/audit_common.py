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
    'workflow-permissions': {
        'spec': 'audits/workflow-standards.md',
        'template': None,
    },
    'pre-commit-config': {
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
    'pyproject-usage': {
        'spec': 'audits/pyproject-usage.md',
        'template': None,
    },
    'version-file-gitignore': {
        'spec': 'audits/version-file-gitignore.md',
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
    'export-repo-config': 'Export repo config',
    'default-branch-naming': 'Default branch naming',
    'github-security': 'GitHub security settings',
    'workflow-permissions': 'Workflow standards',
    'pre-commit-config': 'Workflow standards (linting)',
    'flake8wrap': 'Workflow standards (flake8wrap)',
    'self-hosted-runners': 'Workflow standards (self-hosted runners)',
    'pyproject-usage': 'pyproject.toml usage',
    'version-file-gitignore': 'Generated version file',
}


def gh_search_issues(org, repo, title_prefix, label='consistency'):
    """Search for open issues matching a title prefix and label."""
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
        # "Workflow standards (flake8wrap)")
        return [
            i for i in issues
            if i['title'] == title_prefix
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError):
        return []
