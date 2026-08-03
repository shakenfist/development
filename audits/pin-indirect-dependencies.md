# Audit: Pinning indirect dependencies

## Who this applies to

Only projects which already exactly pin their own direct dependencies,
detected from the `[project] dependencies` array: at least half of the
entries must carry a `==` (or `===`) specifier. Everything else is
reported as not applicable.

That test is the project declaring its own intent. Pinning a
transitive dependency decides, on a consumer's behalf, which version
of a package they will get. In an application we control the runtime
environment, so that is exactly the point -- it is what makes a broken
release three layers away a build failure rather than a mystery in
production. In a library it is an imposition: a distribution packager
building against the versions their archive already ships should not
have to fight our idea of the dependency graph, and neither should
anyone installing our library alongside something else. Our libraries
therefore constrain loosely (`>=`) on purpose, and this audit leaves
them alone.

There is deliberately no configured list of applications and
libraries. A project opts in by pinning its direct dependencies, which
it cannot do accidentally, and the split is unambiguous in practice:
shakenfist and kerbside pin about 97% of their direct dependencies,
while agent-python, client-python, clingwrap, divergulent,
library-utilities and occystrap pin none of theirs.

An earlier version of this audit applied to every project with a
`pyproject.toml` and offered libraries a "library variant" which
recorded pins in a `pinned` extra. That was withdrawn: the base
install was left unconstrained, but the pins still shipped in the
published metadata and Renovate's pep621 manager tracks
`optional-dependencies`, so every recorded version became another
stream of bump pull requests.

## What we check

Projects in scope should have:

* `.github/workflows/pin-indirect-dependencies.yml` -- runs daily and
  reconciles the pinned indirect dependency block against what the
  direct dependencies actually require, creating a PR when the block
  changed (new transitive dependencies pinned, stale pins removed).
* `tools/pin-indirect-dependencies.sh` -- the reconciler script,
  copied unchanged from the template. It demotes existing pins to pip
  constraints for a fresh resolve; see its header comment for details
  including the `# never-pin: <name>` escape hatch.
* `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS` markers in
  `pyproject.toml` delimiting the block the script regenerates
  (without both markers the script refuses to run).

The pinned block lives in `[project] dependencies` alongside the
direct pins.

A `DEPENDENCIES_TOKEN` repository secret with push and PR permissions
is also required. Without it the reconcile still runs and prints its
diff, but the job exits without opening a PR, so the absence is
silent -- check the secret exists when adopting.

## Template

Template: `templates/pin-indirect-dependencies/`
See: `templates/pin-indirect-dependencies/README.md`

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-02T08:26:45.327789+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#80 |
| client-python | non-compliant | shakenfist/client-python#339 |
| clingwrap | non-compliant | shakenfist/clingwrap#87 |
| cloudgood | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#38 |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#159 |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | shakenfist/library-utilities#34 |
| occystrap | non-compliant | shakenfist/occystrap#66 |
| ryll | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3482 |

Details for non-compliant projects:

- **agent-python** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **client-python** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **clingwrap** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **divergulent** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **kerbside** (Status): Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **library-utilities** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **occystrap** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **shakenfist** (Status): Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
<!-- consistency-audit:end -->
