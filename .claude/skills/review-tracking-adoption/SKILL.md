---
name: review-tracking-adoption
description: Deploy the human code review tracking tooling (weAudit, stamps, REVIEWS.md, prune automation) to a Shaken Fist repository, or verify an existing deployment -- especially that expensive CI is skipped on review-only pull requests. Use when adopting a repo into review tracking, re-checking an adopted repo, or after adding new workflows to an adopted repo.
---

# Review Tracking Adoption Skill

Use this skill to deploy the whole-codebase human review tracking
tooling to a repository, and to verify a deployment -- fresh or
existing -- is complete.

## Source of truth

The adoption procedure is the numbered "Adopting a repository" list
in `docs/code-review-tracking.md`. Follow it from the doc rather
than from memory or from this file: the steps move, and several
encode non-obvious constraints (stale marks must not be blessed at
current content; the scope config is what opts the repo into the
`review-coverage` and `review-scope-completeness` audits). This
skill adds the verification pass the doc cannot make mechanical.

After writing or editing `.vscode/review-scope.toml`, run
`review-tracking.py scope-orphans` in the target repo. It lists
tracked files that are out of scope only because no `include`
pattern names them -- the silent half of how a file leaves the
review queue, and what `review-scope-completeness` fails on. Each
one needs either a pattern that covers it or an `exclude` entry
saying why not. Do not settle it by deleting the `include` list
unless that is the choice you would have made anyway.

Ground rules match standards-alignment: branch from the target's
default branch, never create the PR yourself, and run
`pre-commit run --all-files` before every commit.

## Verifying CI skips review-only pull requests

Step 8 of the adoption doc teaches build workflows to ignore
review-only changes. This check is deliberately part of an LLM
skill rather than `scripts/audit-check.py`: every project's CI is
shaped differently, and deciding which workflows are "expensive
build CI" versus "content scanner" takes judgment a deterministic
grep cannot supply. Run the verification:

- at the end of a fresh adoption,
- when asked to re-check an adopted repo, and
- whenever a new workflow lands in a repo that carries
  `.vscode/review-scope.toml`.

The procedure:

1. **Enumerate** every workflow in `.github/workflows/` and note
   which have a `pull_request` trigger. (Remember `on:` parses as
   the YAML boolean `True` key in Python.) Workflows without a
   `pull_request` trigger are out of scope -- push, schedule, and
   `issue_comment` bot workflows do not burn CI on review PRs.

2. **Classify** each `pull_request` workflow:

   - *Code-shaped / expensive* -- unit tests, lint, builds,
     functional test lanes, CodeQL, dependency-pin reconciliation:
     anything whose result cannot change when only review state
     changes. These must carry the `paths-ignore` block with all
     four review paths (`REVIEWS.md`, `.vscode/*.weaudit`,
     `.vscode/*.weaudit-shas.json`, `.vscode/review-scope.toml`).
   - *Content scanners* -- gitleaks, bidi/zero-width or other
     Unicode smuggling checks, anything that reads prose: these
     must **not** skip review-only changes. Review notes are prose,
     and prose is where secrets or smuggled Unicode could land.
   - When unsure which side a workflow falls on, leave it running
     and say so in your report -- a wasted CI run is cheaper than
     an unscanned secret.

3. **Check the merge is not wedged.** Skipping is only safe while
   no skipped workflow provides a required status check: a skipped
   required check sits "expected" forever and blocks the merge.
   Check rulesets and branch protection for the default branch:

   ```bash
   gh api repos/shakenfist/<repo>/rulesets --jq '.[].id' | while read -r id; do
       gh api "repos/shakenfist/<repo>/rulesets/$id" \
           --jq '.rules[] | select(.type == "required_status_checks")'
   done
   gh api "repos/shakenfist/<repo>/branches/<default>/protection" \
       --jq '.required_status_checks.contexts' 2>/dev/null
   ```

   If a required check's job would be skipped by `paths-ignore`,
   flag it rather than silently narrowing the ignore list -- the
   right fix (drop the requirement, or keep that lane running) is
   Michael's call.

4. **Report** the classification table honestly: which workflows
   skip, which deliberately do not and why, and any you were unsure
   about. For a re-check that finds gaps, propose the `paths-ignore`
   additions as a branch on the target repo but leave the PR to
   Michael.

## Related pieces

- `docs/audits/review-coverage.md` -- the deterministic daily backstop
  for review *backlog*; it does not check CI skipping.
- `docs/audits/review-scope-completeness.md` -- the daily backstop for
  the review *scope*: nothing leaves the queue by omission.
- The `pr-re-review` user skill -- bot re-reviews on PRs, unrelated
  to review-state PRs.
