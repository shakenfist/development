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
| Phases 1-6 | images | `PLAN-image-build-modernisation.md`; see its own Execution table for what has landed |

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
  It sits on the audit's excluded list, whose stated reasons are
  internal tooling, historical archives and non-projects; none of
  the three fits a repository built from nightly that the fleet's
  CI depends on.
* **private-ci** decides which images become runner labels, in
  `IMAGE_BUILDS` and `CI_IMAGES`. It is in scope for four plan
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

## Open questions

### Q1. One criterion that reads producers, or a second criterion?

Phase 6 has to measure the producer definitions that `eol-distro`
cannot see. Either `eol-distro` grows the ability to read
`IMAGE_BUILDS`, `CI_IMAGES` and a build list, or a second
criterion does it.

**Default if nobody answers: a separate criterion.** The two
report different defects -- one says "this repository names a
banned label", the other says "this repository *offers* one" --
and they are fixed by different people. More decisively, an issue
title is the fleet-wide idempotency key for filing and closing,
and `scripts/tests/test_metadata.py` freezes those titles
precisely because changing one orphans every issue already open
under the old title. Widening `eol-distro`'s meaning changes what
its existing title claims; a new criterion carries a new title and
orphans nothing.

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

**Operative reading, added 2026-09-14.** Implementing phase 2
showed that "verify the artifact, not the name" taken literally is
the wrong way round: comparing the artifact against the build's own
inputs is exactly what would have passed every night for two years.
The reading that works is **verify the artifact against the name it
will be published under**, and the name is held by whatever
publishes rather than whatever builds. The correction in phase 2
sets out why. Anything else applying D2 -- the private-ci bullet in
that phase, and any future producer -- takes this reading.

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

### D6. 33fl gets no detection here

The Mission says 33fl is a different organisation with its own
conventions, and that applies to detection as much as to fixes.
Its static runners are named as the third producer in the
structural finding because the exposure is real and worth
recording, but phase 1 builds two detections, not three. 33fl#826
tracks its own rollover, and the note in
`group_vars/all/static_runners.yml` is where the exposure is
recorded for the next reader.

What belongs here instead is the general lesson: a static runner
fleet advertises no operating system, so it is structurally
invisible to a label-based audit. Any future fleet of that shape
needs the same treatment, and phase 6 says so.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Alarm on absence | Complete | images acccd2b (#5), images 47ed141 (#6), private-ci ae7b1f8 (#49), private-ci e1f8fb1 (#54), 33fl 6de1764 (#827) |
| 2. Verify the artifact, not the name | Complete | images 6028647 (#7), images 4800c72 (#8), images b872641 (#9), actions 2ac4a94 (#74), 33fl bbbd842 (#836) |
| 3. Unblock the migration | Not started | |
| 4. The consumer sweep | Not started | |
| 5. Retire the end-of-life producers | Not started | |
| 6. Close the audit's blind spot | Not started | |
| 7. Push audit | Not started | |

### 1. Alarm on absence

Closes: part of private-ci#44. Depends on: nothing.

**Status: complete, 2026-09-14.** Both detections are built,
merged and deployed. The shakenfist/images watchdog is images#5;
the conductor's persisted nightly and its staleness gauge are
private-ci#49; the stale-label issue is private-ci#54; the Grafana
rule watching the gauge is 33fl#827. images#6 landed the per-image
failure isolation this phase carries as prevention, and is audited
against the images repository's default branch as part of that
pull request, per phase 7.

The conductor deployed at 20:37 on 2026-09-13, carrying private-ci
`e1f8fb15`. It logged the priming path on startup -- it claimed that
day's slot rather than starting a rebuild in the evening -- and
Prometheus is scraping the gauge, which reads the primed slot rather
than zero. So the fallback that seeds from the claimed slot is
doing its job: without it a fresh deployment would read zero and
alert immediately despite having missed nothing.

**How the invariant at the head of this phase is satisfied.** The
gauge is exported by the conductor process itself, on maui, and
scraped by Prometheus on the same host. Taken alone that would
re-create failure 4: a conductor that is not running exports
nothing, and a rule that only compares a timestamp sees no data and
stays quiet. Two things prevent it, and both are load-bearing
rather than incidental.

`up{job="conductor"}` is synthesised by Prometheus when a scrape
succeeds or fails, not exported by the conductor, so it reports 0
for a conductor that is not running. The pre-existing `Host down`
rule in 33fl watches it. That is the detection which does not
depend on the thing it watches, and it was already in place before
this plan.

33fl#827 also sets `noDataState: Alerting`, so the absence of the
gauge alerts in its own right rather than reading as health.

Stating the division precisely, because the first draft of this
note got it wrong: private-ci#54's stale-label issues cover a
conductor that is running and reports a label going stale. A
conductor that is absent entirely is covered by `Host down` and by
the no-data state of 33fl#827, not by anything conductor-side. The
images producer is covered independently of all of it by images#5,
which reads the published site from a hosted runner.

**Do not use the 26 hour threshold this plan originally specified.**
It was taken from the `Nightly report not dispatched` rule, where
the thing being timed is a workflow dispatch and is instantaneous.
A full image rebuild is eleven images built serially and takes about
an hour and a half -- six runs between 2026-09-07 and 2026-09-13
took between 1h24m and 1h37m -- and the gauge advances on
completion, not on the slot. So the first completion after priming
lands 25.6 hours after the primed slot, which leaves 24 minutes of
margin against a 13 minute observed spread in build duration. The
rule would have had a real chance of firing spuriously on its first
night, which is the worst possible introduction for an alert.

Use 30 hours. A genuinely skipped night reaches 48, so anything
between roughly 28 and 44 separates "skipped" from "slow", and 30
still alerts within about four hours of when completion was due.
33fl#827 landed at 30 hours with that reasoning recorded beside it.

The general point, for any future rule of this shape: a threshold
over a completion timestamp has to cover the slot interval plus how
long the work takes, not just the slot interval.

**What follows is the original specification**, kept verbatim
because the phase 2 correction below shows what is lost by quietly
editing a plan to match what was built. It was departed from in one
place. The private-ci bullet specifies "a scheduled job elsewhere
that reads the published dashboard or the conductor's API"; what
landed reads a Prometheus gauge through a Grafana rule instead,
because that estate already existed, already scraped the conductor,
and already carried two rules of exactly this shape to copy. The
requirement the bullet was protecting -- that the detection not
depend on the thing it watches -- is met as set out above. The
scheduler-persistence item in the last paragraph is done, in
private-ci#49.

Two detections, for the two producers this organisation
controls. Neither may depend on the thing it watches -- a
component that has stopped running also stops reporting that it
has stopped running, which is how failure 4 stayed invisible for a
day. 33fl's static runners get no detection here, per D6.

* **shakenfist/images**: phase 4 of that repository's plan. A
  scheduled `HEAD` against
  `images.shakenfist.com/<image>/latest.qcow2`, comparing
  `Last-Modified` against a 72-hour threshold, from a runner with
  no connection to the build host. This measures what a consumer
  receives, so it also catches a broken publish step or stale
  nginx.
* **private-ci**: the conductor already tracks blob age per label
  in `_update_status(image_ages=...)`, so the data exists. It must
  not be the conductor that reports on it, though: the conductor is
  the component whose restart silently skipped a nightly rebuild,
  and a conductor that is not running cannot tell anyone it is not
  running. The detection is therefore a scheduled job elsewhere
  that reads the published dashboard or the conductor's API and
  files an issue when any label's age exceeds N days, or when the
  cycle summary is absent or stale.

Phase 1 also carries one piece of prevention, because it is what
makes the detection actionable: phase 1 of the shakenfist/images
plan, so one failing image stops one image rather than the whole
run. An alarm that fires for the entire list every time teaches
people to ignore it.

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

**Status: complete, 2026-09-16.** `verify-release` is in the
element list of all fourteen images published on the night of
2026-09-16, the build host is on images `b872641` with a
root-owned checkout, and the desktop and dependency assertions are
on `actions` `main`. private-ci#45's first checkbox is ticked with
the artifact read directly -- Debian 13.7, trixie, GNOME 48 -- so
phase 3's stated dependency is recorded rather than remembered.

**What this phase cost, which belongs in phase 7's audit.** The
self-update it shipped (images#8) broke the nightly build on its
first night. `build.sh` runs under errexit, and
`before=$(git rev-parse HEAD)` on a line of its own is a simple
command, so when git refused the checkout on ownership the run
ended at 05:00:01 having published nothing. The host has no MTA,
so cron discarded the one line that explained it. It was found by
the completion check at the head of phase 3's planning, not by any
alarm: `tools/check-image-freshness.sh` uses a 72 hour threshold
and would not have reported it until 2026-09-17.

Two fixes, one per cause: images#9 puts every git command inside
the `if` condition so any git failure warns and builds anyway, with
two regression tests that exit 128 against the previous script;
33fl#836 owns the checkout as the user cron runs as, and asks
cron's question by stripping `SUDO_UID` rather than sudo's. The
general lesson for the rest of this plan: a check run under `sudo`
is not the check cron runs, and git is one of several tools that
behaves differently between them.

* **shakenfist/images**: after building an image, assert that it
  is what it claims. Landed 2026-09-13 as the `verify-release`
  element (images#7). This was listed under Future work in that
  repository's plan; this plan promoted it, because it is the only
  control that addresses D2.

  **Correction, 2026-09-13.** This bullet used to say that reading
  `/etc/os-release` and comparing it against "the release the build
  asked for" was enough to have caught the two-year bullseye defect
  on its first night. That is wrong, and the way it is wrong is the
  most useful thing in this phase.

  Nothing in those builds disagreed with itself. `build.sh` passed
  `DIB_RELEASE=bullseye`, diskimage-builder built bullseye, and the
  image honestly reported bullseye. Every comparison between the
  image and the build's own inputs would have passed, every night,
  for two years and two months. What was wrong was the name the
  artifact was published under.

  So the invariant worth asserting is not "the image matches what
  was requested" but "the image matches what it is about to be
  called" -- and the name is held by the thing doing the publishing,
  which is usually not the thing doing the building. The element
  therefore compares against the publish label, which `build.sh`
  passes in, and keeps the `DIB_RELEASE` comparison only as a
  secondary check.

  D2 says "verify the artifact, not the name". Read literally that
  is the wrong way round: verifying the artifact against the build
  is what would have failed here. Read as "verify the artifact
  against the name it will be published under", which is what it
  was reaching for, it is exactly right. Anything else applying D2
  -- the private-ci bullet below, and any future producer -- should
  take the second reading.
* **private-ci**: confirm `debian-gnome:13` is genuinely trixie
  before wiring it up, per #45's own caveat, and adopt the
  actions#66 pattern -- end each image build by exercising the
  thing that image exists to provide.

### 3. Unblock the migration

Closes: private-ci#45 (partly -- see decision 5), private-ci#39.
Depends on: phase 2, satisfied. Planning effort: high, because the
sequencing spans two repositories and the gnome snapshot machinery
is not where the original sketch said it was.

Two labels are stuck. `debian-gnome-12` has no trixie successor, so
the `eol-distro` criterion bans a label the fleet still needs. And
the dependencies cache disk -- which every runner and inner CI
primary mounts at `/srv/ci`, and whose absence blocks *all* CI
provisioning -- is built from `debian:11`, a base that can no longer
be built at all: `bullseye-security`'s `Release` expired
2026-09-08.

#### What the survey found

Checked 2026-09-16 against `private-ci` `84f39cc` and `actions`
`origin/main`. The original sketch for this phase was written
before phase 2 executed. Most of it survived; one claim was
materially incomplete and one was a near miss.

**Confirmed as written.**

* `debian-gnome-13` really is absent from `IMAGE_BUILDS`.
  `debian-gnome-12` sits at `conductor/imagebuilder.py:120` with
  `playbook: ansible/ci-image-desktop.yml`, exactly as private-ci#45
  quotes it.
* The dependencies entry really is `base_image: 'debian:11'`, at
  `conductor/imagebuilder.py:143`.
* The two conditions to reword really are at
  `ansible/ci-dependencies.yml:50` and `:61`, still at those exact
  line numbers on `origin/main` after phase 2 edited that file.
* `debian:13` and `rocky:10` really are absent from the cached image
  list (`ansible/ci-dependencies.yml:140-176`).

**Materially incomplete: the gnome snapshot is not one line.** The
sketch treats "should the cache disk snapshot `debian-gnome-13`
instead" as a decision. It is a decision, but acting on it touches
seven places across two repositories, and the master plan did not
say so:

* `conductor/imagebuilder.py:225` -- `GNOME_LABEL =
  'debian-gnome-12'`, a module constant read in six places,
  including the gnome-less marker (`:507-538`), the nightly
  scheduling (`:871`) and the operator log line (`:1213`).
* `ansible/ci-dependencies.yml` -- the label is hardcoded in the jq
  selector at `:220`
  (`select(.source_url == "sf://label/ci-images/debian-gnome-12")`),
  in the skip message at `:230`, and in the download path
  `/tmp/debian-12-gnome-agents` at `:252-253`.

`GNOME_LABEL` and the playbook have to move together. The marker
exists so that a cluster which built `dependencies` before the gnome
label existed rebuilds it once the label appears; if the constant
watches `debian-gnome-12` while the playbook snapshots
`debian-gnome-13`, that rebuild is triggered by the wrong label's
arrival.

**A near miss worth recording so nobody else chases it.**
`ansible/ci-image-desktop.yml:151` hardcodes
`label: "ci-images/debian-gnome-12"`, which looks like it would send
a `debian-gnome-13` build to the 12 label. It does not: the
conductor passes `label` in `extra_vars`
(`conductor/imagebuilder.py:791`, `'label': 'ci-images/%s' %
image['label']`), which overrides the play's default. The same is
true of `ci-dependencies.yml:8`'s `base_image: "debian:11"`. Both
are stale defaults that only bite somebody running the playbook by
hand, and both should be corrected while in the file rather than
left as traps.

**Two findings out of scope, recorded here rather than fixed.**

* The cached image list carries `ubuntu:20.04`, `debian:11` and
  `fedora:40`. `shakenfist/images` builds none of those any more --
  its list is `ubuntu:22.04 ubuntu:24.04 centos:9-stream debian:12
  debian-docker:12 debian-gnome:12 debian-xfce:12 debian:13
  debian-docker:13 debian-gnome:13 debian-xfce:13 rocky:8 rocky:9
  rocky:10`. All three URLs still return 200, so CI is quietly
  caching three frozen artifacts, two of them end of life. That is
  inventory work for phase 4 and retirement for phase 5, and it
  belongs to private-ci#38's collated inventory. File it there
  rather than widening this phase.
* `build.sh`'s reconciliation summary -- `Built:` / `Failed:` /
  `Not attempted:` at `build.sh:736-747` -- is printed to stdout
  only. Per-image logs ship to Loki; the summary does not. Under
  cron on a host with no MTA it is discarded, so the one output that
  distinguishes "never attempted" from "built fine" reaches nobody.
  images#6 built that reconciliation precisely to make that state
  visible. File against `shakenfist/images`; it is a phase 1
  detection gap rather than a phase 3 migration step.

#### Decisions

1. **Order is: label, then base image, then snapshot.** Add
   `debian-gnome-13` first, because steps 3 and 4 need the label to
   exist before anything can point at it. Move the dependencies base
   image second. Switch the gnome snapshot last.

2. **The cross-repository ordering constraint is real and is
   stated.** Deleting the `debian:11` interpreter branch from
   `ci-dependencies.yml` must land *after* the `IMAGE_BUILDS` base
   image move has built successfully, not before. While
   `dependencies` still builds on `debian:11`, removing that branch
   sends bullseye down the auto-detect path -- which is the quirk
   the branch exists for. Two pull requests in two repositories,
   with a build in between, not one flag day.

3. **Delete the `debian:11` branch rather than reword it.** The
   master plan says to reword the two `when:` conditions to name the
   bullseye interpreter quirk. Once the dependencies entry is on
   `debian:13`, nothing invokes `ci-dependencies.yml` with
   `base_image: debian:11` at all -- it is the only entry that uses
   that playbook -- so the condition is not obscure, it is dead. Two
   `add_host` tasks collapse to one with no `when:`. This is a
   deliberate departure from the master plan's wording, on the
   grounds that a clearly-named condition for a case that cannot
   occur is still a thing the next reader has to rule out.

4. **Yes, switch the snapshot to `debian-gnome-13`.** private-ci#45
   leaves it open. The cache disk exists so CI does not pull from
   the network, and a cache of the EOL desktop is a cache of the
   thing phase 5 is about to delete. Switching now means one nightly
   cycle in which the disk still carries the 12 snapshot, which is
   harmless.

5. **private-ci#45 is not closed by this phase.** Its fourth
   checkbox -- retire `debian-gnome-12` once nothing consumes it --
   is phase 5's work, and this phase deliberately leaves both
   `debian-gnome-12` and `debian-11` building so nothing breaks
   mid-plan. The issue keeps three of four boxes ticked and closes
   in phase 5. **This is the decision most likely to be argued
   with:** it leaves two end-of-life labels building for two more
   phases, and `eol-distro` will keep reporting them the whole time.
   The alternative -- retire as we go -- couples this phase to
   finding every consumer, which is exactly what phase 4 is for.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | In `shakenfist/private-ci`, add a `debian-gnome-13` entry to `IMAGE_BUILDS` in `conductor/imagebuilder.py`, immediately after the `debian-gnome-12` entry at line 120. Copy its shape exactly: `name` and `label` both `debian-gnome-13`, `base_image` `debian-gnome:13`, `base_image_user` `debian`, `playbook` `ansible/ci-image-desktop.yml`. Do not touch `GNOME_LABEL` (line 225) in this step. Update `conductor/tests/test_imagebuilder.py` -- lines 91 and 117 assert over the build order and the missing-label set, and both enumerate labels. Run `tox` (or the repo's test command) and confirm green. Commit subject: "Add a debian-gnome-13 CI image." |
| 3b | low | haiku | none | Wait for the conductor to build the new label and confirm `ci-images/debian-gnome-13` exists before step 3c starts. This is an observation step, not a code change: `sf-client --json artifact list` filtered on `sf://label/ci-images/debian-gnome-13`, or the conductor's own log. Report the label's blob uuid. No commit. |
| 3c | high | opus | worktree | In `shakenfist/private-ci`, change the `dependencies` entry in `conductor/imagebuilder.py:143` from `base_image: 'debian:11'` to `'debian:13'`. `base_image_user` stays `debian`. Read the comment block at lines 126-140 before editing -- it explains the gnome-less marker and the first/last build ordering, and it names `debian-gnome-12`; leave that naming alone, step 3e moves it. Check whether any test in `conductor/tests/test_imagebuilder.py` asserts the dependencies base image. Also check `conductor/provisioner.py:47` -- it carries a separate `debian-11` entry with `upstream: debian:11`; that is the runner boot label, not the cache disk, and is out of scope. High effort because the dependencies label gates all CI provisioning: if this build fails, nothing provisions. Commit subject: "Build the dependencies disk on Debian 13." |
| 3d | medium | sonnet | none | In `shakenfist/actions`, edit `ansible/ci-dependencies.yml`. (1) Add `debian:13` and `rocky:10` to the cached image list at lines 140-176, following the existing `- { url: ..., name: ... }` shape exactly. (2) Delete the `Add to ansible (force python3)` task at lines 40-51 and remove the `when: base_image != "debian:11"` from the task at 52-62, so one unconditional `add_host` remains -- see decision 3. (3) Change the stale default at line 8 from `base_image: "debian:11"` to `"debian:13"`. **This must not merge until step 3c has built successfully** -- see decision 2. Verify with `tools/ansible-syntax-check.sh`, which phase 2 added. Commit subject: "Cache Debian 13 and Rocky 10, drop bullseye." |
| 3e | high | opus | worktree | Move the dependencies disk's gnome snapshot from `debian-gnome-12` to `debian-gnome-13`, across two repositories, as two pull requests. In `shakenfist/private-ci`: `GNOME_LABEL` at `conductor/imagebuilder.py:225`, and re-read its six uses (507-538, 871, 1213) plus the comment block at 126-140, which describes the behaviour in terms of the old label. In `shakenfist/actions/ansible/ci-dependencies.yml`: the jq selector at line 220, the skip message at 230, and the `/tmp/debian-12-gnome-agents` path at 252-253 -- rename that path too, it names the release. Also fix the stale play default at `ansible/ci-image-desktop.yml:151`. High effort because the gnome-less marker decides when a cluster rebuilds its cache disk, and a constant that watches one label while the playbook snapshots another produces a rebuild that never fires. Commit subjects: "Snapshot the Debian 13 desktop image." in each repository. |
| 3f | low | haiku | none | Housekeeping. Tick checkboxes two and three on private-ci#45 and leave it open per decision 5, saying in a comment which phase closes it. Close private-ci#39 with the merge commits. File the two out-of-scope findings from the survey: the frozen cache entries against private-ci#38, and the discarded reconciliation summary against `shakenfist/images`. No commit in this repository. |

#### Risks and mitigations

* **The dependencies build fails on trixie and CI stops
  provisioning.** This is the real risk in the phase: a missing
  `dependencies` label blocks everything. Mitigated by step 3c being
  its own pull request with nothing else in it, so a revert is one
  commit; and by `_build_order()` already building `dependencies`
  first when its label is missing, so recovery is the next scan
  rather than a manual intervention. Checked by whoever merges 3c,
  by watching the conductor build the label before merging 3d.
* **3d merges before 3c builds.** Then bullseye takes the
  auto-detect interpreter path and the dependencies build breaks for
  the reason the deleted branch existed. Mitigated by decision 2
  being stated in the step brief itself rather than only here, and
  by 3b existing as an explicit gate.
* **The gnome snapshot switch half-lands.** `GNOME_LABEL` in one
  repository and the playbook in another cannot merge atomically.
  Mitigated by ordering: merge the playbook first (it snapshots
  whatever label it is told to look up, and the lookup failing is
  already handled -- the snapshot is skipped with a warning), then
  the constant. A skipped snapshot for one night is recoverable; a
  marker watching a label nobody builds is not self-correcting.
* **`debian-gnome:13` turns out not to boot a desktop under CI even
  though the guest image is correct.** Phase 2 confirmed the
  artifact is trixie with GNOME 48 and that it reaches the gdm3
  greeter. `ci-image-desktop.yml` now proves this at build time
  (actions#74), so this fails the build loudly rather than producing
  a label that looks fine.

#### Definition of done

* `IMAGE_BUILDS` contains a `debian-gnome-13` entry and
  `ci-images/debian-gnome-13` exists as a label with a blob.
* `grep -rn 'debian:11' conductor/imagebuilder.py` returns only the
  `debian-11` runner boot label at lines 59-62, and no dependencies
  entry.
* `ansible/ci-dependencies.yml` contains no `when:` clause
  mentioning `debian:11`, and `ansible-playbook --syntax-check`
  passes via `tools/ansible-syntax-check.sh`.
* The cached image list contains `debian:13` and `rocky:10`, and a
  `dependencies` disk built after this phase has `/srv/ci/cached`
  entries for both with non-zero size -- the playbook's own
  "Confirm every cache entry has content" assertion at line 318
  covers this, so a successful build is the evidence.
* `grep -rn 'debian-gnome-12' conductor/ ansible/` across both
  repositories returns only the `IMAGE_BUILDS` entry that phase 5
  retires -- no constant, no jq selector, no `/tmp` path.
* private-ci#39 is closed; private-ci#45 has boxes one, two and
  three ticked, box four open, and a comment naming phase 5 as its
  closer.
* Two issues exist for the out-of-scope findings.

#### Back brief

Confirm before step 3c is written: that building the dependencies
cache disk on `debian:13` is acceptable given the disk is snapshotted
and mounted by every runner, and that nothing consuming `/srv/ci`
depends on the disk's own filesystem being bullseye-era. The step is
cheap to propose and expensive to get wrong -- a broken dependencies
label stops all CI provisioning -- and the answer lives in what
mounts the disk rather than in what builds it.

### 4. The consumer sweep

Closes: development#123, private-ci#38. Depends on: phase 3 for
anything naming `debian-gnome-12`.

private-ci#38 is the collated inventory of where the fleet uses
obsolete base images. It is a reference rather than a task, and it
closes when the thing it inventories is gone: this phase clears
the consumer half and phase 5 clears the producer half, so #38
closes at the end of phase 5 rather than when its own checklist is
ticked.

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

The daily audit already files a `consistency` issue per
repository, so this phase tracks those rather than duplicating
them.

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

* **shakenfist/images**: decide whether it joins the audit matrix,
  and on what terms. Scope is stated in three places and a change
  has to make all three agree in one commit, or `scope-coverage`
  reports the repository as decided nowhere and
  `AuditScopeIsStatedOnceTest` fails: the `repo:` matrix in
  `.github/workflows/consistency-audit.yml`, which is what
  actually runs, and the in-scope and excluded lists in
  `docs/audits/README.md`, which are what a reader is told.
  `REPO_OVERRIDES` in `scripts/audit/repo.py` is not a scope
  statement -- it narrows a repository already in the matrix -- so
  it is only edited if images is to be scoped to a subset.

  Joining is otherwise all-or-nothing: in the matrix means all 52
  criteria apply. Measured against the current clone on
  2026-09-13, images would report **8 fail, 6 pass, 38 not
  applicable**. The eight are `llm-context-lint-ci`, `renovate`,
  `ci-review-automation`, `pre-commit-config`, `export-repo-config`,
  `default-branch-naming`, `github-security` and
  `delete-branch-on-merge`. That is a morning of `consistency`
  issues rather than a wall, and every one of them is already an
  item in phase 5 of that repository's own plan, so the decision
  is whether to file them or do them first -- not whether the
  repository can survive being measured. Re-measure before acting;
  this number is from before phase 5 runs.
* **private-ci**: extend `only_checks` to cover a criterion that
  reads `IMAGE_BUILDS` and `CI_IMAGES` for end-of-life bases. It
  is currently scoped to four plan criteria and `sfui-vendor` --
  five `only_checks` entries in total.

  A partial scope is stated a fourth time, as the sentence in the
  partial-scope paragraph of `docs/audits/README.md` that
  `scripts/audit/scope.py` parses and holds against `only_checks`.
  Its docstring records that this is the statement with the worst
  track record: `only_checks` was widened once with the sentence,
  and two other documents, left behind and no test noticing. So
  the same commit updates the sentence in `docs/audits/README.md`,
  the `REPO_OVERRIDES` comment block in `scripts/audit/repo.py`,
  and the comment above `- private-ci` in the audit matrix -- which
  is already stale, still reading "Scoped to the sfui-vendor
  check".
* **33fl**: outside this organisation and this tooling. #826
  records the exposure in `group_vars/all/static_runners.yml`
  where the next reader will find it, which is the available
  answer; note here that a static runner fleet is structurally
  invisible to a label-based audit, so any future fleet of the
  same shape needs the same treatment.

Which instrument does the measuring is Q1 above, and the default
there is a separate criterion.

### 7. Push audit

Per the push audit shared block in `PLAN-TEMPLATE.md`. Runs
`PUSH-AUDIT.md` over the accumulated diff of every phase against
`main`, not the diff of the last phase alone.

Phases landing in other repositories record `<repo> <sha> (#pr)`
and are audited against that repository's default branch as part
of the pull request that lands them; this phase cites those audits
rather than re-running them. That applies to most of this plan --
only phase 6 and this phase land here.

## Agent guidance

Deliberately short. Six of the seven phases land in other
repositories, under their own conventions and, for
shakenfist/images, its own `PLAN-TEMPLATE.md` and the
project-specific checks in it. Per-step effort levels, model
recommendations and review checklists belong in the plan of the
repository the work lands in, not here, so this plan does not
carry the shared blocks `PLAN-TEMPLATE.md` offers for them.

The exception is phase 6, which lands here. It changes what the
fleet is measured against and reaches `scripts/audit/`, so it is
high effort per this repository's own guidance, and it is
exercised with `--dry-run` before anything files an issue.

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

* Every one of the nine issues is closed, explicitly declined in
  writing here, or reduced to a named residual recorded in this
  plan. The residual case is not a loophole: phase 1 closes only
  part of private-ci#44 and says so, because that issue's fourth
  checkbox -- what actually caused the 2026-09-12 miss -- is a
  separate defect that the scheduler fix does not explain.
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

* **Boot-testing published images.** Phase 2 asserts that an image
  matches the name it is published under; nothing asserts that it
  boots. That is the largest
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

Fixed as phases landed:

* **The conductor skipped a nightly rebuild on restart**
  (private-ci#49, phase 1). `builder_loop` recomputed `nightly_due`
  from the clock into a local at every start, so a conductor coming
  back after the nightly hour set the next rebuild to tomorrow and
  nothing recorded that tonight had not happened. The slot is now
  claimed in the database. This does **not** explain the 2026-09-12
  miss that prompted private-ci#44 -- that issue's fourth checkbox
  stays open as a possibly separate defect.
* **A nightly that failed halfway could be retried in full by every
  subsequent restart** (private-ci#49, phase 1). Found while fixing
  the above; the slot is claimed when the cycle starts rather than
  when it completes, while the staleness metric reads only
  completions.

Already fixed before this plan was written, and recorded here
because they are the evidence for it:
images#2 (the sixteen-day outage, the grub filesystem
incompatibility and the two-year bullseye mislabelling) and
actions#66 (`debian-13-docker` publishing without a docker client).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
