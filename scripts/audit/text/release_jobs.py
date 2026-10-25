"""Reading a release workflow's jobs, for the rules that judge a release.

Which job runs the CI gate and which jobs wait for it, which publish,
which start from a cleaned checkout, and the clauses an `if:` uses to
let a job run after a need did not succeed. Both halves of the
release-process criterion read release.yml this way -- the
workflow-safety rules in checks/release.py and the Rust template's
invariants in checks/release_rust.py. The gate rule's selection lives
here too, as ci_gate_findings(): the gate rule reports its messages,
and the Rust rules stay silent about the jobs its findings name, so the
two read one selection rather than two copies of it. Kept here rather
than in either check module because a check module imports shared
readers, never another check module.
"""

import collections
import re

from audit.text.workflows import (
    indented_block, job_environment, job_is_push_guarded, job_level_keys, job_needs, mapping_value_lines,
    step_action, step_with_inputs, workflow_job_blocks, workflow_step_blocks,
)


#: The action which pulls build artifacts back into a later job, and
#: the action which attaches them to the GitHub release.
DOWNLOAD_ARTIFACT_ACTION = 'actions/download-artifact'
GH_RELEASE_ACTION = 'softprops/action-gh-release'
CHECKOUT_ACTION = 'actions/checkout'


#: A reference to the per-job temporary directory.
RUNNER_TEMP_RE = re.compile(r'\$\{\{\s*runner\.temp\s*\}\}')


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


#: The shared actions a release is gated by: the one which refuses a
#: commit CI did not pass, and the one which refuses a wheel carrying
#: its tests. Both are called at @main, so the version is not read.
CI_GATE_ACTION = 'shakenfist/actions/require-ci-success'
CHECK_DIST_ACTION = 'shakenfist/actions/check-dist'
UPLOAD_ARTIFACT_ACTION = 'actions/upload-artifact'


#: The status functions which let a job run whatever became of the
#: jobs it needs. Without one, GitHub's implicit success() skips a job
#: whose needs did not all succeed, which is the property the gate
#: relies on.
STATUS_OVERRIDE_RE = re.compile(r'\balways\(\)|!\s*cancelled\(\)')


#: The clause confining an admission of a skipped need to runs that are
#: not a push, as the template's build guard does.
OFF_PUSH_RE = re.compile(r"github\.event_name\s*!=\s*'push'")


def input_is(inputs, key, expected):
    """Does a step input hold this boolean, however it is quoted?

    step_with_inputs() returns raw values, quotes intact, but an action
    receives every input as a string, so `true` and `"true"` arrive as
    the same thing. Comparing the raw value reported a correctly set
    `fail_on_unmatched_files: "true"` as missing. Case is folded too.
    """
    value = inputs.get(key, '')
    return value.strip().strip('"').strip("'").lower() == expected


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


def job_container_actions(steps):
    """The container actions a job runs, mapped to their path input."""
    return {
        action: CONTAINER_PATH_ACTIONS[action]
        for action in (step_action(step) for step in steps)
        if action in CONTAINER_PATH_ACTIONS
    }


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


def job_graph_problems(needs):
    """A `needs:` graph's defects, as (message, jobs) pairs.

    GitHub refuses to run a workflow whose jobs need a job that does
    not exist, or need each other in a loop, so neither is a shape a
    working release has. They are reported rather than raised: these
    rules read text, and a reading that crashed on a malformed file
    would be an error on every run instead of a finding someone can
    fix. The jobs are the ones the message names as at fault: the job
    with the dangling need, or every job in the cycle.
    """
    problems = []
    for name, wanted in needs.items():
        for need in wanted:
            if need not in needs:
                problems.append((
                    f'the {name} job needs {need}, which is not a job in '
                    'release.yml, so GitHub will not run the workflow and '
                    'which jobs wait for CI cannot be read',
                    frozenset({name})))
    cycle = needs_cycle(needs)
    if cycle:
        problems.append((
            f'the jobs {" -> ".join(cycle)} need each other in a cycle, so '
            'GitHub will not run the workflow and which jobs wait for CI '
            'cannot be read',
            frozenset(cycle)))
    return problems


def job_graph_issues(needs):
    """Findings for a `needs:` graph that cannot be read as one."""
    return [message for message, _ in job_graph_problems(needs)]


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


#: The kinds of finding ci_gate_findings() returns.
NO_GATE = 'no-gate'
SEVERAL_GATES = 'several-gates'
UNREADABLE_GRAPH = 'unreadable-graph'
UNREACHED = 'unreached'
MISSING_SUCCESS = 'missing-success'
ADMISSION_ON_PUSH = 'admission-on-push'
UNGUARDED_BELOW_ADMISSION = 'unguarded-below-admission'

#: The kinds which report a job's `if:`, and so a guard the Rust
#: template's exact guards also rule out.
GUARD_KINDS = frozenset({MISSING_SUCCESS, ADMISSION_ON_PUSH, UNGUARDED_BELOW_ADMISSION})

#: One finding of the gate rule: what it says, which kind it is, and
#: the jobs it is about.
GateFinding = collections.namedtuple('GateFinding', ('kind', 'message', 'jobs'))


def ci_gate_findings(blocks):
    """The findings for a release that can publish without CI having passed.

    The gate is a job running require-ci-success, which fails unless
    the named CI workflows succeeded on the tagged commit. It stops a
    release only for the jobs that wait for it. A job that names an
    environment is the one a reviewer approves and the one that signs
    or publishes, so each of those has to be downstream of the gate
    through `needs:` (UNREACHED). A reviewer is a human check that the
    release is wanted, not that it was tested, and kerbside's ungated
    release showed the two are not the same.

    Being downstream is not enough on its own. A job whose `if:` uses
    always() or !cancelled() runs whatever became of the jobs it
    needs, so a failed gate would not stop it -- unless the same
    condition requires `needs.<job>.result == 'success'` for each job
    it needs (MISSING_SUCCESS). That is asked of every job on the way
    from the gate to an environment, because one job let through breaks
    the chain for everything after it.

    The template's build guard also lets a skipped gate through, so
    that a workflow_dispatch dry run still exercises the build. That
    is accepted only where it cannot reach a release: the admission
    has to sit beside "github.event_name != 'push'" (ADMISSION_ON_PUSH),
    and every environment job below it has to be confined to a pushed
    tag, which is the one event on which the gate always runs
    (UNGUARDED_BELOW_ADMISSION, whose jobs are those environment jobs,
    since it is their `if:` that is at fault). The condition's boolean
    structure is not parsed, so the two clauses are looked for rather
    than proved to be joined by &&.

    No gate (NO_GATE), more than one (SEVERAL_GATES) or a `needs:`
    graph that cannot be read (UNREADABLE_GRAPH) is reported alone,
    since nothing else can be read without one gate and one graph.
    """
    gates = gate_jobs(blocks)
    if not gates:
        return [GateFinding(
            NO_GATE,
            f'release.yml has no job running {CI_GATE_ACTION}, so a tag '
            'publishes the commit it points at whether or not CI passed '
            'there; add the ci-gate job from the template and make the '
            'build need it',
            frozenset())]
    if len(gates) > 1:
        return [GateFinding(
            SEVERAL_GATES,
            f'release.yml runs {CI_GATE_ACTION} in {len(gates)} jobs '
            f'({", ".join(gates)}), so which jobs wait for CI cannot be '
            'read; keep one ci-gate job and have the others need it',
            frozenset(gates))]

    needs = {name: job_needs(body) for name, body in blocks}
    unreadable = job_graph_problems(needs)
    if unreadable:
        return [GateFinding(UNREADABLE_GRAPH, message, jobs) for message, jobs in unreadable]

    jobs = dict(blocks)
    gate = gates[0]
    gated = downstream_jobs(needs, gate)
    environments = [name for name, body in blocks
                    if job_environment(body) is not None]

    findings = []
    for name in environments:
        if name not in gated:
            findings.append(GateFinding(
                UNREACHED,
                f'the {name} job names an environment but is not downstream '
                f'of {gate} through "needs:", so its reviewer can approve a '
                'release of a commit CI never passed; make it need '
                f'{gate}, or a job that does',
                frozenset({name})))

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
            findings.append(GateFinding(
                MISSING_SUCCESS,
                f'the {name} job runs under always() or !cancelled() '
                'without requiring '
                + ' and '.join(f"needs.{need}.result == 'success'" for need in missing)
                + ', so it runs after a failed gate and carries that '
                'commit on towards a release; add the clause',
                frozenset({name})))
        admitted = [need for need in needs[name]
                    if admission_clause_re(need).search(condition)]
        if not admitted:
            continue
        if not OFF_PUSH_RE.search(condition):
            findings.append(GateFinding(
                ADMISSION_ON_PUSH,
                f'the {name} job lets {", ".join(admitted)} through on a '
                'result other than success without confining that to '
                "\"github.event_name != 'push'\", so a tag push whose gate "
                'did not succeed can still reach a release; admit a skipped '
                'gate only off push, as the template does',
                frozenset({name})))
        reached = [job for job in environments
                   if job == name or job in downstream_jobs(needs, name)]
        unguarded = [job for job in reached if not job_is_push_guarded(jobs[job])]
        if unguarded:
            findings.append(GateFinding(
                UNGUARDED_BELOW_ADMISSION,
                f'the {name} job lets {", ".join(admitted)} through on a '
                'result other than success, which is safe only while the '
                'environment jobs below it run on a pushed tag alone, and '
                f'{", ".join(unguarded)} '
                f'{"is" if len(unguarded) == 1 else "are"} not confined to one; '
                "add \"if: github.event_name == 'push' && "
                "startsWith(github.ref, 'refs/tags/v')\"",
                frozenset(unguarded)))
    return findings
