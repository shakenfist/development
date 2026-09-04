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
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.check import STATUSES  # noqa: E402
from audit.github import FakeGitHub  # noqa: E402
from audit.registry import CHECKS  # noqa: E402
from audit.repo import Repo  # noqa: E402
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
    'plan-audit-phase': {
        'spec': 'docs/audits/plan-audit-phase.md',
        'template': None,
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
    'unused-declared-dependency': {
        'spec': 'docs/audits/unused-declared-dependency.md',
        'template': None,
    },
    'undeclared-direct-dependency': {
        'spec': 'docs/audits/undeclared-direct-dependency.md',
        'template': None,
    },
    'renovate-lockstep-groups': {
        'spec': 'docs/audits/renovate-lockstep-groups.md',
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
    'scope-coverage': {
        'spec': 'docs/audits/scope-coverage.md',
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
    'unused-declared-dependency': 'Unused declared dependency',
    'undeclared-direct-dependency': 'Undeclared direct dependency',
    'renovate-lockstep-groups': 'Renovate lockstep groups',
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
    'plan-audit-phase': 'Push audit phase in master plans',
    'push-audit': 'Pre-push audit file',
    'plan-template': 'Plan template',
    'secret-scanning-ci': 'Secret scanning in CI',
    'review-coverage': 'Human review coverage',
    'review-scope-completeness': 'Human review scope completeness',
    'sfui-vendor': 'sfui vendored copy',
    'scope-coverage': 'Audit scope against the organisation',
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

    def test_no_criterion_is_registered_twice(self):
        """CHECKS is the whole schedule, so it is also the whole order.

        There used to be a second table, ORDER, pinning the sequence
        the results JSON reports in, and a test that the two listed the
        same ids. The registry is one list now and its order is the
        order, so the only way it can disagree with itself is by
        scheduling the same criterion twice -- which would report the
        check twice and double-count it in the summary.
        """
        ids = [check.id for check in CHECKS]
        self.assertEqual(sorted(ids), sorted(set(ids)),
                         'a criterion is registered twice in CHECKS')

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


class ContractTest(unittest.TestCase):
    """What must be true of every criterion, whoever writes the next one.

    These read the registry rather than any particular check, so a
    criterion added next year is held to them without anybody
    remembering to.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def repo(self, **props):
        detected = Repo(self.tmp.name, 'testrepo', 'shakenfist').props
        detected.update(props)
        return Repo(self.tmp.name, 'testrepo', 'shakenfist',
                    github=FakeGitHub(), props=detected)

    def results(self, repo):
        for check in CHECKS:
            reason = check.applies(repo)
            yield check, (check.skip(reason) if reason is not None
                          else check.run(repo))

    def test_every_check_is_registered_exactly_once(self):
        ids = [check.id for check in CHECKS]
        self.assertEqual(sorted(ids), sorted(set(ids)))

    def test_every_check_declares_an_id(self):
        for check in CHECKS:
            self.assertTrue(check.id, f'{type(check).__name__} has no id')

    def test_an_empty_directory_produces_a_valid_result_from_every_check(self):
        """The audit meets repositories it was not designed against.

        An empty directory is the cheapest stand-in for one: no git, no
        workflows, no pyproject. A criterion that raises here reports
        nothing about any of the others.
        """
        for check, result in self.results(self.repo()):
            with self.subTest(check=check.id):
                self.assertEqual(result['id'], check.id)
                self.assertIn(result['status'], STATUSES)
                self.assertTrue(result['details'],
                                f'{check.id} returned an empty details')

    def test_a_docs_only_repository_produces_a_valid_result(self):
        for check, result in self.results(self.repo(is_docs_only=True)):
            with self.subTest(check=check.id):
                self.assertIn(result['status'], STATUSES)

    def test_a_directory_that_is_not_a_checkout_produces_a_valid_result(self):
        """Several criteria shell out to git. None may raise when it fails."""
        for check, result in self.results(self.repo(not_python=True)):
            with self.subTest(check=check.id):
                self.assertIn(result['status'], STATUSES)

    def test_every_check_is_reachable_from_a_test_module(self):
        """The guard that stops the next criterion arriving untested.

        It asserts reachability, not coverage: that some module under
        scripts/tests/ instantiates the check. A class nobody exercises
        would still pass, and no cheap test can tell the difference.
        What it does catch is the common case -- a criterion added to
        the registry with no test module touching it at all, which is
        how seventeen of the forty-five came to have none.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        bodies = []
        for name in sorted(os.listdir(here)):
            if name.startswith('test_') and name.endswith('.py'):
                with open(os.path.join(here, name)) as f:
                    bodies.append(f.read())

        untested = []
        for check in CHECKS:
            name = type(check).__name__
            pattern = re.compile(
                rf'\.{name}\(|check_class = \w+\.{name}\b')
            if not any(pattern.search(body) for body in bodies):
                untested.append(check.id)
        self.assertEqual(untested, [])


if __name__ == '__main__':
    unittest.main()
