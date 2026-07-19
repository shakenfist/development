# Pin indirect dependencies

A daily workflow which reconciles the pinned indirect (transitive)
dependency block in `pyproject.toml` against what the direct
dependencies actually require. It complements Renovate: Renovate bumps
the versions of existing pins, this workflow maintains the *set* of
pins -- adding new transitive dependencies and removing pins nothing
requires any more. (An earlier generation of this workflow was
append-only, so deployments accumulated stale pins forever.)

## How it works

`pin-indirect-dependencies.sh` regenerates the block between the
`# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS` markers on
every run:

* The existing pins are demoted to pip *constraints* for a fresh
  resolve of the direct dependencies. A constraint only applies if
  something still requires the package, so surviving pins keep exactly
  their current versions (Renovate remains the only thing that moves
  versions), while packages nothing requires any more drop out.
* The resolve runs against a copy of `pyproject.toml` with the block
  stripped, because a stale pin left in place is itself a requirement
  forcing its own installation and would never look stale.
* The resolve uses an isolated venv (no `--system-site-packages`), so
  `pip freeze` sees the complete dependency closure and packages the
  runner's system python happens to provide are not wrongly dropped.

See the script's header comment for full details, including the
duplicate-pin protections (PEP 503 canonical name comparison, extras
tolerance) and the `# never-pin: <name>` escape hatch for packages
that must never be pinned (e.g. pydantic-core, which pydantic pins
exactly, so an explicit pin can only agree or break resolution).

## Variants

* `pin-indirect-dependencies.yml` -- application variant. The pinned
  block lives in the main `[project] dependencies` list. Safe because
  we control the runtime environment. Used by shakenfist and kerbside.
* `pin-indirect-dependencies-library.yml` -- library variant. The
  pinned block lives in a `pinned` extra under
  `[project.optional-dependencies]` so end users are not forced into
  specific transitive versions; users wanting the exact tested set
  install with `pip install package[pinned]`. Because library projects
  do not exactly pin their direct dependencies, the reconciled block
  also records exact versions of the direct dependencies.

Both variants use the same `pin-indirect-dependencies.sh` unchanged.

## Rollout

1. Copy the appropriate workflow variant to
   `.github/workflows/pin-indirect-dependencies.yml`, replacing the
   `{{PROJECT_NAME}}` placeholder.
2. Copy `pin-indirect-dependencies.sh` to
   `tools/pin-indirect-dependencies.sh`, keeping it executable.
3. Add `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS` marker
   comments to `pyproject.toml` delimiting the pinned block (which may
   initially be empty), in the location the chosen variant expects.
4. Create a `DEPENDENCIES_TOKEN` repository secret with push and PR
   permissions.

The first run after converting an append-only deployment sorts the
block case-insensitively, removes accumulated stale pins, and may add
pins for packages the old system-site-packages venv masked (for
example setuptools), so expect a larger one-time diff in that PR.
