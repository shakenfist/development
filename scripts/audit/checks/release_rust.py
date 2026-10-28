"""The Rust release template's invariants, as release-process checks them.

templates/release-automation-rust/README.md lists what every Rust
release keeps, as a numbered list under "Invariants". Each rule here
checks one of those invariants, or the part of one that the
workflow-safety rules in release.py do not already check. Where one of
those rules reports a defect, the rule here stays silent about it, so
that a defect is one finding rather than two that say the same thing in
different words. docs/audits/release-process.md maps every invariant to
the rule that checks it, and a test fails when the README gains an
invariant the table does not name.

The README is the authority. It defines three guards as exact strings,
and they are compared as exact strings here: a guard spelled another way
may well be safe, but it is no longer the guard the README and the other
Rust releases share, and each such respelling is one more thing for a
reader to prove safe by hand.
"""

import os
import re
import stat

from audit.check import ToolFailure
from audit.files import require_tracked_paths
from audit.repo import DEVELOPMENT_ROOT, read_within
from audit.text.release_jobs import (
    CHECK_DIST_ACTION, CI_GATE_ACTION, DOWNLOAD_ARTIFACT_ACTION, GH_RELEASE_ACTION, GUARD_KINDS, RUNNER_TEMP_RE,
    UNREACHED, UPLOAD_ARTIFACT_ACTION, ci_gate_findings, gate_jobs, gate_workflow_names, holds_write, input_is,
    job_checks_out, job_container_actions, job_graph_issues, job_permissions, job_publishes, trigger_filter,
    upstream_jobs, write_grants,
)
from audit.text.workflows import (
    GITHUB_HOSTED_LABEL_RE, RUNNER_EXCEPTION_RE, concurrency_block, has_workflow_dispatch,
    indented_block, job_environment, job_is_push_guarded, job_level_keys, job_needs, mapping_entries,
    split_runner_labels, step_action, step_keys, step_uses, step_with_inputs, strip_trailing_comment,
    strip_yaml_comments, unquote, workflow_header, workflow_job_blocks, workflow_step_blocks,
)


#: Where the template lives in this repository, and so where the
#: scripts a Rust release copies are compared from.
TEMPLATE_DIR = 'templates/release-automation-rust'

#: The README's "push and tag guard", without the `if: ` it is written
#: after.
PUSH_TAG_GUARD = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"

#: The README's "skipped-gate guard", as job_level_keys() reads it once
#: a folded scalar's lines are joined.
SKIPPED_GATE_GUARD = (
    "${{ !cancelled() && (needs.ci-gate.result == 'success' || "
    "(github.event_name != 'push' && needs.ci-gate.result == 'skipped')) }}")

#: The fixed jobs, which the invariants are written against by name.
CI_GATE_JOB = 'ci-gate'
CHECK_VERSION_JOB = 'check-version'
SIGN_TAG_JOB = 'sign-tag'
GITHUB_RELEASE_JOB = 'github-release'

#: The jobs whose permissions an invariant gives exactly. Anything they
#: hold beyond that is reported by the rule for that job, so the rule
#: about who may hold a write scope leaves them out rather than report
#: the same line twice.
EXACT_PERMISSIONS = {
    CI_GATE_JOB: {'actions': 'read', 'contents': 'read'},
    CHECK_VERSION_JOB: {'contents': 'read'},
    SIGN_TAG_JOB: {'contents': 'write', 'id-token': 'write'},
    GITHUB_RELEASE_JOB: {'contents': 'write'},
}

#: What check-version and sign-tag must run. Each is the template's own
#: command line, and the scripts they name are the ones invariant 14
#: holds to the template.
CHECK_VERSION_COMMAND = 'tools/release-version.sh check "${GITHUB_REF}"'
SIGN_TAG_COMMAND = 'tools/sign-release-tag.sh'

#: A test of github.ref itself, not of github.ref_name or ref_type: a
#: group keyed on either of those is a different key.
GITHUB_REF_RE = re.compile(r'\bgithub\.ref\b')

#: A read of a secret. `secrets.GITHUB_TOKEN` is the run's own token,
#: whose reach the job's permissions block already states, so it is not
#: counted. An index (`secrets['X']`) and `secrets: inherit` cannot be
#: read for a name, so they count as reading something else.
SECRET_RE = re.compile(r'\bsecrets\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*)|\[)|^\s*secrets:\s*inherit\b', re.MULTILINE)

#: A download path the per-job temporary directory is the root of.
RUNNER_TEMP_ROOT_RE = re.compile(r'^\$\{\{\s*runner\.temp\s*\}\}(?:/|$)')

#: The scripts a Rust release copies into tools/, and the two of them
#: that must be copied byte for byte. The other two carry a marked
#: section each repository fills in.
RELEASE_SCRIPTS = ('propose-release.sh', 'tag-release.sh', 'release-version.sh', 'sign-release-tag.sh')
VERBATIM_SCRIPTS = frozenset({'tag-release.sh', 'sign-release-tag.sh'})
SETTINGS_START = b'--- per-repository settings ---'
SETTINGS_END = b'--- end of per-repository settings ---'

#: The scripts that may make a tag, which invariant 15 exempts.
TAGGING_SCRIPTS = frozenset({'tools/tag-release.sh', 'tools/sign-release-tag.sh'})

#: The names make reads a makefile by, in the order it tries them.
MAKEFILE_NAMES = frozenset({'GNUmakefile', 'makefile', 'Makefile'})

#: A `git tag` command, allowing the -C and -c options git takes before
#: its subcommand. `git config tag.gpgsign` is not one.
GIT_TAG_RE = re.compile(r'\bgit(?:\s+-[cC]\s+\S+)*\s+tag\b')


def release_jobs(repo):
    """release.yml's text and its (name, body) jobs, or None if it is unreadable."""
    content = repo.workflow('release.yml')
    if content is None:
        return None
    return content, workflow_job_blocks(content)


def secrets_read(body):
    """The secrets a job reads other than GITHUB_TOKEN, by name ('*' if unnamed)."""
    names = []
    for match in SECRET_RE.finditer(strip_yaml_comments(body, trailing=True)):
        name = match.group(1) or '*'
        if name != 'GITHUB_TOKEN' and name not in names:
            names.append(name)
    return names


def job_effects(body):
    """What gives a job an effect outside the run, as invariant 7 lists them.

    An empty list for a job whose work stays inside the run. The
    effects are named so that a finding can say which one it is, since
    "this job has an effect" is not something its author can act on.
    Every write scope counts, not only contents and id-token:
    `packages: write` pushes an image and `write-all` grants everything,
    and a build-section job holding either would otherwise run its
    effect on a dispatch, whose gate is skipped.
    """
    effects = []
    if job_environment(body) is not None:
        effects.append('names an environment')
    effects.extend(f'holds {grant}' for grant in write_grants(job_permissions(body)))
    effects.extend(f'reads secrets.{name}' if name != '*' else 'reads a secret'
                   for name in secrets_read(body))
    return effects


def needs_push_tag_guard(name, body):
    """Must this job carry the push and tag guard (invariants 7, 8 and 12)?

    ci-gate and check-version are left out: their guards are their own
    invariants', and check-version's is the skipped-gate guard whatever
    else it does.
    """
    if name in (CI_GATE_JOB, CHECK_VERSION_JOB):
        return False
    return bool(job_effects(body)) or name in (SIGN_TAG_JOB, GITHUB_RELEASE_JOB)


def needs_sign_tag(name, body):
    """Must this job need sign-tag (invariants 10 and 12)?"""
    if name in (CI_GATE_JOB, CHECK_VERSION_JOB, SIGN_TAG_JOB):
        return False
    return bool(job_effects(body)) or name == GITHUB_RELEASE_JOB


def build_job_guard(needs):
    """The README's build-job guard for a job with these needs, in their order."""
    return '${{ !cancelled() && ' + ' && '.join(f"needs.{need}.result == 'success'" for need in needs) + ' }}'


def is_build_job_guard(condition, needs):
    """Is condition the build-job guard for these needs?

    The README fixes the clauses and says nothing of their order, so the
    clauses after `!cancelled()` are compared as a multiset: listing the
    needs in another order changes nothing GitHub evaluates. A job with
    no needs has no build-job guard, because there is nothing for it to
    be a guard on.
    """
    match = re.fullmatch(r'\$\{\{ (.*) \}\}', condition)
    if not match or not needs:
        return False
    clauses = match.group(1).split(' && ')
    return (clauses[0] == '!cancelled()'
            and sorted(clauses[1:]) == sorted(f"needs.{need}.result == 'success'" for need in needs))


def gate_reported_jobs(blocks):
    """The jobs whose `if:` release_ci_gate_issues() already reports.

    That rule reports a status override without the success clauses, an
    admission of a skipped need that is not confined off push, and an
    environment job reached by such an admission without being confined
    to a pushed tag. Each of those is a guard the README's exact guards
    also rule out, so the guard rules here would otherwise report the
    same `if:` a second time. The jobs are the ones those findings name,
    read from ci_gate_findings(), which is where that rule's messages
    come from too.
    """
    return {job for finding in ci_gate_findings(blocks) if finding.kind in GUARD_KINDS
            for job in finding.jobs}


def guard_reported(content, blocks, name):
    """Does a workflow-safety rule already report this job's `if:`?

    Two of them read it: the dispatch rule, which wants every publishing
    job confined to a pushed tag once the workflow can be started by
    hand, and the gate rule, for the jobs gate_reported_jobs() names.
    """
    body = dict(blocks)[name]
    if has_workflow_dispatch(content) and job_publishes(body) and not job_is_push_guarded(body):
        return True
    return name in gate_reported_jobs(blocks)


def gate_unreached_jobs(blocks):
    """The environment jobs release_ci_gate_issues() reports as not downstream of the gate.

    The rules here that want a job to need check-version or sign-tag
    leave these out: the gate rule has already said the job needs
    something it does not, and adding the gate through check-version is
    the fix both findings would ask for. They are read from the same
    ci_gate_findings() that rule reports.
    """
    return {job for finding in ci_gate_findings(blocks) if finding.kind == UNREACHED for job in finding.jobs}


def describe_condition(condition):
    """A job's `if:` as a finding quotes it."""
    return f'"if: {condition}"' if condition else 'no "if:"'


def permissions_issue(name, body, expected, invariant):
    """A finding for a job whose permissions are not exactly expected, or None."""
    actual = job_permissions(body)
    if actual == expected:
        return None
    wanted = ', '.join(f'{scope}: {access}' for scope, access in expected.items())
    return (f'the {name} job\'s permissions are {format_permissions(actual)}, not exactly {wanted}, so it '
            f'holds a token scoped differently from every other Rust release\'s (invariant {invariant})')


def format_permissions(permissions):
    """A permissions value as a finding quotes it."""
    if permissions is None:
        return 'not set'
    if isinstance(permissions, dict):
        return '{' + ', '.join(f'{scope}: {access}' for scope, access in permissions.items()) + '}'
    return permissions


def step_runs(steps, command):
    """Does any of these steps run a script containing command?"""
    return any(command in step_keys(step).get('run', '') for step in steps)


def job_runner_labels(body):
    """The labels a job's own `runs-on:` names, in any of its spellings."""
    keys = job_level_keys(body)
    value = keys.get('runs-on')
    if value is None:
        return []
    if value:
        return split_runner_labels(value)
    block = indented_block(body, 'runs-on') or ''
    return [unquote(line.strip()[1:].strip()) for line in block.splitlines() if line.strip().startswith('-')]


def runs_on_marked_github_hosted(body):
    """Is the job's `runs-on:` one GitHub-hosted label, marked on the line above?

    The marker is the self-hosted-runners criterion's, and invariant 8
    asks for it on the line above, where the fleet writes it, rather
    than on the line itself.
    """
    labels = job_runner_labels(body)
    if len(labels) != 1 or not GITHUB_HOSTED_LABEL_RE.fullmatch(labels[0]):
        return False
    lines = body.splitlines()
    # The job's own depth, read as job_level_keys() reads it: a comment
    # introducing the next job sits shallower, inside this job's text.
    depth = min(len(line) - len(line.lstrip()) for line in lines
                if line.strip() and not line.lstrip().startswith('#'))
    for index, line in enumerate(lines):
        if len(line) - len(line.lstrip()) == depth and line.strip().startswith('runs-on:'):
            above = lines[index - 1].strip() if index else ''
            return above.startswith('#') and bool(RUNNER_EXCEPTION_RE.search(above))
    return False


def rust_trigger_issues(repo):
    """Findings for a release that starts on anything but a pushed tag (invariant 1).

    A pushed v* tag is the release, and a dispatch is the dry run. Any
    other trigger starts a release from something the tag scripts did
    not make: a branch push would run the publishing jobs' build on
    every merge, and dispatch inputs would let a run be steered by
    whoever starts it, which no publishing job is written to expect.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    header = strip_yaml_comments(workflow_header(found[0]), trailing=True)
    on = indented_block(header, 'on')
    if on is None:
        return ['release.yml\'s "on:" is not a mapping of its triggers, so it cannot be read as a push of v* '
                'tags and a workflow_dispatch, the two triggers a Rust release has (invariant 1)']

    triggers = job_level_keys(on)
    issues = []
    extra = sorted(set(triggers) - {'push', 'workflow_dispatch'})
    missing = sorted({'push', 'workflow_dispatch'} - set(triggers))
    if extra or missing:
        issues.append(
            f'release.yml triggers on {", ".join(sorted(triggers)) or "nothing"}, not on push and '
            'workflow_dispatch alone, so a release can start from something other than a pushed tag or '
            'has no dry run (invariant 1)')
    if 'push' in triggers:
        push = indented_block(on, 'push') or ''
        tags = trigger_filter(push, 'tags')
        if tags != ['v*']:
            issues.append(
                f'release.yml\'s push trigger filters tags on {tags if tags is not None else "nothing"}, not '
                "['v*'] alone, so the tags it releases are not the ones tools/tag-release.sh makes (invariant 1)")
        branches = [key for key in ('branches', 'branches-ignore') if trigger_filter(push, key) is not None]
        if branches:
            issues.append(
                f'release.yml\'s push trigger has {" and ".join(branches)}, so a push to a branch starts a '
                'release run; filter on tags alone (invariant 1)')
    if 'workflow_dispatch' in triggers:
        dispatch = indented_block(on, 'workflow_dispatch') or triggers['workflow_dispatch']
        if 'inputs' in job_level_keys(dispatch) or 'inputs' in triggers['workflow_dispatch']:
            issues.append(
                'release.yml\'s workflow_dispatch takes inputs, so a dry run can be steered by whoever starts '
                'it; a Rust release\'s dispatch takes none (invariant 1)')
    return issues


def rust_permissions_issues(repo):
    """Findings for a release whose jobs inherit a token they did not ask for (invariant 2).

    `permissions: {}` at the top grants no job anything, so each job
    holds exactly what its own block says. A job without a block of its
    own takes the top level's, which is nothing, and fails on its first
    API call -- or, under any other top level, takes more than it says.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    content, blocks = found
    issues = []
    top = job_permissions(workflow_header(content))
    if top != {}:
        issues.append(
            f'release.yml\'s top-level permissions are {format_permissions(top)}, not {{}}, so a job '
            'without a block of its own holds a token it never asked for (invariant 2)')
    bare = [name for name, body in blocks if 'permissions' not in job_level_keys(body)]
    if bare:
        issues.append(
            f'{", ".join(bare)} {"has" if len(bare) == 1 else "have"} no "permissions:" block of '
            f'{"its" if len(bare) == 1 else "their"} own, so what the job may do is not stated where it '
            'runs (invariant 2)')
    return issues


def rust_concurrency_issues(repo):
    """Findings for a release that can run twice at once or be cancelled (invariant 3).

    One group per ref keeps two runs for the same tag from signing and
    publishing side by side. cancel-in-progress: false keeps the second
    from cancelling the first, which may already have signed its tag or
    begun publishing and has to finish.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    header = workflow_header(found[0])
    block = concurrency_block(header)
    entries = mapping_entries(block) if block is not None else {}
    if block is None and job_level_keys(header).get('concurrency'):
        entries = {'group': job_level_keys(header)['concurrency']}
    if not entries:
        return ['release.yml sets no top-level concurrency, so two runs for one tag can sign and publish at '
                'the same time; set a group on github.ref with cancel-in-progress: false (invariant 3)']
    issues = []
    if not GITHUB_REF_RE.search(entries.get('group', '')):
        issues.append(
            f'release.yml\'s concurrency group "{entries.get("group", "")}" does not contain github.ref, so '
            'it does not hold the runs for one tag to one at a time (invariant 3)')
    if unquote(entries.get('cancel-in-progress', '')) != 'false':
        issues.append(
            'release.yml\'s concurrency does not set cancel-in-progress: false, so a second run for the same '
            'ref can cancel a release that has already signed its tag or begun publishing (invariant 3)')
    return issues


def rust_ci_gate_issues(repo):
    """Findings for a ci-gate job shaped unlike the template's (invariant 4).

    release_ci_gate_issues() checks that exactly one job runs the gate,
    that the jobs below it wait for it, and release_ci_trigger_issues()
    that the workflows it names exist; neither is repeated here. What is
    left is the shape the README fixes: the job's name, which the other
    fixed jobs' guards and needs are written against, its guard, a job
    with nothing above it, on the static pool, with a token that can
    read runs and nothing else, and one timed step at @main naming bare
    file names.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    content, blocks = found
    jobs = dict(blocks)
    if CI_GATE_JOB not in jobs:
        gates = gate_jobs(blocks)
        if not gates:
            return []
        return [f'the gate runs in {", ".join(gates)}, not in a job named {CI_GATE_JOB}, which is the name '
                'check-version\'s guard and needs are written against (invariant 4)']

    body = jobs[CI_GATE_JOB]
    needs = {name: job_needs(job) for name, job in blocks}
    issues = []
    condition = job_level_keys(body).get('if', '')
    if condition != PUSH_TAG_GUARD and not guard_reported(content, blocks, CI_GATE_JOB):
        issues.append(
            f'the {CI_GATE_JOB} job has {describe_condition(condition)}, not the push and tag guard '
            f'"if: {PUSH_TAG_GUARD}", so it runs on something other than a pushed tag (invariant 4)')
    if needs[CI_GATE_JOB]:
        issues.append(
            f'the {CI_GATE_JOB} job needs {", ".join(needs[CI_GATE_JOB])}, so something runs before the '
            'gate has said whether CI passed; it needs nothing (invariant 4)')
    labels = job_runner_labels(body)
    if sorted(labels) != ['self-hosted', 'static']:
        issues.append(
            f'the {CI_GATE_JOB} job runs on [{", ".join(labels)}], not [self-hosted, static], the pool its '
            'few API calls are sized for (invariant 4)')
    issue = permissions_issue(CI_GATE_JOB, body, EXACT_PERMISSIONS[CI_GATE_JOB], 4)
    if issue:
        issues.append(issue)

    steps = workflow_step_blocks(body)
    gate_steps = [step for step in steps if step_action(step) == CI_GATE_ACTION]
    # With no gate anywhere, the gate rule has already said so.
    if len(steps) != 1 or (not gate_steps and gate_jobs(blocks)):
        issues.append(
            f'the {CI_GATE_JOB} job has {len(steps)} step{"" if len(steps) == 1 else "s"}, not one running '
            f'{CI_GATE_ACTION}, so it does more than the gate or not the gate at all (invariant 4)')
    for step in gate_steps:
        uses = step_uses(step)
        if uses != f'{CI_GATE_ACTION}@main':
            issues.append(
                f'the {CI_GATE_JOB} job uses {uses}, not {CI_GATE_ACTION}@main, which every release in the '
                'fleet runs so that a fix to the gate reaches them all at once (invariant 4)')
        if 'timeout-minutes' not in step_keys(step):
            issues.append(
                f'the {CI_GATE_JOB} job\'s gate step sets no timeout-minutes, so a stalled API call holds the '
                'release for GitHub\'s six hours (invariant 4)')
    names = gate_workflow_names(content)
    if gate_steps and not names:
        issues.append(
            f'the {CI_GATE_JOB} job\'s gate names no workflows, so it has no CI to require (invariant 4)')
    # A name with no workflow behind it is the trigger rule's finding;
    # one that resolves through a path is still not a bare file name.
    pathed = [name for name in names if '/' in name and repo.workflow(name) is not None]
    if pathed:
        issues.append(
            f'the {CI_GATE_JOB} job\'s gate names {", ".join(pathed)} by a path, not by a bare file name, '
            'which is the form require-ci-success matches against a run\'s workflow file (invariant 4)')
    return issues


def rust_check_version_issues(repo):
    """Findings for a check-version job shaped unlike the template's (invariant 5).

    check-version is the root of the build graph: it waits for the gate
    alone, lets a skipped gate through only off push, can read the tree
    and nothing else, and refuses a tag that does not name the version
    the tree carries.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    content, blocks = found
    jobs = dict(blocks)
    if CHECK_VERSION_JOB not in jobs:
        return [f'release.yml has no {CHECK_VERSION_JOB} job, so nothing refuses a tag that names a version '
                'the tree does not carry, and the build graph has no root below the gate (invariant 5)']

    body = jobs[CHECK_VERSION_JOB]
    needs = {name: job_needs(job) for name, job in blocks}
    issues = []
    if needs[CHECK_VERSION_JOB] != [CI_GATE_JOB]:
        issues.append(
            f'the {CHECK_VERSION_JOB} job needs {", ".join(needs[CHECK_VERSION_JOB]) or "nothing"}, not '
            f'{CI_GATE_JOB} alone, so its guard does not describe what it waits for (invariant 5)')
    condition = job_level_keys(body).get('if', '')
    if condition != SKIPPED_GATE_GUARD and not guard_reported(content, blocks, CHECK_VERSION_JOB):
        issues.append(
            f'the {CHECK_VERSION_JOB} job has {describe_condition(condition)}, not the skipped-gate guard '
            f'"if: {SKIPPED_GATE_GUARD}" (invariant 5)')
    issue = permissions_issue(CHECK_VERSION_JOB, body, EXACT_PERMISSIONS[CHECK_VERSION_JOB], 5)
    if issue:
        issues.append(issue)
    if not step_runs(workflow_step_blocks(body), CHECK_VERSION_COMMAND):
        issues.append(
            f'the {CHECK_VERSION_JOB} job does not run {CHECK_VERSION_COMMAND}, so a tag naming a version the '
            'tree does not carry is built and released (invariant 5)')
    return issues


def rust_build_root_issues(repo):
    """Findings for jobs that do not wait for check-version (invariant 5).

    Every job below the two at the top needs check-version, directly or
    through a job that does, and so the gate above it. A job that needs
    sign-tag is left to the rule for invariant 10, which asks for more:
    sign-tag needs check-version itself, and a job reported missing both
    needs one finding, not two.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    _, blocks = found
    jobs = dict(blocks)
    needs = {name: job_needs(body) for name, body in blocks}
    if CHECK_VERSION_JOB not in jobs or job_graph_issues(needs):
        return []
    skipped = gate_unreached_jobs(blocks)
    loose = [name for name, body in blocks
             if name not in (CI_GATE_JOB, CHECK_VERSION_JOB) and name not in skipped
             and not (SIGN_TAG_JOB in jobs and needs_sign_tag(name, body))
             and CHECK_VERSION_JOB not in upstream_jobs(needs, name)]
    if not loose:
        return []
    return [f'{", ".join(loose)} {"does" if len(loose) == 1 else "do"} not need {CHECK_VERSION_JOB}, so '
            f'{"it runs" if len(loose) == 1 else "they run"} without the version having been checked and '
            f'without waiting for {CI_GATE_JOB}; only {CI_GATE_JOB} needs nothing (invariant 5)']


def rust_build_guard_issues(repo):
    """Findings for build jobs without the build-job guard (invariant 6).

    A job with no `if:` of its own gets an implicit success(), which
    fails when any job upstream was skipped -- and on a dry run ci-gate
    is -- so the build would not run at all. always() runs it after a
    failure as well. The build-job guard tests only the jobs it needs,
    so a dry run builds and a failed, skipped or cancelled need still
    stops it. A job carrying the push and tag guard instead is not a
    build job, and is the rule for invariant 7's to judge.

    With every other job's guard fixed as an exact string by its own
    invariant, this is also what keeps always() off every job and
    !cancelled() off all but check-version and the build jobs.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    content, blocks = found
    needs = {name: job_needs(body) for name, body in blocks}
    issues = []
    for name, body in blocks:
        if name in (CI_GATE_JOB, CHECK_VERSION_JOB) or needs_push_tag_guard(name, body):
            continue
        condition = job_level_keys(body).get('if', '')
        if condition == PUSH_TAG_GUARD or is_build_job_guard(condition, needs[name]):
            continue
        if guard_reported(content, blocks, name):
            continue
        issues.append(
            f'the {name} job has {describe_condition(condition)}, which is neither the build-job guard '
            f'"if: {build_job_guard(needs[name] or [CHECK_VERSION_JOB])}" nor the push and tag guard, so '
            'it does not build on a dry run, or builds after a job it needs has failed (invariant 6)')
    return issues


def rust_effect_guard_issues(repo):
    """Findings for jobs with an effect outside the run that a dry run reaches (invariant 7).

    A job that names an environment, holds any write scope -- to the
    repository, the package registry, an OIDC token -- or reads a secret
    is one a dispatch must never run, and a pushed tag is the one event on which the gate always
    runs. sign-tag and github-release take the guard whatever their
    permissions say, as invariants 8 and 12 require.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    content, blocks = found
    issues = []
    for name, body in blocks:
        if not needs_push_tag_guard(name, body):
            continue
        condition = job_level_keys(body).get('if', '')
        if condition == PUSH_TAG_GUARD or guard_reported(content, blocks, name):
            continue
        effects = job_effects(body)
        reason = (f'{" and ".join(effects)}, an effect outside the run' if effects
                  else 'is one of the jobs that sign or release')
        issues.append(
            f'the {name} job {reason}, but has {describe_condition(condition)}, not the push and tag guard '
            f'"if: {PUSH_TAG_GUARD}" (invariant 7)')
    return issues


def rust_sign_tag_issues(repo):
    """Findings for a sign-tag job shaped unlike the template's (invariant 8).

    sign-tag is where a reviewer approves the release, and what it signs
    and force-pushes cannot be taken back, so it waits for the release
    environment, holds only what signing needs, runs the template's
    script, and runs on a runner thrown away afterwards: the script
    installs gitsign with sudo and writes global git config.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    jobs = dict(found[1])
    if SIGN_TAG_JOB not in jobs:
        return [f'release.yml has no {SIGN_TAG_JOB} job, so the tag is never signed and no job waits for a '
                'reviewer to approve the release (invariant 8)']
    body = jobs[SIGN_TAG_JOB]
    issues = []
    environment = job_environment(body)
    if environment != 'release':
        issues.append(
            f'the {SIGN_TAG_JOB} job names {"environment " + repr(environment) if environment else "no environment"}, '
            'not release, so no reviewer approves the release before its tag is signed (invariant 8)')
    issue = permissions_issue(SIGN_TAG_JOB, body, EXACT_PERMISSIONS[SIGN_TAG_JOB], 8)
    if issue:
        issues.append(issue)
    if not step_runs(workflow_step_blocks(body), SIGN_TAG_COMMAND):
        issues.append(
            f'the {SIGN_TAG_JOB} job does not run {SIGN_TAG_COMMAND}, the script every Rust release signs its '
            'tag with (invariant 8)')
    if 'vm' not in job_runner_labels(body) and not runs_on_marked_github_hosted(body):
        issues.append(
            f'the {SIGN_TAG_JOB} job runs on [{", ".join(job_runner_labels(body))}], which is neither a vm '
            'runner nor one GitHub-hosted label marked "audit-ok: github-hosted-runner" on the line above, '
            'so the runner that installs gitsign with sudo may be one the next job lands on (invariant 8)')
    return issues


def rust_upload_needs_issues(repo):
    """Findings for a build sign-tag does not wait for (invariant 9).

    A job that uploads an artifact is building something the release
    ships. If sign-tag does not need it, the tag can be signed and
    force-pushed while that build is still running, or after it failed.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    blocks = found[1]
    jobs = dict(blocks)
    needs = {name: job_needs(body) for name, body in blocks}
    if SIGN_TAG_JOB not in jobs or job_graph_issues(needs):
        return []
    upstream = upstream_jobs(needs, SIGN_TAG_JOB)
    unawaited = [name for name, body in blocks
                 if name != SIGN_TAG_JOB and name not in upstream
                 and any(step_action(step) == UPLOAD_ARTIFACT_ACTION for step in workflow_step_blocks(body))]
    if not unawaited:
        return []
    return [f'{", ".join(unawaited)} upload{"s" if len(unawaited) == 1 else ""} an artifact but '
            f'{SIGN_TAG_JOB} does not need {"it" if len(unawaited) == 1 else "them"}, so the tag can be signed '
            'while a build is running or after one has failed (invariant 9)']


def rust_sign_tag_needs_issues(repo):
    """Findings for a publishing job that does not wait for sign-tag (invariant 10).

    sign-tag is the one job a reviewer approves for every release. A job
    with an effect outside the run that does not need it publishes
    without that approval, and before the tag it publishes from is
    signed. github-release is held to the same, as invariant 12 says.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    blocks = found[1]
    jobs = dict(blocks)
    needs = {name: job_needs(body) for name, body in blocks}
    if SIGN_TAG_JOB not in jobs or job_graph_issues(needs):
        return []
    skipped = gate_unreached_jobs(blocks)
    early = [name for name, body in blocks
             if needs_sign_tag(name, body) and name not in skipped
             and SIGN_TAG_JOB not in upstream_jobs(needs, name)]
    if not early:
        return []
    return [f'{", ".join(early)} {"has" if len(early) == 1 else "have"} an effect outside the run but '
            f'{"does" if len(early) == 1 else "do"} not need {SIGN_TAG_JOB}, so '
            f'{"it publishes" if len(early) == 1 else "they publish"} before a reviewer has approved the '
            'release and its tag is signed (invariant 10)']


def rust_write_permission_issues(repo):
    """Findings for a job holding a write scope it should not (invariant 11).

    contents: write can move a tag or a branch, and only sign-tag, which
    force-pushes the signed tag, and github-release, which creates the
    release, need it. id-token: write mints an OIDC token a registry
    trusts, and a reviewer approves each use of one only where the job
    names the release environment. The four fixed jobs are left out:
    their permissions are given exactly by their own invariants.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    contents, id_token = [], []
    for name, body in found[1]:
        if name in EXACT_PERMISSIONS:
            continue
        permissions = job_permissions(body)
        if holds_write(permissions, 'contents'):
            contents.append(name)
        if holds_write(permissions, 'id-token') and job_environment(body) != 'release':
            id_token.append(name)
    issues = []
    if contents:
        issues.append(
            f'{", ".join(contents)} {"holds" if len(contents) == 1 else "hold"} contents: write, which only '
            f'{SIGN_TAG_JOB} and {GITHUB_RELEASE_JOB} need, so a build or publish job can move a tag or a '
            'branch (invariant 11)')
    if id_token:
        issues.append(
            f'{", ".join(id_token)} {"holds" if len(id_token) == 1 else "hold"} id-token: write without '
            'naming environment: release, so it can mint a token a registry trusts without a reviewer '
            'approving it (invariant 11)')
    return issues


def rust_github_release_issues(repo):
    """Findings for a github-release job shaped unlike the template's (invariant 12).

    github-release attaches the artifacts named release-* and nothing
    else, from a directory nothing earlier on the runner can have
    written, and fails when a glob matches nothing. Its guard is the
    rule for invariant 7's, its need for sign-tag the rule for invariant
    10's, and the parts release_asset_issues() and
    release_workspace_issues() already read are not read again.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    jobs = dict(found[1])
    if GITHUB_RELEASE_JOB not in jobs:
        return [f'release.yml has no {GITHUB_RELEASE_JOB} job, so a release publishes no GitHub release '
                '(invariant 12)']
    body = jobs[GITHUB_RELEASE_JOB]
    steps = workflow_step_blocks(body)
    issues = []
    issue = permissions_issue(GITHUB_RELEASE_JOB, body, EXACT_PERMISSIONS[GITHUB_RELEASE_JOB], 12)
    if issue:
        issues.append(issue)

    downloads = [step for step in steps if step_action(step) == DOWNLOAD_ARTIFACT_ACTION]
    if not downloads:
        issues.append(
            f'the {GITHUB_RELEASE_JOB} job downloads no artifacts, so it attaches whatever the runner already '
            'holds (invariant 12)')
    # The workspace rule reports a download outside runner.temp by a job
    # that does not start from a cleaned checkout.
    workspace_judged = not job_checks_out(body) and not job_container_actions(steps)
    for step in downloads:
        inputs = step_with_inputs(step)
        pattern = unquote(inputs.get('pattern', ''))
        if pattern != 'release-*':
            issues.append(
                f'the {GITHUB_RELEASE_JOB} job downloads {"pattern " + pattern if pattern else "without a pattern"}, '
                'not pattern: release-*, so what it attaches is not the set of artifacts named for the release '
                '(invariant 12)')
        path = unquote(inputs.get('path', ''))
        if RUNNER_TEMP_ROOT_RE.match(path):
            continue
        if workspace_judged and not RUNNER_TEMP_RE.search(path):
            continue
        issues.append(
            f'the {GITHUB_RELEASE_JOB} job downloads to "{path or "the workspace"}", not to a path under '
            '${{ runner.temp }}, where nothing an earlier job left can join the release (invariant 12)')

    releases = [step for step in steps if step_action(step) == GH_RELEASE_ACTION]
    if not releases:
        issues.append(
            f'the {GITHUB_RELEASE_JOB} job does not create the release with {GH_RELEASE_ACTION} (invariant 12)')
    for step in releases:
        inputs = step_with_inputs(step)
        # Where the step attaches files, release_asset_issues() reports
        # a missing fail_on_unmatched_files itself.
        if 'files' in inputs or input_is(inputs, 'fail_on_unmatched_files', 'true'):
            continue
        issues.append(
            f'the {GITHUB_RELEASE_JOB} job creates the release without fail_on_unmatched_files: true, so a '
            'release that attached nothing still reports success (invariant 12)')
    return issues


def rust_check_dist_ref_issues(repo):
    """Findings for a check-dist step not at @main (invariant 13).

    release_check_dist_issues() checks that every wheel is checked, and
    where; it does not read the version, because the fleet calls the
    shared actions at @main so that a fix to one reaches every release
    at once. A step pinned anywhere else misses those fixes.
    """
    found = release_jobs(repo)
    if found is None:
        return []
    pinned = [f'{name} ({step_uses(step)})' for name, body in found[1]
              for step in workflow_step_blocks(body)
              if step_action(step) == CHECK_DIST_ACTION and step_uses(step) != f'{CHECK_DIST_ACTION}@main']
    if not pinned:
        return []
    return [f'{", ".join(pinned)} {"runs" if len(pinned) == 1 else "run"} check-dist at a version other than '
            f'@main, so the release misses fixes to what the check rejects (invariant 13)']


def template_script(name):
    """The bytes of one of the template's scripts in this checkout.

    Read as review.py reads its templates. An unreadable template is the
    audit's own tree failing, not the repository's, so it raises.
    """
    path = f'{TEMPLATE_DIR}/{name}'
    data = read_within(DEVELOPMENT_ROOT, os.path.join(DEVELOPMENT_ROOT, path), binary=True)
    if data is None:
        raise ToolFailure(f'Could not read {path} in the audit\'s own checkout')
    return data


def outside_settings(data):
    """A script's lines outside its per-repository settings, or None.

    The marker lines themselves are kept: they are outside the lines
    between them, and a copy that rewords one has lost the marker the
    next comparison looks for. None where the markers are not there once
    each, in order.
    """
    lines = data.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if SETTINGS_START in line]
    ends = [index for index, line in enumerate(lines) if SETTINGS_END in line]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return lines[:starts[0] + 1] + lines[ends[0]:]


def rust_script_issues(repo):
    """Findings for release scripts that are not the template's (invariant 14).

    The scripts carry the release process: what may be tagged, how the
    version is checked and how the tag is signed. tag-release.sh and
    sign-release-tag.sh are the same everywhere, so they are compared
    byte for byte; the other two are compared outside the section each
    repository fills in. A `{{` left anywhere is a placeholder nobody
    filled in.
    """
    issues = []
    for name in RELEASE_SCRIPTS:
        template = template_script(name)
        destination = f'tools/{name}'
        copy = repo.read_blob(destination)
        if copy is None:
            state = ('not a regular file the audit will read' if os.path.lexists(repo.join(destination))
                     else 'missing')
            issues.append(f'{destination} is {state}, and the release process runs it (invariant 14)')
            continue
        if not os.stat(repo.join(destination)).st_mode & stat.S_IXUSR:
            issues.append(f'{destination} is not executable, so the release step that runs it fails '
                          '(invariant 14)')
        if b'{{' in copy:
            issues.append(f'{destination} still holds a {{{{ placeholder from the template (invariant 14)')
        if name in VERBATIM_SCRIPTS:
            if copy != template:
                issues.append(f'{destination} differs from {TEMPLATE_DIR}/{name}, which every Rust release '
                              'copies byte for byte (invariant 14)')
            continue
        expected = outside_settings(template)
        if expected is None:
            raise ToolFailure(f'{TEMPLATE_DIR}/{name} in the audit\'s own checkout has no per-repository '
                              'settings markers')
        actual = outside_settings(copy)
        if actual is None:
            issues.append(f'{destination} does not have its per-repository settings markers once each, so '
                          'it cannot be compared with the template (invariant 14)')
        elif actual != expected:
            issues.append(f'{destination} differs from {TEMPLATE_DIR}/{name} outside its per-repository '
                          'settings (invariant 14)')
    return issues


def rust_git_tag_issues(repo):
    """Findings for anything other than the tag script that makes a tag (invariant 15).

    The release tag must name the commit the merge queue merged, which
    tools/tag-release.sh finds and checks, and sign-release-tag.sh
    re-signs that same tag. Anything else running `git tag` -- a
    Makefile release target, a workflow step -- can tag a commit the
    queue never tested, and the gate would then refuse it only if CI
    happened not to pass there. Comments are not commands, so they are
    not read.
    """
    tracked = require_tracked_paths(repo.path)
    paths = {path for path in tracked
             if (os.path.basename(path) in MAKEFILE_NAMES or path.endswith('.sh'))
             and path not in TAGGING_SCRIPTS}
    paths |= {f'.github/workflows/{name}' for name in repo.workflows()}
    found = []
    for path in sorted(paths):
        text = repo.read(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            # A comment on a line of its own is cut whole.
            if GIT_TAG_RE.search(strip_trailing_comment(line)):
                found.append(f'{path}:{number}')
    if not found:
        return []
    return [f'{", ".join(found)} {"runs" if len(found) == 1 else "run"} git tag, but a release tag is only '
            'ever made by tools/tag-release.sh, which tags the commit the merge queue merged (invariant 15)']


#: Every rule, in the order of the invariants they check.
RUST_RULES = (
    rust_trigger_issues,
    rust_permissions_issues,
    rust_concurrency_issues,
    rust_ci_gate_issues,
    rust_check_version_issues,
    rust_build_root_issues,
    rust_build_guard_issues,
    rust_effect_guard_issues,
    rust_sign_tag_issues,
    rust_upload_needs_issues,
    rust_sign_tag_needs_issues,
    rust_write_permission_issues,
    rust_github_release_issues,
    rust_check_dist_ref_issues,
    rust_script_issues,
    rust_git_tag_issues,
)


def release_rust_issues(repo):
    """Every finding the Rust rules make about this repository."""
    issues = []
    for rule in RUST_RULES:
        issues.extend(rule(repo))
    return issues
