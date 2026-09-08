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

A repository scoped to a subset of the checks is stated a fourth time, as a
sentence rather than a list: which criteria it is audited for, in the
partial-scope paragraph of `docs/audits/README.md`. That one is read against
`only_checks` in `REPO_OVERRIDES` rather than against the other lists, and it
is the statement with the worst track record -- `only_checks` was widened once
with the sentence, and two other documents, left behind and no test noticing.

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

PARTIAL_SCOPE_DOC = 'docs/audits/README.md'
PARTIAL_SCOPE_START = ' is audited for the '
PARTIAL_SCOPE_END = ' checks, and nothing else.'

#: The partially scoped repositories and what the page says each is
#: measured against, read out of the one sentence per repository that
#: says so. Unlike the two lists above this is a sentence rather than a
#: bullet list, because it has to carry the reasoning as well, so the
#: parse is delimited to the span between the two phrases and every
#: check id it collects is backticked. Everything after the end phrase
#: -- including the criteria this repository is *not* audited for, and
#: why -- is prose the parse deliberately never sees, so that adding a
#: reason cannot change what the sentence is read as claiming.
PARTIAL_SCOPE = re.compile(
    r'([a-z0-9][a-z0-9.-]*)' + re.escape(PARTIAL_SCOPE_START) +
    r'(.*?)' + re.escape(PARTIAL_SCOPE_END), re.DOTALL)

#: What a check id looks like, used the way REPO_NAME is used above: to
#: notice a parse that has started collecting prose rather than to
#: validate the id. Anchored at both ends, so a backticked filename
#: like `pyproject.toml` -- which the same paragraph carries a few
#: sentences later -- is rejected rather than read as a criterion.
CHECK_ID = re.compile(r'^[a-z0-9][a-z0-9-]*$')

#: A backticked token inside the delimited span.
BACKTICKED = re.compile(r'`([^`]*)`')

#: A run of blank lines, collapsed to exactly one so that a paragraph
#: break is always the two-newline sequence WRAPPED_LINE is written to
#: leave alone. Applied first, because a blank line carrying trailing
#: whitespace is otherwise indistinguishable from a wrapped one.
PARAGRAPH_BREAK = re.compile(r'\n(?:[ \t]*\n)+')

#: A line break inside a paragraph, as opposed to one between
#: paragraphs. `docs/` is hand-wrapped at about seventy columns, so
#: both the start and the end phrase can be split across two lines by a
#: reflow that changes not one word -- and a split phrase does not
#: match, which would turn "somebody re-wrapped a paragraph" into a
#: parse error naming a phrase that is still there. Joining wrapped
#: lines first makes the parse care about the words rather than the
#: column they landed in.
#:
#: Neither newline of a paragraph break matches: the first is followed
#: by one and the second preceded by one. That is the whole reason for
#: the two lookarounds, and it is load-bearing -- a paragraph break
#: half-joined reads as an ordinary line break, and the guard that
#: catches a span running past the end of its sentence has nothing
#: left to notice.
WRAPPED_LINE = re.compile(r'(?<!\n)\n(?!\n)[ \t]*')

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
    """Read one of the documents a scope list is written in.

    Decoding errors are replaced rather than raised, the same way
    `Repo.read()` does it and for the same reason one level up: a
    `UnicodeDecodeError` is a `ValueError` rather than an `OSError`, so
    it escapes the handler the `scope-coverage` check wraps this parse
    in, and `registry.run_all()` has no handler at all. One undecodable
    byte in either document would abort the whole `development` leg of
    the daily run, taking issue filing and the compliance page with it.
    Replacing the byte lets the parse proceed and either succeed or
    raise `ScopeParseError`, which is a `fail()` a reader can act on.
    """
    with open(os.path.join(root, relative), 'r', errors='replace') as f:
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


def documented_partial_scope(root):
    """What the documentation says each partially scoped repo is audited for.

    Returns `{repository: [check id, ...]}`, read from the one sentence
    per repository in the partial-scope paragraph of
    `docs/audits/README.md`. `only_checks` in `REPO_OVERRIDES` is what
    actually runs; this is what a reader is told, and until this parse
    existed nothing held the two together -- `only_checks` could be
    widened and the sentence left behind without a single test
    noticing, which is exactly what happened the first time it was.

    Raises `ScopeParseError` rather than returning a partial mapping,
    for the same reason the two list parses above do. The dangerous
    drift here is the end phrase rather than the start: a reworded
    start phrase simply yields no matches and raises, while a reworded
    end phrase lets the non-greedy span run on to the *next*
    repository's sentence, or to the end of the file, silently
    collecting whatever backticked tokens it passes. So the span is
    rejected if it crosses a blank line or a heading -- one sentence
    never does -- before anything trusts what it collected.

    Wrapped lines are joined before any of that, so that re-wrapping
    the paragraph is not a parse error. Both phrases are long enough
    to straddle the seventy-odd columns these documents are hand
    wrapped at, and "the phrase is missing" is the wrong thing to tell
    somebody whose only edit was a reflow.
    """
    text = PARAGRAPH_BREAK.sub('\n\n', read(root, PARTIAL_SCOPE_DOC))
    text = WRAPPED_LINE.sub(' ', text)
    matches = PARTIAL_SCOPE.findall(text)
    if not matches:
        raise ScopeParseError(
            f'{PARTIAL_SCOPE_DOC} must say "<repository>'
            f'{PARTIAL_SCOPE_START}...{PARTIAL_SCOPE_END}" at least '
            f'once: it is the sentence the scope parse reads to learn '
            f'which checks a partially scoped repository is audited '
            f'for')
    documented = {}
    for repo, span in matches:
        if repo in documented:
            raise ScopeParseError(
                f'{PARTIAL_SCOPE_DOC} states what "{repo}" is audited '
                f'for more than once, so a reader can be told two '
                f'different things depending on which they read')
        if '\n\n' in span or re.search(r'^#{1,6} ', span, re.MULTILINE):
            raise ScopeParseError(
                f'the text read for "{repo}" from {PARTIAL_SCOPE_DOC} '
                f'runs past the end of its sentence, so "'
                f'{PARTIAL_SCOPE_END.strip()}" is no longer where the '
                f'parse stops')
        ids = BACKTICKED.findall(span)
        if not ids:
            raise ScopeParseError(
                f'no backticked check ids read for "{repo}" from '
                f'{PARTIAL_SCOPE_DOC}; the sentence names the checks '
                f'some other way now, and an empty list compares '
                f'equal to nothing rather than failing loudly')
        for check_id in ids:
            if not CHECK_ID.search(check_id):
                raise ScopeParseError(
                    f'"{check_id}" was read as a check id from the '
                    f'sentence about "{repo}" in {PARTIAL_SCOPE_DOC}, '
                    f'so the parse is picking up something that is '
                    f'not one')
        documented[repo] = ids
    return documented
