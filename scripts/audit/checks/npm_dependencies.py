"""The dependency criteria for npm projects.

The fleet's three Python dependency criteria -- pinning the transitive
tree, dependencies nobody imports, and imports resting on somebody
else's dependency list -- ask questions that are about dependency
management rather than about Python. hunkydory is the fleet's first
TypeScript repository, and until this module existed those three
questions simply went unasked for it.

They are npm equivalents rather than translations. npm answers the
first one by construction: `package-lock.json` records the whole
resolved tree with integrity hashes, and `npm ci` installs exactly
that. So the npm pinning criterion checks that the mechanism npm
already provides is actually in place and actually enforced, rather
than inventing a reconciler npm has no need of. The other two have no
npm equivalent at all, and they are the two that catch real drift.

Reading the source is a scan, not a parse. The audit runs against
checkouts it neither installs nor builds, so there is no module
resolver to ask and no compiler to consult; what is here is the same
bargain `audit/text/python_source.py` makes, and its limits are
documented beside the code that has them.
"""

import json
import os
import re
import subprocess

from audit.check import Check
from audit.files import WALK_SKIP


#: Modules node provides itself. An import of one of these is not a
#: package and must never be reported as an undeclared dependency.
#: Both spellings are current -- `node:fs` and `fs` resolve to the same
#: module -- and the prefixed spelling needs no table at all, because
#: npm forbids a package name containing a colon. This list is for the
#: bare spelling, which is indistinguishable from a package name.
#:
#: A registry package may share a name with a builtin (`path`,
#: `process` and `events` all exist on npm), so the list is applied
#: only after the declared dependencies have been subtracted: a
#: declared `path` matches `import path from 'path'`, and an
#: undeclared one is the builtin.
NODE_BUILTINS = frozenset({
    'assert', 'async_hooks', 'buffer', 'child_process', 'cluster',
    'console', 'constants', 'crypto', 'dgram', 'diagnostics_channel',
    'dns', 'domain', 'events', 'fs', 'http', 'http2', 'https',
    'inspector', 'module', 'net', 'os', 'path', 'perf_hooks',
    'process', 'punycode', 'querystring', 'readline', 'repl', 'sea',
    'sqlite', 'stream', 'string_decoder', 'sys', 'test', 'timers',
    'tls', 'trace_events', 'tty', 'url', 'util', 'v8', 'vm', 'wasi',
    'worker_threads', 'zlib',
})

#: What we read as source. `.d.ts` files arrive through `.ts`, which is
#: deliberate: a declaration file importing a package is as much a
#: dependency on it as a statement that runs.
SOURCE_EXTENSIONS = ('.ts', '.tsx', '.mts', '.cts',
                     '.js', '.jsx', '.mjs', '.cjs')

#: Build output a TypeScript project leaves in the tree. `out/` is the
#: conventional `outDir` and is what hunkydory uses; the rest of the
#: usual suspects (`build`, `dist`, `node_modules`) are already in
#: WALK_SKIP. A compiled copy of the source carries the same imports as
#: the source, so reading it would count one import twice and, worse,
#: keep counting it after the file it came from was deleted.
BUILD_DIRECTORIES = frozenset({'coverage', 'out'})

#: npm's own lockfile formats, in the order npm reads them. Both
#: carry the same schema and both are installed by `npm ci`;
#: `npm-shrinkwrap.json` is the publishable spelling, and npm honours
#: it in preference to `package-lock.json` when a project has both.
#: A project using one is doing what the pinning criterion asks, so it
#: resolves which one is in play rather than reading only the common
#: spelling and reporting the other as a package manager it cannot
#: read.
NPM_LOCKFILES = ('npm-shrinkwrap.json', 'package-lock.json')

#: Lockfiles belonging to another package manager entirely. These do
#: pin a tree, and reading them is work nobody in the fleet needs yet,
#: so a project using one is reported not applicable with that reason.
FOREIGN_LOCKFILES = ('yarn.lock', 'pnpm-lock.yaml', 'bun.lockb')

#: The manifest and every lockfile: read as configuration, they would
#: name every declared dependency and make all of them look used.
MANIFEST_AND_LOCKFILES = frozenset(
    ('package.json',) + NPM_LOCKFILES + FOREIGN_LOCKFILES)

#: What a configuration file is called. A dotfile counts whatever its
#: name; anything else counts by extension. `.mts` and `.cts` are here
#: because a flat eslint config is routinely written in one.
CONFIGURATION_EXTENSIONS = ('.json', '.js', '.cjs', '.mjs', '.ts', '.mts',
                            '.cts', '.yaml', '.yml', '.toml')

#: The documented alternative home for tool configuration -- eslint
#: names it explicitly, and others have followed -- read one level
#: deep beside the repository root.
CONFIGURATION_DIRECTORY = '.config'

#: The dependency maps a package name can be declared in. All four
#: count as "declared" for the undeclared-import criterion -- a peer or
#: optional dependency is a deliberate statement about a package, not
#: an accident -- but only the first two are read by the unused
#: criterion. See `NpmUnusedDeclaredDependency.run()`.
DEPENDENCY_SECTIONS = ('dependencies', 'devDependencies',
                       'peerDependencies', 'optionalDependencies')

#: Manifest keys that name dependencies rather than using them, and so
#: are never evidence that one is used: the four dependency maps, and
#: the block recording why a dependency is installed without being
#: imported. Reading the annotation block as a mention would make an
#: unexplained entry in it exempt a dependency, which is the one thing
#: the reason requirement exists to prevent.
DECLARATION_KEYS = DEPENDENCY_SECTIONS + ('shakenfistAudit',)

#: `import ... from 'x'` and `export ... from 'x'`, including
#: `import type`. The binding list may span lines, so the body matches
#: newlines; it may not span a statement, so it stops at a semicolon.
IMPORT_FROM_RE = re.compile(
    r'(?m)^[ \t]*(?:import|export)\b(?:[^;]{0,2000}?)'
    r'\bfrom[ \t\r\n]*[\'"](?P<spec>[^\'"\n]+)[\'"]')

#: `import 'x'` for side effects only.
BARE_IMPORT_RE = re.compile(
    r'(?m)^[ \t]*import[ \t]*[\'"](?P<spec>[^\'"\n]+)[\'"]')

#: `require('x')` and dynamic `import('x')`.
CALL_IMPORT_RE = re.compile(
    r'\b(?:require|import)[ \t]*\([ \t\r\n]*'
    r'[\'"](?P<spec>[^\'"\n]+)[\'"]')

#: A `run:` key in a workflow, as a step's own key (`- run:`) or as a
#: plain one. What follows on the line is either the command itself or
#: a block scalar indicator introducing several.
RUN_KEY_RE = re.compile(r'^(?P<lead>[ \t]*(?:-[ \t]+)?)run:(?P<rest>.*)$')

#: The block scalar indicators, with their optional chomping and
#: explicit indentation: `|`, `>-`, `|2` and the rest.
BLOCK_SCALAR_RE = re.compile(r'^[|>](?:\d+[-+]?|[-+]?\d*)$')

#: `npm install`, at a position where a shell would run it: the start
#: of a command, or just after a pipe, `&&` or `;`. Anchored, because
#: a workflow names npm in plenty of places that do not run it -- a
#: step `name:`, an `echo`, a trailing comment -- and matching the
#: phrase anywhere in the line fails a repository whose only npm
#: command is `npm ci` for having a step named "do not use npm
#: install".
NPM_RESOLVING_RE = re.compile(r'(?:^|[|&;]\s*)npm\s+(?:install|i|add)\b')

#: A specifier carrying a URI scheme -- `node:`, `bun:`, `data:`,
#: `https:` -- is never a package name.
SCHEME_RE = re.compile(r'^[a-z][a-z0-9+.\-]*:')

#: Characters after which a `/` opens a regular expression rather than
#: dividing. The distinction matters because a regex may contain a
#: quote or a `//`, and mistaking one for a comment blanks the rest of
#: the line.
REGEX_PRECEDERS = frozenset('([{,;:=!&|?+-*%~^<>')

#: Keywords with the same property, where the character before the `/`
#: is a letter and so tells us nothing.
REGEX_PRECEDING_KEYWORDS = frozenset({
    'await', 'case', 'delete', 'do', 'else', 'in', 'instanceof', 'new',
    'of', 'return', 'throw', 'typeof', 'void', 'yield',
})


def read_json(repo, path):
    """Parse a JSON file out of the checkout, or None.

    None covers both absent and unparseable. A criterion that crashed
    on a manifest somebody was in the middle of editing would report
    nothing about that repository at all, so a bad parse is a reason to
    say nothing rather than a reason to raise.
    """
    content = repo.read(path)
    if content is None:
        return None
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def declared_dependencies(manifest, sections=DEPENDENCY_SECTIONS):
    """Map package name to the section it is declared in."""
    declared = {}
    for section in sections:
        entries = manifest.get(section)
        if not isinstance(entries, dict):
            continue
        for name in entries:
            declared.setdefault(name, section)
    return declared


def lockfile_name(repo):
    """Which npm lockfile this project pins with, or None.

    npm's precedence: `npm-shrinkwrap.json` wins when a project has
    both. The two are the same file under different names -- identical
    schema, both installed exactly by `npm ci` -- and the shrinkwrap is
    the one that ships to a consumer, which is why a published CLI
    carries it. Reading only `package-lock.json` would report a project
    that pins its tree the stricter way as using a lockfile format this
    criterion cannot read, and silently stop asking any of these three
    questions about it.
    """
    for name in NPM_LOCKFILES:
        if repo.exists(name):
            return name
    return None


def read_lockfile(repo):
    """The parsed lockfile and the name it was read from.

    Both, because every message that mentions a lockfile should name
    the one the project actually has.
    """
    name = lockfile_name(repo)
    if name is None:
        return None, None
    return read_json(repo, name), name


def manifest_line(repo, name):
    """The package.json line a dependency is declared on, or None.

    A finding that names a line is one somebody can act on without
    searching, which is what the Python criteria cite a
    `pyproject.toml:` line for. A single-line manifest still gets a
    correct verdict, just without the citation.
    """
    content = repo.read('package.json')
    if content is None:
        return None
    pattern = re.compile(r'^\s*"%s"\s*:' % re.escape(name))
    for number, line in enumerate(content.splitlines(), start=1):
        if pattern.match(line):
            return number
    return None


def strip_comments(source):
    """Blank comment bodies, leaving string literals alone.

    The Python equivalent masks strings as well, because it matches
    structure. Here the thing being looked for *is* a string literal --
    the module specifier -- so only comments can be blanked, and the
    scanner has to recognise strings and regular expressions in order
    to know which `/` starts a comment.

    Offsets are preserved: bodies are blanked space for space and
    newlines are kept, so a line number taken from the result still
    addresses the same line of the original.

    A commented-out import is not an import. That is the whole point:
    it is the precise shape the unused criterion exists to find, and
    counting one as a use would report the deadest dependency in the
    tree as the one still in use.
    """
    out = list(source)
    index, length = 0, len(source)
    previous = ''
    while index < length:
        char = source[index]

        if char in '\'"`':
            index = _skip_string(source, index, char)
            previous = char
            continue

        if char == '/' and index + 1 < length:
            following = source[index + 1]
            if following == '/':
                while index < length and source[index] != '\n':
                    out[index] = ' '
                    index += 1
                continue
            if following == '*':
                while index < length and not source.startswith('*/', index):
                    if source[index] != '\n':
                        out[index] = ' '
                    index += 1
                for offset in (0, 1):
                    if index + offset < length:
                        out[index + offset] = ' '
                index += 2
                continue
            if _starts_regex(source, index, previous):
                index = _skip_regex(source, index)
                previous = '/'
                continue

        if not char.isspace():
            previous = char
        index += 1

    return ''.join(out)


def _skip_string(source, index, quote):
    """Return the index just past a string literal."""
    index += 1
    length = len(source)
    while index < length:
        if source[index] == '\\':
            index += 2
            continue
        if source[index] == quote:
            return index + 1
        index += 1
    return index


def _starts_regex(source, index, previous):
    """Does the `/` at `index` open a regular expression literal?

    The standard heuristic, and the only one available without a
    parser: a regex can only appear where a value can, so it is a regex
    when the previous significant character cannot end one. Getting
    this wrong in the division direction would blank source up to the
    next `/`, and getting it wrong in the regex direction would read
    `/https:\\/\\// ` as a line comment.
    """
    if previous in REGEX_PRECEDERS or previous == '':
        return True
    if not (previous.isalnum() or previous == '_'):
        return False

    # Walk back over the word rather than matching a regex against
    # `source[:index]`. That slice is a copy of everything read so
    # far and the search then scans all of it, so the cost of
    # stripping comments grew with the file size times the number of
    # divisions in it: a 140KB file with 500 of them took half a
    # minute. The answer only ever depends on the token immediately
    # before the slash.
    end = index
    while end > 0 and source[end - 1].isspace():
        end -= 1
    start = end
    while start > 0 and (source[start - 1].isalnum()
                         or source[start - 1] in '_$'):
        start -= 1
    return source[start:end] in REGEX_PRECEDING_KEYWORDS


def _skip_regex(source, index):
    """Return the index just past a regular expression literal."""
    index += 1
    length = len(source)
    in_class = False
    while index < length:
        char = source[index]
        if char == '\\':
            index += 2
            continue
        if char == '\n':
            return index
        if char == '[':
            in_class = True
        elif char == ']':
            in_class = False
        elif char == '/' and not in_class:
            return index + 1
        index += 1
    return index


def specifier_package(specifier):
    """The package a module specifier names, or None.

    None for everything that is not a package: a relative or absolute
    path, a `#subpath` import, and anything carrying a URI scheme --
    which is how `node:fs` is excluded without a table.

    Node builtins spelled bare (`fs`, `path`) come back as themselves,
    because at this level they are indistinguishable from a package of
    the same name. Callers subtract NODE_BUILTINS once they know what
    the manifest declares.
    """
    if not specifier:
        return None
    if specifier[0] in './#' or specifier.startswith('\\'):
        return None
    if SCHEME_RE.match(specifier):
        return None

    parts = specifier.split('/')
    if specifier.startswith('@'):
        if len(parts) < 2 or not parts[1]:
            return None
        return '/'.join(parts[:2])
    return parts[0]


def source_files(repo):
    """Every JavaScript or TypeScript file the repository wrote.

    Build output is skipped, including whatever tsconfig.json names as
    its `outDir`, along with the vendored and virtualenv directories in
    WALK_SKIP. A compiled copy of a source file is not evidence about
    anything: it says what the source said at the last build, which is
    exactly what makes a deleted import look alive.

    The `outDir` is matched as the path it names rather than as a
    directory name. An `outDir` of `build/js` says nothing about a
    directory called `build` three levels down, and one of `.` names
    the repository itself, which is a tsconfig saying "compile in
    place" rather than one asking for the whole tree to be skipped.
    """
    skip = set(WALK_SKIP) | set(BUILD_DIRECTORIES)
    root = os.path.normpath(repo.path)
    out_directory = None
    tsconfig = repo.read('tsconfig.json')
    if tsconfig:
        try:
            parsed = json.loads(strip_comments(tsconfig))
        except ValueError:
            parsed = {}
        out_dir = (parsed.get('compilerOptions') or {}).get('outDir')
        if isinstance(out_dir, str) and out_dir.strip():
            candidate = os.path.normpath(os.path.join(root, out_dir.strip()))
            if candidate != root:
                out_directory = candidate

    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in skip
            and os.path.normpath(os.path.join(dirpath, d)) != out_directory)
        for filename in sorted(filenames):
            if not filename.endswith(SOURCE_EXTENSIONS):
                continue
            full = os.path.join(dirpath, filename)
            # The walk opens these itself rather than going through
            # Repo.read, so it applies Repo's containment rule itself:
            # a committed symlink pointing out of the clone is a path
            # the audited repository chose, and following it would let
            # a repository decide what the audit reads. Symlinked
            # directories need no such guard, because os.walk does not
            # follow them.
            if repo.contains(full):
                found.append(full)
    return found


def imported_packages(paths):
    """The set of package names imported across a list of files.

    Every import form counts: `import`, `import type`, a side-effect
    `import 'x'`, `export ... from 'x'`, `require()` and a dynamic
    `import()`. A type-only import is a dependency like any other --
    it is resolved at build time, and the build breaks when it is not
    installed.

    The paths are opened directly rather than through `Repo.read`,
    because there is nothing to cache here and the walk already holds
    absolute paths. `source_files()` applies the containment rule
    `Repo.read` applies, so nothing outside the checkout arrives here.
    """
    names = set()
    for path in paths:
        try:
            with open(path, 'r', errors='replace') as f:
                content = f.read()
        except OSError:
            continue
        code = strip_comments(content)
        for pattern in (IMPORT_FROM_RE, BARE_IMPORT_RE, CALL_IMPORT_RE):
            for match in pattern.finditer(code):
                name = specifier_package(match.group('spec'))
                if name:
                    names.add(name)
    return names


def lockfile_packages(lock):
    """Every package name a lockfile resolves, at any depth.

    lockfileVersion 2 and 3 carry a flat `packages` map keyed by
    install path, so the name is whatever follows the last
    `node_modules/`. Version 1 carries a nested `dependencies` tree
    instead, and is read too: this is used to tell a transitive package
    from one that is merely imagined, and answering "nothing is
    resolved here" for an old lockfile would silently disable the
    criterion rather than report on it.

    Workspace links are skipped. They are sibling packages in the same
    repository rather than anything resolved from a registry.
    """
    names = set()

    packages = lock.get('packages')
    if isinstance(packages, dict):
        for path, entry in packages.items():
            if not path:
                continue
            if isinstance(entry, dict) and entry.get('link'):
                continue
            marker = path.rfind('node_modules/')
            if marker == -1:
                continue
            name = path[marker + len('node_modules/'):]
            if name:
                names.add(name)

    def walk(tree):
        if not isinstance(tree, dict):
            return
        for name, entry in tree.items():
            names.add(name)
            if isinstance(entry, dict):
                walk(entry.get('dependencies'))

    walk(lock.get('dependencies'))
    return names


def binary_names(lock, package):
    """The command names a package installs, from the lockfile.

    `typescript` puts `tsc` on the path, and a `scripts` entry that
    runs `tsc` is using the dependency without ever naming it. The
    lockfile records the mapping, so it is read rather than guessed:
    a hardcoded table would be right about typescript and wrong about
    the next tool anybody adds.
    """
    packages = lock.get('packages')
    if not isinstance(packages, dict):
        return set()

    names = set()
    for path, entry in packages.items():
        if not isinstance(entry, dict):
            continue
        marker = path.rfind('node_modules/')
        if marker == -1 or path[marker + len('node_modules/'):] != package:
            continue
        binaries = entry.get('bin')
        if isinstance(binaries, dict):
            names |= {str(b) for b in binaries}
        elif isinstance(binaries, str):
            # A string `bin` is npm's shorthand for one command named
            # after the package, so `@scope/thing` installs `thing`
            # whatever the file is called. The file name is offered as
            # well, because it usually is the command too and an extra
            # candidate can only make a dependency look used.
            names.add(package.rsplit('/', 1)[-1])
            names.add(os.path.basename(binaries))
    return names


def mentions(text, name):
    """Is a package named as a whole token somewhere in some text?

    A name inside a comment counts, which is the opposite of what
    `files.file_mentions()` does with the same question, and
    deliberately so: the two are asked about opposite things. That one
    asks whether a project does something, where a comment describing
    it would report the project compliant for describing what it does
    not do. This one asks whether a declared dependency is used, and
    reads only for the generous direction `run()` argues for -- a
    spurious mention can only make a dependency look used, and a false
    pass costs a finding the next sweep gets anyway, while a false
    failure sends somebody to justify a dependency nobody doubted. A
    config file whose comment names a plugin is a config file somebody
    is still thinking about.

    Note this is the opposite call to the one `strip_comments()` makes
    about *source*. A commented-out import is the precise shape this
    criterion exists to find, so it must not count; a comment in a
    config file naming a tool is evidence about the tool, not a dead
    import of it.
    """
    if not text:
        return False
    pattern = r'(?<![A-Za-z0-9_@/.\-])%s(?![A-Za-z0-9_.\-])' % re.escape(name)
    return bool(re.search(pattern, text))


def configuration_files(repo):
    """Repository-relative configuration files, root and `.config/`.

    A dotfile counts whatever it is called, because configuration
    routinely has no extension worth matching on; anything else counts
    by extension.
    """
    found = []
    for directory in ('', CONFIGURATION_DIRECTORY):
        base = repo.join(directory) if directory else repo.path
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name in MANIFEST_AND_LOCKFILES:
                continue
            if not (name.startswith('.')
                    or name.endswith(CONFIGURATION_EXTENSIONS)):
                continue
            if not os.path.isfile(os.path.join(base, name)):
                continue
            found.append(os.path.join(directory, name) if directory else name)
    return found


def configuration_text(repo, manifest):
    """Everything a package could be named in other than an import.

    Plenty of npm tooling is selected by name in configuration rather
    than imported: an eslint config in an `extends` array, a postcss
    plugin keyed by name, a command in a `scripts` entry, a tool run
    from a workflow. None of that is an import and all of it is use.

    The dependency maps themselves are removed from the manifest
    first, for the obvious reason: they name every declared dependency,
    so reading them would make all of them look used. So is the
    annotation block, which would otherwise exempt a dependency on the
    strength of an entry carrying no reason. Lockfiles are left out for
    the same reason as the dependency maps.

    Configuration is read from the root and from `.config/`, which is
    the directory eslint documents as the alternative home for
    `eslint.config.js` and which several other tools have followed.
    Missing a config file means missing the only mention of the plugin
    it names, and a plugin nothing imports is exactly what this
    criterion would then report as unused -- a false failure, which is
    the expensive direction. Nothing deeper is read: a tool's config
    lives at one of those two levels, and walking the tree would start
    reading the fixtures of whatever the project tests.
    """
    without_declarations = {
        key: value for key, value in manifest.items()
        if key not in DECLARATION_KEYS
    }
    chunks = [json.dumps(without_declarations, sort_keys=True)]

    for relative in configuration_files(repo):
        content = repo.read(relative)
        if content:
            chunks.append(content)

    for workflow in repo.workflows():
        content = repo.workflow(workflow)
        if content:
            chunks.append(content)

    return '\n'.join(chunks)


def not_imported_exemptions(manifest):
    """The recorded reasons a dependency is installed but not imported.

    package.json is JSON and cannot carry the `# not-imported:` comment
    the Python criterion reads, so the annotation is a field:

        "shakenfistAudit": {
          "notImported": {
            "autoprefixer": "named as a plugin in postcss.config.js"
          }
        }

    npm ignores fields it does not know. The reason is required for the
    same reason it is required there: an unexplained exception is
    indistinguishable from silencing a finding.
    """
    block = manifest.get('shakenfistAudit')
    if not isinstance(block, dict):
        return {}
    recorded = block.get('notImported')
    if not isinstance(recorded, dict):
        return {}
    return {
        name: reason for name, reason in recorded.items()
        if isinstance(reason, str) and reason.strip()
    }


def host_provided_modules(manifest):
    """Modules the runtime injects, which must never be declared.

    `engines` is the manifest saying which hosts a package runs
    inside, and a host that is named there is a host whose API it may
    import. `vscode` is the fleet's case: the extension host provides
    the module, and an extension that declared it as a dependency
    would install a placeholder package and break.
    """
    engines = manifest.get('engines')
    if not isinstance(engines, dict):
        return set()
    return {str(name) for name in engines if str(name) not in ('node', 'npm')}


def run_commands(content):
    """The shell command lines a workflow actually runs.

    Reading a workflow as text finds npm in places nothing runs it: a
    step `name:`, an `echo` warning somebody off, a comment at the end
    of a line, a description in prose. Only the body of a `run:` is a
    command, so only that is returned -- everything else is a workflow
    talking about a command rather than running one, and failing a
    compliant repository for describing what it does not do is the
    expensive direction to be wrong in.

    A block scalar (`run: |`) carries several commands, and ends where
    the indentation returns to the key that introduced it. That is
    tracked rather than assumed, because the line after a block is
    routinely another key of the same step, and reading it as a
    command would put the whole rest of the file back in scope.

    This is a scan and not a YAML parse, for the reason the module
    docstring gives: the audit reads checkouts with the standard
    library alone. The shapes it does not follow -- a quoted scalar
    spanning lines, an anchor -- are shapes no workflow in the fleet
    writes a command in.
    """
    commands = []
    key_column = None
    for line in content.splitlines():
        stripped = line.strip()
        if key_column is not None:
            if not stripped:
                continue
            if len(line) - len(line.lstrip()) > key_column:
                if not stripped.startswith('#'):
                    commands.append(stripped)
                continue
            key_column = None

        match = RUN_KEY_RE.match(line)
        if not match:
            continue
        rest = match.group('rest').strip()
        if BLOCK_SCALAR_RE.match(rest):
            key_column = len(match.group('lead'))
        elif rest and not rest.startswith('#'):
            commands.append(rest)
    return commands


def workspace_reason(manifest):
    """Why a workspace root is out of scope, or None."""
    if manifest.get('workspaces'):
        return ('package.json declares workspaces, so the dependencies '
                'of this tree are spread across several manifests and '
                'this criterion reads only the root one')
    return None


class NpmPackageCheck(Check):
    """Shared applicability: there has to be an npm package here."""

    def applies(self, repo):
        if not repo.exists('package.json'):
            return 'No package.json (not an npm project)'
        return None

    def manifest(self, repo):
        return read_json(repo, 'package.json')


class NpmPinIndirectDependencies(NpmPackageCheck):
    id = 'npm-pin-indirect-dependencies'
    spec = 'docs/audits/npm-pin-indirect-dependencies.md'
    template = None
    issue_title = 'Pin indirect npm dependencies'

    def run(self, repo):
        """Check the whole resolved tree is pinned, and stays pinned.

        npm already solves the problem the Python criterion builds a
        reconciler for. `package-lock.json` records every package in
        the transitive closure with the exact version and an integrity
        hash, and `npm ci` installs precisely that and fails rather
        than resolving anything. So the question here is not whether
        somebody remembered to regenerate a pinned block; it is
        whether the mechanism is present, shared, and honoured.

        Three ways it is not. A lockfile that was never committed pins
        the tree on one laptop and nowhere else. A lockfileVersion 1
        file predates the `packages` map, so npm resolves a tree it
        only partly describes. And a workflow that runs `npm install`
        rather than `npm ci` re-resolves against the range in
        package.json and writes the lockfile it was supposed to obey:
        CI then tests a tree nobody has seen, which is the exact
        failure the pinning criterion exists to prevent, arrived at
        from the other direction.

        A global install (`npm install -g`) is not that. It installs a
        tool beside the project rather than the project's own
        dependencies, and touches no lockfile. Neither is a mention of
        the phrase somewhere in a workflow that is not a command: only
        the body of a `run:` is read, because failing a repository for
        a step named after the thing it does not do is the expensive
        direction to be wrong in.

        Either of npm's two lockfile spellings satisfies this, and
        `npm-shrinkwrap.json` wins when a project has both, because
        that is npm's own precedence.
        """
        manifest = self.manifest(repo)
        if manifest is None:
            return self.skip('package.json is not readable JSON, so there '
                             'is nothing to say about how it pins')

        if not declared_dependencies(manifest):
            return self.skip('No dependencies are declared in package.json, '
                             'so there is no transitive tree to pin')

        name = lockfile_name(repo)
        if name is None:
            for alternative in FOREIGN_LOCKFILES:
                if repo.exists(alternative):
                    return self.skip(
                        f'Dependencies are locked by {alternative} rather '
                        f'than by package-lock.json or npm-shrinkwrap.json, '
                        f'which are the only lockfile formats this '
                        f'criterion reads')
            return self.fail(
                'No package-lock.json or npm-shrinkwrap.json, so nothing '
                'pins the transitive dependency tree: every install '
                'resolves the declared ranges afresh')

        issues = []

        lock = read_json(repo, name)
        if lock is None:
            issues.append(f'{name} is not readable JSON')
        else:
            version = lock.get('lockfileVersion')
            if not isinstance(version, int) or version < 2:
                issues.append(
                    f'{name} is lockfileVersion {version!r}; '
                    f'version 2 or later is what records the full '
                    f'resolved tree that npm ci installs')

        if self._is_untracked(repo, name):
            issues.append(
                f'{name} is not committed, so the pinned tree '
                f'exists only in the working copy that generated it')

        issues.extend(self._resolving_workflows(repo))

        if issues:
            return self.fail('; '.join(issues), issues=issues)

        packages = lockfile_packages(lock) if lock else set()
        noun = 'package' if len(packages) == 1 else 'packages'
        return self.ok(f'{name} is committed and pins all '
                       f'{len(packages)} resolved {noun}')

    def _is_untracked(self, repo, name):
        """True when git is sure the lockfile is not tracked.

        Only when it is sure. A directory that is not a checkout, or a
        git that will not run, says nothing about compliance, and a
        criterion that failed on it would fail on every tarball.
        """
        try:
            result = subprocess.run(
                ['git', '-C', repo.path, 'ls-files', '--', name],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        if result.returncode != 0:
            return False
        return not result.stdout.strip()

    def _resolving_workflows(self, repo):
        """Workflows that install by resolving rather than by the lock."""
        found = []
        for name in sorted(repo.workflows()):
            for command in run_commands(repo.workflow(name) or ''):
                if not NPM_RESOLVING_RE.search(command):
                    continue
                if re.search(r'(?:^|\s)(?:-g|--global)(?:\s|$)', command):
                    continue
                # --package-lock-only resolves and writes the lockfile
                # without installing anything, which is how a lockfile
                # is deliberately refreshed. Nothing is tested against
                # the tree it produces until that lockfile is reviewed
                # and merged, so it is the opposite of the problem.
                if '--package-lock-only' in command:
                    continue
                found.append(
                    f'.github/workflows/{name} runs "{command}" rather '
                    f'than "npm ci", which re-resolves the declared '
                    f'ranges and rewrites the lockfile')
                break
        return found


class NpmUnusedDeclaredDependency(NpmPackageCheck):
    id = 'npm-unused-declared-dependency'
    spec = 'docs/audits/npm-unused-declared-dependency.md'
    template = None
    issue_title = 'Unused declared npm dependency'

    def run(self, repo):
        """Flag declared packages nothing in the repository uses.

        The cost of a dependency nobody uses is the same here as it is
        in Python, and npm makes it larger. library-utilities declaring
        one oslo library it never imported put twelve packages into
        every shakenfist install; an npm dependency's closure is
        routinely measured in hundreds, and every package in it is
        another Renovate pull request, another integrity hash, and
        another supply chain to trust on behalf of a project that would
        behave identically without any of it.

        `dependencies` and `devDependencies` are both read.
        devDependencies do not ship to a consumer, which is the
        argument for reading only runtime dependencies in Python, but
        they are installed by `npm ci` on every CI run and bumped by
        Renovate like everything else -- and in a TypeScript project
        they are where the dead weight accumulates, because the build
        tooling is what gets replaced. `peerDependencies` and
        `optionalDependencies` are not read: both are statements about
        what a *consumer* will provide, so "does this project import
        it" is the wrong question about them.

        Use is read generously, and deliberately so. An npm package is
        as often named as it is imported -- a command in a `scripts`
        entry, a binary that command runs, a plugin keyed by name in a
        config file, a `@types/` package the compiler picks up by
        convention and no file ever imports. A spurious candidate can
        only make a dependency look used, and this criterion files an
        issue when one looks unused: a false pass costs a finding the
        next sweep gets anyway, while a false failure sends somebody to
        justify a dependency nobody doubted.
        """
        manifest = self.manifest(repo)
        if manifest is None:
            return self.skip('package.json is not readable JSON, so there '
                             'is nothing declared to read')

        reason = workspace_reason(manifest)
        if reason:
            return self.skip(reason)

        declared = declared_dependencies(
            manifest, sections=('dependencies', 'devDependencies'))
        if not declared:
            return self.skip('package.json declares no dependencies or '
                             'devDependencies, so there is nothing '
                             'declared to use')

        # Without the lockfile there is no way to learn that
        # `typescript` is what puts `tsc` on the path, so every tool a
        # scripts entry invokes by its command name would be reported
        # as unused. That is the expensive direction to be wrong in,
        # and npm-pin-indirect-dependencies is already failing a
        # project that has no lockfile.
        lock, _ = read_lockfile(repo)
        if lock is None:
            return self.skip(
                'No readable package-lock.json or npm-shrinkwrap.json, so '
                'the command names a dependency installs cannot be read '
                'and a tool invoked from a scripts entry would look unused')

        sources = source_files(repo)
        imported = imported_packages(sources)
        configuration = configuration_text(repo, manifest)
        exempted = not_imported_exemptions(manifest)

        unused, annotated, types = [], 0, 0
        for name in sorted(declared):
            # @types/x is consumed by the TypeScript compiler on the
            # strength of its name alone. Nothing imports it, ever, and
            # the package it describes is usually not a dependency of
            # this project at all -- @types/vscode describes an API the
            # extension host injects.
            if name.startswith('@types/'):
                types += 1
                continue
            if name in imported:
                continue
            if name in exempted:
                annotated += 1
                continue
            if any(mentions(configuration, token)
                   for token in {name} | binary_names(lock, name)):
                continue
            line = manifest_line(repo, name)
            unused.append(f'{name} (package.json:{line})' if line else name)

        if unused:
            return self.fail(
                f'Declared but never imported, run or named in '
                f'configuration: {", ".join(unused)}. Remove each, or '
                f'record why it is installed under '
                f'"shakenfistAudit": {{"notImported": {{...}}}} in '
                f'package.json',
                unused=unused)

        noun = 'dependency is' if len(declared) == 1 else 'dependencies are'
        detail = f'All {len(declared)} declared {noun} used'
        extra = []
        if types:
            extra.append(f'{types} are @types packages the compiler reads')
        if annotated:
            extra.append(f'{annotated} are annotated as used without being '
                         f'imported')
        if extra:
            detail = f'{detail}, of which {" and ".join(extra)}'
        return self.ok(detail)


class NpmUndeclaredDirectDependency(NpmPackageCheck):
    id = 'npm-undeclared-direct-dependency'
    spec = 'docs/audits/npm-undeclared-direct-dependency.md'
    template = None
    issue_title = 'Undeclared direct npm dependency'

    def run(self, repo):
        """Flag imports resolved only by somebody else's dependency.

        npm installs a flat tree, so a package pulled in by a
        dependency of a dependency sits in `node_modules/` beside the
        ones this project asked for, and importing it works. It keeps
        working for exactly as long as the intermediate package
        continues to require it: nothing here changes on the day it
        stops, and the build breaks anyway. shakenfist spent years
        importing oslo_concurrency on an edge that existed only
        because shakenfist-utilities declared a dependency it never
        used, and npm's flat tree makes that arrangement easier to
        reach and harder to see.

        The lockfile is where the coincidence is visible: every package
        in it that package.json does not declare is there because
        something resolved to it, and `npm install` drops it the moment
        that stops being true.

        Imports that resolve to nothing at all are deliberately not
        reported. That is a build failure rather than a latent one, the
        compiler already says so, and the honest candidates for it --
        a tsconfig `paths` alias, a workspace sibling -- are resolver
        configuration this scan does not read.
        """
        manifest = self.manifest(repo)
        if manifest is None:
            return self.skip('package.json is not readable JSON, so there '
                             'is nothing declared to compare imports to')

        reason = workspace_reason(manifest)
        if reason:
            return self.skip(reason)

        lock, name = read_lockfile(repo)
        if lock is None:
            return self.skip('No readable package-lock.json or '
                             'npm-shrinkwrap.json, so there is no resolved '
                             'tree an import could be resting on')

        declared = set(declared_dependencies(manifest))
        transitive = lockfile_packages(lock) - declared
        if not transitive:
            return self.skip(f'{name} resolves nothing that package.json '
                             f'does not declare, so there are no transitive '
                             f'packages to rest on')

        sources = source_files(repo)
        if not sources:
            return self.skip('No JavaScript or TypeScript source outside '
                             'build directories, so nothing here imports '
                             'anything')

        imported = imported_packages(sources)
        # Declared first, then builtins, then the host. Order matters:
        # a project that declares a package named after a builtin has
        # declared it, and subtracting the builtins first would hide
        # that.
        candidates = imported - declared
        candidates -= NODE_BUILTINS
        candidates -= host_provided_modules(manifest)

        undeclared = sorted(candidates & transitive)
        if undeclared:
            return self.fail(
                f'Imported but resolved only as a transitive dependency: '
                f'{", ".join(undeclared)}. Declare each in package.json, '
                f'at the version the lockfile already resolves',
                undeclared=undeclared)

        if len(transitive) == 1:
            return self.ok(f'The one package {name} resolves transitively '
                           f'is not imported directly')
        return self.ok(f'None of the {len(transitive)} transitive packages '
                       f'is imported directly')
