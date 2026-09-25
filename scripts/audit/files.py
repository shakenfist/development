"""Reading files out of a checkout.

The primitives the criteria are built from. They take a repository
path rather than a `Repo` because most of them are called from helper
functions that are pure by design and have no business holding one;
`Repo` wraps the two that every check reaches for and caches them.
"""

import os
import re
import subprocess


#: Directories a tree walk skips: build output, vendored trees and
#: virtualenvs. Whatever a criterion is looking for, a copy inside one
#: of these belongs to a dependency rather than to the repository being
#: audited -- `target` is the realistic case, and the rest are defence
#: in depth against the same mistake reached by another route.
#:
#: `.cargo-cache` is the crate cache the fleet's containerised Rust
#: builds bind-mount into the checkout (ryll, kerbside,
#: visual-digest-rust). It is gitignored, so a fresh CI clone never has
#: one, but a developer clone does, and it holds git checkouts of other
#: repositories -- a kerbside clone carried a whole ryll tree, retired
#: workflows included. Matched exactly: instar tracks `.cargo/`
#: directories holding its own cargo configuration.
#:
#: Shared because two criteria walk for different things and want the
#: same answer about what is not ours. They had a copy each, identical
#: and commented as such, which is the arrangement that drifts the
#: first time somebody adds `.mypy_cache` to one of them.
WALK_SKIP = frozenset({
    '.cargo-cache', '.git', '.tox', '.venv', 'build', 'dist',
    'node_modules', 'target', 'third_party', 'vendor', 'venv',
})


def check_file_exists(repo_path, path):
    """Check if a file exists relative to repo root."""
    return os.path.exists(os.path.join(repo_path, path))


def check_file_contains(repo_path, path, pattern):
    """Check if a file contains a regex pattern."""
    filepath = os.path.join(repo_path, path)
    if not os.path.exists(filepath):
        return False
    with open(filepath, 'r', errors='replace') as f:
        return bool(re.search(pattern, f.read()))


def file_mentions(filepath, needle):
    """Does a file name something, outside of its comments?

    Full-line comments do not count. A config or workflow routinely
    mentions a tool in a header comment explaining that something else
    runs it, and matching those would report a project as compliant
    for describing the thing it does not do.
    """
    if not os.path.exists(filepath):
        return False
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            if line.lstrip().startswith('#'):
                continue
            if needle in line:
                return True
    return False


def toml_section_has_key(content, section, key_pattern):
    """Check a TOML section contains a key matching a regex.

    We do a simple line-based scan rather than full TOML parsing to
    avoid a dependency. A section is a line consisting of the exact
    header (e.g. '[lints]'); the section ends at the next header.
    """
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            in_section = (stripped == f'[{section}]')
            continue
        if in_section and re.match(key_pattern, stripped):
            return True
    return False


def list_workflow_files(repo_path):
    """List all .yml files in .github/workflows/."""
    workflows_dir = os.path.join(repo_path, '.github', 'workflows')
    if not os.path.isdir(workflows_dir):
        return []
    return [
        f for f in os.listdir(workflows_dir)
        if f.endswith('.yml') or f.endswith('.yaml')
    ]


def workflow_has_permissions(repo_path, workflow_file):
    """Check if a workflow file has a top-level permissions block.

    We do a simple line-based check rather than full YAML parsing to
    avoid a PyYAML dependency. A top-level permissions block is a line
    starting with 'permissions:' (no leading whitespace).
    """
    filepath = os.path.join(
        repo_path, '.github', 'workflows', workflow_file
    )
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            if line.startswith('permissions:'):
                return True
    return False


def any_workflow_contains(repo_path, pattern):
    """Check if any workflow file contains a regex pattern."""
    for wf in list_workflow_files(repo_path):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            if re.search(pattern, f.read()):
                return True
    return False


def iter_docs_markdown_files(repo_path, props):
    """Yield repo-relative paths of every .md file under docs/.

    Unlike iter_doc_content_files, plan documents are in scope. Plans
    are synchronised to the documentation site along with the rest of
    docs/, so a link that breaks there breaks for a reader whether or
    not anyone still maintains the file.

    A repository's doc_content_excludes prefixes are skipped for the
    usual reason: they are imported copies of another repository's
    documentation, audited at their source.
    """
    excludes = [
        e.strip('/') + '/'
        for e in props.get('doc_content_excludes', [])
    ]
    for dirpath, dirnames, filenames in os.walk(
        os.path.join(repo_path, 'docs')
    ):
        rel_dir = os.path.relpath(dirpath, repo_path).replace(os.sep, '/')
        dirnames[:] = sorted(
            d for d in dirnames
            if not any(f'{rel_dir}/{d}/'.startswith(e) for e in excludes)
        )
        for filename in sorted(filenames):
            if filename.endswith('.md'):
                yield f'{rel_dir}/{filename}'


def iter_doc_content_files(repo_path, props):
    """Yield repo-relative paths of documentation content to audit.

    The scope is the top-level README.md, AGENTS.md and
    ARCHITECTURE.md plus every .md file under docs/, minus any file
    under a plans/ directory at any depth (plan documents
    legitimately discuss their own phases) and minus the repository's
    doc_content_excludes prefixes (imported copies of other
    repositories' documentation, audited at their source).

    AGENTS.md and ARCHITECTURE.md are in scope for the same reason
    README.md is: they describe the current state of the software to
    a reader who was not present for its construction, so "wired up
    in phase 6" is noise there too.
    """
    for name in ('README.md', 'AGENTS.md', 'ARCHITECTURE.md'):
        if os.path.exists(os.path.join(repo_path, name)):
            yield name

    excludes = [
        e.strip('/') + '/'
        for e in props.get('doc_content_excludes', [])
    ]
    for dirpath, dirnames, filenames in os.walk(
        os.path.join(repo_path, 'docs')
    ):
        rel_dir = os.path.relpath(dirpath, repo_path).replace(
            os.sep, '/'
        )
        dirnames[:] = sorted(
            d for d in dirnames
            if d != 'plans'
            and not any(
                f'{rel_dir}/{d}/'.startswith(e) for e in excludes
            )
        )
        for filename in sorted(filenames):
            if filename.endswith('.md'):
                yield f'{rel_dir}/{filename}'


#: The finding a criterion reports when tracked_paths() returns None.
#: One wording, because the four callers used to carry four.
LS_FILES_FAILED = (
    'Could not list the tracked files: git ls-files failed, or the '
    'audited directory is not the root of a checkout'
)


def tracked_paths(repo_path, *pathspec):
    """Every path in the repository's index, or None if git cannot say.

    `pathspec`, when given, narrows the listing the way it narrows
    `git ls-files -- <pathspec>`: `tracked_paths(path, '*.py')`.

    Every criterion that asks the index what it holds goes through
    here. Four of them used to split `git ls-files` output on lines
    and so met the quoting below the hard way -- one raised, two
    silently under-reported and one rendered an unusable path into an
    issue (shakenfist/development#168) -- and the three that looked at
    the exit status gave three different answers to a failed listing.

    `-z` rather than a line split, and the reason is not only NUL
    safety. `git ls-files` C-quotes any path holding a non-ASCII byte
    unless core.quotePath is off, so a tracked `docs/uber/CLAUDE.md`
    with an umlaut arrives as `"docs/\303\274ber/CLAUDE.md"` -- whose
    basename ends in a quote character and matches nothing. That is a
    silent false negative in exactly the check whose whole job is
    finding a file by its name. `-z` suppresses the quoting, and
    review-tracking.py reached the same flag by the same route.

    Decoding errors are replaced rather than raised, for the reason
    Repo.read gives: an audited repository can contain anything, and a
    check that crashes on one path reports nothing about any of the
    other criteria. A replaced byte can only fall in a directory
    component -- the basenames we match are ASCII -- so it costs the
    match nothing.

    None means git could not answer: the binary is missing, the call
    timed out, the index is unreadable, or the directory is not the
    root of a checkout. That is separated from "the index is empty"
    because the two mean opposite things to a criterion whose finding
    is a file being *present*, and collapsing them would report a
    clean pass for a repository nobody looked at.

    Which is why the work tree root is confirmed rather than inferred
    from a successful listing. `git -C <dir> ls-files` exits 0 for any
    directory *inside* a checkout, listing whatever the enclosing
    index holds below it -- typically nothing -- so a tree copied into
    a subdirectory of an unrelated repository would have reported a
    confident pass. rev-parse is how review-tracking.py asks the same
    question, and test_docs_content.py records the same fail-open
    concern for the mermaid lane.
    """
    def git(*args):
        try:
            return subprocess.run(
                ['git', '-C', repo_path] + list(args),
                capture_output=True, text=True, errors='replace', timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    toplevel = git('rev-parse', '--show-toplevel')
    if toplevel is None or toplevel.returncode != 0:
        return None
    if (os.path.realpath(toplevel.stdout.strip())
            != os.path.realpath(repo_path)):
        return None

    result = git('ls-files', '-z', '--', *pathspec)
    if result is None or result.returncode != 0:
        return None

    return [path for path in result.stdout.split('\0') if path]
