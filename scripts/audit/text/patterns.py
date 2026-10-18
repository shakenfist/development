"""Regular expressions an audited repository wrote.

Two criteria have to evaluate a pattern the audited repository chose:
a `.pre-commit-config.yaml` `exclude:` and a Renovate `/regex/`
package matcher. Both are regular expressions by definition, so the
pattern cannot be read as a literal or a glob instead, and Python's
`re` has no time limit. A pattern such as `^(.|.)*Z` doubles its work
with every character of the subject, so against a thirty-character
path it runs for longer than the workflow's job limit: one committer
in one audited repository could fail its leg every week, and with it
hold back the compliance page for the whole fleet.

So the search runs in a child interpreter that is killed after a few
seconds. A pattern that does not finish is treated as one that does
not compile: the tool it is written for would hang on it too, so it
is the repository's to fix, and no answer is better than one taken
from a search that never ended.
"""

import functools
import json
import subprocess
import sys

from audit.github import environment_without_credentials


#: How long a pattern gets to search every subject it is given. A
#: well-formed pattern answers in milliseconds; this is the bound on a
#: pathological one, not a budget a real configuration comes near.
SEARCH_TIMEOUT = 5

# The child's whole program. It is handed the pattern and the subjects
# on stdin rather than on its command line, so neither is ever parsed
# as an argument.
_SEARCHER = (
    'import json, re, sys\n'
    'job = json.load(sys.stdin)\n'
    'pattern = re.compile(job["pattern"], job["flags"])\n'
    'print(json.dumps([bool(pattern.search(s)) for s in job["subjects"]]))\n'
)


@functools.lru_cache(maxsize=None)
def _bounded_search(pattern, subjects, flags, timeout):
    try:
        result = subprocess.run(
            [sys.executable, '-I', '-c', _SEARCHER],
            input=json.dumps({'pattern': pattern, 'flags': flags, 'subjects': list(subjects)}),
            capture_output=True, text=True, timeout=timeout,
            env=environment_without_credentials())
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        # The child raised: the pattern did not compile, or its search
        # exhausted memory or recursion, which is the same pathology
        # as running out of time.
        return None
    return tuple(json.loads(result.stdout))


def bounded_search(pattern, subjects, flags=0):
    """Which of `subjects` does `pattern` find a match in?

    Returns a tuple of booleans, one per subject in order, or None for
    a pattern that does not compile or does not finish within
    SEARCH_TIMEOUT. Answers are cached, since a Renovate matcher is
    asked about the same names once per rule.
    """
    return _bounded_search(pattern, tuple(subjects), flags, SEARCH_TIMEOUT)
