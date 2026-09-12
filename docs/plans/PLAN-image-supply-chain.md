# Plan: the image supply chain, from end-of-life migration to production that reports its own failures

## Prompt

Written 2026-09-13, consolidating work that had spread across three
concurrent sessions and nine open issues in four repositories. Two
of those sessions have been closed; this plan is the single
statement of what is outstanding and in what order.

Two threads run through every issue here and they are not the same
problem. The first is a migration: Debian 12 reached end of
standard support on 2026-06-10 and the fleet still names it in 80
places. That is finite work with an end. The second is that the
machinery producing our base images failed five separate times in
one week without telling anyone, and was found only because
somebody went looking for something else. That has no end unless
the machinery changes.

Read `docs/audits/eol-distro.md` for what the criterion measures
and, importantly, what it cannot see.

## Situation

### The open work, as filed

| Issue | Repository | What |
|-------|------------|------|
| [#123](https://github.com/shakenfist/development/issues/123) | development | 80 Debian 12 references in 15 repositories |
| [#38](https://github.com/shakenfist/private-ci/issues/38) | private-ci | Collated inventory of obsolete base image usage |
| [#39](https://github.com/shakenfist/private-ci/issues/39) | private-ci | Dependencies cache disk still built from `debian:11` |
| [#40](https://github.com/shakenfist/private-ci/issues/40) | private-ci | Retire the unused `debian-11` runner image and label |
| [#44](https://github.com/shakenfist/private-ci/issues/44) | private-ci | A conductor restart silently skips that day's nightly rebuild |
| [#45](https://github.com/shakenfist/private-ci/issues/45) | private-ci | No `debian-gnome-13`; the last bookworm label with no successor |
| [#826](https://github.com/Mach33Labs/33fl/issues/826) | 33fl | Two GitLab static runners stay on bookworm until deleted by hand |
| Phases 1-6 | images | `PLAN-image-build-modernisation.md`, phase 0 complete |

Already landed and not repeated below: development#119 (the
`eol-distro` criterion), actions#66 (`debian-13-docker` published a
daemon with no client), 33fl#820 (static runners now *built* on
Debian 13), images#2 (the build fixes) and images#3 (that
repository's own plan).

### Five silent failures in one week

Each of these was found by hand, and none of them raised an alert:

1. **shakenfist/images published nothing for sixteen days.**
   `build.sh` is `#!/bin/bash -e` run from cron; one failing image
   aborted the run and cron mailed root, which nobody reads.
2. **`debian-docker:12`, `debian-gnome:12` and `debian-xfce:12`
   were Debian 11 for two years and two months.** They passed
   `DIB_RELEASE=bullseye` while naming `debian-12-extras`, from
   2024-07-06 until 2026-09-12. Nothing checked that the image
   matched its own name.
3. **`ci-images/debian-13-docker` published a working daemon with
   no `docker` binary.** Debian 13 split `docker.io`, and the CI
   images are built with recommends disabled. Fixed in actions#66
   by making the build run `docker version` and fail.
4. **private-ci's nightly rebuild did not fire on 2026-09-12** and
   nothing said so. A day on which the loop neither builds nor
   errors is indistinguishable from a day with nothing to do.
5. **`debian-11` reports `False` in every nightly cycle result**
   and has done since it stopped being buildable. The cycle
   summary carries a permanent failure that nobody reads.

The shape is identical every time: something that produces images
stopped producing correct images, and no signal existed. Four of
the five were found in the same week only because one investigation
led to another.

There is a measured cost to the staleness, beyond the missing
images. While `debian:13` was frozen for those sixteen days, every
CI image built from it apt-upgraded a fortnight of packages during
the build and carried the superseded versions into the published
blob. Measured on the plain `debian-13` label, where nothing
changed but the freshness of the base:

| Version | Size |
|---------|------|
| v61 (stale base) | 1320.4 MB |
| v62 (refreshed base) | 1146.3 MB |

174.1 MB, 13.2%. The same comparison on `debian-13-docker` is
quoted as 15% on #44, but that pair conflates the base refresh
with actions#66's own fix; the plain label above is the clean
measurement.

### The structural finding

The `eol-distro` criterion greps workflows for runner labels and
container images. That makes it a check on **consumers**. Every
place the fleet actually *produces* an end-of-life image is
invisible to it:

* **shakenfist/images** decides which releases get built at all.
  It is on the audit's excluded list as a historical archive,
  which it demonstrably is not.
* **private-ci** decides which images become runner labels, in
  `IMAGE_BUILDS` and `CI_IMAGES`. It is in scope for five plan
  criteria and `sfui-vendor`, and nothing else.
* **33fl's static runners** advertise only `self-hosted` and
  `static`, so no workflow anywhere names an operating system. A
  grep of every consuming repository finds nothing while every one
  of those jobs runs on a retired release.

So the three definitions that create the exposure sit outside the
criterion that bans it, and the 80 findings in #123 are the
downstream shadow of decisions the audit cannot read. Fixing the
80 without fixing the three means the count returns at the next
end-of-life date.

## Mission and problem statement

Close the nine open issues in a sequence that does not break CI on
the way through, and change the image pipeline so that the next
failure announces itself instead of waiting to be noticed.

Not in scope: what goes *inside* the images, the DIB patches
carried in shakenfist/images, and whether the fleet should consume
these images at all. 33fl is a different organisation with its own
conventions, so this plan tracks its one issue and does not
prescribe how it is fixed.

## Decisions

### D1. Signal before surgery

Every later phase changes something that produces images, and the
whole reason this plan exists is that we cannot currently tell when
image production breaks. Making those changes first and the
detection last would be running the same experiment that produced
the sixteen-day outage.

So detection comes first, even though it closes no migration issue
and resolves nothing on #123. Phases 1 and 2 are also the only
phases that address "so they don't occur again"; the rest is
cleanup that a future end-of-life date will otherwise recreate.

### D2. Verify the artifact, not the name

Three of the five silent failures were a published artifact that
did not match its own label: bullseye as `debian:12`, a docker
image with no docker, a runner label whose base no longer builds.
A freshness check catches none of these -- all three were current,
and two were being rebuilt nightly.

So the plan carries a separate phase that asserts what an image
*is* rather than when it was made. actions#66 already established
the pattern by ending the build with `docker version`; this
generalises it.

### D3. `debian-gnome-13` before the consumer sweep

private-ci#45 is the only issue on the critical path of #123.
`debian-gnome-12` is the last bookworm label with no successor, so
any repository whose workflows name it cannot be migrated until the
successor label exists. Everything else in #123 is a swap between
labels that both already work.

### D4. Retire producers last, and only after their consumers

private-ci#40's follow-up retires `debian-12` and
`debian-12-docker` from `IMAGE_BUILDS` and `CI_IMAGES`. Doing that
before #123 completes takes the runners out from under jobs that
still request them. The `debian-11` half of #40 has no such
constraint -- nothing requests it -- so it can go early.

### D5. This plan does not re-plan shakenfist/images

That repository has its own plan, merged in images#3, with seven
phases of its own. Its phase 1 (per-image failure isolation) and
phase 4 (the freshness watchdog) are load-bearing here, so they are
named in the phase table below, but the detail stays there rather
than being copied.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Alarm on absence | Not started | |
| 2. Verify the artifact, not the name | Not started | |
| 3. Unblock the migration | Not started | |
| 4. The consumer sweep | Not started | |
| 5. Retire the end-of-life producers | Not started | |
| 6. Close the audit's blind spot | Not started | |
| 7. Push audit | Not started | |

### 1. Alarm on absence

Closes: part of private-ci#44. Depends on: nothing.

Three producers, three independent detections. None of them may
depend on the thing it watches.

* **shakenfist/images**: phase 4 of that repository's plan. A
  scheduled `HEAD` against
  `images.shakenfist.com/<image>/latest.qcow2`, comparing
  `Last-Modified` against a 72-hour threshold, from a runner with
  no connection to the build host. This measures what a consumer
  receives, so it also catches a broken publish step or stale
  nginx.
* **private-ci**: `_update_status(image_ages=...)` already tracks
  blob age per label, so the data is in hand and the work is
  reporting it. File an issue, or surface it on the dashboard,
  when any label in `IMAGE_BUILDS` has no successful build within
  N days.
* **shakenfist/images** again: phase 1 of its plan, so one failing
  image stops one image. This is prevention rather than detection,
  but it belongs here because it is what makes the detection
  actionable -- an alarm that always fires for the whole list
  teaches people to ignore it.

Also fix the scheduler bug #44 documents: persist the last
completed nightly rather than recomputing `nightly_due` into a
local at every loop start. Note that #44 is honest that this
mechanism does **not** explain the 2026-09-12 miss, so the fourth
checkbox there -- working out what actually happened -- stays open
after this phase and may be a separate defect.

### 2. Verify the artifact, not the name

Closes: nothing on its own. Depends on: nothing.

The phase that would have caught three of the five failures, and
the one most likely to be dropped for being nobody's issue.

* **shakenfist/images**: after building an image, assert that it
  is what it claims. Reading `/etc/os-release` from the built
  image and comparing it against the release the build asked for
  is enough to have caught the two-year bullseye defect on its
  first night. This is currently listed under Future work in that
  repository's plan; this plan promotes it, because it is the only
  control that addresses D2.
* **private-ci**: confirm `debian-gnome:13` is genuinely trixie
  before wiring it up, per #45's own caveat, and adopt the
  actions#66 pattern -- end each image build by exercising the
  thing that image exists to provide.

### 3. Unblock the migration

Closes: private-ci#45, private-ci#39. Depends on: phase 2 for the
`debian-gnome:13` confirmation.

* **private-ci#45**: add a `debian-gnome-13` entry to
  `IMAGE_BUILDS` with `playbook: ansible/ci-image-desktop.yml`.
* **private-ci#39**: move the dependencies cache disk from
  `debian:11` to `debian:13`. `debian:11` can no longer be built
  at all -- `bullseye-security`'s `Release` expired 2026-09-08 --
  so this disk is pinned to a frozen, unpatchable base, and a
  missing `dependencies` label blocks all CI provisioning.
* While in `ci-dependencies.yml`, add `debian:13` and `rocky:10`
  to the cached image list. Neither is currently cached, so CI
  cannot test against current Debian even now that the label
  works. Reword the `base_image == "debian:11"` conditions at
  lines 50 and 61 to test for the bullseye interpreter quirk by
  name, since they stop reading as deliberate once debian:11 is
  gone.

### 4. The consumer sweep

Closes: development#123. Depends on: phase 3 for anything naming
`debian-gnome-12`.

80 references in 15 repositories, repository by repository rather
than as one sweep, so each change carries its own actionlint edit
and is reviewed against the workflows it touches.

Two things make this less mechanical than the count suggests:

* **Each move is two files.** A runner label change also needs the
  replacement declared in that repository's
  `.github/actionlint.yaml` under `self-hosted-runner: labels:`,
  in the same commit. actionlint fails a workflow naming an
  undeclared label, so missing this turns a one-line fix into a
  failing lint.
* **Two findings are not label swaps.** instar boots `debian:12`
  in a functional-test matrix deliberately covering several
  distributions, which is the `audit-ok: eol-distro` case rather
  than a migration. kerbside has two bookworm-tagged rust images
  needing a tag bump, which is a different decision.

The daily audit files a `consistency` issue per repository once
the criterion is live, so this phase tracks those rather than
duplicating them.

### 5. Retire the end-of-life producers

Closes: private-ci#40, 33fl#826. Depends on: phase 4, for the
Debian 12 half only.

* **private-ci#40, `debian-11`**: remove from `IMAGE_BUILDS` and
  `CI_IMAGES`. No workflow requests it, so this can go as soon as
  phase 1 lands -- it also removes the permanent `False` in every
  nightly cycle summary.
* **private-ci#40 follow-up, `debian-12` and `debian-12-docker`**:
  only after phase 4, per D4.
* **33fl#826**: delete the two GitLab static runner instances so
  `static_runner.yml` rebuilds them on `debian:13`, and confirm
  the six GitHub runners have rolled over. Consider a retire tool
  so this is not manual next time.

### 6. Close the audit's blind spot

Closes: nothing filed. Depends on: phases 3-5, so the producers
are compliant before they are measured.

The phase that stops the 80 findings coming back. Per the
structural finding above, all three producers sit outside the
criterion that bans what they produce.

* **shakenfist/images**: decide whether it joins the audit matrix.
  Its exclusion reads "historical archive", which is wrong -- it
  is built from nightly and the fleet's CI depends on its output.
  This is a change to `docs/audits/README.md` and `REPO_OVERRIDES`
  in `scripts/audit/repo.py`, and phase 5 of that repository's own
  plan expects it to be a deliberate decision rather than a side
  effect.
* **private-ci**: extend `only_checks` to cover a criterion that
  reads `IMAGE_BUILDS` and `CI_IMAGES` for end-of-life bases. It
  is currently scoped to five plan criteria and `sfui-vendor`.
* **33fl**: outside this organisation and this tooling. #826
  records the exposure in `group_vars/all/static_runners.yml`
  where the next reader will find it, which is the available
  answer; note here that a static runner fleet is structurally
  invisible to a label-based audit, so any future fleet of the
  same shape needs the same treatment.

An open question this phase must answer rather than assume: whether
the right instrument is extending `eol-distro` to read producer
definitions, or a separate criterion. They fail differently -- one
reports "this repository names a banned label", the other "this
repository *offers* one" -- and the issue titles are the fleet-wide
idempotency key, so the choice is not cosmetic.

### 7. Push audit

Per the push audit shared block in `PLAN-TEMPLATE.md`. Runs
`PUSH-AUDIT.md` over the accumulated diff of every phase against
`main`, not the diff of the last phase alone.

Phases landing in other repositories record `<repo> <sha> (#pr)`
and are audited against that repository's default branch as part
of the pull request that lands them; this phase cites those audits
rather than re-running them. That applies to most of this plan --
only phase 6 and this phase land here.

## Risks and mitigations

**The migration is done and the detection is not.** The most
likely failure of this plan is that phases 3-5 close visible
issues, phases 1, 2 and 6 close none, and the plan is declared
finished. That is the ordering D1 exists to prevent, and it is why
phases 1 and 2 come first despite closing nothing.

**Phase 4 is long and boring.** 15 repositories with an actionlint
edit each. The mitigation is that the daily audit files and closes
the per-repository issues itself, so progress is externally visible
rather than tracked by hand.

**Retiring a label early breaks CI fleet-wide.** D4 and the phase 5
ordering exist for this. A `debian-12` retirement before phase 4
completes takes runners out from under jobs still requesting them.

**`debian:11` is already unrecoverable.** It cannot be rebuilt, so
the published copies are all that will ever exist. Phase 3 moves
the dependencies disk off it; until then, do not delete those
copies -- they are load-bearing and were deliberately kept when the
mislabelled images were deleted on 2026-09-12.

## Administration and logistics

### Success criteria

* All nine issues are closed, or explicitly declined in writing
  here.
* An image that fails to build, or stops being built, produces a
  GitHub issue without a human looking for it -- demonstrated for
  each of the three producers.
* A published image that does not match its own name fails its
  build rather than publishing, demonstrated by a deliberate test.
* `eol-distro` reports zero findings across the fleet, and the
  producer definitions are inside something that measures them.
* No runner label is retired while any workflow still requests it.
* `pre-commit run --all-files` passes, and
  `scripts/audit-check.py` reports no new failures for this
  repository.

### Documentation index maintenance

One row in `docs/plans/index.md`, dated 2026-09-13, status kept
current as phases land. The row reaches `Complete` only when every
phase has completed, been abandoned or been superseded.

### Future work

* **Boot-testing published images.** Phase 2 asserts what an image
  says it is; nothing asserts that it boots. That is the largest
  remaining quality gap in the pipeline and it is a plan of its
  own.
* **A Fedora image that builds.** `fedora:43` and `fedora:44` have
  never built -- both fail on `grpcio-tools` needing a C++
  compiler -- so Fedora is absent from the nightly list entirely
  and the `fedora` convenience symlink points at an end-of-life
  `fedora:42`.
* **The convenience symlinks.** `debian` points at `debian:12` and
  should probably point at `debian:13`; `debian-docker` points at
  `debian-docker:12`, which served Debian 11 for two years.
* **Checksums or signatures for published images.** Consumers
  fetch `latest.qcow2` over HTTPS with no way to verify what they
  received.
* **The ~70 MB of unexplained variance** between two
  `debian-13-docker` builds made hours apart from the same
  refreshed base (1243.3 MB then 1314.1 MB). It does not threaten
  the 174 MB staleness measurement, but it means single size
  measurements are noisier than they look.

### Bugs fixed during this work

To be completed as phases land. Already fixed before this plan was
written, and recorded here because they are the evidence for it:
images#2 (the sixteen-day outage, the grub filesystem
incompatibility and the two-year bullseye mislabelling) and
actions#66 (`debian-13-docker` publishing without a docker client).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
