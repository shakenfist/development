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
SHARED_BLOCK_BEGIN_RE = re.compile(
    r'<!--\s*shared-block:\s*([a-z0-9-]+)\s+v(\d+)\s*-->'
)


SHARED_BLOCK_END = '<!-- shared-block-end -->'


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

    Returns a list of (name, version, block_text) tuples, where
    block_text runs from the begin marker to the end marker
    inclusive. A block missing its end marker yields block_text of
    None.
    """
    blocks = []
    for match in SHARED_BLOCK_BEGIN_RE.finditer(content):
        end = content.find(SHARED_BLOCK_END, match.end())
        if end == -1:
            blocks.append((match.group(1), int(match.group(2)), None))
        else:
            blocks.append((
                match.group(1),
                int(match.group(2)),
                content[match.start():end + len(SHARED_BLOCK_END)],
            ))
    return blocks


def load_canonical_block(name, blocks_dir=None):
    """Load a canonical shared block from templates/shared-blocks/.

    Returns a (version, block_text) tuple, or None if no canonical
    file exists for the name.
    """
    path = os.path.join(blocks_dir or SHARED_BLOCKS_DIR, f'{name}.md')
    if not os.path.exists(path):
        return None
    with open(path, 'r', errors='replace') as f:
        content = f.read()
    for bname, version, text in extract_shared_blocks(content):
        if bname == name and text is not None:
            return (version, text)
    return None


def validate_shared_blocks(content, required=None, blocks_dir=None):
    """Validate every shared block embedded in content.

    required is an iterable of block names that must be present.
    Returns a list of problem strings; empty means compliant.
    """
    problems = []
    embedded = extract_shared_blocks(content)
    seen = set()
    for name, version, text in embedded:
        seen.add(name)
        if text is None:
            problems.append(
                f'shared block {name} has no '
                f'{SHARED_BLOCK_END} marker'
            )
            continue
        canonical = load_canonical_block(name, blocks_dir=blocks_dir)
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
                f'v{canonical_version} current)'
            )
        elif normalize_block(text) != normalize_block(canonical_text):
            problems.append(
                f'shared block {name} has drifted from the canonical '
                f'wording in templates/shared-blocks/{name}.md'
            )
    for name in (required or []):
        if name not in seen:
            problems.append(
                f'missing shared block {name} (copy it verbatim from '
                f'templates/shared-blocks/{name}.md in the '
                f'development repository)'
            )
    return problems
