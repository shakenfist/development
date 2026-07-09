# Code review tracking

This documents the conventions for tracking whole-codebase human
review: periodically reading a repository file by file to catch the
inconsistencies in style, architecture, and approach that creep in
over years of incremental change. It is complementary to both PR
review (which only ever examines deltas) and the consistency audits
(which catch mechanical drift like missing files -- see `audits/`).
The full design rationale is in
`docs/plans/PLAN-code-review-tracking.md`.

Status: conventions (this document) are current; the automation
described in the "Staleness" section is Phase 2 of the plan and not
yet built. Until it exists the corresponding steps are manual.

## The pieces

* **weAudit** (`trailofbits.weaudit` on the VSCode marketplace)
  provides the in-editor workflow: mark files or regions as
  reviewed, attach notes and findings, see progress. Its state
  lives in `.vscode/<username>.weaudit`, one JSON file per
  reviewer, committed to the repository being reviewed.
* **Signed git commits** of that state file provide attestation.
  The signature covers the commit tree, which contains both the
  review mark and the exact content of the reviewed file -- so
  "who reviewed what, when, at which content version" needs
  nothing beyond git.
* **A sidecar file** (`.vscode/<username>.weaudit-shas.json`,
  Phase 2) records the blob SHA and date of each review so
  staleness is a mechanical check, and **`REVIEWS.md`** (also
  Phase 2) surfaces the review state to people who do not know to
  look in `.vscode/`.

## Adopting a repository

1. Ensure `.vscode/*.weaudit*` files are committable. If
   `.gitignore` excludes `.vscode/`, add exceptions:

   ```
   !.vscode/*.weaudit
   !.vscode/*.weaudit-shas.json
   ```

2. Ensure commit signing is configured for the clone(s) reviews
   will be made from (see below).

3. Protect the branch that will carry review state (normally the
   default branch) from history rewrites: a repository ruleset
   with the `non_fast_forward` and `deletion` rules is the
   minimum. For example:

   ```
   gh api -X POST repos/shakenfist/<repo>/rulesets --input - <<'EOF'
   {
     "name": "Protect default branch history",
     "target": "branch",
     "enforcement": "active",
     "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                 "exclude": []}},
     "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]
   }
   EOF
   ```

   (`~DEFAULT_BRANCH` tracks whatever the default branch is, so the
   same command works on every repo.)

   (shakenfist/shakenfist's "Develop branch" ruleset is a stricter
   superset -- PRs, merge queue, status checks -- and also
   satisfies this.)

4. Phase 2 will add: wiring the repo's `.pre-commit-config.yaml`
   to the shared hooks in this repository, and a review scope
   config.

## The review account

Reviews are performed from a dedicated user account on the review
machine, with its own clones of the repositories under review.
This is deliberate isolation: the review clones never contain
in-flight development changes, so the clean-tree rule below holds
structurally rather than by discipline, and there is no risk of
attesting to code mid-edit. Development happens in the primary
account's clones; review marks are only ever made and committed
from the review account.

## Commit signing

Review-state commits must be signed; the simplest way to guarantee
that is to sign all commits made from the review account. The
convention is **gitsign** (Sigstore keyless signing, matching the
Sigstore use in our release automation): the signing certificate
is issued for `mikal@stillhq.com` via GitHub OAuth at commit time,
and every signature is recorded in the Rekor transparency log --
which gives each review attestation an independent public
timestamp as a side effect.

Setup in the review account:

```
git config --global commit.gpgsign true
git config --global gpg.format x509
git config --global gpg.x509.program gitsign
```

Verification, from any account with gitsign installed:

```
gitsign verify \
    --certificate-identity=mikal@stillhq.com \
    --certificate-oidc-issuer=https://github.com/login/oauth \
    <sha>
git log --show-signature -- .vscode/   # with the config above
```

Note that GitHub's web UI shows gitsign commits as "Unverified"
(reason `bad_cert`): GitHub cannot validate Fulcio's short-lived
certificates. This is expected -- the trust path is gitsign
verification against Fulcio and Rekor, not GitHub's badge. An
example review attestation: shakenfist/ryll commit `755a3cc`
("review: glz.rs").

## Session discipline

The signed-commit attestation is only as good as the tree it
covers, which imposes three rules:

1. **Mark reviews only on a clean working tree.** If a file has
   uncommitted edits when it is marked reviewed, the committed
   tree will not match what was actually read. The dedicated
   review account makes this structural: its clones never carry
   development edits.
2. **Commit review state at the end of every session**, before
   pulling or rebasing, so the marking commit's tree reflects the
   reviewed state:

   ```
   git add .vscode/*.weaudit*
   git commit    # signed, with a "reviewed N files" style message
   ```

3. **Never rewrite history** on the branch carrying review state
   (enforced by the ruleset above).

A session therefore looks like:

1. `git pull` on a clean tree. (Phase 2: the prune hook fires here
   and removes marks for files changed since their review;
   `REVIEWS.md` regenerates.)
2. Pick a file. (Phase 2: `next` picks a random unreviewed
   in-scope file and opens it in VSCode.)
3. Read it. weAudit's explorer ticks show what is already done;
   Claude Code in the integrated terminal for questions.
4. Mark it reviewed (`weAudit: Mark File as Reviewed`), attach
   findings or notes as needed.
5. Repeat from 2; commit (signed) at the end of the session.
   (Phase 2: the stamp hook records each newly reviewed file's
   blob SHA in the sidecar and regenerates `REVIEWS.md` as part of
   the commit.)

## Staleness

A review applies to the file content that was read, not the path:
once the file changes, that review is stale and the file should be
treated as unreviewed. weAudit does not track this -- a stale tick
looks identical to a fresh one.

Until the Phase 2 hooks exist, staleness is handled manually: be
suspicious of ticks on files you know have changed, and unmark
them. Phase 2 makes it automatic: a pre-commit hook stamps each
reviewed file's blob SHA into the sidecar, and post-merge /
post-checkout / post-rewrite hooks prune any mark whose stamped
SHA no longer matches `HEAD` (region marks are pruned wholesale
with the file). See the plan for the full design, including why
the stamps live in a sidecar rather than in weAudit's own JSON.
