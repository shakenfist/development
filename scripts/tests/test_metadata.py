#!/usr/bin/env python3

"""The derived metadata tables, pinned against what they used to be.

AUDIT_METADATA, ISSUE_TITLES and COLUMN_NAMES are views over
registry.CHECKS now rather than hand-written tables. That removes the
class of bug where a criterion is scheduled but missing from one of
them -- there is nothing left to be missing from -- and replaces it
with a smaller one: a class attribute edited carelessly changes a table
that reaches other people's repositories.

So the tables are frozen here as literals. A new criterion adds a line;
a changed line is supposed to be hard.

Run with: python3 -m unittest tests.test_metadata
"""

import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.registry import CHECKS  # noqa: E402
from audit_common import AUDIT_METADATA, ISSUE_TITLES  # noqa: E402
from tests.base import REPO_ROOT  # noqa: E402


def _update_docs():
    """Load audit-update-docs.py, whose hyphen makes it unimportable."""
    path = os.path.join(REPO_ROOT, 'scripts', 'audit-update-docs.py')
    spec = importlib.util.spec_from_file_location('audit_update_docs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The tables as they stood when they became derived views, on
#: 2026-09-01. Frozen deliberately.
#:
#: ISSUE_TITLES is the idempotency key for filing and closing:
#: audit-manage-issues.py finds a check's open issue by its title,
#: so a renamed entry orphans every open issue for that criterion
#: across the fleet. Deriving the table from class attributes put
#: 45 of those strings within reach of a careless edit, and this
#: is what makes such an edit fail a test rather than reach a
#: morning run. A new criterion adds a line here; a changed line
#: is supposed to be hard.
FROZEN_METADATA = {
    'llm-tooling': {'spec': 'docs/audits/llm-tooling.md', 'template': None},
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
    'readme-structure': {
        'spec': 'docs/audits/readme-structure.md',
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
    'diagram-format': {
        'spec': 'docs/audits/diagram-format.md',
        'template': None,
    },
    'mermaid-lint-ci': {
        'spec': 'docs/audits/mermaid-lint-ci.md',
        'template': 'templates/mermaid-lint/',
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
    'release-process': {
        'spec': 'docs/audits/release-process.md',
        'template': 'templates/release-automation/',
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
    'vm-runner-size': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'ci-review-automation': {
        'spec': 'docs/audits/ci-review-automation.md',
        'template': 'templates/ci-review-automation/',
    },
    'workflow-permissions': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'pre-commit-config': {
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
    'secret-scanning-ci': {
        'spec': 'docs/audits/secret-handling.md',
        'template': None,
    },
    'review-marks-pre-commit': {
        'spec': 'docs/audits/workflow-standards.md',
        'template': None,
    },
    'review-coverage': {
        'spec': 'docs/audits/review-coverage.md',
        'template': None,
    },
    'review-scope-completeness': {
        'spec': 'docs/audits/review-scope-completeness.md',
        'template': None,
    },
    'sfui-vendor': {'spec': 'docs/audits/sfui-vendor.md', 'template': None},
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
}

FROZEN_ISSUE_TITLES = {
    'llm-tooling': 'LLM tooling',
    'llm-doc-structure': 'AGENTS.md / ARCHITECTURE.md structure',
    'llm-context-lint': 'LLM context linting',
    'llm-context-lint-ci': 'LLM context linting in pre-commit and CI',
    'diagram-format': 'Diagram format',
    'mermaid-lint-ci': 'Mermaid diagrams linted in CI',
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
    'vm-runner-size': 'Workflow standards (vm runner size)',
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
    'review-scope-completeness': 'Human review scope completeness',
    'sfui-vendor': 'sfui vendored copy',
}

FROZEN_COLUMN_NAMES = {
    'workflow-permissions': 'Permissions',
    'pre-commit-config': 'Linting',
    'review-marks-pre-commit': 'Review marks',
    'flake8wrap': 'flake8wrap',
    'self-hosted-runners': 'Runners',
    'static-runner-tags': 'Static tags',
    'vm-runner-size': 'VM size',
    'devpi-fallback': 'devpi fallback',
    'devpi-stale-ip': 'devpi IP',
}


class FrozenMetadataTest(unittest.TestCase):
    def test_audit_metadata_matches_the_frozen_table(self):
        self.assertEqual(AUDIT_METADATA, FROZEN_METADATA)

    def test_issue_titles_match_the_frozen_table(self):
        """A renamed title orphans every open issue for that criterion."""
        self.assertEqual(ISSUE_TITLES, FROZEN_ISSUE_TITLES)

    def test_column_names_match_the_frozen_table(self):
        self.assertEqual(_update_docs().COLUMN_NAMES, FROZEN_COLUMN_NAMES)


class DerivationTest(unittest.TestCase):
    """What the derivation guarantees that the old tables did not."""

    def test_every_registered_check_appears_in_every_table(self):
        ids = {check.id for check in CHECKS}
        self.assertEqual(set(AUDIT_METADATA), ids)
        self.assertEqual(set(ISSUE_TITLES), ids)

    def test_every_check_declares_a_spec_and_an_issue_title(self):
        for check in CHECKS:
            self.assertTrue(check.spec, f'{check.id} has no spec')
            self.assertTrue(check.issue_title,
                            f'{check.id} has no issue title')

    def test_every_spec_file_exists(self):
        missing = [
            check.id for check in CHECKS
            if not os.path.exists(os.path.join(REPO_ROOT, check.spec))
        ]
        self.assertEqual(missing, [])

    def test_every_named_template_exists(self):
        missing = [
            check.id for check in CHECKS
            if check.template
            and not os.path.exists(os.path.join(REPO_ROOT, check.template))
        ]
        self.assertEqual(missing, [])

    def test_criteria_sharing_a_spec_page_all_declare_a_column(self):
        """The step that broke the 2026-08-12 run, made structural.

        A criterion that shares its specification with another cannot
        be labelled from the spec name alone, so it needs a column
        heading. It is a class attribute now, which means it travels
        with the check rather than waiting in a second table.
        """
        by_spec = {}
        for check in CHECKS:
            by_spec.setdefault(check.spec, []).append(check)

        missing = [
            check.id for checks in by_spec.values() if len(checks) > 1
            for check in checks if not check.column
        ]
        self.assertEqual(missing, [])

    def test_issue_titles_are_unique(self):
        """Two criteria sharing a title would close each other's issues."""
        titles = [check.issue_title for check in CHECKS]
        self.assertEqual(len(titles), len(set(titles)))


if __name__ == '__main__':
    unittest.main()
