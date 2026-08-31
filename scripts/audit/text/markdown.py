"""Markdown parsing shared between criteria.

Headings, links, fenced blocks and the generated regions that must not
be judged as if a person wrote them.
"""

import re


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
