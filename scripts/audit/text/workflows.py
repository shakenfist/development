"""Reading workflow YAML without a YAML parser.

The audit scripts are stdlib only and deliberately have no PyYAML
dependency, so everything here is line based. That is not only a
dependency decision: several criteria are about what a file says
rather than what it means -- whether a marker comment sits on the
right line, whether a label was written literally or behind an
expression -- and a parsed document has already thrown that away.
"""

import itertools
import re


# The value portion of a `runs-on:` line, e.g. the
# '[self-hosted, static]' in 'runs-on: [self-hosted, static]'.
RUNS_ON_RE = re.compile(r'^\s*runs-on:\s*(.+?)\s*$')


# Labels that legitimately accompany 'static' on a static runner.
# Anything else (a size like 's'/'l', 'vm', or an operating system
# label like 'debian-12') describes a runner attribute the static
# pool does not advertise, so the job would never be scheduled.
STATIC_ALLOWED_LABELS = frozenset({'self-hosted', 'static'})


# A GitHub Actions expression, which we cannot resolve statically.
# Non-greedy so two expressions on one line stay two spans.
EXPRESSION_RE = re.compile(r'\$\{\{.*?\}\}')


def split_outside_expressions(text):
    """Split on the commas which are not inside a '${{ ... }}' span.

    A naive split() breaks "${{ format('{0},{1}', a, b) }}" into
    fragments, and every fragment after the first has lost the '${{'
    that marks it unresolvable -- so a caller filtering on that marker
    would take the fragments for labels.
    """
    spans = [match.span() for match in EXPRESSION_RE.finditer(text)]
    parts = []
    start = 0
    for i, char in enumerate(text):
        if char != ',':
            continue
        if any(lo <= i < hi for lo, hi in spans):
            continue
        parts.append(text[start:i])
        start = i + 1
    parts.append(text[start:])
    return parts


def split_runner_labels(value):
    """Split the value of a `runs-on:` line into its label strings.

    Handles the inline-list form ('[self-hosted, static]') and the
    bare-scalar form ('static'). Individual elements may still be
    GitHub Actions expressions; callers decide what to do with those.
    """
    # Drop a trailing inline comment (runner labels never contain
    # ' #', so this is safe).
    value = re.sub(r'\s+#.*$', '', value).strip()
    if value.startswith('['):
        inner = value[1:]
        if inner.endswith(']'):
            inner = inner[:-1]
        parts = split_outside_expressions(inner)
    else:
        parts = [value]

    labels = []
    for part in parts:
        label = part.strip().strip('"').strip("'").strip()
        if label:
            labels.append(label)
    return labels


def parse_runner_labels(value):
    """Parse the labels from the value of a `runs-on:` line.

    Returns a list of label strings, or None when the value contains a
    GitHub Actions expression we cannot resolve statically (e.g.
    '${{ matrix.runner }}') -- for a check which must see every label
    to judge a line, one unresolvable element makes the whole line
    unjudgeable.

    The test is applied to the split labels, not to the raw value, so
    that an expression named in a trailing comment does not make a
    perfectly literal `runs-on:` unjudgeable. Both this and
    literal_runner_labels() must reach the same verdict about which
    elements are expressions, or the two disagree about the same line.
    """
    labels = split_runner_labels(value)
    if any('${{' in label for label in labels):
        return None
    return labels


def literal_runner_labels(value):
    """The statically-known labels of a `runs-on:` line.

    Unlike parse_runner_labels() this does not give up when part of
    the value is an expression; it drops the unresolvable elements and
    returns the rest. That suits a check asking whether a particular
    label is *present*, because the fleet writes such labels literally
    even when a sibling element is a matrix expression -- see
    kerbside-patches' functional-tests.yml, which pairs
    "'${{ matrix.test.runs_on }}'" with a literal 'xl'.
    """
    return [
        label for label in split_runner_labels(value)
        if '${{' not in label
    ]


WORKFLOW_JOB_RE = re.compile(r'^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$')


def workflow_job_blocks(content):
    """Split a workflow into (job name, job text) pairs.

    Line-based rather than YAML-parsed, to avoid a PyYAML
    dependency, and matching how the rest of this module reads
    workflows. A job is a two-space-indented key under a top-level
    "jobs:"; the block runs to the next such key or to the end of
    the file.
    """
    lines = content.splitlines()
    in_jobs = False
    blocks = []
    for line in lines:
        if line and not line[0].isspace():
            in_jobs = line.startswith('jobs:')
            continue
        if not in_jobs:
            continue
        match = WORKFLOW_JOB_RE.match(line)
        if match:
            blocks.append([match.group(1), []])
        elif blocks:
            blocks[-1][1].append(line)
    return [(name, '\n'.join(body)) for name, body in blocks]


def strip_yaml_comments(text):
    """Drop full-line comments from a block of YAML.

    Concurrency keys and `if:` conditions are routinely explained by
    a comment directly above them that quotes the very expression
    being warned about, so matching comment text would read those
    explanations as the code they describe.
    """
    return '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('#')
    )


def indented_block(body, key):
    """Extract the `key:` mapping from a job or workflow body.

    Returns the block's text (without the key line), or None when
    there is none. The block runs from the key to the next line at or
    below its indentation, which covers both the inline and the
    folded (`>-`) forms the fleet uses.
    """
    lines = strip_yaml_comments(body).splitlines()
    collected = None
    indent = None
    for line in lines:
        if collected is None:
            match = re.match(r'^(\s*)' + re.escape(key) + r':\s*$', line)
            if match:
                collected = []
                indent = len(match.group(1))
            continue
        if not line.strip():
            collected.append(line)
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        collected.append(line)
    if collected is None:
        return None
    return '\n'.join(collected)


STEP_ITEM_RE = re.compile(r'^(\s*)-\s')


def workflow_step_blocks(body):
    """Split a job body's `steps:` list into per-step text.

    Line based, like the rest of this module. A step is a sequence
    item under the job's `steps:` key, and its block runs from the
    dash to the next item at the same indentation. Anything indented
    further -- a `with:` mapping, the lines of a `run:` script --
    stays part of the step that contains it.

    Returns an empty list for a job with no `steps:`, which is what a
    job that calls a reusable workflow looks like.
    """
    steps = indented_block(body, 'steps')
    if steps is None:
        return []

    indent = None
    blocks = []
    for line in steps.splitlines():
        match = STEP_ITEM_RE.match(line)
        if match and (indent is None or len(match.group(1)) == indent):
            indent = len(match.group(1))
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return ['\n'.join(lines) for lines in blocks]


# The action a step invokes. The optional dash covers a `uses:` written
# as the first key of the step, where the sequence marker shares the
# line.
STEP_USES_RE = re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)\s*$', re.MULTILINE)


def step_action(step):
    """The action a step invokes, without its version.

    Returns None for a step which runs a script rather than an action.
    The version is dropped because every caller here is asking which
    action this is, not which release of it -- and the fleet pins
    majors, so matching on the full string would need updating each
    time one moves.
    """
    match = STEP_USES_RE.search(strip_yaml_comments(step))
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'").split('@')[0]


# One `key: value` pair, captured from a step's `with:` mapping.
WITH_ENTRY_RE = re.compile(
    r'^(\s*)([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$'
)


def step_with_inputs(step):
    """The inputs a step passes, as a dict of key to raw value string.

    Only the top level of the `with:` mapping: a nested mapping under
    one of the inputs describes that input's value, not another
    input, and folding the two together would let a nested key answer
    a question asked about a real one.

    A trailing inline comment is dropped, on the same reasoning as
    split_runner_labels(): the alternative is every caller comparing
    against a value with an explanation stuck to the end of it, and
    "fetch-depth: 0  # needed for setuptools_scm" is how the fleet
    actually writes these.
    """
    block = indented_block(step, 'with')
    if block is None:
        return {}

    entries = {}
    indent = None
    for line in block.splitlines():
        match = WITH_ENTRY_RE.match(line)
        if not match:
            continue
        depth = len(match.group(1))
        if indent is None:
            indent = depth
        if depth == indent:
            value = re.sub(r'\s+#.*$', '', match.group(3)).strip()
            entries[match.group(2)] = value
    return entries


# The three spellings of a manual trigger: a mapping key under `on:`,
# a flow sequence, and a bare scalar. Matched against the workflow
# header alone, so an `if:` inside a job which tests for the event
# cannot be mistaken for the trigger which enables it.
WORKFLOW_DISPATCH_RE = re.compile(r'^\s+workflow_dispatch:', re.MULTILINE)
WORKFLOW_DISPATCH_FLOW_RE = re.compile(
    r'^on:\s*\[[^\]]*\bworkflow_dispatch\b', re.MULTILINE
)
WORKFLOW_DISPATCH_SCALAR_RE = re.compile(
    r'^on:\s*workflow_dispatch\s*$', re.MULTILINE
)


def has_workflow_dispatch(content):
    """Can this workflow be started by hand?

    The question matters wherever a workflow's jobs assume the ref
    they were triggered on: a dispatch arrives on a branch, and every
    assumption about a tag stops holding.
    """
    header = strip_yaml_comments(content.split('\njobs:')[0])
    return bool(
        WORKFLOW_DISPATCH_RE.search(header)
        or WORKFLOW_DISPATCH_FLOW_RE.search(header)
        or WORKFLOW_DISPATCH_SCALAR_RE.search(header)
    )


JOB_KEY_RE = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$')

# A YAML block scalar header: '|', '>', with an optional chomping
# or indentation indicator. It introduces the value rather than
# being part of it.
BLOCK_INDICATOR_RE = re.compile(r'^[|>][+-]?\d*$')


def job_level_keys(body):
    """The job's own keys, mapped to their inline value.

    A job body keeps its original indentation, so the job's own keys
    are the shallowest in it -- everything belonging to a step, or to
    a mapping inside a step, is indented further. Reading only that
    level is what stops a step's `if:` answering a question asked
    about the job's.

    A key which introduces a block rather than a scalar maps to the
    empty string: present, with its value somewhere below.
    """
    lines = [line for line in body.splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if not lines:
        return {}
    indent = min(len(line) - len(line.lstrip()) for line in lines)

    keys = {}
    for position, line in enumerate(lines):
        match = JOB_KEY_RE.match(line)
        if not match or len(match.group(1)) != indent:
            continue
        value = re.sub(r'\s+#.*$', '', match.group(3)).strip()
        # A value continued over later lines -- a folded "if: >-", or a
        # plain scalar simply wrapped -- is gathered in, because a
        # condition read as absent is a criterion that silently passes.
        # An empty value introduces a block (steps:, permissions:) and
        # is left alone: its contents are not the key's value.
        if value:
            value = ' '.join(
                [BLOCK_INDICATOR_RE.sub('', value)]
                + [later.strip() for later in
                   itertools.takewhile(
                       lambda text: len(text) - len(text.lstrip()) > indent,
                       lines[position + 1:])]
            ).strip()
        keys[match.group(2)] = value
    return keys


# An `if:` which confines a job to a tag. The fleet writes the first
# form; the second is the equivalent GitHub offers, accepted so the
# criterion is about the property rather than one spelling of it.
TAG_GUARD_RE = re.compile(
    r"startsWith\(\s*github\.ref\s*,\s*'refs/tags/"
    r"|github\.ref_type\s*==\s*'tag'"
)


def job_is_tag_guarded(body):
    """Is this job confined to runs triggered by a tag?"""
    condition = job_level_keys(body).get('if')
    if not condition:
        return False
    return bool(TAG_GUARD_RE.search(condition))


# The clause which pins a job to the push event. Without it the guard
# tests only the ref, and a workflow_dispatch aimed at a tag satisfies
# it.
PUSH_EVENT_RE = re.compile(r"github\.event_name\s*==\s*'push'")


def job_is_push_guarded(body):
    """Is this job confined to a tag *pushed*, rather than any tag ref?

    A dispatch can be aimed at a tag, so the ref test passes there and
    the job runs -- re-signing and force-pushing a tag that already
    exists, rewriting a signed object someone may already have
    verified. Testing the event as well is what closes that, and costs
    nothing: re-running a failed release uses "Re-run jobs", which
    keeps the event of the original run.
    """
    condition = job_level_keys(body).get('if')
    if not condition:
        return False
    return bool(
        TAG_GUARD_RE.search(condition) and PUSH_EVENT_RE.search(condition)
    )
