"""The criteria about where a workflow runs.

Which runners a job asks for, whether the labels it names are static
enough to schedule against, and whether a VM job declares a size.
"""

import os
import re

from audit.check import Check
from audit.text.workflows import (
    BIND_PREFIX, BOUND_PREFIX, RUNS_ON_RE, STATIC_ALLOWED_LABELS,
    is_runner_label_value, literal_runner_labels, parse_runner_labels,
    split_runner_labels,
)


# GitHub-hosted runner labels (e.g. ubuntu-latest, windows-2022,
# macos-15, ubuntu-24.04-arm). Self-hosted runner labels never use
# these names.
GITHUB_HOSTED_LABEL_RE = re.compile(
    r'\b(?:ubuntu|windows|macos)-(?:latest|\d+(?:\.\d+)?)'
    r'(?:-(?:arm|arm64|large|xlarge))?\b'
)


# Marker acknowledging a deliberate exception, placed on the
# offending line or the line immediately above it.
RUNNER_EXCEPTION_RE = re.compile(r'audit-ok:\s*github-hosted-runner')


# The runner sizes the CI conductor knows about. A "vm" job which
# names none of these has not chosen a size: the conductor matches the
# size out of the labels and falls back to the first entry in its
# CI_SIZES table when it finds none, which is "xs" -- one vCPU and
# 2048 MB -- so the omission is a silent downgrade to the smallest
# runner rather than a job left free to take any runner.
#
# The source of truth is CI_SIZES in shakenfist/private-ci's
# conductor/provisioner.py, which this repository cannot see. A size
# added there and not added here turns every job which correctly names
# it into a finding on the next scheduled run, so the two move
# together -- test_the_size_vocabulary_matches_the_specification keeps
# this list and the spec page in step, but nothing can reach across to
# the conductor.
#
# 's-cloud' and 's-cluster' mirror the two CI_SIZES entries for jobs
# which build a nested test cloud from a small runner. They have the
# same footprint as 's' (2 vCPU, 4096 MB): 's-cloud' is for a nested
# cloud on one node and 's-cluster' for a multi-node cluster.
VM_SIZE_LABELS = frozenset({
    'xs', 's', 'm', 'l', 'xl', 'm-bigdisk', 'xl-bigdisk',
    's-cloud', 's-cluster',
})


# Marker acknowledging a deliberate exception, placed on the offending
# line or the line immediately above it. The escape hatch exists
# because the fleet's other runner checks have one and a repository
# with a real reason needs an answer other than "edit the workflow";
# it is not a way to keep an unsized job, since writing "xs" costs one
# word and states the same decision honestly.
VM_SIZE_EXCEPTION_RE = re.compile(r'audit-ok:\s*vm-runner-size')


# The labels a bound job may name besides its bound- and bind-
# elements. Anything else -- vm, a plain image or size, an expression
# that could evaluate to one -- would let an unbound job take the
# runner started for it, because GitHub hands a job to any runner whose
# labels are a superset of the job's.
BOUND_ALLOWED_LABELS = frozenset({'self-hosted'})

# What _bound_form_problem() returns for the one bound problem the
# audit-ok marker may excuse: a size arriving through an expression.
BOUND_UNSIZED = 'unsized'

# The bind- token must vary per run and per attempt, or a run, or a
# re-run of it, could take a runner started for another.
BIND_RUN_ID_RE = re.compile(r'\$\{\{[^}]*\bgithub\.run_id\b')
BIND_RUN_ATTEMPT_RE = re.compile(r'\$\{\{[^}]*\bgithub\.run_attempt\b')


def _bound_form_problem(raw_labels):
    """Judge a runs-on that uses the bound form.

    Returns None when `raw_labels` does not use it at all, '' when it
    uses it correctly, and otherwise a short reason it does not.
    BOUND_UNSIZED is returned for the one problem the audit-ok marker may
    excuse: a size which could only arrive through an expression.
    """
    bound = [x for x in raw_labels if x.startswith(BOUND_PREFIX)]
    bind = [x for x in raw_labels if x.startswith(BIND_PREFIX)]
    if not bound and not bind:
        return None

    others = [
        x for x in raw_labels
        if x not in bound and x not in bind
        and x not in BOUND_ALLOWED_LABELS
    ]
    if others:
        return (
            'names %s beside the bound form; a bound job names no '
            'plain image, size or "vm" label, or an unbound job could '
            'take its runner' % ', '.join(others))
    if not bind:
        return (
            'has a bound- label but no "bind-<token>" element; a '
            'binding needs its token')
    if len(bind) > 1:
        return 'has more than one "bind-" element'
    if not (BIND_RUN_ID_RE.search(bind[0])
            and BIND_RUN_ATTEMPT_RE.search(bind[0])):
        return (
            'has a "bind-" token without ${{ github.run_id }} and '
            '${{ github.run_attempt }}; a token that is the same in '
            'every run or attempt lets one take another\'s runner')
    if 'self-hosted' not in raw_labels:
        return 'names no "self-hosted" label'
    if len(bound) != 2:
        return (
            'names %d bound- label(s); a bound job names exactly one '
            '"bound-<image>" and one "bound-<size>"' % len(bound))

    sizes = [
        x for x in bound
        if '${{' not in x and x[len(BOUND_PREFIX):] in VM_SIZE_LABELS
    ]
    if len(sizes) > 1:
        return 'names two bound sizes (%s)' % ', '.join(sizes)
    if sizes:
        return ''
    if any('${{' in x for x in bound):
        return BOUND_UNSIZED
    return 'names no "bound-<size>" with a size from the vocabulary'


class SelfHostedRunners(Check):
    id = 'self-hosted-runners'
    spec = 'docs/audits/workflow-standards.md'
    template = None
    issue_title = 'Workflow standards (self-hosted runners)'
    column = 'Runners'

    def run(self, repo):
        """Check workflows use self-hosted runners.

        GitHub-provided runner minutes are limited per month, so jobs
        must run on self-hosted runners except under exceptional
        circumstances (e.g. Windows or macOS builds needing hardware we
        don't own). Exceptions are marked with an
        'audit-ok: github-hosted-runner' comment on the offending line
        or the line immediately above it.

        We scan every workflow line for GitHub-hosted runner labels
        rather than just runs-on lines, so matrix values that feed
        'runs-on: ${{ matrix.os }}' are caught too.
        """
        if not repo.props['has_workflows_dir']:
            return self.skip('No .github/workflows/ directory')

        workflows = repo.workflows()
        if not workflows:
            return self.skip('No workflow files found')

        offenders = []
        for wf in sorted(workflows):
            filepath = os.path.join(
                repo.path, '.github', 'workflows', wf
            )
            with open(filepath, 'r', errors='replace') as f:
                lines = f.read().splitlines()

            for i, line in enumerate(lines):
                match = GITHUB_HOSTED_LABEL_RE.search(line)
                if not match:
                    continue
                if 'self-hosted' in line:
                    continue
                if not is_runner_label_value(line, match.start(), match.end()):
                    continue
                if RUNNER_EXCEPTION_RE.search(line):
                    continue
                if i > 0 and RUNNER_EXCEPTION_RE.search(lines[i - 1]):
                    continue
                offenders.append(f'{wf}:{i + 1} ({match.group(0)})')

        if offenders:
            return self.fail(
                f'{len(offenders)} unmarked GitHub-hosted runner '
                f'reference(s): {", ".join(offenders)}. Move to a '
                f'self-hosted runner, or mark deliberate exceptions '
                f'with an "audit-ok: github-hosted-runner" comment')
        return self.ok(
            f'No unmarked GitHub-hosted runner references in '
            f'{len(workflows)} workflow(s)')


class StaticRunnerTags(Check):
    id = 'static-runner-tags'
    spec = 'docs/audits/workflow-standards.md'
    template = None
    issue_title = 'Workflow standards (static runner tags)'
    column = 'Static tags'

    def run(self, repo):
        """Check that static-runner jobs request only the static labels.

        A static runner advertises exactly the 'self-hosted' and 'static'
        labels. Adding a size (e.g. 's'), 'vm', or an operating system
        label (e.g. 'debian-12') alongside 'static' asks for a runner
        that does not exist, so the job waits forever without being
        scheduled. Such jobs must use '[self-hosted, static]' exactly.

        We scan every 'runs-on:' line, so both the job-level and
        matrix-expansion forms are covered; unresolvable '${{ ... }}'
        expressions are skipped.
        """
        if not repo.props['has_workflows_dir']:
            return self.skip('No .github/workflows/ directory')

        workflows = repo.workflows()
        if not workflows:
            return self.skip('No workflow files found')

        offenders = []
        for wf in sorted(workflows):
            filepath = os.path.join(
                repo.path, '.github', 'workflows', wf
            )
            with open(filepath, 'r', errors='replace') as f:
                lines = f.read().splitlines()

            for i, line in enumerate(lines):
                match = RUNS_ON_RE.match(line)
                if not match:
                    continue
                labels = parse_runner_labels(match.group(1))
                if labels is None or 'static' not in labels:
                    continue
                extras = [
                    label for label in labels
                    if label not in STATIC_ALLOWED_LABELS
                ]
                if extras:
                    offenders.append(
                        f'{wf}:{i + 1} ({", ".join(extras)})'
                    )

        if offenders:
            return self.fail(
                f'{len(offenders)} static runner job(s) requesting '
                f'impossible extra label(s): {", ".join(offenders)}. '
                f'A static runner only advertises the "self-hosted" '
                f'and "static" labels, so requiring a size, "vm", or '
                f'an operating system label alongside "static" means '
                f'the job will never be scheduled. Use '
                f'"[self-hosted, static]" exactly')
        return self.ok(
            f'No static runner jobs request impossible labels in '
            f'{len(workflows)} workflow(s)')


class VmRunnerSize(Check):
    id = 'vm-runner-size'
    spec = 'docs/audits/workflow-standards.md'
    template = None
    issue_title = 'Workflow standards (vm runner size)'
    column = 'VM size'

    def run(self, repo):
        """Check that every 'vm' runner job names a size.

        This is the complement of check_static_runner_tags(): a static job
        must name *no* size because the static pool advertises none, and a
        vm job must name *one* because the conductor otherwise picks for
        it. The two failures look nothing alike -- an over-labelled static
        job is never scheduled and is noticed within one run, whereas an
        under-labelled vm job runs perfectly well on a machine nobody
        chose, and shakenfist/shakenfist#3696 went months that way.

        'xs' counts as naming a size. The rule is that the size is a
        decision, not that it is a large one, so a job which genuinely
        wants the smallest runner says so and passes.

        We scan every 'runs-on:' line and read the labels which are
        statically known, so a line pairing a matrix expression with a
        literal size passes. A line whose size could only arrive through
        an expression is a finding: the fleet writes the size literally
        even when the operating system comes from the matrix. A job which
        genuinely cannot name a size marks the line 'audit-ok:
        vm-runner-size'.

        Only the inline-list and scalar forms of 'runs-on:' are examined,
        because RUNS_ON_RE needs a value on the same line; a block
        sequence spread over the following lines is invisible here. No
        repository in scope writes one, and the sibling runner checks have
        the same blind spot, so this is recorded rather than handled.

        A job bound to its own runner has no 'vm' label, so it is
        recognised by any 'bound-' or 'bind-' element instead. It
        passes when it names 'self-hosted', exactly one 'bind-' element
        (always an expression, so never needing a marker), exactly one
        'bound-<image>' and exactly one literal 'bound-<size>', and
        nothing else; anything else using those prefixes is a finding.
        A 'bound-<size>' which is itself an expression is treated like
        an expression size above, and is the only bound finding the
        marker excuses.
        """
        if not repo.props['has_workflows_dir']:
            return self.skip('No .github/workflows/ directory')

        workflows = repo.workflows()
        if not workflows:
            return self.skip('No workflow files found')

        offenders = []
        bound_offenders = []
        for wf in sorted(workflows):
            filepath = os.path.join(
                repo.path, '.github', 'workflows', wf
            )
            with open(filepath, 'r', errors='replace') as f:
                lines = f.read().splitlines()

            for i, line in enumerate(lines):
                match = RUNS_ON_RE.match(line)
                if not match:
                    continue
                labels = literal_runner_labels(match.group(1))
                excused = bool(
                    VM_SIZE_EXCEPTION_RE.search(line)
                    or (i > 0 and VM_SIZE_EXCEPTION_RE.search(lines[i - 1])))
                problem = _bound_form_problem(
                    split_runner_labels(match.group(1)))
                if problem is not None:
                    # A bound job has no 'vm' label, so it is picked
                    # out by its bound- / bind- elements instead. The
                    # bind- element is always an expression, so it is
                    # read from the raw labels, and needs no marker.
                    # Only a size arriving through an expression may
                    # be excused; a malformed binding never is.
                    if problem == BOUND_UNSIZED:
                        if excused:
                            continue
                        problem = (
                            'names no literal "bound-<size>" with a '
                            'size from the vocabulary')
                    if problem:
                        bound_offenders.append(
                            f'{wf}:{i + 1} ({problem})')
                    continue
                if 'vm' not in labels:
                    continue
                if any(label in VM_SIZE_LABELS for label in labels):
                    continue
                if excused:
                    continue
                offenders.append(f'{wf}:{i + 1} ({", ".join(labels)})')

        if bound_offenders:
            bound_text = (
                f'{len(bound_offenders)} malformed bound runner '
                f'job(s): {", ".join(bound_offenders)}. A bound job '
                f'is "[self-hosted, bound-<image>, bound-<size>, '
                f'bind-<token>]" with no plain image, size or "vm" '
                f'label (see "Binding a job to its runner" in '
                f'shakenfist/private-ci docs/capacity-claims.md)')
        else:
            bound_text = ''
        if offenders and bound_offenders:
            return self.fail(
                self._unsized_text(offenders) + '. ' + bound_text)
        if bound_offenders:
            return self.fail(bound_text)
        if offenders:
            return self.fail(self._unsized_text(offenders))
        return self.ok(
            f'All "vm" runner jobs name a size in '
            f'{len(workflows)} workflow file(s)')

    @staticmethod
    def _unsized_text(offenders):
        return (
            f'{len(offenders)} "vm" runner job(s) naming no size: '
            f'{", ".join(offenders)}. The conductor takes the '
            f'runner size from the labels and falls back to the '
            f'first CI_SIZES entry -- "xs", one vCPU and 2048 MB '
            f'-- when it finds none, so an omitted size is a '
            f'silent downgrade to the smallest runner rather than '
            f'a free choice. Add the size the job actually wants '
            f'(xs/s/m/l/xl; m-bigdisk/xl-bigdisk when the job '
            f'needs the disk; s-cloud/s-cluster when it builds a '
            f'nested cloud or cluster); "xs" is a valid answer '
            f'stated explicitly. A job which genuinely cannot name '
            f'one marks the line "audit-ok: vm-runner-size" with the '
            f'reason')
