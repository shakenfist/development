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
from audit.checks.release_rust import release_rust_issues
from audit.text.release_jobs import (
    CHECK_DIST_ACTION, DOWNLOAD_ARTIFACT_ACTION, GH_RELEASE_ACTION, RUNNER_TEMP_RE, UPLOAD_ARTIFACT_ACTION,
    ci_gate_findings, gate_workflow_names, input_is, job_checks_out, job_container_actions, job_publishes,
    trigger_filter,
)
from audit.text.workflows import (
    BLOCK_INDICATOR_RE, EVENT_NAME_EQUALITY_RE, MERGE_GROUP_FLOW_RE, MERGE_GROUP_SCALAR_RE,
    MERGE_GROUP_TRIGGER_RE, PUSH_FLOW_RE, PUSH_SCALAR_RE, PUSH_TRIGGER_RE, concurrency_block, has_workflow_dispatch,
    indented_block, job_is_push_guarded, job_is_tag_guarded, job_level_keys, mapping_value_lines, step_action,
    step_keys, step_with_inputs, strip_yaml_comments, workflow_header, workflow_job_blocks, workflow_step_blocks,
)


RELEASE_WORKFLOW = '.github/workflows/release.yml'


#: The action which attests the distribution's build provenance.
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


#: Expressions which expand to a path on the runner's own filesystem.
#: Inside a container action these name places that either do not
#: exist or are mounted somewhere else, so a path built from one is
#: wrong in the container however it is spelled.
HOST_PATH_EXPRESSION_RE = re.compile(
    r'\$\{\{\s*(?:runner\.(?:temp|workspace)|github\.workspace)\s*\}\}'
)


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


#: A concurrency key which branches on the push event, and so can give
#: a push a key of its own. What that branch keys on is not read, as
#: merge-group-cancellation does not read its merge_group branch.
PUSH_BRANCH_RE = re.compile(r"github\.event_name\s*[=!]=\s*'push'")

#: What a push run's concurrency group has to be keyed on.
SHA_RE = re.compile(r'\bgithub\.sha\b')

#: The commit a merge group's path filter must diff against.
MERGE_GROUP_BASE_SHA = 'github.event.merge_group.base_sha'


def release_ci_gate_issues(repo):
    """Findings for a release that can publish without CI having passed.

    What is judged, and why, is ci_gate_findings()'s to say: the Rust
    rules read the same findings to know which jobs this rule has
    already reported.
    """
    content = repo.workflow('release.yml')
    if content is None:
        return []
    return [finding.message for finding in ci_gate_findings(workflow_job_blocks(content))]


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
    concurrency rather than excusing it. The flow and scalar spellings
    have nowhere to put a filter, so they reach every branch too.
    """
    if PUSH_FLOW_RE.search(header) or PUSH_SCALAR_RE.search(header):
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

    A group that mentions github.ref passes only when it branches on
    the push event and names github.sha. The expression is not parsed,
    so how the two are combined is taken on trust.

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
        if block is None or 'github.ref' not in block:
            continue
        if PUSH_BRANCH_RE.search(block) and SHA_RE.search(block):
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
                           or MERGE_GROUP_FLOW_RE.search(header)
                           or MERGE_GROUP_SCALAR_RE.search(header))
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
            if repo.props['has_cargo_toml']:
                issues.extend(release_rust_issues(repo))

        if issues:
            return self.fail('; '.join(issues))
        return self.ok('Release process is compliant')
