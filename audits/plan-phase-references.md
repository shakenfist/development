# Audit: Plan phase references

## What we check

Documentation describes the current state of the software, not the
history of how it was built. `README.md` and the files under `docs/`
must not refer to the phase numbers of implementation plans: wording
like "feature YYY, implemented in phase ZZZ" tells a reader nothing
they need -- often without even naming the plan the phase belongs
to. Either the feature is implemented, in which case the docs should
describe it plainly, or it is not, in which case the docs should
link to the master plan in `docs/plans/` rather than citing a phase.

The automated check greps the top-level `README.md` and every `.md`
file under `docs/` for `phase <number>` (case-insensitive),
skipping:

* any file under a `plans/` directory at any depth -- plan
  documents legitimately discuss their own phases;
* per-repository `doc_content_excludes` prefixes from
  `REPO_OVERRIDES` in `scripts/audit-check.py` -- shakenfist's
  `docs/components/` is an automated import of the other
  repositories' documentation, so auditing it would double-report
  findings that must be fixed at their source;
* fenced code blocks and inline code spans; and
* lines carrying an explicit `<!-- audit-ok: phase-reference -->`
  marker.

The word "phase" is reserved for plan documents. A procedural
document describing a live multi-stage process (a release runbook,
say) should call its stages "steps" or "stages"; the suppression
marker exists for the rare line where "phase <number>" is genuinely
not a plan reference.

Repositories with neither a top-level `README.md` nor a `docs/`
directory are reported as N/A.

The judgment half of the policy is enforced at the point where the
references are written: each repository's pre-push audit file
carries the canonical `plan-phase-references` shared block (see the
`push-audit` audit), which instructs the documentation reviewer to
keep plan history out of the docs.

This audit exists because feature documentation across the fleet
accreted phrasing like "since two-tier CI phase 3" and "phase 6 is
what makes..." -- references to the phase numbering of historical
plans that mean nothing to a reader who was not there when the plan
was executed.

## Template

No template -- reword the documentation to describe current
behaviour, moving any forward-looking material into a link to the
relevant master plan in `docs/plans/`. Consult the referenced plan
(`docs/plans/PLAN-*.md`) to work out what the wording should say
instead; the rewording must preserve the information, not delete
it.

## Projects

<!-- consistency-audit:begin -->
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
