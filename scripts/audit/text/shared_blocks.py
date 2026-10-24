"""Shared blocks: the passages this repository ships to the fleet.

A block is a verbatim copy of a canonical file under
templates/shared-blocks/, delimited by markers naming it and its
version. Three criteria check that the copies in a repository still
match, which is why extraction and validation live here rather than
with any one of them.
"""

import os
import re


# --- Shared blocks ---
# Canonical wording embedded verbatim across repositories, delimited
# by versioned markers. Canonical copies live in
# templates/shared-blocks/<name>.md in the development repository.
# See templates/shared-blocks/README.md for the mechanism.
#
# The version is bounded so that int() is never handed more digits
# than it will convert: past 4,300 it raises ValueError, which would
# turn a malformed marker in someone else's file into an audit error.
# A marker over the bound is not a marker, so a required block carried
# under one is reported missing.
SHARED_BLOCK_BEGIN_RE = re.compile(
    r'<!--\s*shared-block:\s*([a-z0-9-]+)\s+v(\d{1,6})\s*-->'
)


SHARED_BLOCK_END = '<!-- shared-block-end -->'


# Either marker, so one pass sees both in document order. Group 1 is
# the block name for a begin marker and None for an end marker.
SHARED_BLOCK_MARKER_RE = re.compile(
    f'{SHARED_BLOCK_BEGIN_RE.pattern}|{re.escape(SHARED_BLOCK_END)}'
)


SHARED_BLOCKS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', 'templates', 'shared-blocks',
)


def normalize_block(text):
    """Normalize a block for comparison.

    Verbatim means verbatim, but trailing whitespace and line-ending
    differences are invisible in an editor and should not fail the
    audit.
    """
    return '\n'.join(
        line.rstrip() for line in text.strip().splitlines()
    )


def extract_shared_blocks(content):
    """Extract shared blocks from file content.

    Returns a list of (name, version, block_text) tuples in document
    order, where block_text runs from the begin marker to the end
    marker inclusive.

    Each begin marker pairs with the first end marker after it, unless
    another begin marker comes first. A block still open when the next
    one begins has no end marker of its own, whether it was never
    closed or has another block nested inside it, and yields
    block_text of None rather than borrowing the next block's end
    marker. Every block_text is bounded by its own pair of markers, so
    together they are never longer than the content, and the content
    is read once.
    """
    blocks = []
    opened = None
    for match in SHARED_BLOCK_MARKER_RE.finditer(content):
        if match.group(1) is not None:
            if opened is not None:
                blocks.append((opened[0], opened[1], None))
            opened = (match.group(1), int(match.group(2)), match.start())
        elif opened is not None:
            name, version, start = opened
            blocks.append((name, version, content[start:match.end()]))
            opened = None
        # An end marker with no block open is ignored. It carries no
        # name to report it by, it encloses nothing that could drift,
        # and the faults that leave one behind -- a nested block's
        # outer end marker, or a begin marker too malformed to match
        # -- are already reported against the block they belong to.
    if opened is not None:
        blocks.append((opened[0], opened[1], None))
    return blocks


def load_canonical_block(name, blocks_dir=None):
    """Load a canonical shared block from templates/shared-blocks/.

    Returns a (version, block_text) tuple, or None if no canonical
    file exists for the name.
    """
    # The name may come from a file in the repository under audit, but
    # only by way of SHARED_BLOCK_BEGIN_RE, whose [a-z0-9-]+ admits no
    # '/' or '.', so the joined path cannot leave blocks_dir.
    path = os.path.join(blocks_dir or SHARED_BLOCKS_DIR, f'{name}.md')
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    for bname, version, text in extract_shared_blocks(content):
        if bname == name and text is not None:
            return (version, text)
    return None


def copy_hint(name):
    """Where a maintainer in another repository finds the fix."""
    return (
        f'copy it verbatim from templates/shared-blocks/{name}.md in '
        f'the development repository'
    )


def validate_shared_blocks(content, required=None, blocks_dir=None):
    """Validate every shared block embedded in content.

    required is an iterable of block names that must be present.
    Returns a list of problem strings, each naming the one block it
    is about; empty means compliant.
    """
    problems = []
    embedded = extract_shared_blocks(content)
    counts = {}
    canonicals = {}
    for name, version, text in embedded:
        counts[name] = counts.get(name, 0) + 1
        if text is None:
            problems.append(
                f'shared block {name} has no {SHARED_BLOCK_END} '
                f'marker of its own (close it before the next block '
                f'begins)'
            )
            continue
        # Loaded once per name, so a block repeated many times does
        # not reopen its canonical file each time.
        if name not in canonicals:
            canonicals[name] = load_canonical_block(
                name, blocks_dir=blocks_dir)
        canonical = canonicals[name]
        if canonical is None:
            problems.append(
                f'unknown shared block {name} (no canonical copy in '
                f'templates/shared-blocks/)'
            )
            continue
        canonical_version, canonical_text = canonical
        if version != canonical_version:
            problems.append(
                f'shared block {name} is stale (v{version} embedded, '
                f'v{canonical_version} current; {copy_hint(name)})'
            )
        elif normalize_block(text) != normalize_block(canonical_text):
            problems.append(
                f'shared block {name} has drifted from the canonical '
                f'wording ({copy_hint(name)})'
            )
    for name, count in counts.items():
        if count > 1:
            problems.append(
                f'shared block {name} is embedded {count} times (keep '
                f'one copy)'
            )
    for name in (required or []):
        if name not in counts:
            problems.append(
                f'missing shared block {name} ({copy_hint(name)})'
            )
    # Copies of one block share their problems, and each is worth
    # saying once.
    return list(dict.fromkeys(problems))
