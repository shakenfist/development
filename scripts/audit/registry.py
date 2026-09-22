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
import sys
import traceback

from audit.check import ERROR, NOT_APPLICABLE
from audit.checks import (
    ci_workflows, distros, docs_content, github_config, llm_docs, npm_dependencies, packaging, plans, review,
    runners,
)


#: Every criterion, as instances, grouped family by family. The
#: order here is the order the results JSON reports them in.
CHECKS = [
    llm_docs.LlmTooling(),
    llm_docs.LlmDocStructure(),
    llm_docs.LlmContextLint(),
    llm_docs.LlmContextLintCi(),
    llm_docs.LlmDocNaming(),
    docs_content.ReadmeStructure(),
    docs_content.ReadmeAbsoluteLinks(),
    docs_content.DocsExternalLinks(),
    docs_content.DiagramFormat(),
    docs_content.MermaidLintCi(),
    plans.PlanPhaseReferences(),
    plans.PlanSourceReferences(),
    plans.PlanIndex(),
    plans.PlanAuditPhase(),
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
    packaging.UndeclaredDirectDependency(),
    packaging.RenovateLockstepGroups(),
    npm_dependencies.NpmPinIndirectDependencies(),
    npm_dependencies.NpmUnusedDeclaredDependency(),
    npm_dependencies.NpmUndeclaredDirectDependency(),
    runners.SelfHostedRunners(),
    runners.StaticRunnerTags(),
    runners.VmRunnerSize(),
    distros.EolDistro(),
    ci_workflows.CiReviewAutomation(),
    ci_workflows.WorkflowPermissions(),
    ci_workflows.PreCommitConfig(),
    ci_workflows.DevpiFallback(),
    ci_workflows.DevpiStaleIp(),
    ci_workflows.ExpensiveLanePathFilter(),
    ci_workflows.MergeGroupCancellation(),
    ci_workflows.SecretScanningCi(),
    ci_workflows.FuzzNightlyReporting(),
    review.ReviewMarksPreCommit(),
    review.ReviewCoverage(),
    review.ReviewScopeCompleteness(),
    review.SfuiVendor(),
    github_config.ExportRepoConfig(),
    github_config.DefaultBranchNaming(),
    github_config.GithubSecurity(),
    github_config.DeleteBranchOnMerge(),
    github_config.MergeQueueConfig(),
    github_config.ScopeCoverage(),
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

    An exception escaping a check costs that one criterion, not every
    criterion for the repository. Before this boundary existed a single
    raising check aborted the whole run, and the class was closed one
    call site at a time -- three times -- while the next one waited to
    be found the same way (shakenfist/development#159).

    The raised check is reported as `error` rather than as a verdict.
    A `fail` would file an issue on the audited repository, under the
    bot's identity, for what is a bug in the audit; `not_applicable`
    would close one it already has. `error` does neither, and the
    traceback goes to stderr so the bug is still in the workflow log.
    The workflow then fails the leg on any `error` result, after its
    results are uploaded, so the bug stays loud without costing the
    other criteria their issue management.

    Exception rather than BaseException: an interrupt or a
    SystemExit is a request to stop, not a broken check.
    """
    try:
        reason = check.applies(repo)
        if reason is not None:
            return check.skip(reason)
        return check.run(repo)
    except Exception as e:
        print(f'audit: {check.id} raised against {repo.name}:',
              file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return check.result(
            ERROR,
            f'The check raised {type(e).__name__}: {e}. This is a bug in '
            f'the audit rather than a finding about {repo.name}; the '
            f'traceback is in the audit log.',
        )


def errored(results):
    """The results in a run_all() document that are errors, not verdicts."""
    return [c for c in results['checks'] if c['status'] == ERROR]


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
        'error': sum(1 for c in results if c['status'] == ERROR),
    }

    return {
        'repo': repo.name,
        'org': repo.org,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'checks': results,
        'summary': summary,
    }
