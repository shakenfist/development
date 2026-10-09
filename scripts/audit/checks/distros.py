"""The criterion about distribution releases we have retired.

An operating system release stops receiving security updates on a
published date, and everything still built on it inherits that. The
fleet notices slowly, because nothing breaks: a CI job pinned to a
retired runner image keeps passing, and a container built `FROM` a
retired base keeps shipping. The only signal is somebody remembering.

`EOL_RELEASES` is that memory, written down. A release is added to it
when it goes end of life, and from the next audit run every
repository still naming it fails this criterion until it moves. The
table is the whole of the policy: adding Ubuntu 22.04 in 2027 is one
entry, not a new check.

Two surfaces are measured, because those are the two ways the fleet
depends on a release mechanically rather than by mentioning it:

  * runner labels, which choose the operating system a CI job runs on;
  * container image references, in a workflow's `container:`/`image:`
    keys and in the `FROM` lines of a Dockerfile.

Guest images a test boots, cached disk images a job copies, and the
upstream job names a matrix names are all out of scope -- see
docs/audits/eol-distro.md for why, and what covers them instead.

`eol-producers` is the other half of the same table, asked of the
other end of the dependency. `eol-distro` asks whether a repository
*names* a banned label; `eol-producers` asks whether it *offers* one.
A consumer naming `debian-12` mostly inherits what the producer
decided, so the two are different defects with different owners: the
consumer moves its workflow, and the producer stops baking the image.
Both read the same `EOL_RELEASES` table, because a second copy of the
banned list is the defect `eol-distro` was written to avoid.
"""

import ast
import collections
import datetime
import os
import re

from audit.check import Check
from audit.files import WALK_SKIP
from audit.text.workflows import (
    is_runner_label_value, strip_trailing_comment,
)


#: One distribution release the fleet has retired.
#:
#: `distro` is the release's own container repository name, and is what
#: makes `version` safe to match on: a bare "12" is meaningful in
#: `debian:12` and meaningless in `rust:1.12`, so the numeric form is
#: only read when the image *is* the distribution. `codenames` carry no
#: such ambiguity and are matched in any image's tag, which is what
#: catches the derived images the fleet actually builds on
#: (`rust:1.97-bookworm`, `python:3.13-bookworm`).
EolRelease = collections.namedtuple(
    'EolRelease',
    'name distro version codenames eol replacement runner_labels',
)


#: Every release the fleet has retired, oldest first.
#:
#: `runner_labels` are the labels the CI conductor advertises for that
#: release -- the source of truth is IMAGE_BUILDS in
#: shakenfist/private-ci's conductor/imagebuilder.py, and CI_IMAGES in
#: its conductor/provisioner.py; see PRODUCER_DEFINITIONS below, which
#: eol-producers reads -- plus the GitHub-hosted spelling where one
#: exists. Every variant needs listing rather than being derived
#: from `distro` and `version`, because the variants are named by
#: hand there too and a derived pattern would either miss
#: `debian-gnome-12` or invent labels that do not exist.
#:
#: `debian-gnome-12` now has a `debian-gnome-13` counterpart in the
#: conductor, built from the `debian-gnome:13` guest image
#: shakenfist/images has published since August 2026, and the
#: `debian-gnome-12` entry it replaced has been retired -- so a
#: finding naming it is a request to stop naming the old label, not a
#: request to add a successor that does not exist.
#:
#: It stays listed because this tuple is the registry of what a
#: workflow may not ask for rather than an inventory of what the
#: conductor builds. The entry is what lets the criterion report a
#: workflow still naming a label nothing can serve, so dropping each
#: release as its producer goes would retire the check along with the
#: image.
EOL_RELEASES = (
    EolRelease(
        name='Ubuntu 20.04 LTS (focal)',
        distro='ubuntu',
        version='20.04',
        codenames=('focal',),
        eol='2025-05-31',
        replacement='ubuntu:24.04 (noble)',
        runner_labels=('ubuntu-20.04', 'ubuntu-2004'),
    ),
    EolRelease(
        name='Debian 12 (bookworm)',
        distro='debian',
        version='12',
        codenames=('bookworm',),
        eol='2026-06-10',
        replacement='the debian-13 runner labels, or a debian:13 '
                    '(trixie) image',
        runner_labels=('debian-12', 'debian-12-docker',
                       'debian-gnome-12'),
    ),
    EolRelease(
        name='Debian 11 (bullseye)',
        distro='debian',
        version='11',
        codenames=('bullseye',),
        eol='2026-08-31',
        replacement='the debian-13 runner labels, or a debian:13 '
                    '(trixie) image',
        runner_labels=('debian-11', 'debian-11-docker'),
    ),
)


def retired_releases(today=None):
    """The listed releases whose end-of-life date has actually passed.

    The date in the table is a fact about the release rather than a
    switch, so an entry can be written before it takes effect -- which
    is the normal way to plan a migration, and the spec invites it by
    promising that adding a release is one entry and one row. Without
    this, an entry added a quarter early fails every repository in the
    fleet at once, with an issue whose own text says the release goes
    end of life in the future.
    """
    if today is None:
        today = datetime.date.today()
    return [
        release for release in EOL_RELEASES
        if datetime.date.fromisoformat(release.eol) <= today
    ]


def _runner_label_re(releases):
    """A regex matching any retired runner label, as a whole label.

    Longest first, so `debian-12-docker` is recognised as itself rather
    than as `debian-12` with a suffix. The lookarounds are the load
    bearing part: a label is a whole token, so `debian-12` must not
    match inside `debian-12-genericcloud-amd64.qcow2` (a guest image
    instar boots), `kolla-ansible-master-debian-12` (an upstream job
    name kerbside dispatches) or `debian-12-gnome-agents` (a cached
    disk). `\\b` would match all three, because a hyphen is not a word
    character.
    """
    labels = sorted(
        {label for release in releases for label in release.runner_labels},
        key=lambda label: (-len(label), label),
    )
    return re.compile(
        r'(?<![\w.-])(' + '|'.join(re.escape(x) for x in labels)
        + r')(?![\w.-])'
    )


RUNNER_LABEL_RE = _runner_label_re(EOL_RELEASES)


#: Which release a retired runner label belongs to.
RELEASE_BY_LABEL = {
    label: release
    for release in EOL_RELEASES
    for label in release.runner_labels
}


#: A container image named as the value of a key. `container:` in its
#: scalar form and `image:` in both the nested form under `container:`
#: and as a matrix entry are the three spellings the fleet uses; a
#: `FROM` line is the Dockerfile equivalent. Anchoring on the key is
#: what keeps `"${setup} ubuntu-2004 /srv/ci/ubuntu:20.04 --shared"`
#: -- a shell argument in shakenfist/actions naming a Shaken Fist guest
#: image -- out of the findings.
#:
#: The value is not anchored to the end of the line. It was, and an
#: `image: debian:12  # renovate pin` matched nothing at all rather
#: than matching the image and ignoring the comment -- so a pinned
#: line carrying the explanation of its own pin, which is exactly the
#: kind most likely to be stale, was the one the criterion could not
#: see. Runner labels on a commented line were caught throughout, so
#: the two surfaces disagreed about the same file. Callers strip the
#: comment with strip_trailing_comment() before matching.
IMAGE_KEY_RE = re.compile(
    r'^\s*(?:-\s+)?(?:container|image):\s*(\S+)'
)


#: A Dockerfile `FROM`, skipping any `--flag` or `--flag=value` that
#: precedes the image. `FROM --platform=$BUILDPLATFORM debian:12` is
#: how a multi-arch build names its base, and capturing the first
#: token alone captured the flag: the platform string holds no colon,
#: so the reference read as untagged and the retired base passed.
DOCKERFILE_FROM_RE = re.compile(
    r'^\s*FROM\s+(?:--\S+\s+)*(\S+)', re.IGNORECASE
)


#: Marker acknowledging a deliberate exception, placed on the offending
#: line or the line immediately above it, in the same shape as the
#: runner criteria's markers. It exists for the repositories whose
#: subject matter *is* old distributions: instar's functional tests
#: boot a `debian:12` container on purpose, because measuring what a
#: bookworm filesystem looks like is the job.
EXCEPTION_RE = re.compile(r'audit-ok:\s*eol-distro')


def is_dockerfile(name):
    """Does this basename name a container build file?

    `Dockerfile`, `Dockerfile.ci`, `ci.dockerfile` and the Podman
    spelling `Containerfile` all build an image, and the fleet writes
    at least the first two.
    """
    return (
        name == 'Dockerfile'
        or name.startswith('Dockerfile.')
        or name.endswith('.dockerfile')
        or name == 'Containerfile'
        or name.startswith('Containerfile.')
    )


def dockerfiles(repo_path):
    """Repository-relative paths of every container build file."""
    found = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP)
        for name in sorted(filenames):
            if is_dockerfile(name):
                found.append(
                    os.path.relpath(
                        os.path.join(dirpath, name), repo_path
                    ).replace(os.sep, '/')
                )
    return sorted(found)


def image_release(reference):
    """Which retired release an image reference names, or None.

    The reference is split the way a container runtime splits it: the
    digest is discarded, the tag is what follows the last colon, and
    the repository is the last path component before it, so a registry
    prefix does not change the answer. A reference with no tag is not
    judged -- an implicit `latest` says nothing about which release it
    resolves to today.

    The tag is then read as hyphen-separated tokens, which is how the
    upstream images spell their variants: `bookworm-slim`,
    `slim-bookworm` and `1.97-bookworm` are all bookworm, and all three
    are in the fleet.
    """
    reference = reference.strip().strip('"').strip("'")
    # An Actions expression or a shell variable: what it resolves to is
    # not decidable here. '${{' is not tested separately because it
    # cannot appear without the '$'.
    if not reference or '$' in reference:
        return None

    # A digest pins bytes rather than a tag, and carries its own colon.
    reference = reference.split('@', 1)[0]
    if ':' not in reference:
        return None
    name, _, tag = reference.rpartition(':')
    if '/' in tag:
        # The colon belonged to a registry port, not a tag.
        return None
    repository = name.rsplit('/', 1)[-1]
    tokens = tag.split('-')

    for release in EOL_RELEASES:
        if any(codename in tokens for codename in release.codenames):
            return release
        if repository != release.distro:
            continue
        head = tokens[0]
        if head == release.version or head.startswith(release.version + '.'):
            return release
    return None


def is_excepted(lines, index):
    """Is this line marked as a deliberate exception?"""
    if EXCEPTION_RE.search(lines[index]):
        return True
    return index > 0 and EXCEPTION_RE.search(lines[index - 1])


def is_comment(line):
    """A whole-line comment, in YAML and in a Dockerfile alike.

    Skipped because the fleet explains its runner choices in prose
    directly above them, quoting the very label being discussed --
    kerbside's release.yml carries "`[self-hosted, debian-12, static]`
    matched no runner and queued" as a comment, which describes a
    mistake rather than making it.
    """
    return line.lstrip().startswith('#')


def scan_workflow(path, content):
    """Findings in one workflow file, as (location, label, release)."""
    found = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if is_comment(line) or is_excepted(lines, i):
            continue

        for match in RUNNER_LABEL_RE.finditer(line):
            if not is_runner_label_value(line, match.start(), match.end()):
                continue
            label = match.group(1)
            found.append(
                (f'{path}:{i + 1}', label, RELEASE_BY_LABEL[label])
            )

        key = IMAGE_KEY_RE.match(strip_trailing_comment(line))
        if key:
            release = image_release(key.group(1))
            if release is not None:
                found.append(
                    (f'{path}:{i + 1}', key.group(1).strip('"\''), release)
                )
    return found


def scan_dockerfile(path, content):
    """Findings in one container build file."""
    found = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if is_comment(line) or is_excepted(lines, i):
            continue
        match = DOCKERFILE_FROM_RE.match(line)
        if not match:
            continue
        release = image_release(match.group(1))
        if release is not None:
            found.append((f'{path}:{i + 1}', match.group(1), release))
    return found


def workflow_templates(repo_path):
    """Repository-relative paths of the workflow templates, if any.

    A template under `templates/` is not a workflow this repository
    runs, so nothing else here reads it -- and it is copied verbatim
    into ten other repositories, which makes a retired label in one a
    finding filed against every project that adopted it while the
    source of it goes on passing. shakenfist/development is the only
    repository with the directory, and `.github/actionlint.yaml`
    already calls it "the fleet's source of truth for files nothing
    else checks before they are copied into ten repositories".

    Only YAML is returned. The templates' README files discuss labels
    in prose, and prose is out of scope for the same reason a plan
    describing a 2025 bookworm build is.
    """
    root = os.path.join(repo_path, 'templates')
    if not os.path.isdir(root):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP)
        for name in sorted(filenames):
            if name.endswith(('.yml', '.yaml')):
                found.append(
                    os.path.relpath(
                        os.path.join(dirpath, name), repo_path
                    ).replace(os.sep, '/')
                )
    return sorted(found)


def scan(repo, build_files=None, templates=None, today=None):
    """Every reference to a retired release in a checkout.

    `build_files` and `templates` are passed in by run(), which has
    already walked for them to decide whether the criterion applies.
    Walking twice was free but untidy, and Repo caches its reads for
    exactly this reason.
    """
    if build_files is None:
        build_files = dockerfiles(repo.path)
    if templates is None:
        templates = workflow_templates(repo.path)

    found = []
    for name in sorted(repo.workflows()):
        content = repo.workflow(name)
        if content:
            found.extend(
                scan_workflow(f'.github/workflows/{name}', content)
            )
    for path in templates:
        content = repo.read(path)
        if content:
            found.extend(scan_workflow(path, content))
    for path in build_files:
        content = repo.read(path)
        if content:
            found.extend(scan_dockerfile(path, content))

    # A release listed ahead of its date is matched, then dropped: the
    # labels are recognised so that the table stays one place, and the
    # finding is withheld until the date arrives.
    retired = set(retired_releases(today))
    return [finding for finding in found if finding[2] in retired]


def release_guidance(found):
    """One sentence per retired release actually referenced.

    Ordered by EOL_RELEASES rather than by where the hits landed, so
    two repositories failing on the same releases produce the same
    sentence and a diff of the compliance page is about the findings.
    """
    hit = {release.name for _, _, release in found}
    return '. '.join(
        f'{release.name} reached end of life on {release.eol}; '
        f'use {release.replacement}'
        for release in EOL_RELEASES if release.name in hit
    )


#: The runner-label producer definitions the fleet owns, as
#: (repository-relative path, module-level name) pairs.
#:
#: Both are in shakenfist/private-ci, which is the only producer of
#: runner labels in the organisation: `images` publishes guest
#: images, which is a different question, and the static fleet in
#: another organisation advertises no release label at all.
#:
#: They are named rather than discovered. A producer is a deliberate
#: thing -- two tables, in one repository, that decide what the whole
#: fleet can boot -- and a walk looking for lists of dicts with a
#: 'label' key would read any such list, including the ones that have
#: nothing to do with runners. Adding the third producer is a line
#: here.
PRODUCER_DEFINITIONS = (
    ('conductor/imagebuilder.py', 'IMAGE_BUILDS'),
    ('conductor/provisioner.py', 'CI_IMAGES'),
)


#: The key in a producer entry that carries the runner label.
#:
#: The entries also carry a 'name' and an upstream image reference,
#: and those are not this criterion: the upstream image is what the
#: label is built *from*, so reading it would report `debian:12` as a
#: finding against the one repository whose job is to turn it into
#: something bootable. What this criterion measures is the label the
#: fleet is offered.
PRODUCER_LABEL_KEY = 'label'


class ProducerParseError(Exception):
    """A producer definition is present and could not be read.

    Raised rather than returned, so `registry.run_check()` reports it
    as `error`. That is the distinction the verdicts cannot carry: a
    `pass` would report a producer nobody actually read as clean,
    which is the vacuous pass this criterion exists to avoid, and a
    `fail` would file an issue under the bot's identity against
    private-ci for what is a bug in this check -- the shape of the
    table changed and the parse here did not keep up. `error` files
    nothing, closes nothing, and fails the audit leg.
    """


def producer_definitions(repo):
    """The producer definitions this checkout actually carries."""
    return [(path, name) for path, name in PRODUCER_DEFINITIONS
            if repo.exists(path)]


#: Methods whose call on a producer table, or on an entry in it,
#: changes what the table offers after the literal this reads.
PRODUCER_MUTATORS = frozenset((
    'append', 'extend', 'insert', 'update', 'setdefault',
    'pop', 'popitem', 'remove', 'clear',
    '__setitem__', '__delitem__', '__iadd__',
))


def _root_name(node):
    """The name an attribute or subscript chain starts from, or None."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _import_time_nodes(node):
    """A node and every node beneath it that runs at import time.

    A function or lambda is skipped whole: its body runs only when it
    is called, which a parse cannot follow. A class body runs at
    import and is walked.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.Lambda)):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _import_time_nodes(child)


def _changes(statement, name):
    """The first import-time node in a statement that changes `name`.

    A rebinding, an augmented assignment, a `del`, a store into the
    table or into one of its entries, or a call to one of
    PRODUCER_MUTATORS on either. Reading the table -- the provisioner
    builds ALL_CI_IMAGE_LABELS from CI_IMAGES at module level -- is
    not a change.
    """
    for node in _import_time_nodes(statement):
        if (isinstance(node, (ast.Name, ast.Attribute, ast.Subscript))
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and _root_name(node) == name):
            return node
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in PRODUCER_MUTATORS
                and _root_name(node.func.value) == name):
            return node
    return None


def _assigned_value(tree, name, path):
    """The value assigned to a module-level name, or None.

    Only module level, and only a plain assignment: a label list built
    at import time by a function call or a comprehension is not a
    literal this can read, and pretending otherwise would return an
    empty list that looks like a clean producer.

    The first assignment is the one read, so a module-level change
    made directly through the table's name after it -- `+=`,
    `.append()`, a second assignment, `NAME[0]['label'] = ...` --
    raises ProducerParseError. Those all offer labels the literal does
    not show, and a parse that read the literal and passed would be
    reporting a table it had only half read.

    Out of reach, and passed on the literal alone: a change made
    through another name -- an alias (`B = NAME; B.append(...)`), a
    loop variable (`for b in NAME: b['label'] = ...`), or
    `list.append(NAME, ...)` -- and anything in a function, including
    its decorators and default arguments, which this does not walk.
    Following names through assignments is data-flow analysis, and
    the tables this reads are plain literals with nothing done to
    them afterwards.
    """
    value = None
    for statement in tree.body:
        if value is None:
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif (isinstance(statement, ast.AnnAssign)
                    and statement.value is not None):
                targets = [statement.target]
            else:
                continue
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in targets):
                value = statement.value
            continue

        changed = _changes(statement, name)
        if changed is not None:
            raise ProducerParseError(
                f'{path}:{changed.lineno} changes {name} after the '
                f'assignment this reads, so the literal is not the '
                f'whole table')
    return value


def _entry_label(element, name, path):
    """The literal label one table entry offers, and its line.

    All or nothing. An entry this cannot read raises rather than
    being skipped: skipping it would pass a table on the entries that
    happened to be literal, which is the vacuous pass of an unread
    producer applied to one entry instead of all of them. Unreadable
    covers an entry that is not a dict literal (`dict(label=...)`, a
    helper call, a `*spread`), a key that is not a literal (including
    a `**spread`, which could carry or override the label), and a
    label that is not a string literal (a constant, an f-string).
    """
    where = f'{path}:{element.lineno}'
    if not isinstance(element, ast.Dict):
        raise ProducerParseError(
            f'{where}: an entry in {name} is not a dict literal')
    for key in element.keys:
        if not isinstance(key, ast.Constant):
            shown = '**' if key is None else ast.unparse(key)
            raise ProducerParseError(
                f'{where}: an entry in {name} has a key that is not a '
                f'literal ({shown}), so its label cannot be read')

    labels = [entry for key, entry in zip(element.keys, element.values)
              if key.value == PRODUCER_LABEL_KEY]
    if len(labels) != 1:
        raise ProducerParseError(
            f'{where}: an entry in {name} has {len(labels)} '
            f'{PRODUCER_LABEL_KEY!r} keys, not one')
    label = labels[0]
    if not (isinstance(label, ast.Constant)
            and isinstance(label.value, str)):
        raise ProducerParseError(
            f'{where}: an entry in {name} has a {PRODUCER_LABEL_KEY!r} '
            f'that is not a string literal')
    return label.value, label.lineno


def definition_labels(source, name, path='<producer>'):
    """The labels one producer definition offers, with line numbers.

    Parsed rather than grepped, and that is the whole point of this
    function. Both producer modules discuss retired labels in their
    comments: at the time of writing, `imagebuilder.py` carries
    "ubuntu-2004 was dropped 2026-07-11" above the entries that
    replaced it, and the plan for the step that reworded the
    STALE_LABEL_SECONDS comment forbade it from quoting a retired
    label precisely so that a grep-shaped gate would not fire forever
    after. A grep cannot tell a live entry from its own obituary; the
    abstract syntax tree has no comments in it at all.

    Raises ProducerParseError for a definition that is there but not
    in the shape this understands, rather than returning nothing: the
    two answers are "this producer offers no banned label" and "this
    producer was not read", and they must not render the same.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ProducerParseError(
            f'{path} is not parseable Python ({e})')

    value = _assigned_value(tree, name, path)
    if value is None:
        raise ProducerParseError(
            f'{path} has no module-level {name} assignment')
    if not isinstance(value, (ast.List, ast.Tuple)):
        raise ProducerParseError(
            f'{path}:{name} is not a list of entries')

    return [_entry_label(element, name, path) for element in value.elts]


def retired_labels(today=None):
    """Which retired release each banned runner label belongs to.

    RELEASE_BY_LABEL covers every entry in the table, including one
    written ahead of its date, and scan() filters those out after
    matching. This is the same filter applied before matching, which
    is all a producer needs: there is no false positive to recognise
    and then withhold.
    """
    return {
        label: release
        for release in retired_releases(today)
        for label in release.runner_labels
    }


def scan_producers(repo, definitions=None, today=None):
    """Every banned runner label this checkout's producers offer.

    Returns (location, label, release) triples, the same shape scan()
    returns, so release_guidance() reads either.

    In definition order -- each producer in the order
    PRODUCER_DEFINITIONS lists it, each label in the order its table
    offers it -- rather than sorted. The fix is read down the file,
    and sorting the locations as strings puts line 123 above line 62,
    which is how eol-distro's list reads today.
    """
    if definitions is None:
        definitions = producer_definitions(repo)
    banned = retired_labels(today)

    found = []
    for path, name in definitions:
        source = repo.read(path)
        if source is None:
            raise ProducerParseError(
                f'{path} exists and could not be read')
        for label, line in definition_labels(source, name, path=path):
            if label in banned:
                found.append((f'{path}:{line}', label, banned[label]))
    return found


class EolDistro(Check):
    id = 'eol-distro'
    spec = 'docs/audits/eol-distro.md'
    template = None
    issue_title = 'End-of-life distributions'

    def run(self, repo):
        """Check nothing is built on a distribution release we retired.

        Runner labels and container image references only, in this
        repository's own workflows, in the workflow templates the
        fleet copies from, and in its container build files. All
        three choose an operating system something then runs on,
        which is what makes them mechanically checkable and worth
        failing over; a release merely named in prose, booted as test
        input, or dispatched to an upstream job by name is not this
        criterion, and matching whole tokens is what keeps those three
        out.

        A repository whose subject matter is old distributions marks
        the line 'audit-ok: eol-distro' with the reason, on the line
        itself or the one above.

        The full list of locations travels in 'findings' rather than in
        'details': the fix is per line, so a truncated list leaves
        somebody re-running the audit to find out what the issue meant,
        while 'details' is printed into a table cell on the compliance
        page and has to stay one.
        """
        build_files = dockerfiles(repo.path)
        templates = workflow_templates(repo.path)
        if not repo.workflows() and not build_files and not templates:
            return self.skip(
                'No workflows, workflow templates or container build '
                'files to read'
            )

        found = scan(repo, build_files=build_files, templates=templates)
        if not found:
            return self.ok(
                f'No workflow runner label or container image names '
                f'any of the {len(retired_releases())} retired '
                f'distribution releases')

        locations = [
            f'{where} ({what})' for where, what, _ in sorted(found)
        ]
        return self.fail(
            f'{len(locations)} reference(s) to end-of-life '
            f'distribution releases. {release_guidance(found)}. '
            f'Moving a runner label also means declaring the new one '
            f'in .github/actionlint.yaml in the same commit, or the '
            f'workflow fails actionlint. A reference that must stay '
            f'-- test input built on the old release, say -- is '
            f'marked "audit-ok: eol-distro" with the reason, on the '
            f'line or the line above',
            findings=locations)


class EolProducers(Check):
    id = 'eol-producers'
    spec = 'docs/audits/eol-producers.md'
    template = None
    issue_title = 'End-of-life runner labels offered'

    def applies(self, repo):
        """Only a repository that defines runner labels is measured.

        A repository with no producer definition reports
        not_applicable rather than pass, and the difference matters
        more here than for most criteria: all but one repository in
        the fleet has no producer, so a criterion that passed them
        would report the whole organisation green on a question it
        never asked any of them.
        """
        if not producer_definitions(repo):
            return (
                'No runner-label producer definition ('
                + ', '.join(f'{name} in {path}'
                            for path, name in PRODUCER_DEFINITIONS)
                + ')'
            )
        return None

    def run(self, repo):
        """Check no runner label is offered for a retired release.

        The producer end of the end-of-life table. `eol-distro` asks
        whether a repository names a banned label; this asks whether
        it offers one, which is the exposure that produced eighty
        consumer findings in shakenfist/development#123 -- a consumer
        mostly boots whatever the producer decided it could.

        The definitions are parsed as Python rather than grepped,
        because both of them discuss retired labels in their comments
        and a grep reports an obituary as a live entry.

        There is no exception marker. A line marked `audit-ok` is how
        a repository says "this reference has to stay", and the three
        reasons the fleet has for that -- test input built on an old
        release, an upstream job name, a guest image somebody boots on
        purpose -- are all consumer shapes. A producer is where the
        label can actually be removed, so a decision to keep serving a
        retired release is a decision about what the fleet may ask
        for, and it belongs in the EOL_RELEASES table rather than in a
        comment on one of two files.
        """
        definitions = producer_definitions(repo)
        found = scan_producers(repo, definitions=definitions)
        read = ', '.join(f'{name} in {path}'
                         for path, name in definitions)
        if not found:
            return self.ok(
                f'No runner label offered by {read} names any of the '
                f'{len(retired_releases())} retired distribution '
                f'releases')

        locations = [f'{where} ({what})' for where, what, _ in found]
        return self.fail(
            f'{len(locations)} runner label(s) offered for end-of-life '
            f'distribution releases. {release_guidance(found)}. '
            f'The fix is to delete the entry, so the label stops being '
            f'served at all -- but a consumer still naming it then '
            f'queues against a label no runner advertises, which fails '
            f'nothing and waits forever, so the consumers move first '
            f'and the eol-distro findings are how they are told',
            findings=locations)
