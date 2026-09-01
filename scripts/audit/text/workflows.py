"""Reading workflow YAML without a YAML parser.

The audit scripts are stdlib only and deliberately have no PyYAML
dependency, so everything here is line based. That is not only a
dependency decision: several criteria are about what a file says
rather than what it means -- whether a marker comment sits on the
right line, whether a label was written literally or behind an
expression -- and a parsed document has already thrown that away.
"""

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
