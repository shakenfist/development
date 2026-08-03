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
  uv itself is installed into a separate venv and drives the target
  venv from outside it, because a uv installed *into* the target venv
  shows up in the freeze and gets recorded as a dependency of projects
  which do not require one.

See the script's header comment for full details, including the
duplicate-pin protections (PEP 503 canonical name comparison, extras
tolerance) and the `# never-pin: <name>` escape hatch for packages
that must never be pinned (e.g. pydantic-core, which pydantic pins
exactly, so an explicit pin can only agree or break resolution).

## Applications only

This belongs in projects which already exactly pin their own direct
dependencies -- currently shakenfist and kerbside. Pinning a
transitive dependency decides on a consumer's behalf which version
they get, which is the whole point in an application whose runtime
environment we control, and an imposition in a library someone else
has to package or install alongside their own dependency graph. Our
libraries constrain loosely (`>=`) on purpose.

There was briefly a library variant which kept the block in a `pinned`
extra so the base install stayed unconstrained. It was withdrawn: the
pins still shipped in the published metadata, and Renovate's pep621
manager tracks `optional-dependencies`, so each recorded version
became another stream of bump pull requests. The
[audit spec](../../audits/pin-indirect-dependencies.md) describes how
the applies-to test is now made.

## Rollout

1. Copy `pin-indirect-dependencies.yml` to
   `.github/workflows/pin-indirect-dependencies.yml`, replacing the
   `{{PROJECT_NAME}}` placeholder.
2. Copy `pin-indirect-dependencies.sh` to
   `tools/pin-indirect-dependencies.sh`, keeping it executable.
3. Add `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS` marker
   comments to `pyproject.toml` delimiting the pinned block (which may
   initially be empty) inside the `[project] dependencies` list.
4. Create a `DEPENDENCIES_TOKEN` repository secret with push and PR
   permissions. Without it the job runs to completion and prints its
   diff but never opens a PR, so a missing secret looks like "there
   was nothing to do".
5. If anything in the dependency closure compiles at install time, add
   its build dependencies to the commented placeholder step in the
   workflow. The isolated venv means a package can no longer fall back
   to a distro build of itself (kerbside needs
   `default-libmysqlclient-dev` for mysqlclient).
6. Generate the first reconciled block by running the script by hand
   and committing the result, so the adoption PR proves CI passes with
   the reconciled set. Run it in a `debian:12` container rather than
   on a workstation: the resolved closure is python-version specific,
   so a resolve on a newer python records a set the first CI run would
   immediately undo.

The first run after converting an append-only deployment sorts the
block case-insensitively, removes accumulated stale pins, and may add
pins for packages the old system-site-packages venv masked (for
example setuptools), so expect a larger one-time diff in that PR.
