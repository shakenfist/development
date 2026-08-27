"""Shared metadata and helpers for the consistency audit scripts.

Imported by audit-manage-issues.py and audit-update-docs.py so that
the check-to-spec mapping and issue title conventions live in one
place.
"""

import json
import subprocess


# The markers delimiting the generated compliance tables on
# docs/audits/compliance.md. audit-update-docs.py writes them; audit-check.py reads them so
# that its documentation checks do not judge generated content. Defined
# here because a writer and a reader that disagree about the marker is
# the failure that silently exempted half of two plan files.
BEGIN_MARKER = '<!-- consistency-audit:begin -->'
END_MARKER = '<!-- consistency-audit:end -->'


# Map from check ID to the audit spec file and optional template.
AUDIT_METADATA = {
    'llm-tooling': {
        'spec': 'docs/audits/llm-tooling.md',
        'template': None,
    },
    'llm-doc-structure': {
        'spec': 'docs/audits/llm-doc-structure.md',
        'template': None,
    },
    'llm-context-lint': {
        'spec': 'docs/audits/llm-context-lint.md',
        'template': None,
    },
    'llm-context-lint-ci': {
        'spec': 'docs/audits/llm-context-lint-ci.md',
        'template': None,
    },
    'release-process': {
        'spec': 'docs/audits/release-process.md',
        'template': 'templates/release-automation/',
    },
    'ci-review-automation': {
        'spec': 'docs/audits/ci-review-automation.md',
        'template': 'templates/ci-review-automation/',
    },
    'renovate': {
        'spec': 'docs/audits/renovate.md',
        'template': 'templates/renovate/',
    },
    'pin-indirect-dependencies': {
        'spec': 'docs/audits/pin-indirect-dependencies.md',
        'template': 'templates/pin-indirect-dependencies/',
    },
    'dependency-name-normalization': {
        'spec': 'docs/audits/dependency-name-normalization.md',
        'template': None,
    },
    'export-repo-config': {
        'spec': 'docs/audits/export-repo-config.md',
        'template': 'templates/export-repo-config/',
    },
    'default-branch-naming': {
        'spec': 'docs/audits/default-branch-naming.md',
        'template': None,
    },
    'github-security': {
        'spec': 'docs/audits/github-security.md',
        'template': 'templates/codeql/',
    },
    'delete-branch-on-merge': {
        'spec': 'docs/audits/delete-branch-on-merge.md',
        'template': None,
    },
    'merge-queue-config': {
        'spec': 'docs/audits/merge-queue-config.md',
        'template': None,
    },
    'workflow-permissions': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'pre-commit-config': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'review-marks-pre-commit': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'flake8wrap': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'self-hosted-runners': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'static-runner-tags': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'devpi-fallback': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'devpi-stale-ip': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'expensive-lane-path-filter': {
        'spec': 'docs/audits/expensive-lane-path-filter.md',
        'template': None,
    },
    'merge-group-cancellation': {
        'spec': 'docs/audits/merge-group-cancellation.md',
        'template': None,
    },
    'pyproject-usage': {
        'spec': 'docs/audits/pyproject-usage.md',
        'template': None,
    },
    'version-file-gitignore': {
        'spec': 'docs/audits/version-file-gitignore.md',
        'template': None,
    },
    'console-logging': {
        'spec': 'docs/audits/console-logging.md',
        'template': None,
    },
    'header-sanitization': {
        'spec': 'docs/audits/security-sanitization.md',
        'template': None,
    },
    'python-version-targeting': {
        'spec': 'docs/audits/python-version.md',
        'template': None,
    },
    'rust-unwrap-lint': {
        'spec': 'docs/audits/rust-unwrap-lint.md',
        'template': None,
    },
    'readme-absolute-links': {
        'spec': 'docs/audits/readme-absolute-links.md',
        'template': None,
    },
    'docs-external-links': {
        'spec': 'docs/audits/docs-external-links.md',
        'template': None,
    },
    'readme-structure': {
        'spec': 'docs/audits/readme-structure.md',
        'template': None,
    },
    'plan-phase-references': {
        'spec': 'docs/audits/plan-phase-references.md',
        'template': None,
    },
    'plan-source-references': {
        'spec': 'docs/audits/plan-source-references.md',
        'template': None,
    },
    'plan-index': {
        'spec': 'docs/audits/plan-index.md',
        'template': 'templates/shared-blocks/',
    },
    'push-audit': {
        'spec': 'docs/audits/push-audit.md',
        'template': 'templates/shared-blocks/',
    },
    'plan-template': {
        'spec': 'docs/audits/plan-template.md',
        'template': 'templates/shared-blocks/',
    },
    'secret-scanning-ci': {
        'spec': 'docs/audits/secret-handling.md',
        'template': None,
    },
    'review-coverage': {
        'spec': 'docs/audits/review-coverage.md',
        'template': None,
    },
    'sfui-vendor': {
        'spec': 'docs/audits/sfui-vendor.md',
        'template': None,
    },
}

# Map from check ID to issue title suffix. Must match existing
# manually created issues where possible.
ISSUE_TITLES = {
    'llm-tooling': 'LLM tooling',
    'llm-doc-structure': 'AGENTS.md / ARCHITECTURE.md structure',
    'llm-context-lint': 'LLM context linting',
    'llm-context-lint-ci': 'LLM context linting in pre-commit and CI',
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
    'expensive-lane-path-filter': 'Expensive lane path filtering',
    'merge-group-cancellation': 'Merge group run cancellation',
    'pyproject-usage': 'pyproject.toml usage',
    'version-file-gitignore': 'Generated version file',
    'console-logging': 'Console script logging setup',
    'header-sanitization': 'HTTP header sanitization',
    'python-version-targeting': 'Python version targeting',
    'rust-unwrap-lint': 'Rust unwrap lint',
    'readme-absolute-links': 'README absolute links',
    'docs-external-links': 'Links out of docs/ are absolute',
    'readme-structure': 'README structure',
    'plan-phase-references': 'Plan phase references',
    'plan-source-references': 'Plan references in source',
    'plan-index': 'Plan index',
    'push-audit': 'Pre-push audit file',
    'plan-template': 'Plan template',
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
