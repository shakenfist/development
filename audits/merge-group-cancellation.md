# Audit: Merge group run cancellation

## What we check

Expensive jobs that can run on a `merge_group` event must sit in a
concurrency group that a superseding merge group actually joins, so
the older run is cancelled rather than left to run a full cloud build
nobody is waiting on.

Concretely, for every workflow with a `merge_group:` trigger and every
reusable `workflow_call:` workflow, each job that holds a scarce
self-hosted runner and is reachable on `merge_group` must have an
effective `concurrency:` block -- job-level, or workflow-level if the
job declares none -- that:

* sets `cancel-in-progress: true`, and
* keys the group on something stable across queue rebuilds. Either the
  group expression does not mention `github.ref` at all, or it
  branches on `github.event_name == 'merge_group'` and uses a stable
  key on that branch.

"Scarce" means any self-hosted pool except `static`. The sibling
`expensive-lane-path-filter` audit uses the narrower `vm` label, which
is right for the question it asks, but instar's ephemeral runners are
tagged `[self-hosted, debian-12, xl]` with no `vm` label and an
abandoned merge group holds one of those just as firmly. The `static`
pool is exempt because it is always-on and shared, and the jobs on it
(path filters, gate jobs) are seconds long. GitHub-hosted runners and
unresolvable `runs-on:` expressions are exempt: there is no fleet
runner to starve.

Reusable workflows are in scope unconditionally, because a callee
published for the fleet cannot know what event it will see. Inferring
reachability from in-repo callers was tried and is wrong: it exempted
shakenfist/actions' `smoke-cluster.yml` -- which every shakenfist merge
group runs four nested clusters through -- on the strength of a
scheduled canary also calling it.

Out of scope: jobs whose `if:` excludes `merge_group`, and jobs that
only call a reusable workflow (the callee carries the group).
Deliberate exceptions are marked with an
`audit-ok: merge-group-cancellation` comment in the workflow file. The
fleet has one: `test-drift-fix.yml` is reusable, but its only caller is
`pr-fix-tests.yml` on `issue_comment`, so it can never see a merge
group.

## Why

`github.ref` is the natural concurrency key and is correct on every
event except this one. On `merge_group` it is the per-attempt queue
branch, `gh-readonly-queue/<base>/pr-<N>-<SHA>`, and GitHub mints a
fresh SHA every time it rebuilds the group -- which it does on every
push to the base branch. Keying on it therefore puts every rebuild in
a concurrency group of its own, `cancel-in-progress` never matches,
and superseded runs are never cancelled. They run to completion
against a queue branch GitHub has already abandoned.

The cost is not theoretical. In shakenfist/kerbside#284, three merge
groups for the same pull request built three complete oVirt clouds
concurrently on the shared sfcbr under-cloud; only the newest could
possibly merge. The lane that failed did so by timing out waiting for
a 12 vCPU / 16GB instance the under-cloud could not place. The same
issue later recorded the cross-repository form: kerbside's merge group
starved on capacity consumed by two superseded
shakenfist/shakenfist merge groups. A fix in one repository does not
hold when its neighbours share the under-cloud, which is what makes
this a fleet audit rather than a per-project judgement call.

## Why cancelling is safe here

Cancelling a `merge_group` run that the queue is still waiting on
reports a failed required check and ejects the pull request. That is
avoided by the fleet's serial queue: the `merge-queue-config` audit
requires `max_entries_to_build: 1` on every repository with a merge
queue, so the queue only ever builds one entry at a time and any
*other* in-flight `merge_group` run is by definition superseded.

This audit therefore depends on [merge-queue-config.md](merge-queue-config.md).
If a repository ever raises `max_entries_to_build` above 1, several
merge groups are live at once, a base-branch key would alias them, and
the pattern below becomes unsafe -- the key would have to narrow to
something unique per live entry.

## Template

No template -- the change is a concurrency key edit per workflow. The
fleet pattern, from kerbside's `functional-tests.yml`:

```yaml
    concurrency:
      group: >-
        ${{ github.workflow }}-<job suffix>-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
```

The `merge_group-` prefix matters: without it a queue run keyed on
`refs/heads/develop` would share a group with a `workflow_dispatch`
run on `develop`, whose `github.ref` is the same string, and the two
would cancel each other.

In a reusable workflow, `github.workflow` resolves to the *caller's*
name, so keep whatever literal prefix the callee already uses to
separate components and substitute only the `github.ref` tail.

## Projects

<!-- consistency-audit:begin -->
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
