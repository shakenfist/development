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

import os

from audit.github import GhCli


# Minimal hardcoded overrides for properties that cannot be detected
# from a clone alone.
REPO_OVERRIDES = {
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
    # rules would only manufacture issues nobody intends to fix. It
    # does vendor sfui, though, and drift in a vendored copy is
    # invisible until somebody thinks to look, so that one check
    # applies. only_checks scopes a repository to a subset of the
    # audit rather than excluding it wholesale.
    'private-ci': {'only_checks': ['sfui-vendor']},
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
    return {
        'has_pyproject_toml': os.path.exists(
            os.path.join(repo_path, 'pyproject.toml')
        ),
        'has_cargo_toml': (
            os.path.exists(os.path.join(repo_path, 'Cargo.toml'))
            or os.path.exists(os.path.join(repo_path, 'src', 'Cargo.toml'))
        ),
        'has_workflows_dir': os.path.exists(
            os.path.join(repo_path, '.github', 'workflows')
        ),
        'has_flake8wrap': os.path.exists(
            os.path.join(repo_path, 'tools', 'flake8wrap.sh')
        ),
        'is_private': overrides.get('is_private', False),
        'is_docs_only': overrides.get('is_docs_only', False),
        'not_python': overrides.get('not_python', False),
        'default_branch_exception': overrides.get(
            'default_branch_exception', ''
        ),
        'only_checks': overrides.get('only_checks', []),
        'doc_content_excludes': overrides.get('doc_content_excludes', []),
    }


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

    def join(self, *parts):
        """Absolute path to something inside the checkout."""
        return os.path.join(self.path, *parts)

    def exists(self, path):
        """Does a repository-relative path exist?"""
        return os.path.exists(self.join(path))

    def read(self, path):
        """Read a repository-relative file, or None if it is absent.

        Decoding errors are replaced rather than raised. An audited
        repository can contain anything, and a check that crashes on
        one file reports nothing about the other forty-four criteria.
        """
        if path in self._reads:
            return self._reads[path]

        full = self.join(path)
        content = None
        if os.path.exists(full):
            with open(full, 'r', errors='replace') as f:
                content = f.read()
        self._reads[path] = content
        return content

    def workflows(self):
        """Workflow file names under .github/workflows/, cached."""
        if self._workflows is None:
            directory = self.join('.github', 'workflows')
            if not os.path.isdir(directory):
                self._workflows = []
            else:
                self._workflows = [
                    f for f in os.listdir(directory)
                    if f.endswith('.yml') or f.endswith('.yaml')
                ]
        return self._workflows

    def workflow(self, name):
        """Read one workflow by file name."""
        return self.read(os.path.join('.github', 'workflows', name))
