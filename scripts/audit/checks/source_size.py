"""The criteria about source size.

How large a unit of source may grow before somebody has to say why, at
two scales: a file-level ratchet, which pins every file over the
threshold to its current length so that growing one is a baseline edit
a reviewer cannot scroll past, and a function-level lint for the
languages whose toolchain can measure one.

Not in `packaging.py` beside `rust-unwrap-lint`, nor in
`ci_workflows.py` beside the other pre-commit criteria, although each
has a claim on one of these. Both modules, and both of their test
suites, sit exactly at their `tools/source-size-baseline` entries, so
adding a criterion to either would mean raising an entry by hand --
which is the question the ratchet exists to ask, and not one a
criterion implementing the ratchet gets to answer with "to land the
criterion".

So a reader looking for the Rust lint beside `rust-unwrap-lint`, or
for the pre-commit criterion beside `pre-commit-config`, finds it
here: this module is the family those two have in common, and the
modules they are not in are the ones that have run out of room.
"""

import ast
import functools
import os
import re

from audit.check import Check, ToolFailure
from audit.files import (
    PRE_COMMIT_RUN_ALL_RE, check_file_contains, file_matches,
    require_tracked_paths, toml_section_has_key,
)
from audit.repo import DEVELOPMENT_ROOT, READ_MAX_BYTES, read_within


#: The template an adopter copies, and the path it copies it to. The
#: copy is compared with the template byte for byte, so both spellings
#: are the criterion's rather than a convenience.
RATCHET_TEMPLATE = 'templates/source-size-ratchet/source-size-ratchet.py'
RATCHET_COPY = 'tools/source-size-ratchet.py'

PRE_COMMIT_CONFIG = '.pre-commit-config.yaml'

#: A workflow step invoking the script, as opposed to naming it. The
#: path is also what an `on.<event>.paths` filter, a paths-filter list
#: and a `git diff --name-only` grep say, and none of those runs it.
#: What runs it is an interpreter in front of it -- `python` or
#: `python3`, with `-I` if the template's own spelling is kept -- or a
#: `./` that executes the file directly.
RATCHET_INVOCATION_RE = re.compile(
    r'(?:\bpython3?\s+(?:-I\s+)?(?:\./)?|\./)'
    + re.escape(RATCHET_COPY) + r'(?![\w.-])')

#: A pre-commit hook's `entry:` key, with whatever follows it on the
#: line. The value is a command and the only place a hook says what it
#: runs: `files:`, `exclude:` and `args:` are regexes and arguments,
#: which can name any path without running it.
ENTRY_KEY_RE = re.compile(r'^(\s*)(?:-\s+)?entry:(.*)$')


def entry_invokes(repo, path, needle):
    """Does a pre-commit config have a hook whose `entry:` names needle?

    Comments do not count, and nor does the needle anywhere but in an
    `entry:` value. The value may continue onto the lines below it,
    which is how a long command or a block scalar is written, so the
    lines indented further than the key are read as its value too.
    """
    content = repo.read(path)
    if content is None:
        return False
    key_indent = None
    for line in content.split('\n'):
        if line.lstrip().startswith('#'):
            continue
        if key_indent is not None:
            indent = len(line) - len(line.lstrip())
            if line.strip() and indent > key_indent:
                if needle in line:
                    return True
                continue
            key_indent = None
        match = ENTRY_KEY_RE.match(line)
        if not match:
            continue
        if needle in match.group(2):
            return True
        key_indent = len(match.group(1))
    return False


@functools.lru_cache(maxsize=None)
def source_extensions():
    """SOURCE_EXTENSIONS as the shipped ratchet spells it.

    The list of what the tool counts belongs to the tool, and a copy
    here would be a second list to keep in step -- a repository whose
    only source is in a language the template learned about later
    would be reported as having none.

    Read out of the template's text rather than imported, because the
    template's filename is hyphenated and so is not a module name. The
    assignment is found with `ast` and its value read as a literal:
    nothing in the file is executed. Importing it by path, exec'ing it
    or shelling out to it would each run the script this criterion
    exists to compare rather than read, inside the audit, for one
    tuple.

    A template the audit cannot read, or whose assignment is not where
    this expects it, is the audit's own checkout being wrong rather
    than a finding about anybody, so it raises.

    Cached, since applies() asks on every run and the template cannot
    change under a running audit. A raise is not cached: the next call
    reads the template again.
    """
    text = read_within(
        DEVELOPMENT_ROOT, os.path.join(DEVELOPMENT_ROOT, RATCHET_TEMPLATE))
    if text is None:
        raise ToolFailure(
            f'Could not read {RATCHET_TEMPLATE} in the audit\'s own checkout')
    try:
        tree = ast.parse(text, filename=RATCHET_TEMPLATE)
    except SyntaxError as e:
        raise ToolFailure(
            f'Could not parse {RATCHET_TEMPLATE} in the audit\'s own '
            f'checkout: {e}') from e
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name)
                   and target.id == 'SOURCE_EXTENSIONS'
                   for target in node.targets):
            continue
        try:
            return tuple(ast.literal_eval(node.value))
        except (ValueError, TypeError) as e:
            raise ToolFailure(
                f'SOURCE_EXTENSIONS in {RATCHET_TEMPLATE} is not a literal '
                f'this check can read: {e}') from e
    raise ToolFailure(
        f'{RATCHET_TEMPLATE} in the audit\'s own checkout declares no '
        f'SOURCE_EXTENSIONS')


class SourceSizeRatchet(Check):
    id = 'source-size-ratchet'
    spec = 'docs/audits/source-size-ratchet.md'
    template = 'templates/source-size-ratchet/'
    issue_title = 'Source size ratchet'

    def applies(self, repo):
        """Skip a repository with no tracked source to measure.

        The index rather than a tree walk, for the reason the tool
        itself uses it: what is on disk includes build output, crate
        caches and virtualenvs, and the index is the one list of what
        the repository actually carries that needs no exclusions
        maintained.

        The generated shapes the tool never counts -- protoc output,
        minified bundles -- and the repository's own exclude file are
        deliberately not applied here. A repository whose every source
        file is generated would still be right to adopt the ratchet,
        and the alternative is this criterion keeping a second copy of
        the tool's exclusion logic in order to declare itself
        inapplicable.
        """
        extensions = source_extensions()
        tracked = require_tracked_paths(
            repo.path, *(f'*{extension}' for extension in extensions))
        if not tracked:
            return (f'No tracked source files for the ratchet to measure '
                    f'(nothing matching {", ".join(extensions)})')
        return None

    def run(self, repo):
        """Check the ratchet is adopted, and that CI reaches it.

        Three things, each reported separately because each has its own
        fix: the script is this repository's template byte for byte,
        `.pre-commit-config.yaml` invokes it, and at least one workflow
        reaches it.

        The bytes rather than the text, as the review tracking
        templates are compared: the script's threshold is a constant
        rather than a flag precisely so that one review of the template
        is a review of every copy, and a copy differing by a byte is a
        different blob that has to be reviewed again wherever it is
        carried.

        The hook is matched by its entry rather than its id, so that a
        repository which renamed the id still counts and a comment
        naming the tool does not -- nor a `files:` pattern, which can
        name the script without running it.

        CI reach is the half a config read cannot answer. A hook in
        `.pre-commit-config.yaml` gates a commit by somebody who has
        hooks installed; it gates a merge only if something in CI runs
        it. Two shapes count, which are the two the fleet has: a
        workflow running every hook over the tree, as `pre-commit run
        --all-files` does, or a workflow invoking the script itself,
        which is how a repository that runs each hook as its own job
        reaches it. Invoking it is an interpreter or `./` in front of
        the path: a `paths:` filter naming the script triggers a
        workflow on it and runs nothing. An invocation naming a single
        other hook is neither: it runs that hook and no others, so
        counting it would pass a repository whose ratchet job had been
        deleted on the strength of a job that lints something else.

        The two halves are judged independently, so each finding says
        only what is true of its half. A repository with the lane and
        no hook is told about the hook alone.

        What this does not do is re-run the ratchet. Whether the
        baseline is honoured is the hook's job on every pull request,
        and an audit re-judging it would only disagree with CI. Neither
        `tools/source-size-baseline` nor `tools/source-size-exclude` is
        required to exist: a missing baseline is an empty one, so a
        repository with nothing over the threshold is compliant without
        the file, and an exclude file is for a repository that has a
        generated tree to exclude.
        """
        problems = []

        canonical = read_within(
            DEVELOPMENT_ROOT,
            os.path.join(DEVELOPMENT_ROOT, RATCHET_TEMPLATE), binary=True)
        if canonical is None:
            raise ToolFailure(
                f'Could not read {RATCHET_TEMPLATE} in the audit\'s own '
                f'checkout')
        copy = repo.read_blob(RATCHET_COPY)
        # lexists rather than repo.exists(), which hides a link out of
        # the checkout: here the name being there is the point.
        if copy is None and os.path.lexists(repo.join(RATCHET_COPY)):
            problems.append(
                f'{RATCHET_COPY} is not a regular file the audit will read '
                f'(a dangling link, a link out of the checkout, a directory, '
                f'or over {READ_MAX_BYTES // (1024 * 1024)} MiB)')
        elif copy is None:
            problems.append(f'{RATCHET_COPY} is missing')
        elif copy != canonical:
            problems.append(f'{RATCHET_COPY} differs from {RATCHET_TEMPLATE}')

        if not entry_invokes(repo, PRE_COMMIT_CONFIG, RATCHET_COPY):
            problems.append(
                f'{PRE_COMMIT_CONFIG} does not invoke {RATCHET_COPY}')

        workflows = [
            f'.github/workflows/{workflow}' for workflow in repo.workflows()
        ]
        # The lane is judged on its own, whether or not the hook is
        # declared: a workflow running every hook is a lane that
        # exists, and the missing hook is its own finding. Reporting
        # the lane as absent as well would say something false about
        # the workflows of a repository that runs `pre-commit run
        # --all-files`. PRE_COMMIT_RUN_ALL_RE rather than the looser
        # PRE_COMMIT_RUN_RE for the reason it carries: a job running
        # one named hook does not run this one.
        runs_every_hook = any(
            file_matches(repo, workflow, PRE_COMMIT_RUN_ALL_RE)
            for workflow in workflows
        )
        runs_directly = any(
            file_matches(repo, workflow, RATCHET_INVOCATION_RE)
            for workflow in workflows
        )
        if not runs_every_hook and not runs_directly:
            problems.append(
                f'no workflow runs every pre-commit hook (as `pre-commit run '
                f'--all-files` does) or invokes {RATCHET_COPY}, so nothing '
                f'gates a merge or a branch pushed with --no-verify')

        if problems:
            return self.fail(
                f'{"; ".join(problems)}. Adopt the ratchet from '
                f'templates/source-size-ratchet/ in shakenfist/development: '
                f'copy the script byte for byte, merge its hook stanza into '
                f'{PRE_COMMIT_CONFIG}, and give CI a lane that reaches it')

        return self.ok(
            f'{RATCHET_COPY} matches {RATCHET_TEMPLATE}, runs from '
            f'{PRE_COMMIT_CONFIG}, and is reached by CI')


#: Where a Cargo.toml turns on the lint. Accepts too_many_lines = "warn",
#: "deny", or the table form { level = "warn", priority = -1 }. The level
#: has to be the value itself, or the table's level key inside its braces:
#: a pattern taking the first quoted warn or deny anywhere on the line
#: reads `too_many_lines = "allow"  # was "deny"` as enabled.
TOO_MANY_LINES_LINT = (
    r'too_many_lines\s*=\s*'
    r'(?:"(?:warn|deny)"|\{[^}#]*\blevel\s*=\s*"(?:warn|deny)")')

#: A manifest that declares a workspace of its own: it is a workspace
#: root, and cargo excludes a nested one from the workspace above it.
#: The `[workspace]` header itself, and not `[workspace.dependencies]`
#: or `[workspace.lints]` alone: those tables only mean something in a
#: manifest that has the header, so one in a member is a copy-paste
#: from the root, and exempting the member on its strength would
#: exempt it from the lint.
OWN_WORKSPACE_RE = re.compile(r'(?m)^[ \t]*\[workspace\]')

#: A character literal, as opposed to a lifetime, which also starts with
#: a quote. Needed so that `'"'` is not read as opening a string.
CHAR_LITERAL_RE = re.compile(r"'(?:\\[^']+|[^\\'])'")

#: An opening raw string, `r"`, `r#"`, `br##"` and so on.
RAW_STRING_RE = re.compile(r'b?r(#*)"')


def is_raw_string_start(text, pos):
    """Does a raw string literal open at pos, rather than an identifier?"""
    if not RAW_STRING_RE.match(text, pos):
        return False
    return pos == 0 or not (text[pos - 1].isalnum() or text[pos - 1] == '_')


def skip_raw_string(text, pos):
    """The offset after the raw string opening at pos, and its body."""
    opening = RAW_STRING_RE.match(text, pos)
    closer = '"' + opening.group(1)
    close = text.find(closer, opening.end())
    if close == -1:
        return len(text), text[opening.end():]
    return close + len(closer), text[opening.end():close]


def skip_block_comment(text, pos):
    """The offset after the (nestable) block comment starting at pos."""
    depth = 0
    while pos < len(text):
        if text.startswith('/*', pos):
            depth += 1
            pos += 2
        elif text.startswith('*/', pos):
            depth -= 1
            pos += 2
            if depth == 0:
                return pos
        else:
            pos += 1
    return pos


def skip_string(text, pos):
    """The offset after the string literal whose opening quote is at pos."""
    pos += 1
    while pos < len(text):
        if text[pos] == '\\':
            pos += 2
        elif text[pos] == '"':
            return pos + 1
        else:
            pos += 1
    return len(text)


def mask_string(body):
    """A string's content with every visible character as `x`."""
    return '"' + ''.join(c if c.isspace() else 'x' for c in body) + '"'


def mask_attribute(text, pos):
    """Masked text of the attribute whose `[` is at pos, and where it ends.

    Comments inside it are blanked and the content of each string is
    masked, so that a match on the result cannot be fooled by a reason
    that happens to mention a lint, and can still tell an empty reason
    from a stated one. An attribute that never closes -- the file ends
    first -- ends at the end of the file rather than raising: a file
    that does not parse is rustc's to complain about.
    """
    out = []
    depth = 0
    while pos < len(text):
        char = text[pos]
        if text.startswith('//', pos):
            newline = text.find('\n', pos)
            stop = len(text) if newline == -1 else newline
            out.append(' ' * (stop - pos))
            pos = stop
            continue
        if text.startswith('/*', pos):
            stop = skip_block_comment(text, pos)
            out.append(''.join(
                c if c == '\n' else ' ' for c in text[pos:stop]))
            pos = stop
            continue
        if char == '"':
            stop = skip_string(text, pos)
            out.append(mask_string(text[pos + 1:max(pos + 1, stop - 1)]))
            pos = stop
            continue
        if is_raw_string_start(text, pos):
            pos, body = skip_raw_string(text, pos)
            out.append(mask_string(body))
            continue
        literal = CHAR_LITERAL_RE.match(text, pos) if char == "'" else None
        if literal:
            out.append("'x'")
            pos = literal.end()
            continue
        out.append(char)
        pos += 1
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                break
    return ''.join(out), pos


def rust_attributes(text):
    """Every attribute in Rust source, as (offset, masked text).

    The offset is where the `#` is.

    Found by scanning rather than by matching lines, because attributes
    span them: rustfmt wraps a long `#[expect(..., reason = "...")]`
    onto several lines, and a line-oriented search finds none of the
    thirteen in the repository the criterion was piloted on. The scan
    understands comments, strings (raw ones included) and char
    literals, which is what it takes for a bracket inside one not to end
    an attribute. It does not understand macros, so an attribute inside
    a `macro_rules!` body is found like any other.
    """
    found = []
    pos = 0
    end = len(text)
    while pos < end:
        char = text[pos]
        if text.startswith('//', pos):
            newline = text.find('\n', pos)
            pos = end if newline == -1 else newline
        elif text.startswith('/*', pos):
            pos = skip_block_comment(text, pos)
        elif char == '"':
            pos = skip_string(text, pos)
        elif char == "'":
            literal = CHAR_LITERAL_RE.match(text, pos)
            pos = literal.end() if literal else pos + 1
        elif is_raw_string_start(text, pos):
            pos, _ = skip_raw_string(text, pos)
        elif char == '#':
            start = pos
            pos += 1
            if text.startswith('!', pos):
                pos += 1
            if text.startswith('[', pos):
                masked, pos = mask_attribute(text, pos)
                found.append((start, masked))
        else:
            pos += 1
    return found


def allows_too_many_lines(masked):
    """Does a masked attribute have `too_many_lines` inside an `allow(...)`?

    The lint named anywhere in the attribute is not enough:
    `cfg_attr(test, expect(clippy::too_many_lines), allow(dead_code))`
    names it, and what it does to it is `expect`, which the compiler
    checks. So each `allow(` group is found and read to its closing
    parenthesis. The attribute is masked, so a parenthesis in a string
    or a comment is not there to be counted.
    """
    for opening in re.finditer(r'\ballow\s*\(', masked):
        depth = 1
        pos = opening.end()
        while pos < len(masked) and depth:
            if masked[pos] == '(':
                depth += 1
            elif masked[pos] == ')':
                depth -= 1
            pos += 1
        if re.search(r'\bclippy::too_many_lines\b',
                     masked[opening.end():pos]):
            return True
    return False


def unexplained_length_allows(text):
    """Line numbers of each `allow(clippy::too_many_lines)` with no reason.

    A reason is `reason = "..."` with something in the string, inside
    the attribute, or a plain comment on the line immediately above the
    attribute's first line. A doc comment does not count: it describes
    the function to its callers, and nearly every function long enough
    to want an allow has one, so accepting it would accept them all.

    `expect` is not looked for. An `#[expect]` is itself checked by the
    compiler -- it warns once the function drops under the threshold and
    the attribute is stale -- which is why the spec asks for it in
    preference to `allow`; an `allow` goes stale silently.
    """
    if 'too_many_lines' not in text:
        return []
    lines = text.split('\n')
    flagged = []
    for offset, masked in rust_attributes(text):
        if not allows_too_many_lines(masked):
            continue
        if re.search(r'\breason\s*=\s*"\s*x[x\s]*"', masked):
            continue
        line = text.count('\n', 0, offset)
        above = lines[line - 1].strip() if line > 0 else ''
        # `///` and `//!` are doc comments, and a bare `//` says nothing.
        plain = re.match(r'//(?![/!])\s*\S', above)
        block = re.match(r'/\*(?![*!])', above) and above.endswith('*/')
        if plain or block:
            continue
        flagged.append(line + 1)
    return flagged


class RustFunctionLengthLint(Check):
    id = 'rust-function-length-lint'
    spec = 'docs/audits/rust-function-length-lint.md'
    template = None
    issue_title = 'Rust function length lint'

    def applies(self, repo):
        """Skip a repository that is not a Rust project.

        From the repository's properties, which read the filesystem
        rather than the index, so an empty directory is a skip as it is
        for `rust-unwrap-lint`.
        """
        if not repo.props['has_cargo_toml']:
            return 'No Cargo.toml (not a Rust project)'
        return None

    def run(self, repo):
        """Check Rust projects enable clippy's too_many_lines lint.

        The sibling of `rust-unwrap-lint`, with the same structure: the
        root Cargo.toml sets the lint to warn or deny, a clippy.toml
        beside it sets a threshold, and every other first-party crate
        manifest inherits the workspace lints or defines the lint
        itself.

        The threshold's value is not judged. Clippy's default of 100 is
        the wrong number for some codebases, and which number is right
        is a measurement of that codebase, which the spec describes how
        to make; what is required is that somebody chose.

        Two kinds of manifest are not asked. A fuzz harness crate, as
        for `rust-unwrap-lint`. And a manifest declaring a workspace of
        its own, which cargo excludes from the workspace above it:
        `[workspace.lints]` in the root cannot reach it, so requiring it
        to inherit would ask the impossible. That is the `[workspace]`
        header: a member with only `[workspace.dependencies]` has
        copied a table from the root, declares no workspace, and is
        asked like any other member.

        Finally every tracked Rust file is scanned for an
        `#[allow(clippy::too_many_lines)]` giving no reason.

        The helpers are imported from `audit.files`, where they live;
        they are not in `packaging.py`, so that module is not a
        dependency of this one.
        """
        if repo.isfile('Cargo.toml'):
            root_manifest = 'Cargo.toml'
        else:
            root_manifest = 'src/Cargo.toml'
        root_dir = os.path.dirname(root_manifest)

        issues = []

        root_content = repo.read(root_manifest)
        if root_content is None:
            return self.skip('No Cargo.toml (not a Rust project)')
        if not (
            toml_section_has_key(
                root_content, 'workspace.lints.clippy', TOO_MANY_LINES_LINT)
            or toml_section_has_key(
                root_content, 'lints.clippy', TOO_MANY_LINES_LINT)
        ):
            issues.append(
                f'clippy too_many_lines lint not set to warn or deny '
                f'in {root_manifest}')

        clippy_toml = os.path.join(root_dir, 'clippy.toml')
        if not check_file_contains(
            repo, clippy_toml,
            r'(?m)^\s*too-many-lines-threshold\s*=\s*\d',
        ):
            issues.append(
                f'{clippy_toml} missing too-many-lines-threshold')

        for manifest in require_tracked_paths(repo.path, '*Cargo.toml'):
            if manifest == root_manifest:
                continue
            if 'fuzz' in manifest.split('/'):
                continue
            # Through Repo.read, so a manifest the index lists but the
            # checkout does not hold is skipped rather than raised on.
            content = repo.read(manifest)
            if content is None or '[package]' not in content:
                continue
            if OWN_WORKSPACE_RE.search(content):
                continue
            inherits = (
                toml_section_has_key(
                    content, 'lints', r'workspace\s*=\s*true')
                or re.search(
                    r'^\s*lints\.workspace\s*=\s*true', content,
                    re.MULTILINE)
            )
            defines = toml_section_has_key(
                content, 'lints.clippy', TOO_MANY_LINES_LINT)
            if not (inherits or defines):
                issues.append(
                    f'{manifest} neither inherits workspace lints '
                    f'([lints] workspace = true) nor defines '
                    f'too_many_lines itself')

        bare = []
        for path in require_tracked_paths(repo.path, '*.rs'):
            content = repo.read(path)
            if content is None:
                continue
            bare.extend(
                f'{path}:{line}'
                for line in unexplained_length_allows(content))
        if bare:
            shown = ', '.join(bare[:10])
            more = f' and {len(bare) - 10} more' if len(bare) > 10 else ''
            issues.append(
                f'#[allow(clippy::too_many_lines)] without a reason at '
                f'{shown}{more}; give it `reason = "..."` or a comment on '
                f'the line above, or use #[expect]')

        if issues:
            return self.fail('; '.join(issues))
        return self.ok(
            'clippy too_many_lines lint is enabled with a '
            'too-many-lines-threshold')
