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
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-14T07:28:36.672476+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#64 |
| instar | non-compliant | shakenfist/instar#490 |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#268 |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3732 |

Details for non-compliant projects:

- **divergulent** (Status): 7 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/deterministic-rules.md:352, docs/deterministic-rules.md:375, docs/workflow.md:122, docs/workflow.md:124, docs/workflow.md:127, docs/workflow.md:146, docs/workflow.md:176
- **instar** (Status): 200 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/bench.md:192, docs/bench.md:194, docs/bench.md:227, docs/bench.md:362, docs/bench.md:410, docs/bench.md:412, docs/bench.md:524, docs/chain-config.md:65, docs/chain-config.md:66, docs/chain-config.md:67 (+190 more)
- **ryll** (Status): 43 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/configuration.md:53, docs/libvirt-spice-recommendations.md:162, docs/libvirt-spice-recommendations.md:182, docs/libvirt-spice-recommendations.md:208, docs/libvirt-spice-recommendations.md:283, docs/libvirt-spice-recommendations.md:461, docs/macos-metrics-verification.md:4, docs/macos-metrics-verification.md:135, docs/macos-metrics-verification.md:136, docs/macos-metrics-verification.md:228 (+33 more)
- **shakenfist** (Status): 49 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/glossary.md:11, docs/developer_guide/mypy.md:61, docs/developer_guide/mypy.md:69, docs/developer_guide/mypy.md:75, docs/developer_guide/mypy.md:83, docs/developer_guide/network_dispatcher.md:161, docs/developer_guide/network_dispatcher.md:163, docs/developer_guide/network_dispatcher.md:200, docs/developer_guide/network_dispatcher.md:202, docs/developer_guide/network_dispatcher.md:221 (+39 more)
<!-- consistency-audit:end -->
