"""Markdown parsing shared between criteria.

Headings, links, fenced blocks and the generated regions that must not
be judged as if a person wrote them.
"""

import re

from audit.markers import BEGIN_MARKER, END_MARKER


# An ATX heading: one to six hashes, whitespace, then the title. The
# whitespace is required, so `###foo` is not a heading and a bare
# `###` on a line of its own is not one either.
MD_HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')


# A markdown table's separator row -- |---|, | :--- | ---: |, and the
# rest of its spellings. Only ever applied to a line that already
# starts with a pipe, because `---` on its own is a horizontal rule
# and ends a table rather than continuing one.
MD_TABLE_SEPARATOR_RE = re.compile(r'^[\s|:-]+$')


def iter_lines_outside_fences(lines):
    """Yield (offset, line) for each line, blanking fenced code.

    The one fence loop the markdown-structure readers in this package
    share. A `## foo` or a `| Phase |` row inside a fenced block is
    sample text rather than document structure, and every caller that
    reads structure has to skip it.

    A fence's opening marker, its body and its closing marker all come
    back as empty strings rather than being dropped. That keeps an
    offset usable as an index into the caller's own list of lines, and
    it makes a fence break a run of table rows instead of silently
    joining the tables either side of it -- an example table shown
    between two real ones would otherwise hand its column names to the
    second. It is the same choice blank_generated_blocks makes, for
    the same reason.

    Both fence characters are recognised, and a block is closed only
    by its own marker, so a ``` shown inside a ~~~ block does not end
    it. An unterminated fence blanks the rest of the document, which
    is what the heading scan has always done: a malformed file reads
    as having no structure after the stray marker rather than as
    having structure invented from its code.
    """
    fence = None
    for offset, line in enumerate(lines):
        stripped = line.lstrip()
        marker = None
        if stripped.startswith('```'):
            marker = '```'
        elif stripped.startswith('~~~'):
            marker = '~~~'

        if fence is not None:
            if marker == fence:
                fence = None
            yield offset, ''
            continue
        if marker is not None:
            fence = marker
            yield offset, ''
            continue

        yield offset, line


def markdown_heading(line):
    """Return (level, text) for an ATX heading line, or None.

    The text is what follows the hashes, untouched otherwise: callers
    differ on whether closing hashes and markdown decoration are
    noise, so trimming them is left to them.
    """
    match = MD_HEADING_RE.match(line.lstrip())
    if not match:
        return None
    return len(match.group(1)), match.group(2)


def markdown_table_cells(line):
    """The cells of a markdown table row, outer empties trimmed."""
    return [c.strip() for c in line.strip().strip('|').split('|')]


def iter_markdown_table_rows(lines, columns=None):
    """Yield (offset, line, is_header, header, cells) for every line.

    One record per line of the document rather than per table row, so
    that a caller can walk a file once: the rows of its tables, and
    the prose, headings and blank lines between them, arrive in
    document order from a single loop. A line that is not a table row
    comes back with `cells` None, which is also the signal that any
    run of rows has ended -- prose between two tables means the second
    is never read against the first one's header.

    `is_header` marks the row a separator underlines. Recognising a
    header by its position rather than by what it contains means a
    data row that happens to carry no link, or no date, cannot be
    mistaken for the start of a new table. The separator row itself is
    consumed rather than yielded.

    `header` is the column names of the table the row belongs to, or
    None outside one. `columns` maps a header row's cells to those
    names and defaults to stripping and lower-casing them; a caller
    whose cells carry markdown decoration passes its own.

    Fenced code is blanked by iter_lines_outside_fences, so a fenced
    line arrives as a non-row whose `line` is empty. Callers that
    scan the text of non-rows -- for links, for headings -- therefore
    see nothing there, which is the point.
    """
    normalise = columns or (lambda cells: [c.strip().lower() for c in cells])
    blanked = [line for _, line in iter_lines_outside_fences(lines)]

    header = None
    for offset, line in enumerate(blanked):
        stripped = line.strip()
        if not stripped.startswith('|'):
            header = None
            yield offset, line, False, None, None
            continue
        if MD_TABLE_SEPARATOR_RE.match(stripped):
            continue

        cells = markdown_table_cells(stripped)
        following = blanked[offset + 1].strip() if offset + 1 < len(blanked) else ''
        if (following.startswith('|')
                and MD_TABLE_SEPARATOR_RE.match(following)):
            header = normalise(cells)
            yield offset, line, True, header, cells
            continue

        yield offset, line, False, header, cells


def iter_markdown_headings(content, levels=(2, 3)):
    """Yield (level, text, line) for ATX headings outside code fences.

    A `## foo` inside a fenced block is sample text, not a heading, so
    fenced regions are skipped the same way strip_markdown_code skips
    them. The raw line comes back too, so callers can look for an
    audit-ok marker on it.
    """
    for _offset, line in iter_lines_outside_fences(content.splitlines()):
        heading = markdown_heading(line)
        if heading and heading[0] in levels:
            text = heading[1].strip().rstrip('#').strip()
            if text:
                yield heading[0], text, line


def normalise_heading(text):
    """Fold a heading (or a docs/ filename stem) to a comparison key.

    Case and hyphen-versus-space are presentation, not meaning:
    `## Code Organisation`, `## code organisation` and
    `docs/code-organisation.md` are all the same subject.
    """
    return re.sub(r'\s+', ' ', text.replace('-', ' ')).strip().lower()


# Inline markdown link or image: [text](target) or ![alt](target).
# The captured group is everything between the parentheses (which may
# include a "title" and/or <angle brackets> that we strip later).
MD_LINK_RE = re.compile(r'!?\[[^\]]*\]\(\s*([^)]+?)\s*\)')


# Reference-style link definition at the start of a line:
# [label]: target "optional title".
MD_REFDEF_RE = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(\S+)', re.MULTILINE)


# A URL scheme prefix (http:, https:, mailto:, data:, ...). A link
# target carrying one is absolute and renders anywhere.
URL_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:')


# An inline code span: a run of backticks, then anything that is not
# that same run, then the matching run. Applied per paragraph, so the
# content may cross lines but not a blank line.
INLINE_CODE_RE = re.compile(r'(`+)(?:(?!\1)[\s\S])*?\1')


def blank_generated_blocks(markdown):
    """Return markdown with consistency-audit blocks blanked out.

    The compliance tables between those markers are written by
    audit-update-docs.py, and the "Details for non-compliant projects"
    notes inside them are detail strings harvested from other
    repositories and rendered as bare prose. They are not this
    repository's documentation and must not be judged as it: a
    plan-index detail reading 'Complete (phases 1-5, 2026-08-15)'
    would fail plan-phase-references here the next morning, and a
    harvested markdown link would fail docs-external-links, in both
    cases through no commit anyone made in this repository. This
    became reachable when the audits tree moved under docs/ and its
    36 files entered the scope of both checks. Those tables are now on
    one page, docs/audits/compliance.md, which narrows what this has
    to blank but does not make it optional -- that page is still under
    docs/ and still full of other repositories' prose.

    A marker is recognised only as a whole line, and only in the exact
    spelling audit-update-docs.py emits -- which is why both scripts
    read it from audit_common. A substring test instead matched prose
    that merely names the markers, and documents that write the pair
    as one token (`<!-- consistency-audit:begin/end -->`) matched the
    begin without ever matching the end: that exempted 148 of 309
    lines of one plan file and 96 of 159 of another from
    docs-external-links, invisibly.

    Lines are replaced with empty ones rather than removed so that
    line numbers in reported offenders still point at the right line
    of the file.

    An unterminated block blanks nothing at all. Swallowing to the end
    of the file is the one outcome worth avoiding: an exemption that
    hides content is invisible, while scanning generated content that
    should have been skipped produces a visible false positive
    somebody can act on. With whole-line matching an unterminated
    block means a malformed file rather than a false trigger, so
    failing towards more scanning is both safe and loud.

    Markers inside a fenced code block are ignored, because a document
    may show what a generated block looks like -- README.md in the
    audits tree does exactly that. The fence tracking has to happen
    here rather than in the callers, which both handle fences only
    after this function has run: a closing fence sitting between a
    real marker pair would be blanked away, leaving the fence open and
    silently exempting the rest of the file. That is the same invisible
    exemption whole-line matching was introduced to remove, so it is
    closed at the same place rather than left to the order in which
    two callers happen to compose their passes.
    """
    lines = markdown.splitlines()
    blanked = list(lines)
    fence = None
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        opener = stripped[:3] if stripped[:3] in ('```', '~~~') else None
        if fence is not None:
            if opener == fence:
                fence = None
            continue
        if opener is not None:
            fence = opener
            continue
        if start is None:
            if stripped == BEGIN_MARKER:
                start = index
            continue
        if stripped == END_MARKER:
            for position in range(start, index + 1):
                blanked[position] = ''
            start = None
    out = '\n'.join(blanked)
    # Keep the trailing newline, so that the blanked text has exactly
    # as many lines as the original however the file ended.
    if markdown.endswith('\n'):
        out += '\n'
    return out


def strip_markdown_code(markdown):
    """Return markdown with fenced blocks and inline code spans removed.

    A documented command or example may legitimately contain something
    that looks like a relative link (e.g. `[x](y)` shown as sample
    text). Markdown does not render links inside code, so we must not
    audit them either.
    """
    out = []
    fence = None
    for line in markdown.splitlines():
        stripped = line.lstrip()
        marker = None
        if stripped.startswith('```'):
            marker = '```'
        elif stripped.startswith('~~~'):
            marker = '~~~'

        if fence is None:
            if marker is not None:
                fence = marker
                continue
            out.append(line)
        elif marker == fence:
            fence = None

    # Inline code spans. A span may wrap across lines -- prose wrapped
    # at 65 columns does it constantly -- but it cannot contain a
    # blank line, so strip one paragraph at a time. That bound matters:
    # without it an unpaired backtick would swallow the rest of the
    # document and hide every link after it.
    return '\n\n'.join(
        INLINE_CODE_RE.sub('', paragraph)
        for paragraph in re.split(r'\n[ \t]*\n', '\n'.join(out))
    )


def link_target(raw):
    """Return the bare target from the inside of a markdown link.

    Unwraps the <angle bracket> form and drops an optional "title"
    following the URL, so callers see just the destination.
    """
    target = raw.strip()
    if target.startswith('<'):
        return target[1:].split('>', 1)[0].strip()
    # Drop an optional "title" following the URL.
    parts = target.split()
    return parts[0] if parts else ''


def link_target_is_relative(raw):
    """Decide whether a markdown link target is relative.

    Absolute (returns False): a scheme-qualified URL, a
    protocol-relative //host URL, or a pure in-page #anchor (which
    resolves against the rendered page wherever it is shown). Anything
    else -- docs/x.md, ./x.md, ../x.md, /x, x.md -- is relative and
    breaks when the README is rendered off the repo landing page
    (PyPI, crates.io, mirrors).
    """
    target = link_target(raw)

    if not target:
        return False
    if target.startswith('#'):
        return False
    if target.startswith('//'):
        return False
    if URL_SCHEME_RE.match(target):
        return False
    return True


def iter_fenced_blocks(content):
    """Yield (language, start line, body lines) for each code fence.

    Both fence characters are recognised, and a block is closed only
    by its own marker, so a ``` inside a ~~~ block does not end it.
    An unterminated fence yields nothing, which is the safe direction:
    a malformed document is not evidence of a diagram.
    """
    fence = None
    lang = ''
    start = 0
    body = []
    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith('```'):
            marker = '```'
        elif stripped.startswith('~~~'):
            marker = '~~~'
        else:
            marker = None

        if fence is not None:
            if marker == fence:
                yield lang, start, body
                fence = None
                body = []
            else:
                body.append(line)
        elif marker is not None:
            fence = marker
            lang = stripped[3:].strip()
            start = lineno
            body = []
