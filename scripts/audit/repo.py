"""The repository under audit.

`Repo` replaces the `(repo_path, props, repo_name, org)` tuple that
used to be threaded through every check signature, and caches what the
checks read. The caching is not premature: `workflows()` was called
from fourteen places and there were forty-one separate `open()` sites,
so a single audit re-read every workflow file a dozen times.

`REPO_OVERRIDES` holds the handful of properties that cannot be
detected from a clone. Every entry needs a stated reason: an override
is a decision that a rule does not apply, and an unexplained one is
indistinguishable from silencing a real finding.
"""

import io
import os

from audit.github import GhCli


#: The largest file a check reads, in bytes. Anything larger reads as
#: absent rather than in part: a truncated file is a wrong verdict
#: rather than a declined one, and a wrong verdict files an issue on
#: somebody else's repository. The size is the audited repository's to
#: choose, so without a cap a committed file of any size -- or a read
#: that never ends -- is the audit's to absorb.
#:
#: Two MiB because that is the cap plan-source-references applied
#: before every read shared it, and because the largest file any check
#: reads across the fleet is well inside it: shakenfist's
#: shakenfist/mariadb.py at 1.1 MB, a Python source file the dependency
#: checks scan, measured from the local clones on 2026-10-17. The 247
#: tracked files above it are all kerbside-patches CI data (.jsonl and
#: .csv), which only plan-source-references and plan-history-in-source
#: would read, and which they already skipped.
READ_MAX_BYTES = 2 * 1024 * 1024


# Minimal hardcoded overrides for properties that cannot be detected
# from a clone alone.
REPO_OVERRIDES = {
    # cloudgood is a book. Its examples/ directory holds the Python, C
    # and shell the chapters walk through, so from a clone it looks like
    # a code project, but it ships no software: the examples are there
    # to be read and run by hand, and nothing is packaged or released.
    'cloudgood': {'is_docs_only': True},
    # kerbside-patches carries Python helper scripts but is a patch
    # archive, not a Python project.
    'kerbside-patches': {'not_python': True},
    # actions is a library of composite actions, reusable workflows and
    # their helper scripts. Those helpers include Python, but there is
    # nothing to package, so the Python packaging checks do not apply.
    'actions': {
        'not_python': True,
        'default_branch_exception': (
            'every consumer pins to @main, so renaming the default '
            'branch would break the whole fleet at once'
        ),
    },
    # development holds the audit specifications and the tooling that
    # enforces them, and is audited by that tooling like everything
    # else: a standard we exempt ourselves from is a standard we stop
    # noticing the cost of. Its Python is the audit scripts, run from
    # a checkout and never packaged. It publishes no releases, so it
    # has no release branch for "develop" to be the integration branch
    # against.
    'development': {
        'not_python': True,
        'default_branch_exception': (
            'publishes no releases, so there is no release branch for '
            '"develop" to be distinct from'
        ),
    },
    # private-ci is internal tooling and stays outside the conventions
    # audits: it is a legacy setup.py project with no workflows of its
    # own, and holding it to the fleet's release, renovate and README
    # rules would only manufacture issues nobody intends to fix. Two
    # checks still apply, though: it vendors sfui, and drift in a
    # vendored copy is invisible until somebody thinks to look; and it
    # owns IMAGE_BUILDS (conductor/imagebuilder.py) and CI_IMAGES
    # (conductor/provisioner.py), the runner-label producer
    # definitions eol-producers checks against the end-of-life table
    # in docs/audits/eol-distro.md. only_checks scopes a repository to
    # a subset of the audit rather than excluding it wholesale.
    #
    # It plans like the rest of the fleet, though, which the original
    # scoping did not anticipate: it adopted PLAN-TEMPLATE.md and a
    # plan index in September 2026, so the criteria that keep plans
    # honest apply too. The plan family has seven members and each is a
    # separate decision, so each is stated here rather than left to be
    # inferred from the list:
    #
    #  * plan-phase-references, plan-source-references, plan-index and
    #    plan-template are in. All four report a defect in a plan as
    #    written -- a reference that does not resolve, a plan missing
    #    from the index, a shared block that has drifted from
    #    canonical -- rather than a convention adopted after the plan
    #    was, so none of them can manufacture a retrofit issue.
    #  * plan-audit-phase is deliberately out. The eight plans written
    #    there before the template predate the push audit phase rule
    #    and track progress as checkpoint sections rather than as a
    #    phase table, so enabling it would file an issue for a
    #    retrofit nobody has decided to do. It goes in when those
    #    plans are revisited, alongside the Status column the index
    #    does not yet carry.
    #  * push-audit is out because it would be a no-op either way: it
    #    reports not_applicable when a repository has no
    #    PUSH-AUDIT.md, and private-ci has none. It goes in with
    #    plan-audit-phase, which is what would create one.
    #  * plan-history-in-source is out, and goes in with push-audit.
    #    Elsewhere in the fleet it applies whether or not a repository
    #    carries the plan-references-in-code block it measures. Here
    #    nothing applies until it is opted into, and this one is part
    #    of the same decision as the runbook that carries that block.
    #
    # Ordered the way registry.CHECKS orders them, so the two read
    # side by side. Membership and the rendered reason are both
    # order-independent.
    'private-ci': {
        'only_checks': [
            'plan-phase-references', 'plan-source-references',
            'plan-index', 'plan-template', 'eol-producers',
            'sfui-vendor',
        ],
    },
    # images publishes guest images and has been excluded from the
    # conventions as a "historical archive", which it demonstrably is
    # not: it is built from every night and the fleet's CI boots from
    # its output. Joining the audit fully would file twelve findings,
    # eight of which no plan anywhere owns -- an audit filing work
    # outward at a repository whose maintainers did not ask for it.
    # So it joins scoped to eol-distro alone: the pass this was
    # confirmed to have by hand becomes a verdict the audit reports
    # on a schedule, without manufacturing the other eleven findings.
    'images': {
        'only_checks': ['eol-distro'],
    },
    # sfui is a CSS/JavaScript design system with no build step. Its
    # only Python is incidental test tooling (pytest and the
    # consistency checker), so there is nothing to package and the
    # Python packaging checks do not apply.
    'sfui': {'not_python': True},
    # shakenfist's docs/components/ is an automated import of the
    # other repositories' documentation directories. Documentation
    # content checks skip it: the canonical copies are audited in
    # their source repositories, and flagging the import would
    # double-report findings that must be fixed at the source.
    'shakenfist': {'doc_content_excludes': ['docs/components/']},
}


def detect_repo_properties(repo_path, repo_name):
    """Auto-detect repo type from files present."""
    overrides = REPO_OVERRIDES.get(repo_name, {})
    real_root = os.path.realpath(repo_path)

    def present(*parts):
        # The containment Repo.exists() applies, so that a property
        # cannot say a file is there which every read of it refuses.
        return exists_within(
            real_root, os.path.join(repo_path, *parts))

    return {
        'has_pyproject_toml': present('pyproject.toml'),
        'has_cargo_toml': (
            present('Cargo.toml') or present('src', 'Cargo.toml')
        ),
        'has_workflows_dir': present('.github', 'workflows'),
        'has_flake8wrap': present('tools', 'flake8wrap.sh'),
        'is_private': overrides.get('is_private', False),
        'is_docs_only': overrides.get('is_docs_only', False),
        'not_python': overrides.get('not_python', False),
        'default_branch_exception': overrides.get(
            'default_branch_exception', ''
        ),
        'only_checks': overrides.get('only_checks', []),
        'doc_content_excludes': overrides.get('doc_content_excludes', []),
    }


def path_is_within(root, candidate, root_is_real=False):
    """Does candidate resolve to something under root?

    The one realpath comparison in this package. Repo.contains()
    answers it for the clone; a criterion that has to hold a path to
    a narrower root -- a plan inside docs/plans/, say -- answers it
    for that root through the same function, because two realpath
    comparisons written a few hundred lines apart are how one of them
    ends up textual. os.path.normpath is not a substitute: it stops a
    `../..` in a link target and it does not stop a symlink, since
    os.path.isfile() follows one.

    `root_is_real` is for the caller that has already resolved its
    root once and keeps it, rather than resolving it per candidate.
    """
    real_root = root if root_is_real else os.path.realpath(root)
    return os.path.realpath(candidate).startswith(real_root + os.sep)


def exists_within(real_root, full):
    """Does full exist, as a file or a directory, inside real_root?

    The existence half of read_within(). A directory counts, because
    some of what is asked about is one (`.claude`, `docs/`); a symlink
    out of the root does not, for the reason read_within() gives.
    """
    return os.path.exists(full) and path_is_within(
        real_root, full, root_is_real=True)


def read_within(real_root, full, binary=False):
    """Read the file at full, or None if it is not one we will read.

    The one guarded open of an audited repository's files. `Repo.read`
    is the interface to it; the only other caller is audit.scope, whose
    parser takes a directory rather than a Repo because its tests point
    it at this repository's own tree, while the scope-coverage check
    points it at the checkout under audit.

    None covers every shape that would otherwise raise or read
    something the repository did not contain:

    - anything that is not a regular file once symlinks are followed:
      a directory, a dangling link, a device. isfile() rather than
      exists(), because a directory named `helper.sh` opens as an
      IsADirectoryError and a link to /dev/zero is a read that never
      ends;
    - anything resolving outside real_root. A committed symlink is a
      path the audited repository chose, and following one out of the
      clone would let a repository decide what the audit reads and
      quotes into an issue;
    - a file that cannot be opened;
    - a file over READ_MAX_BYTES. The bytes are counted as they are
      read rather than taken from a stat, so the cap holds for a file
      that grows or lies about its size.

    Decoding errors are replaced rather than raised, and newlines are
    translated, as open() in text mode would: the bytes are read first
    so that the cap is in bytes, then decoded the way text mode decodes
    them. Always as UTF-8 rather than in the runner's locale, so that a
    verdict does not depend on where the audit ran. With binary, the
    bytes are returned as read, for a check that compares a file's
    exact content rather than its text: a copy with CRLF line endings
    reads as the same text, but is a different blob.
    """
    if not (os.path.isfile(full)
            and path_is_within(real_root, full, root_is_real=True)):
        return None
    try:
        with open(full, 'rb') as f:
            data = f.read(READ_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > READ_MAX_BYTES:
        return None
    if binary:
        return data
    return io.TextIOWrapper(io.BytesIO(data), encoding='utf-8',
                            errors='replace').read()


class Repo:
    """A checkout being audited, and the answers read from it."""

    def __init__(self, path, name, org='shakenfist', github=None,
                 props=None):
        self.path = path
        self.name = name
        self.org = org
        self.github = github if github is not None else GhCli()
        self.props = (props if props is not None
                      else detect_repo_properties(path, name))
        self._reads = {}
        self._workflows = None
        # Resolved once rather than per call: contains() is asked
        # about every file a criterion's tree walk finds, and about
        # every candidate path the fuzz reporting check offers for a
        # reference.
        self._real_path = os.path.realpath(path)

    def join(self, *parts):
        """Absolute path to something inside the checkout."""
        return os.path.join(self.path, *parts)

    def exists(self, path):
        """Does a repository-relative path exist inside the checkout?

        A file or a directory. A symlink out of the clone does not
        exist as far as a check is concerned, for the reason read()
        gives. Use isfile() for "is there a file here to read".
        """
        return exists_within(self._real_path, self.join(path))

    def isfile(self, path):
        """Is there a file at path that read() will return?

        Defined as exactly that, so that a criterion asking whether a
        file is present and the read that follows cannot disagree: a
        dangling link, a directory, a link out of the clone and an
        oversize file are all "no file" to both.
        """
        return self.read(path) is not None

    def read(self, path):
        """Read a repository-relative file, or None if it is absent.

        Every read of an audited repository's files comes through
        here, and read_within() says what None covers. An audited
        repository can contain anything, and a check that crashes on
        one file reports nothing about any of the other criteria.

        Nothing outside the checkout is read. Checks derive the paths
        they read from the repository's own files -- a workflow names
        a script, a script names its helper, pyproject.toml names an
        entry point module -- so a `..`, an absolute path or a
        committed symlink is a path the audited repository chose. A
        symlink that stays inside the checkout is fine and is read
        through.
        """
        if path in self._reads:
            return self._reads[path]

        content = read_within(self._real_path, self.join(path))
        self._reads[path] = content
        return content

    def read_blob(self, path):
        """Read a repository-relative file's exact bytes, or None.

        The same guarded open as read(), and None for the same shapes,
        for the checks that compare a copy with its template byte for
        byte. Not cached: the few files read this way are read once.
        """
        return read_within(self._real_path, self.join(path), binary=True)

    def relative(self, full):
        """The repository-relative spelling of a path under self.path.

        For the tree walks, which find absolute paths and hand them
        to read().
        """
        return os.path.relpath(full, self.path)

    def contains(self, full):
        """Does an absolute path resolve to something in the checkout?

        Both sides are resolved, because the checkout itself is often
        reached through a symlink and comparing a resolved file to an
        unresolved root would then reject every file in the
        repository.

        Public for the questions read() does not answer: whether a
        directory is inside the checkout before a check lists it, as
        workflows() does. Reads still go through read(), and the
        containment rule is one implementation, path_is_within(),
        rather than a second realpath comparison that drifts.
        """
        return path_is_within(self._real_path, full, root_is_real=True)

    def workflows(self):
        """Workflow file names under .github/workflows/, cached.

        Only names workflow() will read. A dangling link or a
        directory named `ci.yml` is not a workflow, and listing one
        would hand every check that walks the list a None to trip
        over.
        """
        if self._workflows is None:
            directory = self.join('.github', 'workflows')
            if not (os.path.isdir(directory) and self.contains(directory)):
                self._workflows = []
            else:
                self._workflows = [
                    f for f in os.listdir(directory)
                    if (f.endswith('.yml') or f.endswith('.yaml'))
                    and self.workflow(f) is not None
                ]
        return self._workflows

    def workflow(self, name):
        """Read one workflow by file name."""
        return self.read(os.path.join('.github', 'workflows', name))
