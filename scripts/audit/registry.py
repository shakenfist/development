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


#: Every criterion, as instances. Populated family by family.
CHECKS = []


def scheduled(checks=None, legacy=None):
    """Pair every check id with the call that produces its result.

    Returns a list of (check_id, callable). The calls are deferred so
    that a repository scoped by only_checks can skip a check without
    paying for it first.
    """
    pairs = [(check.id, check) for check in (checks
                                             if checks is not None
                                             else CHECKS)]
    return pairs + list(legacy or [])


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
