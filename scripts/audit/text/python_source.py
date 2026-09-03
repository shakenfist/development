"""Reading Python source without executing it.

Masking comments and string literals so a pattern matches code rather
than prose about code, finding the modules a console entry point
reaches, and reading class statements without importing anything.

None of it is a parse in the strict sense. The audit runs against
checkouts of repositories it does not install, so it cannot import
them, and a real parse would still not tell it what a wrapper script
does at run time.
"""

import os
import re
import tomllib


def mask_source(content, comments=True, strings=True):
    """Blank out comment and/or string-literal bodies, keeping offsets.

    Every search in this file is a grep over source text, which
    counts a commented-out call as a call. That is not a cosmetic
    inaccuracy: a file whose logging.basicConfig() is commented out
    -- the state of anything somebody was debugging -- passed the
    console-logging check, which exists to catch precisely that, and
    a docstring saying a module deliberately does not call
    setup_console() made it a caller and failed it.

    Bodies are replaced space for space and newlines are kept, so
    every offset and line number in the result still addresses the
    same character of the original. Callers can therefore match
    structure against the masked text and still read audit-ok
    markers, which live in comments, out of the original.
    """
    out = list(content)
    index, length = 0, len(content)
    while index < length:
        char = content[index]
        if char == '#':
            # Recognised even when it is not being blanked: an
            # apostrophe in a comment ("don't") would otherwise open a
            # string literal and swallow the rest of the file.
            while index < length and content[index] != '\n':
                if comments:
                    out[index] = ' '
                index += 1
            continue
        if char not in '\'"':
            index += 1
            continue

        # The quote characters are left in place. Only the body is
        # blanked, so a triple-quoted block holding a code sample
        # becomes blank lines rather than disappearing and pulling
        # the lines after it up into a new position.
        quote = char * 3 if content.startswith(char * 3, index) else char
        index += len(quote)
        while index < length:
            if content[index] == '\\':
                for offset in (0, 1):
                    if strings and index + offset < length and (
                            content[index + offset] != '\n'):
                        out[index + offset] = ' '
                index += 2
                continue
            if content.startswith(quote, index):
                index += len(quote)
                break
            if strings and content[index] != '\n':
                out[index] = ' '
            index += 1
    return ''.join(out)


def mask_comments_and_strings(content):
    """The view structure is matched against: code only."""
    return mask_source(content)


def mask_strings(content):
    """The view an audit-ok marker is read from: code and comments.

    A marker is a comment, but the membership test that looked for
    one ran over the whole file, so `DOC = "audit-ok:
    header-sanitization"` -- an ordinary string constant, on the line
    above a class -- silently exempted a request handler from the
    CWE-113 check. That is the mirror of the false pass
    mask_comments_and_strings() was written to close: this half fixed
    "a docstring made it a caller" and left "a docstring made it
    exempt".
    """
    return mask_source(content, comments=False)


def console_entry_point_files(repo_path):
    """Return (resolved files, unresolved targets) for the entry points.

    The spec is about how a *CLI entry point* uses setup_console(),
    not about every module that logs. Anchoring on the declared
    console scripts is what makes that distinction mechanical:
    occystrap calls logs.setup_console(__name__) at the top of all
    24 of its modules, and only occystrap/main.py is the entry point
    any of it is reached through.

    All three spellings of the declaration are read. [project.scripts]
    is what the fleet uses today, but a gui-scripts or an explicit
    entry-points.console_scripts table names an entry point just as
    much, and reading only the first reported those packages as
    having none at all -- a clean bill for a file nobody looked at.

    Anything declared that cannot be turned into a file is returned
    rather than dropped: a repository laying its packages out under
    lib/ declares entry points and resolves none of them, and
    returning only the files said it had declared none. A malformed
    table and a non-string target reach the same false statement by
    another door, so they come back the same way.
    """
    pyproject = os.path.join(repo_path, 'pyproject.toml')
    if not os.path.exists(pyproject):
        return [], []
    try:
        with open(pyproject, 'rb') as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return [], []

    # Guarded at every level rather than assumed, the way
    # renovate_enables_pre_commit does. A pyproject.toml declaring
    # scripts as a string is malformed, but the AttributeError it
    # raised propagated out of run_checks and took every other
    # check's result for that repository with it.
    unresolved = []

    def table(value, *keys):
        # Named at the level that is wrong, not at the level asked
        # for: a string where [project.entry-points] should be takes
        # console_scripts down with it, and reporting the leaf would
        # point at a table the file does not contain.
        seen = []
        for key in keys:
            if not isinstance(value, dict):
                break
            seen.append(key)
            value = value.get(key)
        if value is None:
            return {}
        if isinstance(value, dict) and len(seen) == len(keys):
            return value
        problem = (
            f'[{".".join(seen)}] is a {type(value).__name__}, '
            f'not a table')
        if problem not in unresolved:
            unresolved.append(problem)
        return {}

    project = data if isinstance(data, dict) else {}
    targets = []
    for name in ('scripts', 'gui-scripts'):
        targets += list(table(project, 'project', name).values())
    targets += list(
        table(project, 'project', 'entry-points',
              'console_scripts').values())

    files = []
    for target in targets:
        if not isinstance(target, str) or not target.split(
                ':', 1)[0].strip():
            unresolved.append(f'{target!r} does not name a module')
            continue
        module = target.split(':', 1)[0].strip()
        relative = module.replace('.', os.sep)
        for candidate in (
            f'{relative}.py',
            os.path.join(relative, '__init__.py'),
            os.path.join('src', f'{relative}.py'),
            os.path.join('src', relative, '__init__.py'),
        ):
            if os.path.exists(os.path.join(repo_path, candidate)):
                if candidate not in files:
                    files.append(candidate)
                break
        else:
            if module not in unresolved:
                unresolved.append(module)
    return sorted(files), sorted(unresolved)


def sets_own_logger_propagate(code):
    """Does this file stop *its own* logger propagating to root?

    A bare search for .propagate = False is satisfied by a line
    silencing an unrelated third-party logger, which is the precise
    case this rule exists to catch: the entry point still emits every
    one of its own INFO lines twice. So the receiver is matched
    instead -- the name bound to setup_console()'s return, or
    getLogger() called with the same argument setup_console() was
    given, which are the two spellings the standard uses.

    Expects the masked code rather than the file. Every call is
    considered, not the first: an entry point configuring another
    package's logger before its own had its receivers taken from
    that one, so a correct LOG.propagate = False did not match and
    the file was failed for a line it had.
    """
    setups = list(re.finditer(
        r'(?:((?:\w+\.)*\w+)\s*=\s*)?'
        r'(?:\w+\.)*(?<!def )setup_console\s*\(([^)]*)\)',
        code,
    ))
    if not setups:
        return False

    # A call given __name__ is the file configuring itself, so when
    # there is one it is the only one that decides this. Accepting
    # any receiver would let silencing somebody else's logger stand
    # in for silencing your own, which is the defect this catches.
    own = [s for s in setups if s.group(2).strip() == '__name__']
    receivers = []
    for setup in own or setups:
        # Dotted targets included: self.LOG = setup_console(...) is
        # written self.LOG.propagate = False, and matching only the
        # bare name meant the lookbehind below rejected it.
        if setup.group(1):
            receivers.append(re.escape(setup.group(1)))
        argument = setup.group(2).strip()
        if not argument:
            continue
        get_logger = (
            r'(?:\w+\.)*getLogger\s*\(\s*'
            + re.escape(argument) + r'\s*\)'
        )
        receivers.append(get_logger)
        # And anything bound to that logger, for the entry point
        # that fetches it by name rather than keeping what
        # setup_console() handed back.
        receivers += [
            re.escape(match.group(1))
            for match in re.finditer(r'(\w+)\s*=\s*' + get_logger, code)
        ]
    # The lookbehind is what makes the receiver the *whole* name.
    # Without it re.escape('LOG') matches inside URLLIB_LOG, and an
    # entry point silencing urllib3 was read as having silenced
    # itself -- a pass for exactly the defect this exists to catch.
    # It also rejects wrapper.LOG, an attribute of something else,
    # and is harmless for the getLogger() alternative, which already
    # begins with its own optional dotted prefix.
    return any(
        re.search(
            r'(?<![\w.])' + receiver + r'\s*\.propagate\s*=\s*False',
            code,
        )
        for receiver in receivers
    )


# Every one of these inherits the unsanitized send_header() from
# http.server, so subclassing any of them is the exposure. Naming only
# the root class read "class Handler(SimpleHTTPRequestHandler)" as
# having no raw HTTP server in it at all.
HTTP_HANDLER_BASES = (
    'BaseHTTPRequestHandler',
    'SimpleHTTPRequestHandler',
    'CGIHTTPRequestHandler',
)


def parse_class_statements(content):
    """Yield (name, bases, start, end) for each class statement.

    The base list is closed by counting parentheses rather than with
    \\(([^)]*)\\), which stops at the first ")" and so read nothing at
    all from "class H(make_base(), BaseHTTPRequestHandler):". Skipping
    a class is a clean bill for a handler nobody looked at, so a base
    list the walk cannot close is yielded with bases of None for the
    caller to report rather than dropped here.

    Classes with no base list are not yielded: they inherit nothing,
    so they cannot be a request handler.

    A PEP 695 type parameter list may sit between the name and the
    bases, and a bound in one can itself hold brackets, so it is
    walked the same way rather than matched -- "class H[T](Base)"
    otherwise looked like a class with no base list at all, which is
    the silent skip this function exists to avoid.
    """
    # Comments and string bodies are blanked first, so a ")" in
    # either no longer closes the walk early and a code sample in a
    # docstring is no longer a class. Masking preserves offsets, so
    # the positions yielded here still address `content`.
    code = mask_comments_and_strings(content)
    for match in re.finditer(
        r'^[ \t]*class\s+(\w+)\s*', code, re.MULTILINE,
    ):
        index = match.end()
        if index < len(code) and code[index] == '[':
            depth, index = 1, index + 1
            while index < len(code) and depth:
                if code[index] == '[':
                    depth += 1
                elif code[index] == ']':
                    depth -= 1
                index += 1
            if depth:
                yield match.group(1), None, match.start(), len(code)
                continue
            while index < len(code) and code[index] in ' \t':
                index += 1
        if index >= len(code) or code[index] != '(':
            continue
        opens = index
        depth, index = 1, index + 1
        while index < len(code) and depth:
            if code[index] == '(':
                depth += 1
            elif code[index] == ')':
                depth -= 1
            index += 1
        if depth:
            yield match.group(1), None, match.start(), len(code)
            continue
        yield (match.group(1), code[opens + 1:index - 1],
               match.start(), index)


def handler_base_names(content):
    """Local names that refer to an http.server request handler base.

    The base list is compared against names, not searched for
    substrings, so an alias has to be resolved or the class is
    dropped: "from http.server import BaseHTTPRequestHandler as BHR"
    followed by "class Handler(BHR)" was reported as a repository
    with no raw HTTP server in it, which is the false clean bill this
    check exists to refuse. An alias may sit on a continuation line
    of either kind, parenthesised or backslashed, so the import
    statement is read whole rather than a line at a time.
    """
    names = set(HTTP_HANDLER_BASES)
    for match in re.finditer(
        # Both continuation forms, because an import list long enough
        # to need one is exactly where an alias hides: the capture
        # stopped at the newline, so the parenthesised spelling
        # resolved nothing and the class using the alias was dropped
        # without a word.
        r'^[ \t]*(?:from\s+[\w.]+\s+)?import\s+'
        r'(\([^)]*\)|(?:[^\n\\]|\\\n)+)',
        mask_comments_and_strings(content), re.MULTILINE,
    ):
        # The backslash is punctuation joining the statement, just
        # as the parens are. Left in place it became a fourth token
        # and the "name as alias" shape stopped matching, so the
        # capture spanned the continuation and resolved nothing.
        for clause in match.group(1).replace('(', ' ').replace(
                ')', ' ').replace('\\', ' ').split(','):
            parts = clause.split()
            if len(parts) == 3 and parts[1] == 'as' and (
                    parts[0].split('.')[-1] in HTTP_HANDLER_BASES):
                names.add(parts[2])
    return names


def python_specifier_clauses(specifier):
    """Reduce a PEP 440 specifier to a comparable set of clauses.

    Whitespace, clause order and a trailing ".0" are spelling rather
    than meaning: ">= 3.8", ">=3.8" and ">=3.8.0" are one floor said
    three ways. Comparing the raw strings filed a fleet issue whose
    only remedy was a cosmetic edit, and asserted one of the two was
    stale when neither was.
    """
    clauses = set()
    for clause in specifier.split(','):
        clause = re.sub(r'\s+', '', clause)
        if clause:
            clauses.add(re.sub(r'(\.0)+$', '', clause))
    return clauses


#: Directories whose Python is not this project's source. build/ and
#: dist/ hold copies of it, and a copy is what makes a dead import look
#: alive: an import deleted from the tree survives in the last build
#: until somebody runs `git clean`. .tox, .venv and node_modules hold
#: everybody else's source, by a factor of forty-odd more files than
#: the project wrote -- docs/audits/unused-declared-dependency.md
#: counts shakenfist's -- and cover/ holds annotated listings that read
#: as source but are output.
NON_SOURCE_DIRS = frozenset((
    '.git', '.tox', '.venv', 'venv', '.eggs', 'build', 'dist', 'cover',
    'node_modules', '__pycache__',
))


#: An import statement at the start of a line: `import a.b`,
#: `import a, b` or `from a.b import c`. Anchored at the left margin
#: only in the sense that leading whitespace is allowed -- an import
#: inside a function or a try block is still an import.
IMPORT_RE = re.compile(
    r'''^[ \t]*
        (?:from[ \t]+(?P<from>[A-Za-z0-9_.]+)[ \t]+import
          |import[ \t]+(?P<import>[A-Za-z0-9_.]+
                        (?:[ \t]*,[ \t]*[A-Za-z0-9_.]+)*))
    ''',
    re.MULTILINE | re.VERBOSE,
)


def python_source_files(repo_path):
    """The Python files belonging to the checkout's own package.

    NON_SOURCE_DIRS is pruned rather than filtered afterwards, so the
    walk does not descend into a virtualenv at all.

    A subdirectory carrying its own pyproject.toml is pruned too. It is
    a separate distribution that happens to live in the same
    repository, and it declares its own dependencies: reading its
    imports against the root manifest asks whether one package
    declares another package's requirements, which is not a question
    with a right answer. kerbside is why -- its tempest-plugin/ imports
    oslo_config and declares oslo.config in tempest-plugin/
    pyproject.toml, and reporting that as an undeclared dependency of
    kerbside itself was a finding whose only honest remedy was to
    ignore it.

    The root is never pruned: the walk only tests subdirectories, so a
    checkout is always read against its own manifest.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [
            d for d in dirnames
            if d not in NON_SOURCE_DIRS
            and not d.endswith('.egg-info')
            and not os.path.exists(
                os.path.join(dirpath, d, 'pyproject.toml'))
        ]
        for filename in filenames:
            if filename.endswith('.py'):
                found.append(os.path.join(dirpath, filename))
    return sorted(found)


def imported_top_level_modules(sources):
    """The set of top-level module names imported in a list of files.

    Lowercased, because the question asked of this set is whether a
    distribution's module is among them and distribution names are
    compared case-insensitively everywhere else in this file.

    Comments and string literals are masked first. A commented-out
    import is the exact shape the unused-dependency criterion exists
    to find, and counting one as a use would report the dependency
    that is most certainly dead as the one still in use.

    Relative imports (`from . import thing`) contribute nothing: their
    first segment is empty, and they name this project rather than a
    dependency of it.

    `sources` is a python_source_files() list rather than a checkout to
    walk. Every caller already makes that call, to test whether there
    is any source at all before asking what it imports, and walking
    shakenfist's tree a second time to answer the same question twice
    is work for nothing. Taking the list is also the only way to be
    sure the two answers describe the same set of files.
    """
    modules = set()
    for path in sources:
        with open(path, 'r', errors='replace') as f:
            code = mask_comments_and_strings(f.read())
        for match in IMPORT_RE.finditer(code):
            targets = match.group('from') or match.group('import')
            for target in targets.split(','):
                head = target.strip().split('.')[0]
                if head:
                    modules.add(head.lower())
    return modules
