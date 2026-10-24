"""The criteria about how a project cuts a release.

What the release workflow publishes and from where, which job may
publish, what it waits for before it does, and the paths that workflow
hands to actions which run in a container, together with the leftovers
of the older process (release.sh, requirements.txt) and the setup notes
a release depends on.
"""

import fnmatch
import re

from audit.check import Check
from audit.text.workflows import (
    BLOCK_INDICATOR_RE, EVENT_NAME_EQUALITY_RE, MERGE_GROUP_FLOW_RE,
    MERGE_GROUP_TRIGGER_RE, PUSH_FLOW_RE, PUSH_TRIGGER_RE, concurrency_block, has_workflow_dispatch,
    indented_block, job_environment, job_is_push_guarded, job_is_tag_guarded, job_level_keys,
    job_needs, mapping_value_lines, step_action, step_keys, step_with_inputs, strip_yaml_comments,
    workflow_header, workflow_job_blocks, workflow_step_blocks,
)


RELEASE_WORKFLOW = '.github/workflows/release.yml'


#: The action which pulls build artifacts back into a later job, and
#: the action which attaches them to the GitHub release.
DOWNLOAD_ARTIFACT_ACTION = 'actions/download-artifact'
GH_RELEASE_ACTION = 'softprops/action-gh-release'
CHECKOUT_ACTION = 'actions/checkout'
ATTEST_ACTION = 'actions/attest-build-provenance'

#: The inputs which name the directory a step reads the distribution
#: from, each tied to the action that takes it. Matching on the input
#: name alone would read any step's "files:" as a claim about the
#: distribution, which it need not be.
#:
#: packages-dir is deliberately absent. gh-action-pypi-publish
#: delegates to a Docker container action, and a container sees the
#: workspace at /github/workspace and no more of the runner's
#: filesystem, so that input must be relative to the workspace -- the
#: opposite of what this criterion asks of the others.
DIST_CONSUMING_INPUTS = {
    ATTEST_ACTION: 'subject-path',
    GH_RELEASE_ACTION: 'files',
}

#: A reference to the per-job temporary directory.
RUNNER_TEMP_RE = re.compile(r'\$\{\{\s*runner\.temp\s*\}\}')

#: Expressions which expand to a path on the runner's own filesystem.
#: Inside a container action these name places that either do not
#: exist or are mounted somewhere else, so a path built from one is
#: wrong in the container however it is spelled.
HOST_PATH_EXPRESSION_RE = re.compile(
    r'\$\{\{\s*(?:runner\.(?:temp|workspace)|github\.workspace)\s*\}\}'
)

#: Actions which take a path input and run the work inside a Docker
#: container, so that input must be relative to the workspace.
#:
#: A list of names rather than a test for containerisation, because
#: nothing in the workflow text says whether an action is
#: containerised -- gh-action-pypi-publish looks like a composite
#: action and only reveals the container in the generated action it
#: delegates to. Naming what we know is honest and checkable; a
#: general rule here would be a guess. Each entry maps the action to
#: the input naming its directory.
CONTAINER_PATH_ACTIONS = {
    'pypa/gh-action-pypi-publish': 'packages-dir',
}


def input_is(inputs, key, expected):
    """Does a step input hold this boolean, however it is quoted?

    step_with_inputs() returns raw values, quotes intact, but an action
    receives every input as a string, so `true` and `"true"` arrive as
    the same thing. Comparing the raw value reported a correctly set
    `fail_on_unmatched_files: "true"` as missing. Case is folded too.
    """
    value = inputs.get(key, '')
    return value.strip().strip('"').strip("'").lower() == expected


def release_asset_issues(repo):
    """Findings for a release.yml which can publish an empty release.

    Two defects, and it takes both to make the failure silent.

    `actions/download-artifact` invoked with no inputs downloads every
    artifact of the run and decides the destination itself, which is
    not where a later `files: dist/*` looks. The sibling publish job
    in the same template gets this right -- `name: dist` with
    `path: dist/` names the destination -- so the two jobs in one file
    disagreed about where the distribution lives.

    `softprops/action-gh-release` then treats a glob matching nothing
    as a warning rather than an error (`fail_on_unmatched_files`
    defaults to false), so the job goes green having attached no
    files.

    The bug presents as intermittent because the release jobs run on
    the persistent `[self-hosted, static]` pool. When the release job
    lands on the same runner as the build it finds the build's
    leftover `dist/` in the reused workspace and the glob matches --
    which also means the assets came from unverified files on disk
    rather than from the download, whose SHA256 nothing then checked.
    library-utilities v0.8.7 built on shakenfist-static-2, released
    from shakenfist-static-1, and shipped an empty release; kerbside
    v0.5.0 did both on shakenfist-static-1 and shipped two files,
    from identical workflow files.

    Only jobs which actually attach files are judged, so a project
    that publishes no release assets is not asked to configure how it
    would.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    issues = []
    for name, body in workflow_job_blocks(content):
        steps = workflow_step_blocks(body)
        attaching = [
            step for step in steps
            if step_action(step) == GH_RELEASE_ACTION
            and step_with_inputs(step).get('files')
        ]
        if not attaching:
            continue

        for step in steps:
            if step_action(step) != DOWNLOAD_ARTIFACT_ACTION:
                continue
            inputs = step_with_inputs(step)
            if inputs.get('name') or input_is(inputs, 'merge-multiple', 'true'):
                continue
            issues.append(
                f'the {name} job downloads artifacts without "name:" or '
                '"merge-multiple: true", so the files do not land where '
                'its "files:" glob looks and the release is published '
                'empty; add "name: dist" and "path: dist/" as the '
                'publish job does')

        for step in attaching:
            if input_is(step_with_inputs(step), 'fail_on_unmatched_files', 'true'):
                continue
            issues.append(
                f'the {name} job attaches release assets without '
                '"fail_on_unmatched_files: true", so a glob which '
                'matches nothing is a warning and an empty release '
                'still reports success')

    return issues


def job_checks_out(body):
    """Does this job start from a workspace checkout has cleaned?

    Which decides whether its workspace can be trusted. Checkout
    cleans by default -- `git clean -ffdx && git reset --hard HEAD` --
    and that removes gitignored build output such as dist/, so a job
    which checks out starts from a known state even on a runner that
    has built this repository before.

    `clean: false` turns that off and hands back exactly the inherited
    workspace the exemption exists to rule out, so it does not count.
    The whole exemption rests on the cleaning, not on the checkout.
    """
    for step in workflow_step_blocks(body):
        if step_action(step) != CHECKOUT_ACTION:
            continue
        if input_is(step_with_inputs(step), 'clean', 'false'):
            continue
        return True
    return False


def dist_directory(value):
    """The directory part of a path or glob naming the distribution.

    Quoting and the glob are spelling; what two of these have to agree
    about is the directory. A glob's directory is everything before its
    first wildcard component, so `dist/*`, `dist/*.tar.gz` and
    `dist/**/*.whl` all read `dist`, while a plain path is a directory
    in its own right. Returns '' for an empty value.
    """
    value = value.strip().strip('"').strip("'").strip()
    parts = value.split('/')
    for index, part in enumerate(parts):
        if any(c in part for c in '*?['):
            parts = parts[:index]
            break
    return '/'.join(parts).rstrip('/')


def release_workspace_issues(repo):
    """Findings for publishing jobs which trust a shared workspace.

    The static runner pool is persistent and the publishing jobs do
    not check out, so the directory such a job starts in is whatever
    the last job on that runner left there. download-artifact then
    extracts *into* its target rather than replacing it, so a stale
    file from a previous run survives and joins the set the job goes
    on to publish. That is not hypothetical: leftover dist/ on these
    runners is what made the earlier empty-release bug intermittent.

    Downloading into runner.temp settles it, because runner.temp is
    per job: nothing else can have written there. The requirement is
    then that everything reading the distribution agrees about where
    it is. pypa/gh-action-pypi-publish is the trap, since its
    packages-dir defaults to ./dist and so keeps reading the shared
    workspace unless it is told not to -- and that is the job whose
    directory feeds both the attestation and the upload.

    Jobs which check out are not judged: checkout cleans, which is
    the same guarantee arrived at by a different route.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    issues = []
    for name, body in workflow_job_blocks(content):
        steps = workflow_step_blocks(body)
        downloads = [step for step in steps
                     if step_action(step) == DOWNLOAD_ARTIFACT_ACTION]
        if not downloads or job_checks_out(body):
            continue
        # A job feeding a container action is governed by the rule
        # below instead, which requires the opposite: workspace-relative
        # paths, and therefore a checkout. Reporting both would tell
        # the reader to do two incompatible things.
        if job_container_actions(steps):
            continue

        for step in downloads:
            path = step_with_inputs(step).get('path', '')
            if RUNNER_TEMP_RE.search(path):
                continue
            issues.append(
                f'the {name} job does not start from a cleaned checkout, so '
                'its workspace is whatever the previous job on that runner '
                f'left behind, but it downloads artifacts to '
                f'"{path or "the workspace"}"; either check out (which runs '
                'git clean -ffdx) or download to "${{ runner.temp }}/dist/", '
                'so that a stale file cannot join the set being published')

    return issues


def job_container_actions(steps):
    """The container actions a job runs, mapped to their path input."""
    return {
        action: CONTAINER_PATH_ACTIONS[action]
        for action in (step_action(step) for step in steps)
        if action in CONTAINER_PATH_ACTIONS
    }


def is_workspace_relative(value):
    """Is this a path a container action can actually follow?

    Absolute paths are rejected even when they point inside the
    workspace. The host and the container disagree about where the
    workspace is -- the runner bind-mounts it at /github/workspace --
    so an absolute path is right in at most one of the two contexts,
    and which one depends on mount layout that is not part of any
    action's contract. Relative is the only spelling correct on both
    sides.
    """
    value = value.strip().strip('"').strip("'").strip()
    if not value:
        return False
    return not value.startswith('/') and not HOST_PATH_EXPRESSION_RE.search(value)


def release_container_path_issues(repo):
    """Findings for a container action pointed at a runner path.

    gh-action-pypi-publish is a composite action which delegates the
    upload to a Docker container action. The runner does mount the
    directories, but not where the workflow expressions say they are:

        docker run --workdir /github/workspace \\
          -v ".../_work/_temp":"/github/runner_temp" \\
          -v ".../_work/<repo>/<repo>":"/github/workspace"

    RUNNER_TEMP is there, at /github/runner_temp, while
    "${{ runner.temp }}" expands to the host path on the left of that
    mount -- which does not exist inside the container. So a job can
    satisfy every other criterion here, with a download and its
    consumers all agreeing on a runner.temp directory, and still fail
    every upload. That configuration was shipped and had to be found
    by running it.

    The requirement is therefore the opposite of the workspace rule:
    workspace-relative paths, and so a checkout to clean the
    workspace they sit in.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    issues = []
    for name, body in workflow_job_blocks(content):
        steps = workflow_step_blocks(body)
        containers = job_container_actions(steps)
        if not containers:
            continue

        for step in steps:
            action = step_action(step)
            key = containers.get(action)
            if key is None:
                continue
            value = step_with_inputs(step).get(key)
            if value is not None and not is_workspace_relative(value):
                issues.append(
                    f'the {name} job passes "{key}: {value}" to {action}, '
                    'which does its work in a Docker container where the '
                    'runner\'s own paths are not where the workflow '
                    'expressions say; use a path relative to the workspace, '
                    f'such as "{key}: dist/"')

        for step in steps:
            if step_action(step) != DOWNLOAD_ARTIFACT_ACTION:
                continue
            path = step_with_inputs(step).get('path', '')
            if path and not is_workspace_relative(path):
                issues.append(
                    f'the {name} job downloads to "{path}", which the '
                    f'container running {sorted(containers)[0]} cannot '
                    'read; download into the workspace with "path: dist/"')

        if not job_checks_out(body):
            issues.append(
                f'the {name} job runs {sorted(containers)[0]} in a '
                'container, so its paths must be workspace-relative, but it '
                'does not check out -- and this pool is persistent, so the '
                'workspace is whatever the last job left. Add '
                '"actions/checkout", which cleans with git clean -ffdx')

    return issues


def dist_agreement_issues(repo):
    """Findings for a step reading the distribution from elsewhere.

    Separate from where the download goes, and asked of every job that
    downloads rather than only the ones which skip checkout: a step
    pointed at a directory the download did not fill reads an empty
    glob wherever that directory is. Moving the download and leaving a
    consumer behind is the way this breaks in practice, because the
    two are several lines apart and only one of them is obviously
    about the distribution.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    issues = []
    for name, body in workflow_job_blocks(content):
        steps = workflow_step_blocks(body)
        downloads = [step for step in steps
                     if step_action(step) == DOWNLOAD_ARTIFACT_ACTION]
        paths = [step_with_inputs(step).get('path') for step in downloads]
        # A download with no `path:` at all decides the destination
        # itself, so there is nothing here to agree or disagree with.
        # The asset criterion already reports that job, and guessing a
        # destination in order to have something to compare against
        # would turn one finding into two saying the same thing.
        if not downloads or any(path is None for path in paths):
            continue
        targets = {dist_directory(path) for path in paths}

        for step in steps:
            key = DIST_CONSUMING_INPUTS.get(step_action(step))
            if key is None:
                continue
            value = step_with_inputs(step).get(key)
            # A block scalar's header is not the value: the paths are
            # on the lines below it, one per line, and reading the
            # header as a path reports '|' as somewhere else.
            if value is None or BLOCK_INDICATOR_RE.match(value):
                continue
            if dist_directory(value) in targets:
                continue
            issues.append(
                f'the {name} job downloads the distribution to '
                f'{" or ".join(sorted(repr(t) for t in targets))} but its '
                f'"{key}: {value}" reads somewhere else, so that step sees '
                'an empty directory')

    return issues


def job_publishes(body):
    """Does this job have an effect outside the workflow run?

    Two signals, and between them they separate the publishing jobs
    from the build jobs across every release.yml in the fleet.

    An `environment:` is the stronger one: the fleet gates sign-tag,
    publish-pypi, publish-proxy-pypi and publish-collection behind the
    `release` environment precisely because they publish, and a build
    job has never had one.

    github-release needs the second signal because it carries no
    environment -- it is identified by attaching assets, which is the
    thing it does that a build job does not. Uploading an artifact
    does not count: that is visible only to later jobs in the same
    run, which is exactly the distinction being drawn.
    """
    if 'environment' in job_level_keys(body):
        return True
    return any(
        step_action(step) == GH_RELEASE_ACTION
        for step in workflow_step_blocks(body)
    )


def release_dispatch_guard_issues(repo):
    """Findings for publishing jobs a manual dispatch can reach.

    release.yml offers `workflow_dispatch` so that a maintainer can
    exercise the build and the twine check without cutting a release.
    That is only true while the publishing jobs decline to run on the
    resulting ref, because a dispatch arrives on `refs/heads/<branch>`
    and every job below the build assumes a tag.

    Unguarded, sign-tag computes its tag name from the ref it was
    given, so `refs/heads/develop` becomes the literal tag name and it
    force-pushes `refs/tags/refs/heads/develop`. The run then carries
    on into the PyPI publish. A smoke test turns into a release of
    whatever happened to be on the branch.

    Build jobs are deliberately left out. They are what the dispatch
    is for, and requiring the guard on them would remove the feature
    rather than make it safe.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    if not has_workflow_dispatch(content):
        return []

    publishing = [(name, body) for name, body in workflow_job_blocks(content)
                  if job_publishes(body)]
    unguarded = [name for name, body in publishing
                 if not job_is_tag_guarded(body)]
    # Jobs testing the ref but not the event are reported apart from
    # those testing neither. They are a step further along, and saying
    # so names the clause that is actually missing.
    ref_only = [name for name, body in publishing
                if job_is_tag_guarded(body) and not job_is_push_guarded(body)]

    issues = []
    # One unguarded job is the common case once a repository has been
    # partially fixed, so agree the verb with the list rather than
    # emitting "publish-pypi lack".
    if unguarded:
        lacks = 'lacks' if len(unguarded) == 1 else 'lack'
        issues.append(
            'release.yml can be started by hand but its publishing jobs are '
            f'not confined to tags: {", ".join(unguarded)} {lacks} '
            '"if: startsWith(github.ref, \'refs/tags/v\')", so a manual '
            'run on a branch force-pushes a "refs/tags/refs/heads/<branch>" '
            'tag and proceeds to publish')
    if ref_only:
        tests = 'tests' if len(ref_only) == 1 else 'test'
        issues.append(
            f'the publishing job{"" if len(ref_only) == 1 else "s"} '
            f'{", ".join(ref_only)} {tests} the ref but not the event, and a '
            'workflow_dispatch can be aimed at a tag, so a manual run '
            're-signs and force-pushes an existing tag; require '
            '"github.event_name == \'push\'" as well, which costs nothing '
            'because re-running a release keeps the original push event')
    return issues


#: The shared actions a release is gated by: the one which refuses a
#: commit CI did not pass, and the one which refuses a wheel carrying
#: its tests. Both are called at @main, so the version is not read.
CI_GATE_ACTION = 'shakenfist/actions/require-ci-success'
CHECK_DIST_ACTION = 'shakenfist/actions/check-dist'
UPLOAD_ARTIFACT_ACTION = 'actions/upload-artifact'
PATHS_FILTER_ACTION = 'dorny/paths-filter'

#: Compared case-folded, because GitHub resolves an action's owner and
#: name without regard to case and this one is written both ways.
MATURIN_ACTION = 'pyo3/maturin-action'

#: Where check-dist looks when its step names no `path:`.
CHECK_DIST_DEFAULT_PATH = 'dist/'

#: The branch a push run has to be on for the gate to count it. Every
#: repository's default branch is develop, which the default-branch
#: criterion enforces, so it is a constant here rather than an API call
#: from a criterion that otherwise reads only the checkout.
DEFAULT_BRANCH = 'develop'

#: A step building a distribution with the PyPA builder, however the
#: interpreter is spelled: python, python3, or a venv's python3.11.
PYTHON_BUILD_RE = re.compile(r'\bpython[0-9.]*\s+-m\s+build\b')

#: The status functions which let a job run whatever became of the
#: jobs it needs. Without one, GitHub's implicit success() skips a job
#: whose needs did not all succeed, which is the property the gate
#: relies on.
STATUS_OVERRIDE_RE = re.compile(r'\balways\(\)|!\s*cancelled\(\)')

#: The clause confining an admission of a skipped need to runs that are
#: not a push, as the template's build guard does.
OFF_PUSH_RE = re.compile(r"github\.event_name\s*!=\s*'push'")

#: A concurrency key which branches on the push event, and so can give
#: a push a key of its own. What that branch keys on is not read, as
#: merge-group-cancellation does not read its merge_group branch.
PUSH_BRANCH_RE = re.compile(r"github\.event_name\s*[=!]=\s*'push'")

#: The commit a merge group's path filter must diff against.
MERGE_GROUP_BASE_SHA = 'github.event.merge_group.base_sha'


def need_result_re(need, comparison):
    """A test of `needs.<need>.result`, in either spelling GitHub takes.

    `needs.ci-gate.result` and `needs['ci-gate'].result` are the same
    property, and a clause written the second way is still the clause.
    """
    name = re.escape(need)
    return re.compile(
        r"needs(?:\." + name + r"|\[\s*'" + name + r"'\s*\])"
        r'\.result\s*' + comparison)


def success_clause_re(need):
    """The clause requiring that a needed job succeeded."""
    return need_result_re(need, r"==\s*'success'")


def admission_clause_re(need):
    """A clause letting a job through on some other result of a need."""
    return need_result_re(need, r"(?:==\s*'(?!success')[a-z]+'|!=)")


def needs_cycle(needs):
    """The first cycle in a `needs:` graph, as a list of names, or None.

    The list starts and ends with the same job, so it reads as the
    loop it is. A need naming no job is not followed: that is reported
    on its own, and following it would only find nothing.
    """
    state = {}

    def visit(name, trail):
        state[name] = 'open'
        trail.append(name)
        for need in needs[name]:
            if need not in needs:
                continue
            if state.get(need) == 'open':
                return trail[trail.index(need):] + [need]
            if need not in state:
                found = visit(need, trail)
                if found:
                    return found
        trail.pop()
        state[name] = 'done'
        return None

    for name in needs:
        if name not in state:
            found = visit(name, [])
            if found:
                return found
    return None


def job_graph_issues(needs):
    """Findings for a `needs:` graph that cannot be read as one.

    GitHub refuses to run a workflow whose jobs need a job that does
    not exist, or need each other in a loop, so neither is a shape a
    working release has. They are reported rather than raised: these
    rules read text, and a reading that crashed on a malformed file
    would be an error on every run instead of a finding someone can
    fix.
    """
    issues = []
    for name, wanted in needs.items():
        for need in wanted:
            if need not in needs:
                issues.append(
                    f'the {name} job needs {need}, which is not a job in '
                    'release.yml, so GitHub will not run the workflow and '
                    'which jobs wait for CI cannot be read')
    cycle = needs_cycle(needs)
    if cycle:
        issues.append(
            f'the jobs {" -> ".join(cycle)} need each other in a cycle, so '
            'GitHub will not run the workflow and which jobs wait for CI '
            'cannot be read')
    return issues


def downstream_jobs(needs, root):
    """Every job which needs root, directly or through other jobs."""
    found = set()
    frontier = [root]
    while frontier:
        current = frontier.pop()
        for name, wanted in needs.items():
            if current in wanted and name not in found:
                found.add(name)
                frontier.append(name)
    return found


def upstream_jobs(needs, job):
    """Every job which job needs, directly or through other jobs."""
    found = set()
    frontier = [job]
    while frontier:
        for need in needs.get(frontier.pop(), []):
            if need in needs and need not in found:
                found.add(need)
                frontier.append(need)
    return found


def gate_jobs(blocks):
    """The jobs with a step that runs the CI gate, in file order."""
    return [
        name for name, body in blocks
        if any(step_action(step) == CI_GATE_ACTION
               for step in workflow_step_blocks(body))
    ]


def release_ci_gate_issues(repo):
    """Findings for a release that can publish without CI having passed.

    The gate is a job running require-ci-success, which fails unless
    the named CI workflows succeeded on the tagged commit. It stops a
    release only for the jobs that wait for it. A job that names an
    environment is the one a reviewer approves and the one that signs
    or publishes, so each of those has to be downstream of the gate
    through `needs:`. A reviewer is a human check that the release is
    wanted, not that it was tested, and kerbside's ungated release
    showed the two are not the same.

    Being downstream is not enough on its own. A job whose `if:` uses
    always() or !cancelled() runs whatever became of the jobs it
    needs, so a failed gate would not stop it -- unless the same
    condition requires `needs.<job>.result == 'success'` for each job
    it needs. That is asked of every job on the way from the gate to
    an environment, because one job let through breaks the chain for
    everything after it.

    The template's build guard also lets a skipped gate through, so
    that a workflow_dispatch dry run still exercises the build. That
    is accepted only where it cannot reach a release: the admission
    has to sit beside "github.event_name != 'push'", and every
    environment job below it has to be confined to a pushed tag, which
    is the one event on which the gate always runs. The condition's
    boolean structure is not parsed, so the two clauses are looked for
    rather than proved to be joined by &&.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    blocks = workflow_job_blocks(content)
    gates = gate_jobs(blocks)
    if not gates:
        return [
            f'release.yml has no job running {CI_GATE_ACTION}, so a tag '
            'publishes the commit it points at whether or not CI passed '
            'there; add the ci-gate job from the template and make the '
            'build need it']
    if len(gates) > 1:
        return [
            f'release.yml runs {CI_GATE_ACTION} in {len(gates)} jobs '
            f'({", ".join(gates)}), so which jobs wait for CI cannot be '
            'read; keep one ci-gate job and have the others need it']

    needs = {name: job_needs(body) for name, body in blocks}
    unreadable = job_graph_issues(needs)
    if unreadable:
        return unreadable

    jobs = dict(blocks)
    gate = gates[0]
    gated = downstream_jobs(needs, gate)
    environments = [name for name, body in blocks
                    if job_environment(body) is not None]

    issues = []
    for name in environments:
        if name not in gated:
            issues.append(
                f'the {name} job names an environment but is not downstream '
                f'of {gate} through "needs:", so its reviewer can approve a '
                'release of a commit CI never passed; make it need '
                f'{gate}, or a job that does')

    # The jobs between the gate and an environment, gate included:
    # anything not on such a path cannot carry a failed gate into a
    # release, and judging it would ask a build-only job to be guarded.
    path = {gate}
    for name in environments:
        if name in gated:
            path |= {name} | (upstream_jobs(needs, name) & gated)

    for name, body in blocks:
        if name not in path:
            continue
        condition = job_level_keys(body).get('if', '')
        if not STATUS_OVERRIDE_RE.search(condition):
            continue
        missing = [need for need in needs[name]
                   if not success_clause_re(need).search(condition)]
        if missing:
            issues.append(
                f'the {name} job runs under always() or !cancelled() '
                'without requiring '
                + ' and '.join(f"needs.{need}.result == 'success'" for need in missing)
                + ', so it runs after a failed gate and carries that '
                'commit on towards a release; add the clause')
        admitted = [need for need in needs[name]
                    if admission_clause_re(need).search(condition)]
        if not admitted:
            continue
        if not OFF_PUSH_RE.search(condition):
            issues.append(
                f'the {name} job lets {", ".join(admitted)} through on a '
                'result other than success without confining that to '
                "\"github.event_name != 'push'\", so a tag push whose gate "
                'did not succeed can still reach a release; admit a skipped '
                'gate only off push, as the template does')
        reached = [job for job in environments
                   if job == name or job in downstream_jobs(needs, name)]
        unguarded = [job for job in reached if not job_is_push_guarded(jobs[job])]
        if unguarded:
            issues.append(
                f'the {name} job lets {", ".join(admitted)} through on a '
                'result other than success, which is safe only while the '
                'environment jobs below it run on a pushed tag alone, and '
                f'{", ".join(unguarded)} '
                f'{"is" if len(unguarded) == 1 else "are"} not confined to one; '
                "add \"if: github.event_name == 'push' && "
                "startsWith(github.ref, 'refs/tags/v')\"")
    return issues


def gate_workflow_names(content):
    """The workflow files release.yml's gate steps name, in order.

    The `workflows:` input is a block scalar in the template, one file
    per line, and an inline value in a repository naming only one.
    """
    names = []
    for _, body in workflow_job_blocks(content):
        for step in workflow_step_blocks(body):
            if step_action(step) != CI_GATE_ACTION:
                continue
            for line in mapping_value_lines(indented_block(step, 'with'), 'workflows'):
                name = line.strip('"').strip("'").strip()
                if name and name not in names:
                    names.append(name)
    return names


def trigger_filter(trigger, key):
    """The patterns under one filter of a trigger, or None if it has none.

    A filter is written inline (`branches: [develop]`, or a bare
    `branches: develop`) or as a block sequence below its key, and the
    two read the same.
    """
    keys = job_level_keys(trigger)
    if key not in keys:
        return None
    text = keys[key] or indented_block(trigger, key) or ''
    text = text.strip()
    if text.startswith('['):
        text = text[1:].rstrip(']')
    patterns = []
    for part in re.split(r'[,\n]', text):
        part = part.strip()
        if part.startswith('- '):
            part = part[2:]
        part = part.strip().strip('"').strip("'").strip()
        if part:
            patterns.append(part)
    return patterns


def branch_matches(patterns, branch):
    """Does a `branches:` filter admit this branch?

    GitHub reads the patterns in order, and a `!` pattern excludes a
    branch an earlier pattern admitted, so the last pattern to match
    decides.
    """
    admitted = False
    for pattern in patterns:
        if pattern.startswith('!'):
            if fnmatch.fnmatchcase(branch, pattern[1:]):
                admitted = False
        elif fnmatch.fnmatchcase(branch, pattern):
            admitted = True
    return admitted


def pushes_to_default_branch(header):
    """Does this workflow run on a push to the default branch?

    The question require-ci-success asks of a push run: one counts
    only if it ran on the default branch. A push trigger filtered to
    other branches, or to tags alone, gives the default branch no run
    at all, so it is not a trigger the gate can use.

    A push trigger whose filters cannot be found below it is taken to
    reach every branch. That errs towards judging the workflow's
    concurrency rather than excusing it.
    """
    if PUSH_FLOW_RE.search(header):
        return True
    if not PUSH_TRIGGER_RE.search(header):
        return False
    on = indented_block(strip_yaml_comments(header, trailing=True), 'on')
    trigger = indented_block(on, 'push') if on is not None else None
    if trigger is None:
        return True
    branches = trigger_filter(trigger, 'branches')
    if branches is not None:
        return branch_matches(branches, DEFAULT_BRANCH)
    ignored = trigger_filter(trigger, 'branches-ignore')
    if ignored is not None:
        return not branch_matches(ignored, DEFAULT_BRANCH)
    keys = job_level_keys(trigger)
    return 'tags' not in keys and 'tags-ignore' not in keys


def push_concurrency_issues(wf, content):
    """Findings for a gated workflow whose push runs cancel each other.

    On a push the ref is the same for every commit on the branch, so a
    group keyed on github.ref puts each commit in the group of the one
    before it. With cancel-in-progress the newer run cancels the older
    one. Without it the cancelling still happens: a group holds one
    running and one pending run, so a third arrival cancels the
    pending one even with cancel-in-progress: false. A cancelled run
    is not a success, so the gate finds nothing for that commit and
    its tag cannot be released. Measured on develop before the fleet
    moved to merge queues, this took out 12 of 74 push runs in
    hunkydory. Keying the group on github.sha when the event is a push
    gives each commit a run of its own, and is the only fix: what
    cancel-in-progress says does not matter.

    The workflow's group and each job's are both read, because either
    can cancel, in the mapping form and in the inline form that names
    the group alone. A job's own `if:` is not, so a job kept off push
    runs is still judged; keying it by sha on push costs nothing.
    """
    def group_of(body):
        block = concurrency_block(body)
        if block is not None:
            return block
        return job_level_keys(body).get('concurrency') or None

    blocks = [('the workflow', group_of(workflow_header(content)))]
    blocks.extend((f'the {name} job', group_of(body))
                  for name, body in workflow_job_blocks(content))
    issues = []
    for where, block in blocks:
        if block is None or 'github.ref' not in block or PUSH_BRANCH_RE.search(block):
            continue
        issues.append(
            f'{wf}, which the gate counts on a push to {DEFAULT_BRANCH}, keys '
            f'the concurrency group of {where} on github.ref, so successive '
            'commits share one group and a later run cancels an earlier one '
            '-- running or pending, whatever cancel-in-progress says -- and '
            'the gate finds no success for that commit; key it on github.sha '
            "when github.event_name == 'push'")
    return issues


def confined_to_pull_request(condition):
    """Does an `if:` confine its step or job to pull_request events?

    Read as job_excluded_from_merge_group() reads its second form: the
    condition names events by equality and every one it names is
    pull_request. The boolean structure is not parsed.
    """
    events = EVENT_NAME_EQUALITY_RE.findall(condition or '')
    return bool(events) and set(events) == {'pull_request'}


def merge_group_path_filter_issues(wf, content):
    """Findings for a gated path filter that misreads a merge group.

    A merge group's changes have to be diffed against the commit the
    group was built on, github.event.merge_group.base_sha. Diffed
    against the branch name instead, a re-run after the entry has
    merged compares the branch with itself, finds nothing changed,
    skips the tests and passes. The gate then counts that run as CI
    passing on the commit. So the filter step either passes that base,
    or does not run on a merge group at all, by its own `if:` or its
    job's, and the merge group then runs everything.

    Only dorny/paths-filter is read. A filter written in shell cannot
    be told from any other script, and none of the gated workflows has
    one.
    """
    issues = []
    for name, body in workflow_job_blocks(content):
        job_condition = job_level_keys(body).get('if')
        for step in workflow_step_blocks(body):
            if step_action(step) != PATHS_FILTER_ACTION:
                continue
            base = ' '.join(mapping_value_lines(indented_block(step, 'with'), 'base'))
            if MERGE_GROUP_BASE_SHA in base:
                continue
            if confined_to_pull_request(step_keys(step).get('if')):
                continue
            if confined_to_pull_request(job_condition):
                continue
            issues.append(
                f'{wf}, which the gate counts, runs {PATHS_FILTER_ACTION} in '
                f'the {name} job on merge groups without diffing against '
                f'{MERGE_GROUP_BASE_SHA}, so a re-run after the entry has '
                'merged finds no changes and passes untested; pass "base: '
                f"${{{{ {MERGE_GROUP_BASE_SHA} || '{DEFAULT_BRANCH}' }}}}\", or "
                "confine the step to \"github.event_name == 'pull_request'\"")
    return issues


def release_ci_trigger_issues(repo):
    """Findings for gated workflows that cannot give the gate a run.

    require-ci-success counts a workflow's run only from a merge group
    on the default branch's queue, or from a push to the default
    branch. A named workflow which triggers on neither never has a run
    the gate counts, so every release fails at the gate -- after the
    tag is pushed, which is the expensive moment to find out. A name
    with no file behind it fails the same way.

    Each trigger brings a way to pass a commit that was not tested. On
    a push, a concurrency group keyed on the ref cancels the previous
    commit's run. On a merge group, a path filter diffing against the
    branch passes a re-run untested. The cancellation of merge group
    runs is merge-group-cancellation's to judge, and not repeated here.

    The gate also refuses a run that concluded skipped, so a workflow
    whose jobs all skip on a merge group fails every release. That is
    decided by the jobs' conditions at run time, and is not read here.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    issues = []
    for wf in gate_workflow_names(content):
        text = repo.workflow(wf)
        if text is None:
            issues.append(
                f'release.yml\'s gate names {wf}, which is not a workflow in '
                '.github/workflows/, so the gate finds no run of it and every '
                'release fails; name each workflow by its bare file name')
            continue
        header = workflow_header(text)
        merge_group = bool(MERGE_GROUP_TRIGGER_RE.search(header)
                           or MERGE_GROUP_FLOW_RE.search(header))
        push = pushes_to_default_branch(header)
        if not (merge_group or push):
            issues.append(
                f'release.yml\'s gate names {wf}, which runs neither on '
                f'merge_group nor on a push to {DEFAULT_BRANCH}, so no merged '
                'commit has a run the gate counts and every release fails; '
                'add a merge_group trigger')
            continue
        if push:
            issues.extend(push_concurrency_issues(wf, text))
        if merge_group:
            issues.extend(merge_group_path_filter_issues(wf, text))
    return issues


def builds_wheels(step):
    """Does this step build a distribution this criterion can see?"""
    action = step_action(step)
    if action is not None:
        return action.lower() == MATURIN_ACTION
    return bool(PYTHON_BUILD_RE.search(strip_yaml_comments(step, trailing=True)))


def upload_paths(step):
    """The paths an upload-artifact step uploads, one per entry."""
    return [line.strip('"').strip("'").strip()
            for line in mapping_value_lines(indented_block(step, 'with'), 'path')]


def release_check_dist_issues(repo):
    """Findings for a release that uploads wheels nobody checked.

    check-dist fails a wheel with a member under a directory named
    tests or *_tests. Every wheel in the table that started this was
    built and published without anything looking inside it, and the
    release copy of the check is the one that stops a bad wheel, since
    it runs on the artefact that is uploaded.

    A job handles wheels if it builds them with `python -m build` or
    PyO3/maturin-action, or uploads a path naming *.whl. In such a job
    check-dist has to run after every build and before every upload --
    before the build it checks a stale directory, after the upload it
    checks what has already left -- and on the directory the upload
    reads, compared as dist_directory() compares the other paths here.

    A job that builds through a script and uploads a bare directory
    shows neither signal, and is not judged. ryll and andris build
    through scripts but upload *.whl, so they are.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []

    issues = []
    for name, body in workflow_job_blocks(content):
        steps = workflow_step_blocks(body)
        builds = [index for index, step in enumerate(steps) if builds_wheels(step)]
        uploads = [(index, upload_paths(step)) for index, step in enumerate(steps)
                   if step_action(step) == UPLOAD_ARTIFACT_ACTION]
        if not builds and not any('.whl' in path for _, paths in uploads for path in paths):
            continue

        checks = [(index, dist_directory(step_with_inputs(step).get('path') or CHECK_DIST_DEFAULT_PATH))
                  for index, step in enumerate(steps)
                  if step_action(step) == CHECK_DIST_ACTION]
        if not checks:
            issues.append(
                f'the {name} job builds or uploads wheels without running '
                f'{CHECK_DIST_ACTION}, so a wheel carrying its tests is '
                'published unchecked; add a check-dist step between the '
                'build and the upload')
            continue
        last_build = max(builds, default=-1)
        checked = [(index, directory) for index, directory in checks if index > last_build]
        if not checked:
            issues.append(
                f'the {name} job runs check-dist before it builds, so it '
                'checks whatever was there already rather than the wheels '
                'it publishes; move check-dist after the build')
            continue
        for index, paths in uploads:
            before = {directory for position, directory in checked if position < index}
            if not before:
                issues.append(
                    f'the {name} job uploads {", ".join(paths) or "its artifact"} '
                    'before check-dist runs, so the wheels leave the job '
                    'unchecked; move check-dist above the upload')
                continue
            for path in paths:
                if dist_directory(path) in before:
                    continue
                issues.append(
                    f'the {name} job runs check-dist on '
                    f'{" and ".join(sorted(repr(d + "/") for d in before))} but '
                    f'uploads "{path}", so the wheels it publishes are not the '
                    'ones it checked; point check-dist\'s "path:" at '
                    f'"{dist_directory(path)}/"')
    return issues


class ReleaseProcess(Check):
    id = 'release-process'
    spec = 'docs/audits/release-process.md'
    template = 'templates/release-automation/'
    issue_title = 'Release process'

    def applies(self, repo):
        """A Python package, or anything with a release workflow.

        Two halves with different scopes. The packaging rules are about
        how a Python project is laid out for release, so they need a
        pyproject.toml. The workflow-safety rules are about what
        release.yml does, and a Rust or TypeScript project's release.yml
        signs and publishes exactly as a Python one does -- keying the
        whole criterion on pyproject.toml left four release repositories
        unmeasured.
        """
        if repo.props['has_pyproject_toml'] or repo.isfile(RELEASE_WORKFLOW):
            return None
        return f'No pyproject.toml or {RELEASE_WORKFLOW} (nothing is released from here)'

    def run(self, repo):
        """Check release process compliance."""
        issues = []
        if repo.props['has_pyproject_toml']:
            if repo.exists('release.sh'):
                issues.append('release.sh still exists (should be removed)')
            if repo.exists('requirements.txt'):
                issues.append('requirements.txt still exists (use pyproject.toml)')
            if not repo.isfile(RELEASE_WORKFLOW):
                issues.append(f'Missing {RELEASE_WORKFLOW}')
            if not repo.exists('RELEASE-SETUP.md'):
                issues.append('Missing RELEASE-SETUP.md')

        if repo.isfile(RELEASE_WORKFLOW):
            issues.extend(release_asset_issues(repo))
            issues.extend(release_dispatch_guard_issues(repo))
            issues.extend(release_workspace_issues(repo))
            issues.extend(dist_agreement_issues(repo))
            issues.extend(release_container_path_issues(repo))
            issues.extend(release_ci_gate_issues(repo))
            issues.extend(release_ci_trigger_issues(repo))
            issues.extend(release_check_dist_issues(repo))

        if issues:
            return self.fail('; '.join(issues))
        return self.ok('Release process is compliant')
