"""Reading pre-commit's YAML without a YAML parser.

Two files share one shape: a repository's `.pre-commit-config.yaml`,
whose `repos:` list holds entries that each hold a `hooks:` list, and
a hook repository's `.pre-commit-hooks.yaml`, which is a bare list of
hooks. Both are lists of mappings, so one scanner reads both, and the
criteria that care about a hook ask it for the keys that hook set.

Line based rather than YAML-parsed, like the workflow helpers beside
it, which avoids a PyYAML dependency. Flow-style mappings, multi-line
quoted scalars and a list item written as a bare `-` line with its
first key on the next are not understood; none of the fleet's configs
use them.
"""

import re


_BLOCK_SCALAR = re.compile(r'([|>])([+-]?)(\d?)([+-]?)\s*(?:#.*)?$')
_SINGLE_QUOTED = re.compile(r"'((?:[^']|'')*)'")
_DOUBLE_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _block_scalar_text(match, lines):
    """The string a YAML block scalar (`|`, `>`, `|-`, `>+`...) holds.

    `lines` are the raw continuation lines. The content indent is taken
    as that of the least indented non-blank line, which is how every
    config in the fleet writes one; an explicit indentation digit is
    accepted but not needed for that.
    """
    body = list(lines)
    while body and not body[-1].strip():
        body.pop()
    trailing = len(lines) - len(body)
    if not body:
        return ''
    indent = min(len(line) - len(line.lstrip(' ')) for line in body if line.strip())
    body = [line[indent:] if line.strip() else '' for line in body]
    if match.group(1) == '|':
        text = '\n'.join(body)
    else:
        text = body[0]
        for previous, line in zip(body, body[1:]):
            if not line:
                text += '\n'
            elif not previous:
                text += line
            else:
                text += ' ' + line
    chomp = match.group(2) or match.group(4)
    if chomp == '-':
        return text
    return text + '\n' * (1 + trailing if chomp == '+' else 1)


def _scalar_value(value):
    """The string a plain or quoted YAML scalar on one line holds."""
    for quoted, unescape in ((_SINGLE_QUOTED, lambda s: s.replace("''", "'")),
                             (_DOUBLE_QUOTED, lambda s: re.sub(r'\\(["\\])', r'\1', s))):
        match = quoted.match(value)
        if match:
            return unescape(match.group(1))
    return re.sub(r'\s+#.*$', '', value).strip()


class PreCommitYaml:
    """What scan_pre_commit_yaml() found in one file.

    `top` maps each key at column 0 to the list of values it was given
    there. `sections` maps a column-0 key whose value is a nested
    mapping -- `default_language_version:` -- to that mapping's keys
    and values. `items` is every list item that set at least one key
    of its own, in the order the items close, each a dict with `keys`
    (key name to the list of values the item gave it) and `parent`
    (the item it is nested in, or None).
    """

    def __init__(self, top, sections, items):
        self.top = top
        self.sections = sections
        self.items = items

    def hooks(self):
        """Every item with an `id:`, which is what pre-commit calls a hook."""
        return [item for item in self.items if 'id' in item['keys']]


def item_value(item, key):
    """The last value an item gave `key`, or None if it gave none."""
    values = item['keys'].get(key)
    return values[-1] if values else None


def scan_pre_commit_yaml(text):
    """Read a pre-commit config or hook manifest into a PreCommitYaml.

    A key belongs to the list item whose mapping it is part of, found
    from indentation: the column a key starts in must be the column
    the item's first key started in. That is what keeps a hook's own
    `exclude:` apart from a top-level one, which pre-commit applies to
    every hook, and a hook's `language:` apart from anything written
    inside its `args:`. A block scalar contributes the text it holds,
    never its `|` or `>` indicator, and its lines are never read as
    keys.
    """
    lines = text.splitlines()
    top = {}
    sections = {}
    section = None
    items = []
    open_items = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.strip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        while open_items and indent <= open_items[-1]['dash']:
            item = open_items.pop()
            if item['keys']:
                items.append(item)

        body = raw.strip()
        column = indent
        dash = re.match(r'-\s+', body)
        if dash:
            column = indent + dash.end()
            open_items.append({'dash': indent, 'column': column, 'keys': {},
                               'parent': open_items[-1] if open_items else None})
            body = body[dash.end():]

        key = re.match(r'([\w-]+):(?:\s+(.*?))?\s*$', body)
        if not key:
            continue
        name, value = key.group(1), key.group(2) or ''

        block = _BLOCK_SCALAR.match(value)
        if block:
            content = []
            while i < len(lines) and (not lines[i].strip()
                                      or len(lines[i]) - len(lines[i].lstrip(' ')) > column):
                content.append(lines[i])
                i += 1
            value = _block_scalar_text(block, content)
        else:
            value = _scalar_value(value)

        if column == 0:
            top.setdefault(name, []).append(value)
            section = name if not value else None
        elif open_items:
            owner = open_items[-1]
            if owner['column'] == column:
                owner['keys'].setdefault(name, []).append(value)
        elif section is not None:
            sections.setdefault(section, {})[name] = value
    while open_items:
        item = open_items.pop()
        if item['keys']:
            items.append(item)
    return PreCommitYaml(top, sections, items)


def scan_pre_commit_config(text):
    """Find the `exclude:` patterns in a .pre-commit-config.yaml.

    Returns (top_level_excludes, hooks), where hooks is a list of
    (hook_id, excludes) with one entry per `- id:` in the file. An
    exclude belongs to the hook whose mapping it is a key of, since
    pre-commit applies a top-level exclude to every hook but a hook's
    own to that hook alone.
    """
    scanned = scan_pre_commit_yaml(text)
    return (scanned.top.get('exclude', []),
            [(item_value(hook, 'id'), hook['keys'].get('exclude', [])) for hook in scanned.hooks()])
