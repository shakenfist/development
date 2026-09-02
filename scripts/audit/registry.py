"""What checks exist, and the order they run in.

The registry is the schedule. A check that is not in `CHECKS` does not
run, and a check that is in it needs nothing else registered anywhere:
its specification path and its issue title are attributes of the class,
and `audit_common` derives its tables from here rather than repeating
them.

The order of `CHECKS` is the order the results are reported in, so
entries are grouped by family and appended rather than reshuffled: the
results JSON is a published artifact and its ordering is part of its
bytes. It was pinned separately, in an `ORDER` table of ids, while the
migration off `audit-check.py`'s `check_calls()` ran -- back then some
criteria came from here and some from a legacy table, and a family
moving between the two would have reordered the JSON. Every criterion
is a `Check` now, so the list is the order and there is no second table
to keep in step.
"""

from datetime import datetime, timezone

from audit.check import NOT_APPLICABLE
from audit.checks import ci_workflows, docs_content, github_config, llm_docs, packaging, plans, review, runners


#: Every criterion, as instances, grouped family by family. The
#: order here is the order the results JSON reports them in.
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
    packaging.UnusedDeclaredDependency(),
    packaging.RenovateLockstepGroups(),
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
    github_config.ExportRepoConfig(),
    github_config.DefaultBranchNaming(),
    github_config.GithubSecurity(),
    github_config.DeleteBranchOnMerge(),
    github_config.MergeQueueConfig(),
]


def scheduled(checks=None):
    """Pair every check id with the check that produces its result.

    Returns a list of (check_id, check), in registry order. Nothing is
    run here: a repository scoped by only_checks has to be able to skip
    a check without paying for it first.
    """
    return [(check.id, check)
            for check in (checks if checks is not None else CHECKS)]


def run_check(check, repo):
    """Run one scheduled check against a repository.

    The check is asked whether it applies before it is asked to run.
    """
    reason = check.applies(repo)
    if reason is not None:
        return check.skip(reason)
    return check.run(repo)


def run_all(repo, checks=None):
    """Run every scheduled check and assemble the results document.

    A repository scoped with an only_checks override runs just those
    checks. The rest are reported not_applicable rather than left out:
    audit-update-docs.py renders a check it cannot find as "unknown",
    and out of scope is a decision we have made rather than something
    we failed to measure.
    """
    only = repo.props['only_checks']

    results = []
    for check_id, check in scheduled(checks=checks):
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
        results.append(run_check(check, repo))

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
