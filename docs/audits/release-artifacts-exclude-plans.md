# Audit: Release artifacts exclude docs/plans

## What we check

`docs/plans/` is working history: the plans, their phases, and the
audit notes and diffs filed under them. It stays in git, where somebody
asking why the code is shaped the way it is can find it, but it is not
source, and nobody who installs a release needs it. Every packaging
tool the fleet releases with defaults to taking what git tracks (or
everything git does not ignore) from the directory its manifest sits
in, so a manifest at the repository root ships `docs/plans/` unless it
is told not to.

This was found when client-python-k3s's sdist size gate tripped in
October 2026: the sdist was 2.3MB, and 0.9MB of it was plans. Pruning
`docs/plans` took it to 1.4MB without changing a line of what it
builds. client-python, divergulent, kerbside, library-utilities,
occystrap and shakenfist were shipping their plans the same way.

For a repository that tracks files under `docs/plans/`, each release
artifact built from the repository root (or from `docs/`) must exclude
that directory with its own mechanism:

| Artifact | Built from | Passes when |
|----------|------------|-------------|
| sdist, setuptools with setuptools_scm | `pyproject.toml` or `setup.py` | `MANIFEST.in` has `prune docs/plans` (or `prune docs`), not undone by a later `graft`, or a `recursive-include` whose patterns select a plan |
| sdist, plain setuptools | `pyproject.toml` or `setup.py` | `MANIFEST.in` does not add `docs/plans` back in |
| sdist, hatchling | `pyproject.toml` | `[tool.hatch.build.targets.sdist]` `exclude` covers `docs/plans` (it wins over an include list), or an `include`/`only-include` list does not select it |
| crate | `Cargo.toml` with a `[package]` that is published | `exclude` covers `docs/plans`, or an `include` list does not select it, either set directly or inherited from `[workspace.package]` |
| npm package | `package.json` that is not `private` | a `files` list does not select `docs/plans`, or `.npmignore` covers it |
| Ansible collection | `galaxy.yml` | a `build_ignore` entry matches `docs/plans` or `docs` as a path from the collection root, or the `manifest` directives prune it |

Wheels are not checked: they carry the import package, not the
repository. A crate published with `publish = false`, a virtual Cargo
workspace, a private npm package, and any manifest in a subdirectory
(client-python-k3s's `collection/`, ryll's crates) do not apply, since
none of them can reach `docs/plans/`.

A `pyproject.toml` whose build backend is none of the above is reported
rather than passed: a check that cannot measure an artifact must not
claim it is clean. The fix there is to confirm by building the sdist,
and to teach the check about the backend.

## What this does not cover

Pattern matching follows gitignore semantics closely enough for the
exclusions people write -- `docs/plans`, `/docs/plans/`, `docs`,
`docs/**` -- but is not a full implementation of any one tool's rules.
The exception is galaxy's `build_ignore`, which ansible-galaxy
fnmatches against each path relative to the collection root, and which
the check matches the same way: `plans`, `/docs/plans` and
`docs/plans/` exclude nothing there, and do not pass. An npm `files`
entry is matched at any depth, which can only report more, not less.

`MANIFEST.in` (and a galaxy `manifest`) is evaluated for the directives
that act on whole directories, plus the file patterns of
`recursive-include` and `global-include`; an `exclude` that names
individual plans is not treated as excluding the directory, because the
next plan would not be named, and an `include` of individual plan paths
is not modelled. File patterns are judged against a Markdown plan, so
`recursive-include docs *.png` does not count as shipping the plans
even if a plans directory holds an image. A galaxy `manifest` that keeps
the default directives is treated as shipping the plans, since those
defaults take `.txt`, `.json` and `.yml` files from `docs/`.

Docker images are not checked. The build context is chosen by the
command that builds the image rather than by a file in the repository,
so there is no manifest to read; a `COPY . .` from the repository root
would ship plans, and a `.dockerignore` entry is the fix.

Release tarballs assembled by hand in a workflow (instar's staging
directory, ryll's binary tarball) list their contents explicitly, and
are not checked.

## Template

No template. For the common case, a setuptools_scm project, add this
to `MANIFEST.in` at the repository root:

```
prune docs/plans
```

and confirm with `python3 -m build --sdist` and
`tar tzf dist/*.tar.gz | grep docs/plans` printing nothing.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](compliance.md#release-artifacts-exclude-plans).
