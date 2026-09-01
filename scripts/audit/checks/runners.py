"""The criteria about where a workflow runs.

Which runners a job asks for, whether the labels it names are static
enough to schedule against, and whether a VM job declares a size.
"""

import os
import re

from audit.check import Check
from audit.text.workflows import (
    RUNS_ON_RE, STATIC_ALLOWED_LABELS, literal_runner_labels,
    parse_runner_labels,
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


# A GitHub-hosted label only names a runner when it sits where YAML
# puts a value: after "runs-on:", as an element of a "[...]" list, or
# as a "- " item in a matrix. The same text inside a shell command is
# not a runner reference -- shakenfist/actions ships a step which
# uploads an image artifact named "ubuntu-2004", and reporting that
# asked someone to mark a deliberate exception on a line which never
# described a runner at all.
RUNNER_VALUE_PREFIXES = frozenset({':', '-', '[', ','})


RUNNER_VALUE_SUFFIXES = frozenset({',', ']', '#'})


def is_runner_label_value(line, start, end):
    """Does a matched label sit where a YAML value could?

    Scanning every line, rather than only "runs-on:" lines, is
    deliberate -- matrix values feeding "runs-on: ${{ matrix.os }}"
    have to be caught too -- so the position test replaces the
    context a "runs-on:" anchor would have given.

    The test is about token boundaries, not just neighbouring
    characters. A label glued to preceding text is part of a longer
    name ("build-ubuntu-latest"), and only a sequence opener can
    legitimately abut one; a label separated by whitespace is a value
    when what precedes it opens one.
    """
    before = line[:start]
    after = line[end:]

    # Treat 'ubuntu-latest' the same as ubuntu-latest.
    if before.endswith(('"', "'")):
        before = before[:-1]
    if after.startswith(('"', "'")):
        after = after[1:]

    if before and not before.endswith((' ', '\t')):
        # Glued to what comes before, so this is the tail of a longer
        # name unless a list or flow-sequence opener abuts it.
        if not before.endswith(('[', ',')):
            return False
    else:
        stripped = before.rstrip()
        if stripped and stripped[-1] not in RUNNER_VALUE_PREFIXES:
            return False

    if after and not after.startswith((' ', '\t')):
        if not after.startswith((',', ']')):
            return False
    else:
        stripped = after.lstrip()
        if stripped and stripped[0] not in RUNNER_VALUE_SUFFIXES:
            return False

    return True


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
# it into a finding on the next daily run, so the two move together --
# test_the_size_vocabulary_matches_the_specification keeps this list
# and the spec page in step, but nothing can reach across to the
# conductor.
VM_SIZE_LABELS = frozenset({
    'xs', 's', 'm', 'l', 'xl', 'm-bigdisk', 'xl-bigdisk',
})


# Marker acknowledging a deliberate exception, placed on the offending
# line or the line immediately above it. The escape hatch exists
# because the fleet's other runner checks have one and a repository
# with a real reason needs an answer other than "edit the workflow";
# it is not a way to keep an unsized job, since writing "xs" costs one
# word and states the same decision honestly.
VM_SIZE_EXCEPTION_RE = re.compile(r'audit-ok:\s*vm-runner-size')


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
                labels = literal_runner_labels(match.group(1))
                if 'vm' not in labels:
                    continue
                if any(label in VM_SIZE_LABELS for label in labels):
                    continue
                if VM_SIZE_EXCEPTION_RE.search(line):
                    continue
                if i > 0 and VM_SIZE_EXCEPTION_RE.search(lines[i - 1]):
                    continue
                offenders.append(f'{wf}:{i + 1} ({", ".join(labels)})')

        if offenders:
            return self.fail(
                f'{len(offenders)} "vm" runner job(s) naming no size: '
                f'{", ".join(offenders)}. The conductor takes the '
                f'runner size from the labels and falls back to the '
                f'first CI_SIZES entry -- "xs", one vCPU and 2048 MB '
                f'-- when it finds none, so an omitted size is a '
                f'silent downgrade to the smallest runner rather than '
                f'a free choice. Add the size the job actually wants '
                f'(xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job '
                f'needs the disk); "xs" is a valid answer stated '
                f'explicitly. A job which genuinely cannot name one '
                f'marks the line "audit-ok: vm-runner-size" with the '
                f'reason')
        return self.ok(
            f'All "vm" runner jobs name a size in '
            f'{len(workflows)} workflow file(s)')
