# Audit: Moving ref source protection

## What we check

A repository the rest of the fleet consumes at a moving ref has no
private default branch. `shakenfist/actions` serves the shared reusable
workflows and composite actions at `@main`, so what a caller runs is
whatever that branch holds at the moment its job starts, and the caller
reviewed none of it. Local clones of the fleet's default branches on
2026-10-02 held 93 such references, in 14 repositories; the count is
recorded in `PLAN-workflow-ref-trust.md`, which is also where the
decision to keep the moving ref rather than pin it is argued.

A composite action is the stronger case. A reusable workflow runs as
its own job with the secrets its caller named, while a composite action
runs inside the caller's job, with that job's environment, token and
checkout. Either way the review that protects the caller is the one
that happened on the default branch of the repository it called, which
is why that branch has to be a gate rather than a place anyone with
write access can push.

Two rules make it one, and both have to be in the default branch's
effective rules:

* a `pull_request` rule, so nothing reaches the branch except through a
  pull request; and
* a `required_status_checks` rule, so that pull request waits for CI.

Either on its own leaves a way through. Without the first, a push lands
without a pull request ever existing. Without the second, a pull request
merges with nothing having run, so the pull request is a formality
rather than a review.

A rule that is reported is not necessarily a rule that refuses
anything. `GET /repos/shakenfist/<repo>/rules/branches/<branch>` is
what the check reads, because it is the only endpoint that resolves
organisation and repository rulesets together, and it already leaves
out a ruleset that is `disabled`. It does not leave out one set to
`evaluate`, where every rule is reported on and nothing is refused. So
the check collects the `ruleset_id` of each of the two rules it
matched, reads `GET /repos/shakenfist/<repo>/rulesets/<id>` once per
distinct id, and fails unless every one of them is `enforcement:
active`.

It also fails a `required_status_checks` rule whose context list is
empty. That is the shape an operator leaves behind by clearing the
contexts instead of removing the rule: it satisfies "the rule is
there" while requiring nothing, and a pull request merges as soon as
it is opened. The finding names the checks that belong there -- `Check
paths`, `Lint`, `Unit tests` and `gitleaks` -- so that the issue says
what to put back. Whether the contexts still match the workflow's job
names is deliberately not measured: that needs the workflow read as
well, and the symptom of a renamed job is loud, because every pull
request stops offering a merge at once.

Everywhere else the criterion reports `not_applicable`, naming the
repository, and asks GitHub nothing: a caller that pins what it
consumes, or consumes nothing from this repository, is not a source
whose default branch runs in somebody else's job. The list of
moving-ref sources is `MOVING_REF_REPOS` on the check, and holds
`actions` alone. A repository joining that list is a decision somebody
makes, and the next run measures it.

Anything the audit could not ask is a check error rather than a
verdict. That covers the branch-rules read and each ruleset read
failing or answering something unparseable, a ruleset that reports no
`enforcement`, and a matched rule that names no `ruleset_id` -- with no
id there is nothing to read the enforcement from. It also covers one
case where every read answered: a rule whose `ruleset_source_type` is
`Organization` came from an organisation ruleset, whose enforcement is
only on `GET /orgs/shakenfist/rulesets/<id>`, and the audit's
read-only token is refused that endpoint. The rule may be refusing
pushes or merely reporting on them, and the audit cannot tell which, so
it says so instead of guessing. Nothing in the fleet is configured that
way today; it becomes measurable when the audit has a read identity
that can reach the organisation endpoint
([#352](https://github.com/shakenfist/development/issues/352)).

### Required, but confirmed by a reviewer

**The bypass list holds exactly one actor.** On `actions`' ruleset that
is the review-marks App, `shakenfist-review-marks`, `actor_id`
5538020, `actor_type` `Integration`, `bypass_mode` `always` -- read
back on 2026-11-11 with a credential that can edit the ruleset, with
`current_user_can_bypass: "never"` for the operator's own credential,
so the App's per-run token is the only identity that can push to the
branch. The bypass is what lets the `prune-reviews` workflow land
review marks on a branch that otherwise requires a pull request.

This is confirmed by a reviewer rather than measured, and for a
measured reason: GitHub omits `bypass_actors` for a caller that cannot
edit the ruleset, and the audit's read-only token cannot, which was
read twice -- the second time under the organisation's security
manager role -- before this criterion was written. Widening that token
to make the check fit would hand the audit the ability to edit every
ruleset in the fleet, which is a worse trade than a reviewer reading a
list. How a reviewer judges an actor by its type is the table in
[bypass-actor-identity.md](bypass-actor-identity.md); when the audit
gains a read identity that can see the key, a missing key becomes an
error there and here, never an empty set.

**Required approvals are zero, deliberately.** "Requires a pull
request" is not "requires an approval": with one maintainer, an
approval count of one cannot be met by the author of the change. What
the rule buys is that every change is visible as a pull request, with
CI reported on it, before it lands. The fleet's other pull-request
rulesets are configured the same way.

**One accepted residual.** The runner-controller App
`shakenfist-k3s-ci-arc` holds `administration: write` on hunkydory,
where the review-marks App's key is also held, so whoever holds the
runner-controller App's key can widen hunkydory's `review-marks`
deployment branch policy, read the review-marks key from a branch
workflow, and mint push tokens for every repository that App is
installed on -- `actions` among them since 2026-11-11. The chain ends
in a bypass of the very ruleset this criterion measures. It is accepted
knowingly rather than treated as a finding here, because narrowing
another App's permission is its own piece of work;
[bypass-actor-identity.md](bypass-actor-identity.md) records it as the
residual it is.

## Template

No template -- this is a one-time ruleset configuration change.

To read what is currently enforced:

```bash
gh api repos/shakenfist/<repo> --jq '.default_branch'
gh api repos/shakenfist/<repo>/rules/branches/<default branch> --jq \
    '.[] | {type, ruleset_id, ruleset_source_type}'
gh api repos/shakenfist/<repo>/rulesets/<id> --jq \
    '{enforcement, bypass_actors}'
```

To add the two rules, fetch the ruleset, append them preserving every
other rule and the `~DEFAULT_BRANCH` condition, and PUT it back. A PUT
replaces the ruleset rather than merging into it, so anything left out
of the body is removed -- including `bypass_actors`, which GitHub omits
from the GET for a credential that cannot edit the ruleset, so a body
built from such a read silently empties the list. The GET-then-PUT
shape that guards against that is under *Template* in
[bypass-actor-identity.md](bypass-actor-identity.md); add these two
entries to its `rules` array:

```json
{"type": "pull_request",
 "parameters": {"required_approving_review_count": 0}}
{"type": "required_status_checks",
 "parameters": {"required_status_checks": [
     {"context": "Check paths", "integration_id": 15368},
     {"context": "Lint", "integration_id": 15368},
     {"context": "Unit tests", "integration_id": 15368},
     {"context": "gitleaks", "integration_id": 15368}]}}
```

`integration_id` 15368 is GitHub Actions; a context without it matches
a check run of that name from any app. The contexts are job names from
the repository's own `ci.yml`, so a repository joining
`MOVING_REF_REPOS` brings its own.

Before trusting the rule, open a throwaway pull request and confirm
every required context resolves to success or skipped and that the
merge button is offered. A required context that never reports wedges
every pull request, and the only way out is an admin edit to the
ruleset. A job that skips satisfies its required check, so a path
filter that skips a lane is not a problem; a job that never runs at all
on the pull requests the repository actually sees is.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](compliance.md#moving-ref-source-protection).
