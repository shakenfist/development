"""The criterion about distribution releases we have retired.

An operating system release stops receiving security updates on a
published date, and everything still built on it inherits that. The
fleet notices slowly, because nothing breaks: a CI job pinned to a
retired runner image keeps passing, and a container built `FROM` a
retired base keeps shipping. The only signal is somebody remembering.

`EOL_RELEASES` is that memory, written down. A release is added to it
when it goes end of life, and from the next morning's run every
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
"""

import collections
import os
import re

from audit.check import Check
from audit.text.workflows import is_runner_label_value


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
#: release -- the source of truth is IMAGES in shakenfist/private-ci's
#: conductor/imagebuilder.py, which this repository cannot see -- plus
#: the GitHub-hosted spelling where one exists. Every variant needs
#: listing rather than being derived from `distro` and `version`,
#: because the variants are named by hand there too and a derived
#: pattern would either miss `debian-gnome-12` or invent labels that do
#: not exist.
#:
#: `debian-gnome-12` has no trixie successor built yet, so a finding
#: naming it is a request to private-ci rather than a one-line edit in
#: the repository the issue lands on. It is listed anyway: the point of
#: the criterion is that the dependency is visible, and leaving the
#: fleet's one remaining bookworm image off the list would make the
#: page say Debian 12 was gone when it was not.
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
IMAGE_KEY_RE = re.compile(
    r'^\s*(?:-\s+)?(?:container|image):\s*(\S+)\s*$'
)


DOCKERFILE_FROM_RE = re.compile(r'^\s*FROM\s+(\S+)', re.IGNORECASE)


#: Marker acknowledging a deliberate exception, placed on the offending
#: line or the line immediately above it, in the same shape as the
#: runner criteria's markers. It exists for the repositories whose
#: subject matter *is* old distributions: instar's functional tests
#: boot a `debian:12` container on purpose, because measuring what a
#: bookworm filesystem looks like is the job.
EXCEPTION_RE = re.compile(r'audit-ok:\s*eol-distro')


#: Build output, vendored trees and virtualenvs: a Dockerfile inside
#: one belongs to a dependency rather than to the repository being
#: audited. Mirrors FUZZ_WALK_SKIP in ci_workflows.py.
WALK_SKIP = frozenset({
    '.git', '.tox', '.venv', 'build', 'dist', 'node_modules',
    'target', 'third_party', 'vendor', 'venv',
})


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
    if not reference or '${{' in reference or '$' in reference:
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

        key = IMAGE_KEY_RE.match(line)
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


def scan(repo):
    """Every reference to a retired release in a checkout."""
    found = []
    for name in sorted(repo.workflows()):
        content = repo.workflow(name)
        if content:
            found.extend(
                scan_workflow(f'.github/workflows/{name}', content)
            )
    for path in dockerfiles(repo.path):
        content = repo.read(path)
        if content:
            found.extend(scan_dockerfile(path, content))
    return found


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


class EolDistro(Check):
    id = 'eol-distro'
    spec = 'docs/audits/eol-distro.md'
    template = None
    issue_title = 'End-of-life distributions'

    def run(self, repo):
        """Check nothing is built on a distribution release we retired.

        Runner labels and container image references only. Both choose
        an operating system the repository's own CI or artefacts then
        run on, which is what makes them mechanically checkable and
        worth failing over; a release merely named in prose, booted as
        test input, or dispatched to an upstream job by name is not
        this criterion, and matching whole tokens is what keeps those
        three out.

        A repository whose subject matter is old distributions marks
        the line 'audit-ok: eol-distro' with the reason, on the line
        itself or the one above.

        The full list of locations travels in 'findings' rather than in
        'details': the fix is per line, so a truncated list leaves
        somebody re-running the audit to find out what the issue meant,
        while 'details' is printed into a table cell on the compliance
        page and has to stay one.
        """
        if not repo.workflows() and not dockerfiles(repo.path):
            return self.skip(
                'No workflows and no container build files to read'
            )

        found = scan(repo)
        if not found:
            return self.ok(
                f'No workflow runner label or container image names '
                f'any of the {len(EOL_RELEASES)} retired distribution '
                f'releases')

        locations = [
            f'{where} ({what})' for where, what, _ in sorted(found)
        ]
        return self.fail(
            f'{len(locations)} reference(s) to end-of-life '
            f'distribution releases. {release_guidance(found)}. '
            f'A reference that must stay -- test input built on the '
            f'old release, say -- is marked "audit-ok: eol-distro" '
            f'with the reason, on the line or the line above',
            findings=locations)
