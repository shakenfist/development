"""What checks exist, and the order they run in.

The registry is the schedule. A check that is not in `CHECKS` does not
run, and -- once the migration finishes -- a check that is in it needs
nothing else registered anywhere: its specification path and its issue
title are attributes of the class, and `audit_common` derives its
tables from here rather than repeating them.

During the migration `CHECKS` is populated one family at a time and
the scheduler also runs whatever is left in `audit-check.py`'s
`check_calls()`. That hybrid is a working state, which is exactly why
the plan says the migration phase is not finished until the legacy
half is empty.
"""

from datetime import datetime, timezone

from audit.check import NOT_APPLICABLE
from audit.checks import ci_workflows, docs_content, llm_docs, packaging, plans, review, runners


#: The order the criteria are reported in. Pinned here because the
#: results JSON is a published artifact and its ordering is part of its
#: bytes: while the migration runs, some checks come from CHECKS and
#: some from the legacy table, and without a declared order the JSON
#: would reshuffle every time a family moved.
ORDER = [
    'llm-tooling',
    'llm-doc-structure',
    'llm-context-lint',
    'llm-context-lint-ci',
    'release-process',
    'ci-review-automation',
    'renovate',
    'pin-indirect-dependencies',
    'dependency-name-normalization',
    'export-repo-config',
    'default-branch-naming',
    'github-security',
    'delete-branch-on-merge',
    'merge-queue-config',
    'workflow-permissions',
    'pre-commit-config',
    'review-marks-pre-commit',
    'flake8wrap',
    'self-hosted-runners',
    'static-runner-tags',
    'vm-runner-size',
    'devpi-fallback',
    'devpi-stale-ip',
    'expensive-lane-path-filter',
    'merge-group-cancellation',
    'pyproject-usage',
    'version-file-gitignore',
    'console-logging',
    'header-sanitization',
    'python-version-targeting',
    'rust-unwrap-lint',
    'readme-absolute-links',
    'docs-external-links',
    'readme-structure',
    'plan-phase-references',
    'diagram-format',
    'mermaid-lint-ci',
    'plan-source-references',
    'plan-index',
    'push-audit',
    'plan-template',
    'secret-scanning-ci',
    'review-coverage',
    'review-scope-completeness',
    'sfui-vendor',
]


#: Every criterion, as instances. Populated family by family.
CHECKS = [
    llm_docs.LlmTooling(),
    llm_docs.LlmDocStructure(),
    llm_docs.LlmContextLint(),
    llm_docs.LlmContextLintCi(),
    docs_content.ReadmeStructure(),
    docs_content.ReadmeAbsoluteLinks(),
    docs_content.DocsExternalLinks(),
    docs_content.DiagramFormat(),
    docs_content.MermaidLintCi(),
    plans.PlanPhaseReferences(),
    plans.PlanSourceReferences(),
    plans.PlanIndex(),
    plans.PushAudit(),
    plans.PlanTemplate(),
    packaging.ReleaseProcess(),
    packaging.Renovate(),
    packaging.PinIndirectDependencies(),
    packaging.DependencyNameNormalization(),
    packaging.PyprojectUsage(),
    packaging.VersionFileGitignore(),
    packaging.ConsoleLogging(),
    packaging.HeaderSanitization(),
    packaging.PythonVersionTargeting(),
    packaging.RustUnwrapLint(),
    packaging.Flake8Wrap(),
    runners.SelfHostedRunners(),
    runners.StaticRunnerTags(),
    runners.VmRunnerSize(),
    ci_workflows.CiReviewAutomation(),
    ci_workflows.WorkflowPermissions(),
    ci_workflows.PreCommitConfig(),
    ci_workflows.DevpiFallback(),
    ci_workflows.DevpiStaleIp(),
    ci_workflows.ExpensiveLanePathFilter(),
    ci_workflows.MergeGroupCancellation(),
    ci_workflows.SecretScanningCi(),
    review.ReviewMarksPreCommit(),
    review.ReviewCoverage(),
    review.ReviewScopeCompleteness(),
    review.SfuiVendor(),
]


def scheduled(checks=None, legacy=None):
    """Pair every check id with the call that produces its result.

    Returns a list of (check_id, callable). The calls are deferred so
    that a repository scoped by only_checks can skip a check without
    paying for it first.
    """
    pairs = [(check.id, check) for check in (checks
                                             if checks is not None
                                             else CHECKS)]
    pairs += list(legacy or [])

    position = {check_id: n for n, check_id in enumerate(ORDER)}
    return sorted(pairs, key=lambda pair: position.get(pair[0], len(ORDER)))


def run_check(entry, repo):
    """Run one scheduled entry against a repository.

    An entry is either a Check -- which is asked whether it applies
    before it is asked to run -- or a zero-argument callable left over
    from the legacy table.
    """
    if callable(entry) and not hasattr(entry, 'run'):
        return entry()

    reason = entry.applies(repo)
    if reason is not None:
        return entry.skip(reason)
    return entry.run(repo)


def run_all(repo, legacy=None, checks=None):
    """Run every scheduled check and assemble the results document.

    A repository scoped with an only_checks override runs just those
    checks. The rest are reported not_applicable rather than left out:
    audit-update-docs.py renders a check it cannot find as "unknown",
    and out of scope is a decision we have made rather than something
    we failed to measure.
    """
    only = repo.props['only_checks']

    results = []
    for check_id, entry in scheduled(checks=checks, legacy=legacy):
        if only and check_id not in only:
            results.append({
                'id': check_id,
                'status': NOT_APPLICABLE,
                'details': (
                    f'{repo.name} is audited for '
                    f'{", ".join(sorted(only))} only'
                ),
            })
            continue
        results.append(run_check(entry, repo))

    summary = {
        'total': len(results),
        'pass': sum(1 for c in results if c['status'] == 'pass'),
        'fail': sum(1 for c in results if c['status'] == 'fail'),
        'not_applicable': sum(
            1 for c in results if c['status'] == 'not_applicable'
        ),
    }

    return {
        'repo': repo.name,
        'org': repo.org,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'checks': results,
        'summary': summary,
    }
