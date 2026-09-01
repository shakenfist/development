"""Markdown parsing shared between criteria.

Headings, links, fenced blocks and the generated regions that must not
be judged as if a person wrote them.
"""

import re

from audit.markers import BEGIN_MARKER, END_MARKER


def iter_markdown_headings(content, levels=(2, 3)):
    """Yield (level, text, line) for ATX headings outside code fences.

    A `## foo` inside a fenced block is sample text, not a heading, so
    fenced regions are skipped the same way strip_markdown_code skips
    them. The raw line comes back too, so callers can look for an
    audit-ok marker on it.
    """
    fence = None
    for line in content.splitlines():
        stripped = line.lstrip()
        marker = None
        if stripped.startswith('```'):
            marker = '```'
        elif stripped.startswith('~~~'):
            marker = '~~~'

        if fence is not None:
            if marker == fence:
                fence = None
            continue
        if marker is not None:
            fence = marker
            continue

        match = re.match(r'(#{1,6})\s+(.*)', stripped)
        if match and len(match.group(1)) in levels:
            text = match.group(2).strip().rstrip('#').strip()
            if text:
                yield len(match.group(1)), text, line


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
