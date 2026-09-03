"""Who the audit says it measures, read out of the three places that say so.

Scope is written down three times: the matrix in
`.github/workflows/consistency-audit.yml` is what actually runs, and the
in-scope and excluded lists in `docs/audits/README.md` are what a reader is
told. Reading two of the three means splitting prose on a literal phrase, and
reading the third means splitting YAML on a line of indentation, in files
nobody edits with a parser in mind.

This module is where that parse lives, so that there is exactly one of it.
`AuditScopeIsStatedOnceTest` holds the three lists to each other and the
`scope-coverage` check holds them to the organisation; a copy of the parse in
either would let the two disagree about what the lists say, which is the
failure the check exists to prevent, one level up.

Every function here raises `ScopeParseError` rather than returning a partial
list. A start phrase that gets reworded away is loud, because the split
raises. An end phrase that gets reworded away is the dangerous one -- the
block simply runs on to the end of the file and collects every bullet after
it, and a comparison of two sets of repository names can still pass on that.
So the phrases are named constants and `bulleted_block()` asserts they still
delimit a list of repository names before anything trusts them.
"""

import os
import re


class ScopeParseError(Exception):
    """A scope list could not be read the way this module expects.

    Raised rather than returned so that a caller cannot mistake a parse that
    overran its list for a list. The check turns it into a `fail()` result
    naming the phrase and the file; the test asserts on the same text.
    """


EXCLUDED_DOC = 'docs/audits/README.md'
EXCLUDED_START = 'are **excluded**'
EXCLUDED_END = 'The `actions` repository'
EXCLUDED_BULLET = '* '

IN_SCOPE_DOC = 'docs/audits/README.md'
IN_SCOPE_START = '## In-scope projects'
IN_SCOPE_END = 'One project is in scope'
IN_SCOPE_BULLET = '- '

MATRIX_WORKFLOW = '.github/workflows/consistency-audit.yml'
MATRIX_START = '        repo:\n'
MATRIX_BULLET = '          - '

#: What a GitHub repository in any of these lists looks like. The point is not
#: to validate the name but to notice a parse that has started collecting
#: prose: a swallowed paragraph brings back bullets like "The configured
#: version file path must be covered".
#:
#: Anchored at both ends. An end-anchor alone matches any sentence closing on
#: a lowercase word -- including that exact example, which is what this guard
#: exists to reject.
REPO_NAME = re.compile(r'^[a-z0-9][a-z0-9.-]*$')


def read(root, relative):
    with open(os.path.join(root, relative)) as f:
        return f.read()


def bulleted_block(root, path, start, end, bullet):
    """Return the bullet list delimited by two literal phrases.

    Every check here is about the parse rather than the content, so that a
    reworded document fails with the phrase it needs to carry rather than with
    a comparison of two sets of repository names that no longer means
    anything.
    """
    text = read(root, path)
    if text.count(start) != 1:
        raise ScopeParseError(
            f'{path} must contain the phrase "{start}" exactly once: '
            f'it is where the scope parse starts reading the list that '
            f'follows it')
    after = text.split(start, 1)[1]
    if after.count(end) != 1:
        raise ScopeParseError(
            f'{path} must contain the phrase "{end}" exactly once '
            f'after "{start}": it is where the scope parse stops reading, '
            f'and without it the parse runs to the end of the file')
    block = after.split(end, 1)[0]
    # Any heading level, not just '## '. The excluded-projects list this
    # guards sits under a '### ', so a '###' subsection inserted inside the
    # block would have slipped past a check for '## ' alone.
    if re.search(r'^#{1,6} ', block, re.MULTILINE):
        raise ScopeParseError(
            f'the list after "{start}" in {path} now runs past a '
            f'heading, so "{end}" is no longer the end of it')
    entries = [
        line[len(bullet):].strip() for line in block.splitlines()
        if line.startswith(bullet)
    ]
    if not entries:
        raise ScopeParseError(
            f'no "{bullet}" bullets between "{start}" and "{end}" in '
            f'{path}; the list has moved or changed its bullet style')
    for entry in entries:
        if not REPO_NAME.search(entry):
            raise ScopeParseError(
                f'"{entry}" was read as a repository name from the '
                f'list after "{start}" in {path}, so the parse is '
                f'picking up something that is not that list')
    return entries


def matrix_repos(root):
    """The repositories the daily audit actually runs against."""
    text = read(root, MATRIX_WORKFLOW)
    if text.count(MATRIX_START) != 1:
        raise ScopeParseError(
            f'{MATRIX_WORKFLOW} must contain the matrix key '
            f'"{MATRIX_START.strip()}" at exactly one indentation the '
            f'scope parse recognises')
    block = text.split(MATRIX_START, 1)[1]
    repos = []
    for line in block.splitlines():
        if line.startswith(MATRIX_BULLET):
            repos.append(line[len(MATRIX_BULLET):].strip())
        elif line.strip() and not line.lstrip().startswith('#'):
            break
    if not repos:
        raise ScopeParseError(
            f'no matrix entries read from {MATRIX_WORKFLOW}; the '
            f'list is indented differently to "{MATRIX_BULLET}"')
    for repo in repos:
        if not REPO_NAME.search(repo):
            raise ScopeParseError(
                f'"{repo}" was read as a repository name from the '
                f'audit matrix, so the parse has overrun the list')
    return repos


def documented_in_scope(root):
    """The repositories the documentation says are audited."""
    return bulleted_block(
        root, IN_SCOPE_DOC, IN_SCOPE_START, IN_SCOPE_END, IN_SCOPE_BULLET)


def documented_excluded(root):
    """The repositories the documentation says are excluded."""
    return bulleted_block(
        root, EXCLUDED_DOC, EXCLUDED_START, EXCLUDED_END, EXCLUDED_BULLET)
