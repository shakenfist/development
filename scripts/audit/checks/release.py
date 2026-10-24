"""The criteria about how a project cuts a release.

What the release workflow publishes and from where, which job may
publish, and the paths that workflow hands to actions which run in a
container, together with the leftovers of the older process (release.sh,
requirements.txt) and the setup notes a release depends on.
"""

import re

from audit.check import Check
from audit.text.workflows import (
    BLOCK_INDICATOR_RE, has_workflow_dispatch, job_is_push_guarded, job_is_tag_guarded,
    job_level_keys, step_action, step_with_inputs, workflow_job_blocks,
    workflow_step_blocks,
)


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


class ReleaseProcess(Check):
    id = 'release-process'
    spec = 'docs/audits/release-process.md'
    template = 'templates/release-automation/'
    issue_title = 'Release process'

    def run(self, repo):
        """Check release process compliance."""
        if not repo.props['has_pyproject_toml']:
            return self.skip('No pyproject.toml (not a Python package)')

        issues = []
        if repo.exists('release.sh'):
            issues.append('release.sh still exists (should be removed)')
        if repo.exists('requirements.txt'):
            issues.append('requirements.txt still exists (use pyproject.toml)')
        if not repo.isfile('.github/workflows/release.yml'):
            issues.append('Missing .github/workflows/release.yml')
        if not repo.exists('RELEASE-SETUP.md'):
            issues.append('Missing RELEASE-SETUP.md')
        issues.extend(release_asset_issues(repo))
        issues.extend(release_dispatch_guard_issues(repo))
        issues.extend(release_workspace_issues(repo))
        issues.extend(dist_agreement_issues(repo))
        issues.extend(release_container_path_issues(repo))

        if issues:
            return self.fail('; '.join(issues))
        return self.ok('Release process is compliant')
