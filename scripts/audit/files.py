"""Reading files out of a checkout.

The primitives the criteria are built from. They take a repository
path rather than a `Repo` because most of them are called from helper
functions that are pure by design and have no business holding one;
`Repo` wraps the two that every check reaches for and caches them.
"""

import os
import re


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
