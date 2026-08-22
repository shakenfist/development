#!/usr/bin/env python3
"""Check a cloned repository against Shaken Fist consistency audit criteria.

Usage:
    python audit-check.py --repo-path /tmp/clone --repo-name occystrap

Outputs JSON results to stdout.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
from datetime import datetime, timezone


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

# Map from check ID to the human-readable name used in issue titles.
# Must match the titles used in the manually created issues from phase 1.
CHECK_NAMES = {
    'llm-tooling': 'LLM tooling',
    'llm-doc-structure': 'AGENTS.md / ARCHITECTURE.md structure',
    'llm-context-lint': 'LLM context linting',
    'llm-context-lint-ci': 'LLM context linting in pre-commit and CI',
    'release-process': 'Release process',
    'ci-review-automation': 'CI review automation',
    'renovate': 'Renovate',
    'pin-indirect-dependencies': 'Pin indirect dependencies',
    'dependency-name-normalization': 'Dependency name normalization',
    'export-repo-config': 'Export repo config',
    'default-branch-naming': 'Default branch naming',
    'github-security': 'GitHub security settings',
    'delete-branch-on-merge': 'Delete branch on merge',
    'merge-queue-config': 'Merge queue reasonability',
    'workflow-permissions': 'Workflow standards',
    'pre-commit-config': 'Workflow standards',
    'review-marks-pre-commit': 'Workflow standards',
    'flake8wrap': 'Workflow standards (flake8wrap)',
    'self-hosted-runners': 'Workflow standards (self-hosted runners)',
    'static-runner-tags': 'Workflow standards (static runner tags)',
    'devpi-fallback': 'Workflow standards (devpi cache fallback)',
    'devpi-stale-ip': 'Workflow standards (devpi cache address)',
    'expensive-lane-path-filter': 'Expensive lane path filtering',
    'version-file-gitignore': 'Generated version file',
    'pyproject-usage': 'pyproject.toml usage',
    'rust-unwrap-lint': 'Rust unwrap lint',
    'readme-absolute-links': 'README absolute links',
    'docs-external-links': 'Links out of docs/ are absolute',
    'readme-structure': 'README structure',
    'plan-phase-references': 'Plan phase references',
    'plan-source-references': 'Plan references in source',
    'plan-index': 'Plan index',
    'push-audit': 'Pre-push audit file',
    'plan-template': 'Plan template',
    'secret-scanning-ci': 'Secret scanning in CI',
    'review-coverage': 'Human review coverage',
    'sfui-vendor': 'sfui vendored copy',
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


# --- Check functions ---
# Each returns a dict with: id, status (pass/fail/not_applicable), details


def check_llm_tooling(repo_path, props):
    """Check for AGENTS.md and ARCHITECTURE.md."""
    missing = []
    if not check_file_exists(repo_path, 'AGENTS.md'):
        missing.append('AGENTS.md')
    if not check_file_exists(repo_path, 'ARCHITECTURE.md'):
        missing.append('ARCHITECTURE.md')

    if missing:
        return {
            'id': 'llm-tooling',
            'status': 'fail',
            'details': f'Missing: {", ".join(missing)}',
            'missing': missing,
        }
    return {
        'id': 'llm-tooling',
        'status': 'pass',
        'details': 'AGENTS.md and ARCHITECTURE.md both exist',
    }


def check_release_process(repo_path, props):
    """Check release process compliance."""
    if not props['has_pyproject_toml']:
        return {
            'id': 'release-process',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    issues = []
    if check_file_exists(repo_path, 'release.sh'):
        issues.append('release.sh still exists (should be removed)')
    if check_file_exists(repo_path, 'requirements.txt'):
        issues.append('requirements.txt still exists (use pyproject.toml)')
    if not check_file_exists(
        repo_path, '.github/workflows/release.yml'
    ):
        issues.append('Missing .github/workflows/release.yml')
    if not check_file_exists(repo_path, 'RELEASE-SETUP.md'):
        issues.append('Missing RELEASE-SETUP.md')

    if issues:
        return {
            'id': 'release-process',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'release-process',
        'status': 'pass',
        'details': 'Release process is compliant',
    }


# The comment addresser was retired in August 2026. It answered
# "@shakenfist-bot please address comments" by handing the review's items
# to Claude Code and pushing a commit per item, which nobody used: fixes
# are worked through interactively with the reviewer instead, and a bot
# authoring commits from a review no human had read was the part that
# stopped it being used. What it leaves behind is not inert. The workflow
# runs on issue_comment, so it holds contents: write against the pull
# request branch, and it is the last consumer of a project's own copies
# of render-review.py and review-schema.json -- which is why those are no
# longer audited for either. Reap the whole chain rather than the trigger
# alone.
RETIRED_ADDRESSER_WORKFLOW = '.github/workflows/pr-address-comments.yml'

# Searched for by name anywhere in the tree, not just under tools/. The
# canonical home is tools/, but deployments put them elsewhere -- the
# check this replaced found a contrib/ copy -- and a script that is dead
# is dead wherever it sits.
RETIRED_ADDRESSER_SCRIPTS = (
    'address-comments-with-claude.sh',
    'render-review.py',
    'review-schema.json',
)

ADDRESSER_RETIRED_DETAIL = (
    'the retired comment addresser is still deployed (%s); it is '
    'unused, and its workflow holds contents: write on the pull '
    'request branch'
)


def carries_retired_comment_addresser(repo_path):
    """Return the retired addresser's files which are still deployed.

    Reported as one finding naming every file found, not one finding per
    file: they are a single chain, they are removed in a single commit,
    and a repository which deletes the workflow but keeps the scripts has
    not finished the job.
    """
    found = []
    if check_file_exists(repo_path, RETIRED_ADDRESSER_WORKFLOW):
        found.append(RETIRED_ADDRESSER_WORKFLOW)
    for dirpath, dirnames, filenames in os.walk(repo_path):
        # .git holds whatever another branch left behind, which is not
        # something this repository can act on.
        dirnames[:] = [d for d in dirnames if d != '.git']
        for name in RETIRED_ADDRESSER_SCRIPTS:
            if name in filenames:
                found.append(
                    os.path.relpath(
                        os.path.join(dirpath, name), repo_path
                    )
                )
    return sorted(found)


def pr_re_review_open_codes_the_trigger(repo_path):
    """True when pr-re-review.yml hand-rolls what pr-bot-trigger does.

    An earlier version of the template open-coded the trigger handling --
    the phrase match, the permission lookup, the reaction and the
    refusal reply -- in about thirty lines of inline shell. That copy
    then missed every fix made to the shared action, and the one that
    matters is a security fix.

    pr-bot-trigger refuses pull requests from forks. Its pr-ref output is
    .head.ref, the branch name in the *head* repository, with nothing to
    say which repository that is; callers check that name out and push to
    it in their own repository. A fork pull request opened from the
    fork's default branch names "main", so the checkout succeeds against
    the target's main and the push lands unreviewed bot commits there. No
    malice is needed -- a maintainer typing the trigger phrase on a fork
    pull request is enough.

    pr-retest.yml uses the action and inherits that guard at @main
    without changing. A hand-rolled pr-re-review.yml does not, and
    cannot, until it is replaced.

    Returns False when the workflow is absent: its absence is already
    reported separately, and saying both would be two findings for one
    missing file.
    """
    path = '.github/workflows/pr-re-review.yml'
    if not check_file_exists(repo_path, path):
        return False
    return not check_file_contains(repo_path, path, r'pr-bot-trigger@main')


def check_ci_review_automation(repo_path, props):
    """Check for automated review and developer automation workflows."""
    if props['is_docs_only']:
        # cloudgood: only pr-re-review is expected.
        missing = []
        if not check_file_exists(
            repo_path, '.github/workflows/pr-re-review.yml'
        ):
            missing.append('pr-re-review.yml')
        deployed = carries_retired_comment_addresser(repo_path)
        if deployed:
            return {
                'id': 'ci-review-automation',
                'status': 'fail',
                'details': ADDRESSER_RETIRED_DETAIL % ', '.join(deployed),
            }
        if pr_re_review_open_codes_the_trigger(repo_path):
            return {
                'id': 'ci-review-automation',
                'status': 'fail',
                'details': (
                    'pr-re-review.yml does not use '
                    'shakenfist/actions/pr-bot-trigger@main, so it '
                    'hand-rolls the trigger handling and does not inherit '
                    "the action's fork pull request guard"
                ),
            }
        if missing:
            return {
                'id': 'ci-review-automation',
                'status': 'fail',
                'details': f'Missing workflows: {", ".join(missing)}',
                'missing': missing,
            }
        return {
            'id': 'ci-review-automation',
            'status': 'pass',
            'details': 'Developer automation workflows exist',
        }

    issues = []
    # Check developer automation workflows
    for wf in [
        'pr-re-review.yml',
        'pr-retest.yml',
    ]:
        if not check_file_exists(
            repo_path, f'.github/workflows/{wf}'
        ):
            issues.append(f'Missing {wf}')

    # Check that at least one workflow uses the shared review action
    if not any_workflow_contains(
        repo_path, r'review-pr-with-claude@main'
    ):
        issues.append(
            'No workflow uses shared action '
            'review-pr-with-claude@main'
        )

    # A hand-rolled pr-re-review.yml misses the shared action's fork
    # guard. See the helper's docstring.
    if pr_re_review_open_codes_the_trigger(repo_path):
        issues.append(
            'pr-re-review.yml does not use '
            'shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the '
            "trigger handling and does not inherit the action's fork pull "
            'request guard'
        )

    # The comment addresser is retired. See the helper's docstring.
    deployed = carries_retired_comment_addresser(repo_path)
    if deployed:
        issues.append(ADDRESSER_RETIRED_DETAIL % ', '.join(deployed))

    if issues:
        return {
            'id': 'ci-review-automation',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'ci-review-automation',
        'status': 'pass',
        'details': (
            'Automated review and developer automation '
            'workflows are compliant'
        ),
    }


def uses_remote_pre_commit_hooks(repo_path):
    """True when .pre-commit-config.yaml pins at least one remote hook.

    Remote hooks are the only kind that carry a version to bump: a
    `repo: local` entry runs a script from the tree itself. Every remote
    entry pins a `rev:`, so the presence of one is the signal that there
    is something for renovate to manage.
    """
    return check_file_contains(
        repo_path, '.pre-commit-config.yaml', r'(?m)^\s*rev:'
    )


def renovate_manages_pre_commit(repo_path):
    """True when renovate.json enables the pre-commit manager.

    Renovate ships the pre-commit manager disabled, so it has to be
    turned on deliberately. There are three supported ways to do that
    and all of them count.
    """
    filepath = os.path.join(repo_path, 'renovate.json')
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, 'r', errors='replace') as f:
            config = json.load(f)
    except ValueError:
        return False
    if not isinstance(config, dict):
        return False

    manager = config.get('pre-commit')
    if isinstance(manager, dict) and manager.get('enabled') is True:
        return True

    enabled = config.get('enabledManagers')
    if isinstance(enabled, list) and 'pre-commit' in enabled:
        return True

    extends = config.get('extends')
    if isinstance(extends, list):
        for preset in extends:
            if isinstance(preset, str) and preset.endswith(
                ':enablePreCommit'
            ):
                return True
    return False


def check_renovate(repo_path, props):
    """Check for renovate workflow and config."""
    missing = []
    if not check_file_exists(
        repo_path, '.github/workflows/renovate.yml'
    ):
        missing.append('.github/workflows/renovate.yml')
    if not check_file_exists(repo_path, 'renovate.json'):
        missing.append('renovate.json')

    if missing:
        return {
            'id': 'renovate',
            'status': 'fail',
            'details': f'Missing: {", ".join(missing)}',
            'missing': missing,
        }

    if uses_remote_pre_commit_hooks(repo_path) and not (
        renovate_manages_pre_commit(repo_path)
    ):
        return {
            'id': 'renovate',
            'status': 'fail',
            'details': (
                'renovate.json does not enable the pre-commit manager, '
                'so the hook revisions in .pre-commit-config.yaml are '
                'unmanaged and drift silently'
            ),
        }

    return {
        'id': 'renovate',
        'status': 'pass',
        'details': 'Renovate workflow and config exist',
    }


# A project is in scope for indirect dependency pinning when it already
# exactly pins its own direct dependencies. That is the project declaring
# it controls its runtime environment, which is exactly the condition
# under which pinning transitive dependencies is safe. Libraries we
# publish deliberately constrain loosely (">=") so that downstream
# consumers -- distribution packagers especially -- are free to resolve
# against whatever they already ship, and pinning transitive versions on
# their behalf takes that freedom away. The split is unambiguous in
# practice: our applications pin ~97% of their direct dependencies, our
# libraries pin none of theirs.
PIN_INTENT_THRESHOLD = 0.5


def pins_direct_dependencies(repo_path):
    """Report whether a project exactly pins its own direct dependencies.

    Returns (in_scope, exact, total). Only the [project] dependencies
    array is considered: optional-dependencies groups are extras, and a
    pinned test extra says nothing about how the project wants its
    runtime resolved.
    """
    pyproject = os.path.join(repo_path, 'pyproject.toml')
    try:
        with open(pyproject, 'rb') as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False, 0, 0

    dependencies = parsed.get('project', {}).get('dependencies', [])
    if not dependencies:
        return False, 0, 0

    exact = 0
    for dependency in dependencies:
        match = DEP_SPEC_RE.match(dependency)
        # A bare name with no specifier at all ("python-debian") has no
        # spec group to test, and is as unpinned as a dependency gets.
        spec = match.group('spec') if match else None
        if spec and EXACT_PIN_RE.match(spec.strip()):
            exact += 1
    ratio = exact / len(dependencies)
    return ratio >= PIN_INTENT_THRESHOLD, exact, len(dependencies)


def check_pin_indirect_deps(repo_path, props):
    """Check for indirect dependency pinning."""
    if not props['has_pyproject_toml']:
        return {
            'id': 'pin-indirect-dependencies',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    in_scope, exact, total = pins_direct_dependencies(repo_path)
    if not in_scope:
        return {
            'id': 'pin-indirect-dependencies',
            'status': 'not_applicable',
            'details': (
                f'Direct dependencies are not exactly pinned '
                f'({exact} of {total}), so this is a library rather than '
                f'an application we control the environment of. Pinning '
                f'transitive versions here would constrain downstream '
                f'consumers and distribution packagers'
            ),
        }

    issues = []
    if not check_file_exists(
        repo_path,
        '.github/workflows/pin-indirect-dependencies.yml',
    ):
        issues.append(
            'Missing .github/workflows/'
            'pin-indirect-dependencies.yml'
        )
    for marker in ['START_OF_INDIRECT_DEPS', 'END_OF_INDIRECT_DEPS']:
        if not check_file_contains(
            repo_path, 'pyproject.toml', marker
        ):
            issues.append(
                f'Missing # {marker} marker in pyproject.toml'
            )
    if not check_file_exists(
        repo_path, 'tools/pin-indirect-dependencies.sh'
    ):
        issues.append(
            'Missing tools/pin-indirect-dependencies.sh '
            '(reconciler script from the template)'
        )

    if issues:
        return {
            'id': 'pin-indirect-dependencies',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'pin-indirect-dependencies',
        'status': 'pass',
        'details': 'Indirect dependency pinning is configured',
    }


# A pinned dependency entry in a pyproject.toml array, e.g.
# '    "typing-extensions==4.16.0",' or
# '    "gunicorn[gevent]==25.3.0",  # mit'. Captures the distribution
# name, optional [extras], and the leading version specifier.
DEP_PIN_RE = re.compile(
    r'''^\s*["']
        (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)
        (?P<extras>\[[^\]]*\])?
        \s*(?P<spec>(?:[<>=!~]=|===)\s*[^"',;]+)
    ''',
    re.VERBOSE,
)

# A PEP 508 requirement string as it appears once TOML parsing has
# already stripped the quoting, e.g. 'click>=8.0.0' or
# 'gunicorn[gevent]==26.0.0'. Unlike DEP_PIN_RE this matches the value
# rather than the source line, so it does not need to tolerate quotes,
# indentation or trailing comments.
DEP_SPEC_RE = re.compile(
    r'''^\s*
        (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)
        (?P<extras>\[[^\]]*\])?
        \s*(?P<spec>(?:[<>=!~]=|===)\s*[^,;]+)?
    ''',
    re.VERBOSE,
)

# An exact pin (== or ===), capturing the version. A floor/ceiling
# constraint (>=, <=, ~=, !=) is not an exact pin: a direct dependency
# declared as ">=X" that also carries an exact "==Y" pin (appended by
# the indirect-dependency workflow) is intentional and resolves fine.
EXACT_PIN_RE = re.compile(r'^={2,3}\s*(\S+)')

# The start of a TOML array (e.g. 'dependencies = [') or a table
# header (e.g. '[project.optional-dependencies]'). Either boundary
# starts a fresh dependency grouping, so a name pinned once in the main
# dependencies array and once in an optional-dependencies group is not
# treated as a conflict.
DEP_ARRAY_OPEN_RE = re.compile(r'=\s*\[')
TOML_SECTION_RE = re.compile(r'^\s*\[[A-Za-z_]')


def canonical_dependency_name(name):
    """Return the PEP 503 canonical form of a distribution name.

    Lowercase, with any run of '-', '_' and '.' collapsed to a single
    '-'. This is how pip, uv and Renovate compare names, so
    'typing_extensions' and 'typing-extensions' are one package.
    """
    return re.sub(r'[-_.]+', '-', name).lower()


def check_dependency_name_normalization(repo_path, props):
    """Check pyproject.toml has no duplicate pins under PEP 503 names.

    A distribution pinned twice under different spellings (e.g.
    'typing-extensions' and 'typing_extensions') is silently fine while
    both copies carry the same version, but the moment one spelling is
    bumped -- for instance by a Renovate PR -- the two exact pins
    diverge and the resolver rejects the project as unsatisfiable.
    Renovate also treats the two spellings as separate packages and
    opens duplicate PRs. We flag, within a single dependency array,
    any canonical name carrying two or more exact (==) pins with no
    extras, or two or more conflicting exact versions.
    """
    if not props['has_pyproject_toml']:
        return {
            'id': 'dependency-name-normalization',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    pyproject = os.path.join(repo_path, 'pyproject.toml')
    with open(pyproject, 'r', errors='replace') as f:
        lines = f.read().splitlines()

    conflicts = []

    def evaluate(group):
        for canonical, entries in sorted(group.items()):
            if len(entries) < 2:
                continue
            exact_versions = {e['version'] for e in entries if e['version']}
            exact_plain = [
                e for e in entries if e['version'] and not e['has_extras']
            ]
            # Two conflicting exact versions are an outright
            # unsatisfiable pin. Two plain exact pins (differing only
            # by spelling) are the latent form that breaks the instant
            # one is bumped -- the shape that produced the duplicate
            # Renovate PRs. A base+extras pair at one version (e.g.
            # gunicorn and gunicorn[gevent]) is intentional.
            if len(exact_versions) > 1 or len(exact_plain) > 1:
                spellings = sorted({e['raw'] for e in entries})
                conflicts.append(f'{canonical} ({", ".join(spellings)})')

    group = {}
    for line in lines:
        if TOML_SECTION_RE.match(line) or DEP_ARRAY_OPEN_RE.search(line):
            evaluate(group)
            group = {}
        match = DEP_PIN_RE.match(line)
        if not match:
            continue
        exact = EXACT_PIN_RE.match(match.group('spec').strip())
        group.setdefault(
            canonical_dependency_name(match.group('name')), []
        ).append({
            'raw': match.group('name') + (match.group('extras') or ''),
            'version': exact.group(1) if exact else None,
            'has_extras': bool(match.group('extras')),
        })
    evaluate(group)

    if conflicts:
        return {
            'id': 'dependency-name-normalization',
            'status': 'fail',
            'details': (
                f'{len(conflicts)} distribution(s) pinned under multiple '
                f'spellings that PEP 503 treats as one package: '
                f'{"; ".join(conflicts)}. Consolidate to a single '
                f'canonical pin -- divergent spellings become '
                f'unsatisfiable when one is bumped and cause duplicate '
                f'Renovate PRs'
            ),
        }
    return {
        'id': 'dependency-name-normalization',
        'status': 'pass',
        'details': (
            'No duplicate dependency pins under PEP 503 normalization'
        ),
    }


def check_export_repo_config(repo_path, props):
    """Check for repo config export workflow."""
    if not check_file_exists(
        repo_path, '.github/workflows/export-repo-config.yml'
    ):
        return {
            'id': 'export-repo-config',
            'status': 'fail',
            'details': 'Missing .github/workflows/export-repo-config.yml',
        }
    return {
        'id': 'export-repo-config',
        'status': 'pass',
        'details': 'export-repo-config.yml exists',
    }


def check_default_branch(repo_path, props, repo_name, org):
    """Check default branch is 'develop' via GitHub API."""
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq', '.default_branch',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {
                'id': 'default-branch-naming',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API: '
                    f'{result.stderr.strip()}'
                ),
            }
        branch = result.stdout.strip()

        # Exceptions: docs-only repos, and repositories carrying a
        # documented reason in REPO_OVERRIDES, may use main
        if props['is_docs_only']:
            return {
                'id': 'default-branch-naming',
                'status': 'not_applicable',
                'details': (
                    f'Docs-only repo (current: {branch}, '
                    f'exception allowed)'
                ),
            }

        if props['default_branch_exception']:
            return {
                'id': 'default-branch-naming',
                'status': 'not_applicable',
                'details': (
                    f'Exempt: {props["default_branch_exception"]} '
                    f'(current: {branch})'
                ),
            }

        if branch != 'develop':
            return {
                'id': 'default-branch-naming',
                'status': 'fail',
                'details': (
                    f'Default branch is "{branch}", '
                    f'expected "develop"'
                ),
            }
        return {
            'id': 'default-branch-naming',
            'status': 'pass',
            'details': 'Default branch is "develop"',
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'default-branch-naming',
            'status': 'fail',
            'details': f'Error checking default branch: {e}',
        }


def check_github_security(repo_path, props, repo_name, org):
    """Check GitHub security settings and CodeQL workflow."""
    issues = []

    # Fetch visibility and security settings in one API call.
    # Visibility is queried live rather than hardcoded because repos
    # change visibility over time and a stale override would silently
    # skip the CodeQL check.
    is_private = props['is_private']
    security = None
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq',
                '{private: .private, security: .security_and_analysis}',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                repo_info = json.loads(result.stdout.strip())
                is_private = repo_info.get('private', is_private)
                security = repo_info.get('security')
            except json.JSONDecodeError:
                issues.append(
                    'Could not parse security settings response'
                )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append('Could not query GitHub API for security settings')

    # Check CodeQL workflow (file-based, not API)
    if is_private:
        pass  # Private repos can't use CodeQL without GHAS
    elif props['is_docs_only']:
        pass  # No code to scan
    elif not check_file_exists(
        repo_path, '.github/workflows/codeql-analysis.yml'
    ):
        issues.append('Missing .github/workflows/codeql-analysis.yml')

    if security:
        secret_scanning = security.get('secret_scanning', {})
        if secret_scanning.get('status') != 'enabled':
            issues.append('Secret scanning not enabled')

        push_protection = security.get(
            'secret_scanning_push_protection', {}
        )
        if push_protection.get('status') != 'enabled':
            issues.append(
                'Secret scanning push protection not enabled'
            )

    if issues:
        return {
            'id': 'github-security',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'github-security',
        'status': 'pass',
        'details': 'Security settings and CodeQL are compliant',
    }


def check_delete_branch_on_merge(repo_path, props, repo_name, org):
    """Check head branches are deleted automatically when a PR merges."""
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq', '.delete_branch_on_merge',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {
                'id': 'delete-branch-on-merge',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API: '
                    f'{result.stderr.strip()}'
                ),
            }
        setting = result.stdout.strip()

        if setting == 'true':
            return {
                'id': 'delete-branch-on-merge',
                'status': 'pass',
                'details': 'Delete branch on merge is enabled',
            }
        if setting == 'false':
            return {
                'id': 'delete-branch-on-merge',
                'status': 'fail',
                'details': 'Delete branch on merge is not enabled',
            }
        # The API omits this field (returns null) when the token
        # lacks push access to the repository.
        return {
            'id': 'delete-branch-on-merge',
            'status': 'fail',
            'details': (
                f'Could not determine delete branch on merge setting '
                f'(API returned "{setting or "null"}"; the token may '
                f'lack push access)'
            ),
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'delete-branch-on-merge',
            'status': 'fail',
            'details': f'Error checking delete branch on merge: {e}',
        }


def evaluate_merge_queue_rules(rules):
    """Evaluate effective branch rules for merge queue reasonability.

    Takes the rule list returned by the
    /repos/{org}/{repo}/rules/branches/{branch} endpoint. Returns a
    list of problem strings (empty when compliant), or None when no
    merge queue rule is present.

    The expectations encode two mechanics that are easy to get wrong
    (learned on shakenfist/shakenfist, August 2026):

    * max_entries_to_build > 1 enables speculative stacking: entry
      N+1 builds on top of entry N, so any failure ahead of it
      ejects that work and rebuilds the group on a new SHA. On CI
      that fails under cluster load, the speculative builds both
      waste runs (entries observed rebuilding five times in a day)
      and add the load that causes the failures.
    * min_entries_to_merge > 1 makes the queue idle for up to
      min_entries_to_merge_wait_minutes hoping to batch merges, but
      batching saves no CI (the queue builds one merge group and one
      CI run per entry regardless of how merges are batched), so it
      is pure latency. With min_entries_to_merge = 1 the wait timer
      never engages.
    """
    merge_queue = [r for r in rules if r.get('type') == 'merge_queue']
    if not merge_queue:
        return None

    problems = []
    for rule in merge_queue:
        params = rule.get('parameters') or {}

        build = params.get('max_entries_to_build')
        if build != 1:
            problems.append(
                f'max_entries_to_build is {build}, expected 1: '
                f'speculative stacked builds are ejected and rebuilt '
                f'whenever an entry ahead of them fails, wasting CI '
                f'and adding load'
            )

        min_merge = params.get('min_entries_to_merge')
        if min_merge != 1:
            problems.append(
                f'min_entries_to_merge is {min_merge}, expected 1: '
                f'waiting to batch merges adds up to the configured '
                f'wait time to every merge and saves no CI, which '
                f'runs once per queue entry regardless'
            )
    return problems


def check_merge_queue_config(repo_path, props, repo_name, org):
    """Check any merge queue on the default branch is serialized."""
    try:
        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}',
                '--jq', '.default_branch',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API for the default '
                    f'branch: {result.stderr.strip()}'
                ),
            }
        branch = result.stdout.strip()

        result = subprocess.run(
            [
                'gh', 'api',
                f'repos/{org}/{repo_name}/rules/branches/{branch}',
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': (
                    f'Could not query GitHub API for branch rules: '
                    f'{result.stderr.strip()}'
                ),
            }
        try:
            rules = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': 'Could not parse branch rules response',
            }

        problems = evaluate_merge_queue_rules(rules)
        if problems is None:
            return {
                'id': 'merge-queue-config',
                'status': 'not_applicable',
                'details': (
                    f'No merge queue on default branch "{branch}"'
                ),
            }
        if problems:
            return {
                'id': 'merge-queue-config',
                'status': 'fail',
                'details': '; '.join(problems),
            }
        return {
            'id': 'merge-queue-config',
            'status': 'pass',
            'details': (
                f'Merge queue on "{branch}" is serialized '
                f'(max_entries_to_build 1, min_entries_to_merge 1)'
            ),
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'merge-queue-config',
            'status': 'fail',
            'details': f'Error checking merge queue config: {e}',
        }


def check_workflow_permissions(repo_path, props):
    """Check all workflows have top-level permissions blocks."""
    if not props['has_workflows_dir']:
        return {
            'id': 'workflow-permissions',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'workflow-permissions',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    missing = [
        wf for wf in workflows
        if not workflow_has_permissions(repo_path, wf)
    ]

    if missing:
        return {
            'id': 'workflow-permissions',
            'status': 'fail',
            'details': (
                f'{len(missing)} workflow(s) missing top-level '
                f'permissions: {", ".join(sorted(missing))}'
            ),
            'missing': sorted(missing),
        }
    return {
        'id': 'workflow-permissions',
        'status': 'pass',
        'details': (
            f'All {len(workflows)} workflows have '
            f'top-level permissions'
        ),
    }


def check_pre_commit_config(repo_path, props):
    """Check for .pre-commit-config.yaml."""
    if not check_file_exists(repo_path, '.pre-commit-config.yaml'):
        return {
            'id': 'pre-commit-config',
            'status': 'fail',
            'details': 'Missing .pre-commit-config.yaml',
        }
    return {
        'id': 'pre-commit-config',
        'status': 'pass',
        'details': '.pre-commit-config.yaml exists',
    }


# Paths a review session rewrites, used to test candidate pre-commit
# exclude patterns. The weAudit file and its sidecar are named for the
# reviewing account, so the leading component varies per repository.
REVIEW_MARK_SAMPLE_PATHS = (
    '.vscode/reviewer.weaudit',
    '.vscode/reviewer.weaudit-shas.json',
)

# Hooks that rewrite the files they are handed. Only these fight the
# weAudit generator, so only these need the exclude -- and a repo that
# runs none of them needs no exclude at all.
#
# Read-only hooks deliberately do not appear here, and must keep seeing
# the review marks. gitleaks and the bidi/zero-width scanners are the
# reason step 8 of the adoption procedure refuses to let content
# scanners skip review-only changes: review notes are human prose, and
# prose is where a secret or a smuggled character would land.
FILE_REWRITING_HOOK_IDS = (
    'end-of-file-fixer',
    'trailing-whitespace',
    'mixed-line-ending',
    'pretty-format-json',
    'file-contents-sorter',
)


def pre_commit_rewriting_hooks(repo_path):
    """Which file-rewriting hooks does .pre-commit-config.yaml run?"""
    filepath = os.path.join(repo_path, '.pre-commit-config.yaml')
    found = set()
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            match = re.match(r'\s*-\s*id:\s*(\S+)', line)
            if not match:
                continue
            hook_id = match.group(1).strip('\'"')
            if hook_id in FILE_REWRITING_HOOK_IDS:
                found.add(hook_id)
    return sorted(found)


def pre_commit_excludes_review_marks(repo_path):
    """Does .pre-commit-config.yaml exempt the weAudit review marks?

    Line-based rather than YAML-parsed, matching the rest of this
    file, which avoids a PyYAML dependency. Every `exclude:` value is
    tried as the regex pre-commit would apply, and the check passes if
    any one of them matches both sample paths -- so a top-level
    exclude and a per-hook exclude both count. A value we cannot
    compile is skipped rather than raising: an unrelated malformed
    pattern is pre-commit's problem to report, not this audit's.
    """
    filepath = os.path.join(repo_path, '.pre-commit-config.yaml')
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            match = re.match(r'\s*exclude:\s*(\S.*?)\s*$', line)
            if not match:
                continue
            pattern = match.group(1).strip('\'"')
            try:
                compiled = re.compile(pattern)
            except re.error:
                continue
            if all(compiled.search(p) for p in REVIEW_MARK_SAMPLE_PATHS):
                return True
    return False


def check_review_marks_pre_commit(repo_path, props):
    """Check review marks are exempt from file-rewriting pre-commit hooks.

    Applies only to repositories with review tracking deployed,
    detected the same way check_review_coverage does, and then only
    where a rewriting hook is actually configured. The weAudit state
    files are generated, and the generator emits no trailing newline,
    so end-of-file-fixer rewrites them on every `pre-commit run
    --all-files`. That reports a failure nobody can fix: committing
    the newline only means the next regen drops it again.

    A repo that runs no rewriting hook has nothing to exclude, and
    telling it to add one would be actively harmful: a blanket exclude
    also hides the marks from whatever read-only scanners it does run.
    ryll is exactly that shape -- gitleaks and a bidi scanner, no
    formatter -- so it reports not applicable rather than failing.
    """
    if not check_file_exists(repo_path, '.vscode/review-scope.toml'):
        return {
            'id': 'review-marks-pre-commit',
            'status': 'not_applicable',
            'details': (
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)'
            ),
        }
    if not check_file_exists(repo_path, '.pre-commit-config.yaml'):
        return {
            'id': 'review-marks-pre-commit',
            'status': 'not_applicable',
            'details': 'No .pre-commit-config.yaml',
        }
    rewriting = pre_commit_rewriting_hooks(repo_path)
    if not rewriting:
        return {
            'id': 'review-marks-pre-commit',
            'status': 'not_applicable',
            'details': (
                'No file-rewriting pre-commit hooks, so nothing '
                'rewrites the review marks'
            ),
        }
    if not pre_commit_excludes_review_marks(repo_path):
        return {
            'id': 'review-marks-pre-commit',
            'status': 'fail',
            'details': (
                f'{", ".join(rewriting)} rewrite(s) the review marks; '
                r'add exclude: ^\.vscode/.*\.weaudit to those hooks'
            ),
        }
    return {
        'id': 'review-marks-pre-commit',
        'status': 'pass',
        'details': (
            f'Review marks excluded from {", ".join(rewriting)}'
        ),
    }


def check_flake8wrap(repo_path, props):
    """Check flake8wrap.sh for correct SC2086 handling."""
    if not props['has_flake8wrap']:
        return {
            'id': 'flake8wrap',
            'status': 'not_applicable',
            'details': 'No tools/flake8wrap.sh',
        }

    filepath = os.path.join(repo_path, 'tools', 'flake8wrap.sh')
    with open(filepath, 'r', errors='replace') as f:
        content = f.read()

    issues = []
    if 'SC2086' not in content:
        issues.append('Missing shellcheck disable=SC2086 directive')

    # Check for quoted ${filtered_files} on diff/flake8 lines
    # (the variable must NOT be quoted)
    for line in content.splitlines():
        if ('filtered_files' in line
                and ('diff' in line or 'FLAKE' in line or 'flake' in line)):
            if '"${filtered_files}"' in line:
                issues.append(
                    'filtered_files is incorrectly quoted '
                    'on diff/flake8 line'
                )
                break

    if issues:
        return {
            'id': 'flake8wrap',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'flake8wrap',
        'status': 'pass',
        'details': 'flake8wrap.sh has correct SC2086 handling',
    }


# GitHub-hosted runner labels (e.g. ubuntu-latest, windows-2022,
# macos-15, ubuntu-24.04-arm). Self-hosted runner labels never use
# these names.
GITHUB_HOSTED_LABEL_RE = re.compile(
    r'\b(?:ubuntu|windows|macos)-(?:latest|\d+(?:\.\d+)?)'
    r'(?:-(?:arm|arm64|large|xlarge))?\b'
)

# Marker acknowledging a deliberate exception, placed on the
# offending line or the line immediately above it.
RUNNER_EXCEPTION_RE = re.compile(r'audit-ok:\s*github-hosted-runner')

# A GitHub-hosted label only names a runner when it sits where YAML
# puts a value: after "runs-on:", as an element of a "[...]" list, or
# as a "- " item in a matrix. The same text inside a shell command is
# not a runner reference -- shakenfist/actions ships a step which
# uploads an image artifact named "ubuntu-2004", and reporting that
# asked someone to mark a deliberate exception on a line which never
# described a runner at all.
RUNNER_VALUE_PREFIXES = frozenset({':', '-', '[', ','})
RUNNER_VALUE_SUFFIXES = frozenset({',', ']', '#'})


def is_runner_label_value(line, start, end):
    """Does a matched label sit where a YAML value could?

    Scanning every line, rather than only "runs-on:" lines, is
    deliberate -- matrix values feeding "runs-on: ${{ matrix.os }}"
    have to be caught too -- so the position test replaces the
    context a "runs-on:" anchor would have given.

    The test is about token boundaries, not just neighbouring
    characters. A label glued to preceding text is part of a longer
    name ("build-ubuntu-latest"), and only a sequence opener can
    legitimately abut one; a label separated by whitespace is a value
    when what precedes it opens one.
    """
    before = line[:start]
    after = line[end:]

    # Treat 'ubuntu-latest' the same as ubuntu-latest.
    if before.endswith(('"', "'")):
        before = before[:-1]
    if after.startswith(('"', "'")):
        after = after[1:]

    if before and not before.endswith((' ', '\t')):
        # Glued to what comes before, so this is the tail of a longer
        # name unless a list or flow-sequence opener abuts it.
        if not before.endswith(('[', ',')):
            return False
    else:
        stripped = before.rstrip()
        if stripped and stripped[-1] not in RUNNER_VALUE_PREFIXES:
            return False

    if after and not after.startswith((' ', '\t')):
        if not after.startswith((',', ']')):
            return False
    else:
        stripped = after.lstrip()
        if stripped and stripped[0] not in RUNNER_VALUE_SUFFIXES:
            return False

    return True


def check_self_hosted_runners(repo_path, props):
    """Check workflows use self-hosted runners.

    GitHub-provided runner minutes are limited per month, so jobs
    must run on self-hosted runners except under exceptional
    circumstances (e.g. Windows or macOS builds needing hardware we
    don't own). Exceptions are marked with an
    'audit-ok: github-hosted-runner' comment on the offending line
    or the line immediately above it.

    We scan every workflow line for GitHub-hosted runner labels
    rather than just runs-on lines, so matrix values that feed
    'runs-on: ${{ matrix.os }}' are caught too.
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'self-hosted-runners',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'self-hosted-runners',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    offenders = []
    for wf in sorted(workflows):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            lines = f.read().splitlines()

        for i, line in enumerate(lines):
            match = GITHUB_HOSTED_LABEL_RE.search(line)
            if not match:
                continue
            if 'self-hosted' in line:
                continue
            if not is_runner_label_value(line, match.start(), match.end()):
                continue
            if RUNNER_EXCEPTION_RE.search(line):
                continue
            if i > 0 and RUNNER_EXCEPTION_RE.search(lines[i - 1]):
                continue
            offenders.append(f'{wf}:{i + 1} ({match.group(0)})')

    if offenders:
        return {
            'id': 'self-hosted-runners',
            'status': 'fail',
            'details': (
                f'{len(offenders)} unmarked GitHub-hosted runner '
                f'reference(s): {", ".join(offenders)}. Move to a '
                f'self-hosted runner, or mark deliberate exceptions '
                f'with an "audit-ok: github-hosted-runner" comment'
            ),
        }
    return {
        'id': 'self-hosted-runners',
        'status': 'pass',
        'details': (
            f'No unmarked GitHub-hosted runner references in '
            f'{len(workflows)} workflow(s)'
        ),
    }


# The value portion of a `runs-on:` line, e.g. the
# '[self-hosted, static]' in 'runs-on: [self-hosted, static]'.
RUNS_ON_RE = re.compile(r'^\s*runs-on:\s*(.+?)\s*$')

# Labels that legitimately accompany 'static' on a static runner.
# Anything else (a size like 's'/'l', 'vm', or an operating system
# label like 'debian-12') describes a runner attribute the static
# pool does not advertise, so the job would never be scheduled.
STATIC_ALLOWED_LABELS = frozenset({'self-hosted', 'static'})


def parse_runner_labels(value):
    """Parse the labels from the value of a `runs-on:` line.

    Handles the inline-list form ('[self-hosted, static]') and the
    bare-scalar form ('static'). Returns a list of label strings, or
    None when the value is a GitHub Actions expression we cannot
    resolve statically (e.g. '${{ matrix.runner }}').
    """
    # Drop a trailing inline comment (runner labels never contain
    # ' #', so this is safe).
    value = re.sub(r'\s+#.*$', '', value).strip()
    if '${{' in value:
        return None
    if value.startswith('['):
        inner = value[1:]
        if inner.endswith(']'):
            inner = inner[:-1]
        parts = inner.split(',')
    else:
        parts = [value]

    labels = []
    for part in parts:
        label = part.strip().strip('"').strip("'").strip()
        if label:
            labels.append(label)
    return labels


def check_static_runner_tags(repo_path, props):
    """Check that static-runner jobs request only the static labels.

    A static runner advertises exactly the 'self-hosted' and 'static'
    labels. Adding a size (e.g. 's'), 'vm', or an operating system
    label (e.g. 'debian-12') alongside 'static' asks for a runner
    that does not exist, so the job waits forever without being
    scheduled. Such jobs must use '[self-hosted, static]' exactly.

    We scan every 'runs-on:' line, so both the job-level and
    matrix-expansion forms are covered; unresolvable '${{ ... }}'
    expressions are skipped.
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'static-runner-tags',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'static-runner-tags',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    offenders = []
    for wf in sorted(workflows):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            lines = f.read().splitlines()

        for i, line in enumerate(lines):
            match = RUNS_ON_RE.match(line)
            if not match:
                continue
            labels = parse_runner_labels(match.group(1))
            if labels is None or 'static' not in labels:
                continue
            extras = [
                label for label in labels
                if label not in STATIC_ALLOWED_LABELS
            ]
            if extras:
                offenders.append(
                    f'{wf}:{i + 1} ({", ".join(extras)})'
                )

    if offenders:
        return {
            'id': 'static-runner-tags',
            'status': 'fail',
            'details': (
                f'{len(offenders)} static runner job(s) requesting '
                f'impossible extra label(s): {", ".join(offenders)}. '
                f'A static runner only advertises the "self-hosted" '
                f'and "static" labels, so requiring a size, "vm", or '
                f'an operating system label alongside "static" means '
                f'the job will never be scheduled. Use '
                f'"[self-hosted, static]" exactly'
            ),
        }
    return {
        'id': 'static-runner-tags',
        'status': 'pass',
        'details': (
            f'No static runner jobs request impossible labels in '
            f'{len(workflows)} workflow(s)'
        ),
    }


# The trigger events that run a workflow on proposed changes:
# pull_request on the PR itself and merge_group in the merge queue.
# Anchored to line start so expression contexts like
# "github.event_name == 'pull_request'" do not match, and the colon
# requirement keeps pull_request_target (a different event with
# different security properties) out of scope. The flow form catches
# "on: [push, pull_request]" style triggers.
PR_TRIGGER_RE = re.compile(
    r'^\s*(pull_request|merge_group):', re.MULTILINE
)
PR_TRIGGER_FLOW_RE = re.compile(
    r'^on:\s*\[[^\]]*\b(pull_request|merge_group)\b', re.MULTILINE
)

# Marks a deliberate exception to the expensive-lane path filter
# check: a lane that must run even when only docs or review marks
# changed. Anywhere in the workflow file, ideally with a reason.
PATH_FILTER_EXCEPTION_RE = re.compile(r'audit-ok:\s*no-path-filter')


WORKFLOW_JOB_RE = re.compile(r'^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$')


def workflow_job_blocks(content):
    """Split a workflow into (job name, job text) pairs.

    Line-based rather than YAML-parsed, to avoid a PyYAML
    dependency, and matching how the rest of this module reads
    workflows. A job is a two-space-indented key under a top-level
    "jobs:"; the block runs to the next such key or to the end of
    the file.
    """
    lines = content.splitlines()
    in_jobs = False
    blocks = []
    for line in lines:
        if line and not line[0].isspace():
            in_jobs = line.startswith('jobs:')
            continue
        if not in_jobs:
            continue
        match = WORKFLOW_JOB_RE.match(line)
        if match:
            blocks.append([match.group(1), []])
        elif blocks:
            blocks[-1][1].append(line)
    return [(name, '\n'.join(body)) for name, body in blocks]


def is_dedicated_scanner_workflow(content):
    """Does this workflow do nothing but scan content?

    The exemption from path filtering exists because a scanner has
    to read the human-written text a filter would skip -- a secret
    lands in docs or review notes as easily as in code. That is an
    argument about the scanner job, not about the file it happens
    to live in, so it only carries the whole workflow when the
    whole workflow is the scanner.

    Asking merely whether a scanner is mentioned anywhere gave
    shakenfist/actions a pass for a ci.yml that ran lint, unit
    tests and the LLM reviewer on ephemeral VMs for every
    documentation typo, on the strength of the gitleaks job sitting
    beside them.
    """
    jobs = workflow_job_blocks(content)
    if not jobs:
        return False
    return all(
        job_runs_a_scanner(body) for _name, body in jobs
    )


def job_runs_a_scanner(body):
    """Does a job body invoke a secret scanner, outside comments?

    Full-line comments do not count, for the reason file_mentions()
    gives: a job routinely names a tool in a header comment
    explaining that something else runs it, and matching those would
    let one such comment in an unrelated lane make a whole workflow
    look like a dedicated scanner. actions/ci.yml has exactly that
    shape -- a comment in its lint job mentioning gitleaks-scan.sh.
    """
    for line in body.splitlines():
        if line.lstrip().startswith('#'):
            continue
        if any(scanner in line for scanner in SECRET_SCANNERS):
            return True
    return False


def check_expensive_lane_path_filter(repo_path, props):
    """Check expensive PR lanes are path-filtered.

    Ephemeral VM runners (the 'vm' label) are the expensive pool:
    the lanes on them build clouds or boot guests, and a run costs
    tens of minutes to hours. A pull request or merge queue entry
    touching only content no lane exercises -- docs/ and the
    review-tracking state -- should not pay for them, so every
    workflow running vm jobs on pull_request or merge_group must be
    path-filtered, and the filter must exclude the repository's
    non-code content: docs/** where a docs/ directory exists, and
    REVIEWS.md where review tracking is deployed.

    Two mechanisms count. A workflow backing no required status
    check may use trigger-level paths/paths-ignore. A workflow
    backing a required check must use a filter job instead (e.g.
    dorny/paths-filter feeding job-level ifs, as kerbside's
    check_paths jobs do): a required check in a paths-ignore'd
    workflow never reports on a filtered PR, and a required check
    that never reports blocks the merge forever, while a skipped
    one satisfies it. An inclusion-style trigger filter (paths:
    listing what the lane exercises, as rust workflows do) excludes
    everything else by construction, so it passes without pattern
    checks. Deliberate exceptions are marked with an
    'audit-ok: no-path-filter' comment in the workflow file.

    Dedicated content-scanner workflows -- detected as an
    unfiltered workflow invoking a SECRET_SCANNERS tool -- are
    exempt: their whole point is to read the human-written text a
    filter would skip, since a secret lands in docs or review marks
    as easily as in code. That is the same reasoning that keeps
    content scanners out of paths-ignore in the review-tracking
    adoption procedure (see workflow-standards.md). A workflow that
    mixes scanner jobs with expensive lanes and already carries a
    filter is still held to the exclusion requirements; its scanner
    jobs should simply not consume the filter's output.

    Repositories with neither a docs/ directory nor review tracking
    have nothing for a filter to exclude and are not applicable.
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'expensive-lane-path-filter',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'expensive-lane-path-filter',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    excludables = []
    if os.path.isdir(os.path.join(repo_path, 'docs')):
        excludables.append(('docs/', 'docs/**'))
    if check_file_exists(repo_path, '.vscode/review-scope.toml'):
        excludables.append(('review marks', 'REVIEWS.md'))
    if not excludables:
        return {
            'id': 'expensive-lane-path-filter',
            'status': 'not_applicable',
            'details': (
                'No docs/ directory and no review tracking, so '
                'there is no non-code content for a filter to '
                'exclude'
            ),
        }

    offenders = []
    expensive = 0
    for wf in sorted(workflows):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            content = f.read()

        if not (PR_TRIGGER_RE.search(content)
                or PR_TRIGGER_FLOW_RE.search(content)):
            continue

        has_vm = False
        for line in content.splitlines():
            match = RUNS_ON_RE.match(line)
            if not match:
                continue
            labels = parse_runner_labels(match.group(1))
            if labels and 'vm' in labels:
                has_vm = True
                break
        if not has_vm:
            continue
        expensive += 1

        if PATH_FILTER_EXCEPTION_RE.search(content):
            continue

        has_ignore = bool(
            re.search(r'^\s*paths-ignore:', content, re.MULTILINE)
        )
        has_include = bool(
            re.search(r'^\s*paths:', content, re.MULTILINE)
        )
        has_filter_job = 'paths-filter' in content

        if not (has_ignore or has_include or has_filter_job):
            # A workflow that is nothing but content scanning is
            # exempt by design: scanning the text a filter would
            # skip is the whole job. A workflow that merely
            # contains a scanner is not -- see
            # is_dedicated_scanner_workflow. The exemption does not
            # extend to workflows that carry a filter either: a
            # monolithic workflow mixing scanner jobs with
            # expensive lanes (ryll's ci.yml) is held to the
            # exclusion requirements below, and its scanner jobs
            # should simply not consume the filter's output.
            if is_dedicated_scanner_workflow(content):
                continue
            if any(s in content for s in SECRET_SCANNERS):
                offenders.append(
                    f'{wf} (no path filtering; a scanner job does '
                    f'not exempt the expensive jobs beside it -- '
                    f'filter the workflow and leave the scanner job '
                    f'off the filter)'
                )
                continue
            offenders.append(f'{wf} (no path filtering)')
            continue

        if has_include and not (has_ignore or has_filter_job):
            # An inclusion list runs the lane only when the listed
            # paths change, so docs and review marks are excluded
            # by construction.
            continue

        missing = [
            name for name, pattern in excludables
            if pattern not in content
        ]
        if missing:
            offenders.append(
                f'{wf} (filter does not exclude '
                f'{", ".join(missing)})'
            )

    if offenders:
        return {
            'id': 'expensive-lane-path-filter',
            'status': 'fail',
            'details': (
                f'{len(offenders)} expensive lane(s) triggered by '
                f'pull_request or merge_group without adequate path '
                f'filtering: {", ".join(offenders)}. Add a '
                f'check_paths filter job (see kerbside '
                f'functional-tests.yml) or, only for workflows '
                f'backing no required status check, trigger-level '
                f'paths-ignore, excluding docs/** and the '
                f'review-tracking files; mark deliberate exceptions '
                f'with an "audit-ok: no-path-filter" comment'
            ),
        }
    if expensive == 0:
        return {
            'id': 'expensive-lane-path-filter',
            'status': 'pass',
            'details': (
                f'No pull_request or merge_group workflow runs '
                f'vm-runner jobs in {len(workflows)} workflow(s)'
            ),
        }
    return {
        'id': 'expensive-lane-path-filter',
        'status': 'pass',
        'details': (
            f'{expensive} expensive PR lane(s) are path-filtered '
            f'or exempt (content scanners, marked exceptions)'
        ),
    }


# The local devpi PyPI cache. A job that points pip at it via
# PIP_INDEX_URL must also set PIP_EXTRA_INDEX_URL (pypi) as a fallback:
# devpi's root/pypi mirror serves an empty index the first time it is
# asked for a package it has not cached, so without a fallback pip
# reports "from versions: none" and the job fails on that cold-cache
# miss. Matches both the LAN address and the TLS hostname.
DEVPI_INDEX_RE = re.compile(
    r'PIP_INDEX_URL\s*:\s*\S*'
    r'(?:192\.168\.1\.15:3141|devpi\.home\.stillhq\.com)'
)
PIP_EXTRA_INDEX_RE = re.compile(r'PIP_EXTRA_INDEX_URL\s*:')

# The devpi PyPI cache moved to 192.168.1.15 some time ago; the old
# 192.168.1.4 address no longer resolves to a running server, so any
# workflow still pointing pip at it fails every install. The negative
# lookahead stops 192.168.1.4 from also matching 192.168.1.40 through
# 192.168.1.49.
DEVPI_STALE_IP_RE = re.compile(r'192\.168\.1\.4(?!\d)')
DEVPI_CURRENT_IP = '192.168.1.15'


def env_mapping_has_sibling(lines, idx, pattern):
    """Whether a sibling key in the same YAML mapping matches pattern.

    `lines[idx]` is a mapping key (e.g. PIP_INDEX_URL). We scan the
    contiguous run of lines belonging to the same mapping -- those
    indented at least as far as `lines[idx]`, with blank lines treated
    as continuation -- and return True if any line at exactly that
    key's indentation matches `pattern`. Scoping to a single env block
    means an unrelated job elsewhere in the same workflow file cannot
    mask a missing fallback.
    """
    def indent_of(s):
        return len(s) - len(s.lstrip())

    indent = indent_of(lines[idx])

    start = idx
    while start > 0:
        prev = lines[start - 1]
        if prev.strip() == '' or indent_of(prev) >= indent:
            start -= 1
        else:
            break
    end = idx
    while end + 1 < len(lines):
        nxt = lines[end + 1]
        if nxt.strip() == '' or indent_of(nxt) >= indent:
            end += 1
        else:
            break

    for j in range(start, end + 1):
        line = lines[j]
        if (line.strip() and indent_of(line) == indent
                and pattern.search(line)):
            return True
    return False


def check_devpi_fallback(repo_path, props):
    """Check devpi-backed jobs set a pypi fallback index.

    A job that points pip at the local devpi cache via PIP_INDEX_URL
    must also set PIP_EXTRA_INDEX_URL in the same env block so a devpi
    cold-cache miss (an empty index for a first-touch package) falls
    back to pypi instead of failing the job with "from versions:
    none".
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'devpi-fallback',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    devpi_seen = False
    offenders = []
    for wf in sorted(list_workflow_files(repo_path)):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            lines = f.read().splitlines()

        for i, line in enumerate(lines):
            if not DEVPI_INDEX_RE.search(line):
                continue
            devpi_seen = True
            if not env_mapping_has_sibling(
                lines, i, PIP_EXTRA_INDEX_RE
            ):
                offenders.append(f'{wf}:{i + 1}')

    if not devpi_seen:
        return {
            'id': 'devpi-fallback',
            'status': 'not_applicable',
            'details': 'No jobs use the local devpi cache',
        }
    if offenders:
        return {
            'id': 'devpi-fallback',
            'status': 'fail',
            'details': (
                f'{len(offenders)} devpi-backed env block(s) missing '
                f'a PIP_EXTRA_INDEX_URL pypi fallback: '
                f'{", ".join(offenders)}. Add '
                f'"PIP_EXTRA_INDEX_URL: https://pypi.org/simple/" '
                f'alongside PIP_INDEX_URL so a devpi cold-cache miss '
                f'(empty index for a first-touch package) falls back '
                f'to pypi instead of failing with '
                f'"from versions: none"'
            ),
        }
    return {
        'id': 'devpi-fallback',
        'status': 'pass',
        'details': (
            'All devpi-backed jobs set a PIP_EXTRA_INDEX_URL fallback'
        ),
    }


def check_devpi_stale_ip(repo_path, props):
    """Check workflows do not reference the retired devpi address.

    The devpi PyPI cache moved to 192.168.1.15; the old 192.168.1.4
    host no longer exists, so a job still pointing pip at it (via
    PIP_INDEX_URL, PIP_TRUSTED_HOST, or anywhere else) fails every
    install. Flag any workflow line referencing the retired address.
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'devpi-stale-ip',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    offenders = []
    for wf in sorted(list_workflow_files(repo_path)):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            lines = f.read().splitlines()

        for i, line in enumerate(lines):
            if DEVPI_STALE_IP_RE.search(line):
                offenders.append(f'{wf}:{i + 1}')

    if offenders:
        return {
            'id': 'devpi-stale-ip',
            'status': 'fail',
            'details': (
                f'{len(offenders)} reference(s) to the retired devpi '
                f'address 192.168.1.4: {", ".join(offenders)}. The '
                f'devpi PyPI cache now lives at {DEVPI_CURRENT_IP}; '
                f'point PIP_INDEX_URL/PIP_TRUSTED_HOST there instead '
                f'(192.168.1.4 no longer exists, so pip fails every '
                f'install against it)'
            ),
        }
    return {
        'id': 'devpi-stale-ip',
        'status': 'pass',
        'details': 'No references to the retired devpi address',
    }


def check_pyproject_usage(repo_path, props):
    """Check Python projects use pyproject.toml for packaging.

    Any project with Python code must have a pyproject.toml, and
    must not carry legacy packaging files (setup.py, setup.cfg)
    alongside it.
    """
    if props['is_docs_only']:
        return {
            'id': 'pyproject-usage',
            'status': 'not_applicable',
            'details': 'Docs-only repo',
        }
    if props['has_cargo_toml']:
        return {
            'id': 'pyproject-usage',
            'status': 'not_applicable',
            'details': 'Rust project (any Python is helper scripts)',
        }
    if props['not_python']:
        return {
            'id': 'pyproject-usage',
            'status': 'not_applicable',
            'details': 'Not a Python project (per overrides)',
        }

    if props['has_pyproject_toml']:
        legacy = [
            f for f in ['setup.py', 'setup.cfg']
            if check_file_exists(repo_path, f)
        ]
        if legacy:
            return {
                'id': 'pyproject-usage',
                'status': 'fail',
                'details': (
                    f'Legacy packaging files exist alongside '
                    f'pyproject.toml: {", ".join(legacy)}'
                ),
            }
        return {
            'id': 'pyproject-usage',
            'status': 'pass',
            'details': (
                'pyproject.toml exists with no legacy '
                'packaging files'
            ),
        }

    # No pyproject.toml: only a problem if there is Python code.
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'ls-files', '--', '*.py'],
            capture_output=True, text=True, timeout=30,
        )
        python_files = [
            line for line in result.stdout.splitlines()
            if line.strip()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'pyproject-usage',
            'status': 'fail',
            'details': f'Could not run git ls-files: {e}',
        }

    if python_files:
        return {
            'id': 'pyproject-usage',
            'status': 'fail',
            'details': (
                f'{len(python_files)} Python file(s) but no '
                f'pyproject.toml'
            ),
        }
    return {
        'id': 'pyproject-usage',
        'status': 'not_applicable',
        'details': 'No Python code',
    }


def check_version_file(repo_path, props):
    """Check generated version files are gitignored and not tracked.

    setuptools_scm writes a version file (usually _version.py) at
    build time. That file must never be committed: it should be
    covered by .gitignore and must not be tracked by git.
    """
    if not props['has_pyproject_toml']:
        return {
            'id': 'version-file-gitignore',
            'status': 'not_applicable',
            'details': 'No pyproject.toml (not a Python package)',
        }

    pyproject = os.path.join(repo_path, 'pyproject.toml')
    with open(pyproject, 'r', errors='replace') as f:
        content = f.read()

    match = re.search(
        r'^\s*(?:write_to|version_file|version-file)\s*=\s*'
        r'["\']([^"\']+)["\']',
        content, re.MULTILINE,
    )

    issues = []

    # A tracked generated version file is always wrong, whether or
    # not we can work out the configured path.
    try:
        result = subprocess.run(
            [
                'git', '-C', repo_path,
                'ls-files', '--', '*_version.py',
            ],
            capture_output=True, text=True, timeout=30,
        )
        tracked = [
            line for line in result.stdout.splitlines()
            if line.strip()
        ]
        if tracked:
            issues.append(
                f'Generated version file tracked in git '
                f'(use git rm --cached): {", ".join(sorted(tracked))}'
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        issues.append(f'Could not run git ls-files: {e}')

    if not match:
        if issues:
            return {
                'id': 'version-file-gitignore',
                'status': 'fail',
                'details': '; '.join(issues),
            }
        return {
            'id': 'version-file-gitignore',
            'status': 'not_applicable',
            'details': (
                'No generated version file configured in '
                'pyproject.toml'
            ),
        }

    version_file = match.group(1)
    try:
        result = subprocess.run(
            [
                'git', '-C', repo_path,
                # --no-index so a tracked copy doesn't mask the
                # .gitignore coverage answer.
                'check-ignore', '-q', '--no-index', version_file,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 1:
            issues.append(
                f'{version_file} is not covered by .gitignore'
            )
        elif result.returncode != 0:
            issues.append(
                f'Could not check .gitignore coverage: '
                f'{result.stderr.strip()}'
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        issues.append(f'Could not run git check-ignore: {e}')

    if issues:
        return {
            'id': 'version-file-gitignore',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'version-file-gitignore',
        'status': 'pass',
        'details': (
            f'{version_file} is gitignored and no generated '
            f'version file is tracked'
        ),
    }


def check_rust_unwrap_lint(repo_path, props):
    """Check Rust projects enable clippy's unwrap_used lint.

    The root Cargo.toml must set unwrap_used to warn or deny (under
    [workspace.lints.clippy], or [lints.clippy] for single-crate
    repos), a clippy.toml must exempt test code with
    allow-unwrap-in-tests, and every first-party crate manifest must
    either inherit the workspace lints or define the lint itself.
    Fuzz harness crates are exempt.
    """
    if not props['has_cargo_toml']:
        return {
            'id': 'rust-unwrap-lint',
            'status': 'not_applicable',
            'details': 'No Cargo.toml (not a Rust project)',
        }

    if check_file_exists(repo_path, 'Cargo.toml'):
        root_manifest = 'Cargo.toml'
    else:
        root_manifest = 'src/Cargo.toml'
    root_dir = os.path.dirname(root_manifest)

    # Accepts unwrap_used = "warn", "deny", or the table form
    # { level = "warn", priority = -1 }.
    lint_pattern = r'unwrap_used\s*=\s*.*"(warn|deny)"'

    issues = []

    with open(
        os.path.join(repo_path, root_manifest), 'r', errors='replace'
    ) as f:
        root_content = f.read()
    if not (
        toml_section_has_key(
            root_content, 'workspace.lints.clippy', lint_pattern
        )
        or toml_section_has_key(
            root_content, 'lints.clippy', lint_pattern
        )
    ):
        issues.append(
            f'clippy unwrap_used lint not set to warn or deny '
            f'in {root_manifest}'
        )

    clippy_toml = os.path.join(root_dir, 'clippy.toml')
    if not check_file_contains(
        repo_path, clippy_toml,
        r'(?m)^\s*allow-unwrap-in-tests\s*=\s*true',
    ):
        issues.append(
            f'{clippy_toml} missing allow-unwrap-in-tests = true'
        )

    # Every other first-party crate manifest must inherit the
    # workspace lints or define the lint itself.
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'ls-files', '--', '*Cargo.toml'],
            capture_output=True, text=True, timeout=30,
        )
        manifests = [
            line for line in result.stdout.splitlines()
            if line.strip()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'rust-unwrap-lint',
            'status': 'fail',
            'details': f'Could not run git ls-files: {e}',
        }

    for manifest in manifests:
        if manifest == root_manifest:
            continue
        if 'fuzz' in manifest.split('/'):
            continue
        with open(
            os.path.join(repo_path, manifest), 'r', errors='replace'
        ) as f:
            content = f.read()
        if '[package]' not in content:
            continue
        inherits = (
            toml_section_has_key(
                content, 'lints', r'workspace\s*=\s*true'
            )
            or re.search(
                r'^\s*lints\.workspace\s*=\s*true', content,
                re.MULTILINE,
            )
        )
        defines = toml_section_has_key(
            content, 'lints.clippy', lint_pattern
        )
        if not (inherits or defines):
            issues.append(
                f'{manifest} neither inherits workspace lints '
                f'([lints] workspace = true) nor defines '
                f'unwrap_used itself'
            )

    if issues:
        return {
            'id': 'rust-unwrap-lint',
            'status': 'fail',
            'details': '; '.join(issues),
        }
    return {
        'id': 'rust-unwrap-lint',
        'status': 'pass',
        'details': (
            'clippy unwrap_used lint is enabled with '
            'allow-unwrap-in-tests'
        ),
    }


# Inline markdown link or image: [text](target) or ![alt](target).
# The captured group is everything between the parentheses (which may
# include a "title" and/or <angle brackets> that we strip later).
MD_LINK_RE = re.compile(r'!?\[[^\]]*\]\(\s*([^)]+?)\s*\)')

# Reference-style link definition at the start of a line:
# [label]: target "optional title".
MD_REFDEF_RE = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(\S+)', re.MULTILINE)

# A URL scheme prefix (http:, https:, mailto:, data:, ...). A link
# target carrying one is absolute and renders anywhere.
URL_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:')

# An inline code span: a run of backticks, then anything that is not
# that same run, then the matching run. Applied per paragraph, so the
# content may cross lines but not a blank line.
INLINE_CODE_RE = re.compile(r'(`+)(?:(?!\1)[\s\S])*?\1')


def strip_markdown_code(markdown):
    """Return markdown with fenced blocks and inline code spans removed.

    A documented command or example may legitimately contain something
    that looks like a relative link (e.g. `[x](y)` shown as sample
    text). Markdown does not render links inside code, so we must not
    audit them either.
    """
    out = []
    fence = None
    for line in markdown.splitlines():
        stripped = line.lstrip()
        marker = None
        if stripped.startswith('```'):
            marker = '```'
        elif stripped.startswith('~~~'):
            marker = '~~~'

        if fence is None:
            if marker is not None:
                fence = marker
                continue
            out.append(line)
        elif marker == fence:
            fence = None

    # Inline code spans. A span may wrap across lines -- prose wrapped
    # at 65 columns does it constantly -- but it cannot contain a
    # blank line, so strip one paragraph at a time. That bound matters:
    # without it an unpaired backtick would swallow the rest of the
    # document and hide every link after it.
    return '\n\n'.join(
        INLINE_CODE_RE.sub('', paragraph)
        for paragraph in re.split(r'\n[ \t]*\n', '\n'.join(out))
    )


def link_target(raw):
    """Return the bare target from the inside of a markdown link.

    Unwraps the <angle bracket> form and drops an optional "title"
    following the URL, so callers see just the destination.
    """
    target = raw.strip()
    if target.startswith('<'):
        return target[1:].split('>', 1)[0].strip()
    # Drop an optional "title" following the URL.
    parts = target.split()
    return parts[0] if parts else ''


def link_target_is_relative(raw):
    """Decide whether a markdown link target is relative.

    Absolute (returns False): a scheme-qualified URL, a
    protocol-relative //host URL, or a pure in-page #anchor (which
    resolves against the rendered page wherever it is shown). Anything
    else -- docs/x.md, ./x.md, ../x.md, /x, x.md -- is relative and
    breaks when the README is rendered off the repo landing page
    (PyPI, crates.io, mirrors).
    """
    target = link_target(raw)

    if not target:
        return False
    if target.startswith('#'):
        return False
    if target.startswith('//'):
        return False
    if URL_SCHEME_RE.match(target):
        return False
    return True


def check_readme_absolute_links(repo_path, props):
    """Check that every link in the top-level README.md is absolute.

    Only the top-level README.md is audited: it is the file rendered
    off the repository landing page (PyPI long description, crates.io,
    mirrors), where relative links -- resolved against the wrong base
    -- silently break. READMEs in subdirectories are only ever viewed
    on the GitHub tree, where relative links resolve correctly, so
    they are intentionally out of scope.
    """
    if not check_file_exists(repo_path, 'README.md'):
        return {
            'id': 'readme-absolute-links',
            'status': 'not_applicable',
            'details': 'No top-level README.md',
        }

    with open(
        os.path.join(repo_path, 'README.md'), 'r', errors='replace'
    ) as f:
        content = f.read()

    scannable = strip_markdown_code(content)
    relative = []
    for match in MD_LINK_RE.finditer(scannable):
        if link_target_is_relative(match.group(1)):
            relative.append(match.group(1).strip())
    for match in MD_REFDEF_RE.finditer(scannable):
        if link_target_is_relative(match.group(1)):
            relative.append(match.group(1).strip())

    if relative:
        uniq = sorted(set(relative))
        shown = ', '.join(uniq[:10])
        more = '' if len(uniq) <= 10 else f' (+{len(uniq) - 10} more)'
        return {
            'id': 'readme-absolute-links',
            'status': 'fail',
            'details': (
                f'{len(uniq)} relative link target(s) in README.md '
                f'(use absolute URLs so the README renders off the '
                f'repo landing page): {shown}{more}'
            ),
        }
    return {
        'id': 'readme-absolute-links',
        'status': 'pass',
        'details': 'All README.md links are absolute',
    }


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


def check_docs_external_links(repo_path, props):
    """Check docs/ links resolve inside docs/, or else are absolute.

    docs/ is not only rendered on the GitHub file tree. It is
    synchronised into shakenfist/shakenfist under docs/components/
    and published on shakenfist.com, where the tree above docs/ does
    not exist. A relative link out of docs/ -- ../tools/x.sh,
    ../README.md -- resolves against the wrong base there and 404s,
    while the same link looks fine on GitHub, so nothing catches it.

    Links whose target stays inside docs/ are fine and stay relative:
    they move with the tree and resolve in both renderings. Anything
    pointing outside docs/ must be an absolute
    https://github.com/<org>/<repo>/blob/<branch>/... URL.

    A relative target that resolves inside docs/ but names no file
    that exists is reported too. It is nearly always a link out of
    docs/ written against the repository root (ryll/src/app.rs rather
    than ../../ryll/src/app.rs), which is the same defect wearing a
    different spelling, and it is dead on GitHub as well.

    Site-root-absolute targets (/operator_guide/locks/) are left
    alone. They are the mkdocs convention for addressing another page
    of the same site and resolve on the published site, which is the
    rendering this audit exists to protect.
    """
    if not os.path.isdir(os.path.join(repo_path, 'docs')):
        return {
            'id': 'docs-external-links',
            'status': 'not_applicable',
            'details': 'No docs/ directory',
        }

    offenders = []
    for rel_path in iter_docs_markdown_files(repo_path, props):
        with open(
            os.path.join(repo_path, rel_path), 'r', errors='replace'
        ) as f:
            scannable = strip_markdown_code(f.read())

        rel_dir = os.path.dirname(rel_path)
        raw_targets = [m.group(1) for m in MD_LINK_RE.finditer(scannable)]
        raw_targets += [m.group(1) for m in MD_REFDEF_RE.finditer(scannable)]
        for raw in raw_targets:
            if not link_target_is_relative(raw):
                continue
            target = link_target(raw)
            if target.startswith('/'):
                continue
            # Drop the fragment: it addresses a heading in the target
            # document, not a path component.
            path = target.split('#', 1)[0]
            if not path:
                continue
            resolved = os.path.normpath(
                os.path.join(rel_dir, urllib.parse.unquote(path))
            )
            if resolved == 'docs' or resolved.startswith('docs/'):
                if os.path.exists(os.path.join(repo_path, resolved)):
                    continue
            offenders.append(f'{rel_path} -> {target}')

    if offenders:
        uniq = sorted(set(offenders))
        shown = ', '.join(uniq[:10])
        more = '' if len(uniq) <= 10 else f' (+{len(uniq) - 10} more)'
        return {
            'id': 'docs-external-links',
            'status': 'fail',
            'details': (
                f'{len(uniq)} relative link(s) in docs/ that do not '
                f'resolve to a file inside docs/ (use absolute '
                f'https://github.com/... URLs, which survive the docs '
                f'site import): {shown}{more}'
            ),
        }
    return {
        'id': 'docs-external-links',
        'status': 'pass',
        'details': 'All links out of docs/ are absolute',
    }


# README structure limits: the top-level README is a pitch, not a
# reference manual. See audits/readme-structure.md.
README_MAX_LINES = 150
README_MAX_WORDS = 1200


def check_readme_structure(repo_path, props):
    """Check that the top-level README.md reads as a pitch.

    "Is this a good pitch" is a judgment call enforced at push time by
    the readme-discipline shared block (see check_push_audit); this
    check enforces the measurable proxies: a length cap, and a link
    into docs/ when a docs/ directory exists.
    """
    if not check_file_exists(repo_path, 'README.md'):
        return {
            'id': 'readme-structure',
            'status': 'not_applicable',
            'details': 'No top-level README.md',
        }

    with open(
        os.path.join(repo_path, 'README.md'), 'r', errors='replace'
    ) as f:
        content = f.read()

    problems = []
    lines = len(content.splitlines())
    words = len(content.split())
    if lines > README_MAX_LINES or words > README_MAX_WORDS:
        problems.append(
            f'README.md is {lines} lines / {words} words (limits: '
            f'{README_MAX_LINES} lines, {README_MAX_WORDS} words); '
            f'move detail into docs/ and keep the README a pitch'
        )

    if os.path.isdir(os.path.join(repo_path, 'docs')):
        scannable = strip_markdown_code(content)
        targets = [
            m.group(1) for m in MD_LINK_RE.finditer(scannable)
        ] + [
            m.group(1) for m in MD_REFDEF_RE.finditer(scannable)
        ]
        if not any('docs/' in t for t in targets):
            problems.append(
                'README.md has no link into docs/ despite a docs/ '
                'directory existing; add curated links to the '
                'detailed documentation'
            )

    if problems:
        return {
            'id': 'readme-structure',
            'status': 'fail',
            'details': '; '.join(problems),
        }
    return {
        'id': 'readme-structure',
        'status': 'pass',
        'details': 'README.md is pitch-sized and links into docs/',
    }


# AGENTS.md / ARCHITECTURE.md structure limits: both files are a
# summary and an index into docs/, not reference manuals. AGENTS.md
# is loaded into every session, so it gets the tighter cap.
# See audits/llm-doc-structure.md.
LLM_DOC_LIMITS = {
    'AGENTS.md': (300, 2500),
    'ARCHITECTURE.md': (500, 4000),
}
LLM_DOC_STRUCTURE_OK = '<!-- audit-ok: llm-doc-structure -->'

# A reference to a documentation page under docs/, in any form: a
# markdown link target, an inline-code path, or bare prose. Unlike
# README.md -- which is rendered off the repository landing page and
# so needs real absolute links (see readme-absolute-links) -- these
# two files are read on GitHub and by agents, where a backticked
# `docs/design-tokens.md` points just as well as a link does.
# docs/plans/ is excluded: a plan is a design record, not the
# documentation these files should be delegating to.
DOCS_PAGE_REFERENCE_RE = re.compile(r'\bdocs/(?!plans/)[\w./-]+\.md\b')


def iter_markdown_headings(content, levels=(2, 3)):
    """Yield (level, text, line) for ATX headings outside code fences.

    A `## foo` inside a fenced block is sample text, not a heading, so
    fenced regions are skipped the same way strip_markdown_code skips
    them. The raw line comes back too, so callers can look for an
    audit-ok marker on it.
    """
    fence = None
    for line in content.splitlines():
        stripped = line.lstrip()
        marker = None
        if stripped.startswith('```'):
            marker = '```'
        elif stripped.startswith('~~~'):
            marker = '~~~'

        if fence is not None:
            if marker == fence:
                fence = None
            continue
        if marker is not None:
            fence = marker
            continue

        match = re.match(r'(#{1,6})\s+(.*)', stripped)
        if match and len(match.group(1)) in levels:
            text = match.group(2).strip().rstrip('#').strip()
            if text:
                yield len(match.group(1)), text, line


def normalise_heading(text):
    """Fold a heading (or a docs/ filename stem) to a comparison key.

    Case and hyphen-versus-space are presentation, not meaning:
    `## Code Organisation`, `## code organisation` and
    `docs/code-organisation.md` are all the same subject.
    """
    return re.sub(r'\s+', ' ', text.replace('-', ' ')).strip().lower()


def check_llm_doc_structure(repo_path, props):
    """Check AGENTS.md and ARCHITECTURE.md are a summary and an index.

    The llm-tooling check covers whether these files exist; this one
    covers their shape. "Is this a good summary" is a judgment call
    enforced at push time by the llm-doc-discipline shared block (see
    check_push_audit); this check enforces the measurable proxies: a
    length cap per file, a pointer to a docs/ page when docs/ holds
    any, and two duplication signals -- a heading shared between the
    two files, and a heading naming a page that docs/ already has.
    """
    present = {
        name: os.path.join(repo_path, name)
        for name in LLM_DOC_LIMITS
        if check_file_exists(repo_path, name)
    }
    if not present:
        return {
            'id': 'llm-doc-structure',
            'status': 'not_applicable',
            'details': 'No AGENTS.md or ARCHITECTURE.md',
        }

    contents = {}
    for name, path in present.items():
        with open(path, 'r', errors='replace') as f:
            contents[name] = f.read()

    # docs/ pages the two files could be delegating to, keyed by
    # normalised filename stem. A docs/ directory holding nothing but
    # plans/ has no documentation to point at, so an empty mapping
    # switches both docs/-aware proxies off rather than demanding a
    # pointer to something that does not exist.
    docs_pages = {}
    docs_dir = os.path.join(repo_path, 'docs')
    if os.path.isdir(docs_dir):
        for entry in sorted(os.listdir(docs_dir)):
            if entry.endswith('.md') and entry != 'index.md':
                docs_pages[normalise_heading(entry[:-3])] = f'docs/{entry}'
    has_docs = bool(docs_pages) or os.path.exists(
        os.path.join(docs_dir, 'index.md')
    )
    problems = []

    for name in sorted(contents):
        content = contents[name]
        max_lines, max_words = LLM_DOC_LIMITS[name]
        lines = len(content.splitlines())
        words = len(content.split())
        if lines > max_lines or words > max_words:
            problems.append(
                f'{name} is {lines} lines / {words} words (limits: '
                f'{max_lines} lines, {max_words} words); move detail '
                f'into docs/ and leave a summary and a link'
            )

        if has_docs and not DOCS_PAGE_REFERENCE_RE.search(content):
            problems.append(
                f'{name} references no page under docs/ despite a '
                f'docs/ directory existing; it should point at the '
                f'detailed documentation rather than restate it'
            )

    # Duplication signal one: the same subject documented in both
    # files. Only ## headings, because ### headings are subdivisions
    # whose names collide innocently ("Overview", "Example").
    headings = {}
    for name, content in contents.items():
        headings[name] = {
            normalise_heading(text): line
            for _, text, line in iter_markdown_headings(
                content, levels=(2,)
            )
        }
    if len(headings) == 2:
        agents, architecture = (
            headings['AGENTS.md'], headings['ARCHITECTURE.md']
        )
        shared = sorted(
            key for key in agents.keys() & architecture.keys()
            if LLM_DOC_STRUCTURE_OK not in agents[key]
            and LLM_DOC_STRUCTURE_OK not in architecture[key]
        )
        if shared:
            problems.append(
                'AGENTS.md and ARCHITECTURE.md share the headings '
                + ', '.join(f'"{key}"' for key in shared)
                + '; give each fact one home and link to it from the '
                'other file'
            )

    # Duplication signal two: a heading naming a docs/ page. index.md
    # is excluded from docs_pages because a "## Index" style heading
    # pointing at it is exactly the behaviour we want.
    if docs_pages:
        for name in sorted(contents):
            hits = sorted({
                f'"{text}" ({docs_pages[normalise_heading(text)]})'
                for _, text, line in iter_markdown_headings(
                    contents[name]
                )
                if normalise_heading(text) in docs_pages
                and LLM_DOC_STRUCTURE_OK not in line
            })
            if hits:
                problems.append(
                    f'{name} has headings restating a docs/ page: '
                    + ', '.join(hits)
                    + '; summarise and link instead'
                )

    if problems:
        return {
            'id': 'llm-doc-structure',
            'status': 'fail',
            'details': '; '.join(problems),
        }
    return {
        'id': 'llm-doc-structure',
        'status': 'pass',
        'details': (
            'AGENTS.md and ARCHITECTURE.md are summary-sized and do '
            'not restate docs/'
        ),
    }


# Plan phase references: documentation outside plans directories
# describes current behaviour, not the phase of the plan that built
# it. See audits/plan-phase-references.md.
PHASE_REFERENCE_RE = re.compile(r'\bphases?\s+\d+\b', re.IGNORECASE)
PHASE_REFERENCE_OK = '<!-- audit-ok: phase-reference -->'


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


def check_plan_phase_references(repo_path, props):
    """Check documentation does not cite implementation plan phases.

    Docs describe the current state of the software; "implemented in
    phase 5" describes the history of how it was built, usually
    without even naming the plan. The word "phase" is reserved for
    plan documents (procedural docs use "step" or "stage"), so any
    "phase <number>" outside a plans/ directory is flagged. Fenced
    code, inline code spans, and lines carrying the
    audit-ok: phase-reference marker are skipped.
    """
    files = list(iter_doc_content_files(repo_path, props))
    if not files:
        return {
            'id': 'plan-phase-references',
            'status': 'not_applicable',
            'details': 'No documentation content to audit',
        }

    hits = []
    for rel in files:
        with open(
            os.path.join(repo_path, rel), 'r', errors='replace'
        ) as f:
            content = f.read()

        fence = None
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.lstrip()
            marker = None
            if stripped.startswith('```'):
                marker = '```'
            elif stripped.startswith('~~~'):
                marker = '~~~'

            if fence is not None:
                if marker == fence:
                    fence = None
                continue
            if marker is not None:
                fence = marker
                continue
            if PHASE_REFERENCE_OK in line:
                continue
            scannable = re.sub(r'`+[^`\n]*`+', '', line)
            if PHASE_REFERENCE_RE.search(scannable):
                hits.append(f'{rel}:{lineno}')

    if hits:
        shown = ', '.join(hits[:10])
        more = '' if len(hits) <= 10 else f' (+{len(hits) - 10} more)'
        return {
            'id': 'plan-phase-references',
            'status': 'fail',
            'details': (
                f'{len(hits)} plan phase reference(s) in '
                f'documentation (describe the current behaviour, or '
                f'link the master plan in docs/plans/ instead of '
                f'citing a phase number): {shown}{more}'
            ),
        }
    return {
        'id': 'plan-phase-references',
        'status': 'pass',
        'details': (
            'No plan phase references in README.md or docs/'
        ),
    }


# Plan source references: a plan pointer written into source or
# configuration must resolve in this repository, or else be an
# absolute URL. See audits/plan-source-references.md.
PLAN_SOURCE_REF_RE = re.compile(r'[\w./-]*PLAN-[\w.-]*\.md')
PLAN_SOURCE_URL_RE = re.compile(r'[a-z][a-z0-9+.-]*://\S*', re.IGNORECASE)
PLAN_SOURCE_REF_OK = 'audit-ok: plan-reference'

# The file-scope form of the marker above, for a file that is made of
# plan paths rather than merely containing one -- a suite exercising
# this check has to build both references that resolve and references
# that deliberately do not, and neither kind is a pointer a reader
# follows. It exempts the whole file, so it is the blunter instrument
# of the two: a file carrying it stops being audited for plan
# references entirely, including for prose that really has rotted.
# Prefer the line marker, and say in the file why the exemption is
# right.
PLAN_SOURCE_FILE_OK = 'audit-ok: plan-reference-file'

PLAN_SOURCE_MAX_BYTES = 2 * 1024 * 1024

# PLAN-TEMPLATE.md is not a plan. It is the template plans are written
# from, it sits at the repository root rather than in docs/plans/, and
# the plan-template audit is what holds it there. Naming it in a script
# or a config is therefore not a pointer into docs/plans/ that can rot
# out from under a reader, so it is not this audit's business.
PLAN_SOURCE_TEMPLATE_NAME = 'PLAN-TEMPLATE.md'


def plan_file_names(repo_path):
    """Basenames of every markdown file under docs/plans/, any depth.

    Archived plans live in docs/plans/completed/, so the index is
    built recursively: a bare `PLAN-<name>.md` in a comment names no
    directory and should resolve wherever the file actually sits.
    """
    names = set()
    for _dirpath, _dirnames, filenames in os.walk(
        os.path.join(repo_path, 'docs', 'plans')
    ):
        for filename in filenames:
            if filename.endswith('.md'):
                names.add(filename)
    return names


def plan_reference_resolves(repo_path, token, names):
    """Whether a plan reference names a file this repository has.

    A path-qualified reference (docs/plans/PLAN-<name>.md) is resolved
    as written, from the repository root and then from docs/ -- the
    latter because mkdocs navigation addresses pages relative to the
    documentation root. A bare filename is matched against every
    plan file in the repository.
    """
    if os.path.exists(os.path.join(repo_path, token)):
        return True
    if os.path.exists(os.path.join(repo_path, 'docs', token)):
        return True
    return '/' not in token and token in names


def check_plan_source_references(repo_path, props):
    """Check plan references in source and configuration resolve.

    Comments and configuration point at docs/plans/PLAN-*.md to say
    where a decision is recorded. Nothing renders those pointers, so
    when a plan is renamed or archived into docs/plans/completed/
    they rot silently. Every reference must resolve in this
    repository or be an absolute URL; markdown files are out of
    scope, being covered by docs-external-links.

    A test suite is deliberately not out of scope. Test files carry
    rotted pointers like anything else -- instar's
    tests/test_adversarial.py cites a plan that no longer exists in
    its module docstring -- so a suite that genuinely is all fixture
    paths marks itself with PLAN_SOURCE_FILE_OK rather than being
    skipped by its name.
    """
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'ls-files'],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            'id': 'plan-source-references',
            'status': 'fail',
            'details': f'Could not run git ls-files: {e}',
        }

    names = plan_file_names(repo_path)
    hits = []
    total = 0
    for rel in result.stdout.splitlines():
        rel = rel.strip()
        if not rel or rel.endswith('.md'):
            continue
        path = os.path.join(repo_path, rel)
        if not os.path.isfile(path):
            continue
        if os.path.getsize(path) > PLAN_SOURCE_MAX_BYTES:
            continue
        with open(path, 'r', errors='replace') as f:
            content = f.read()
        if 'PLAN-' not in content:
            continue
        if PLAN_SOURCE_FILE_OK in content:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if PLAN_SOURCE_REF_OK in line:
                continue
            scannable = PLAN_SOURCE_URL_RE.sub('', line)
            for match in PLAN_SOURCE_REF_RE.finditer(scannable):
                token = match.group(0)
                if os.path.basename(token) == PLAN_SOURCE_TEMPLATE_NAME:
                    continue
                total += 1
                if not plan_reference_resolves(repo_path, token, names):
                    hits.append(f'{rel}:{lineno} -> {token}')

    if not total:
        return {
            'id': 'plan-source-references',
            'status': 'not_applicable',
            'details': 'No plan references outside markdown files',
        }

    if hits:
        shown = ', '.join(hits[:10])
        more = '' if len(hits) <= 10 else f' (+{len(hits) - 10} more)'
        return {
            'id': 'plan-source-references',
            'status': 'fail',
            'details': (
                f'{len(hits)} of {total} plan reference(s) in source '
                f'or configuration do not resolve (update the path, '
                f'or use an absolute https://github.com/... URL for a '
                f'plan in another repository): {shown}{more}'
            ),
        }
    return {
        'id': 'plan-source-references',
        'status': 'pass',
        'details': (
            f'All {total} plan reference(s) outside markdown resolve'
        ),
    }


# --- Shared blocks ---
# Canonical wording embedded verbatim across repositories, delimited
# by versioned markers. Canonical copies live in
# templates/shared-blocks/<name>.md in the development repository.
# See templates/shared-blocks/README.md for the mechanism.

SHARED_BLOCK_BEGIN_RE = re.compile(
    r'<!--\s*shared-block:\s*([a-z0-9-]+)\s+v(\d+)\s*-->'
)
SHARED_BLOCK_END = '<!-- shared-block-end -->'

SHARED_BLOCKS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'templates', 'shared-blocks',
)


def normalize_block(text):
    """Normalize a block for comparison.

    Verbatim means verbatim, but trailing whitespace and line-ending
    differences are invisible in an editor and should not fail the
    audit.
    """
    return '\n'.join(
        line.rstrip() for line in text.strip().splitlines()
    )


def extract_shared_blocks(content):
    """Extract shared blocks from file content.

    Returns a list of (name, version, block_text) tuples, where
    block_text runs from the begin marker to the end marker
    inclusive. A block missing its end marker yields block_text of
    None.
    """
    blocks = []
    for match in SHARED_BLOCK_BEGIN_RE.finditer(content):
        end = content.find(SHARED_BLOCK_END, match.end())
        if end == -1:
            blocks.append((match.group(1), int(match.group(2)), None))
        else:
            blocks.append((
                match.group(1),
                int(match.group(2)),
                content[match.start():end + len(SHARED_BLOCK_END)],
            ))
    return blocks


def load_canonical_block(name, blocks_dir=None):
    """Load a canonical shared block from templates/shared-blocks/.

    Returns a (version, block_text) tuple, or None if no canonical
    file exists for the name.
    """
    path = os.path.join(blocks_dir or SHARED_BLOCKS_DIR, f'{name}.md')
    if not os.path.exists(path):
        return None
    with open(path, 'r', errors='replace') as f:
        content = f.read()
    for bname, version, text in extract_shared_blocks(content):
        if bname == name and text is not None:
            return (version, text)
    return None


def validate_shared_blocks(content, required=None, blocks_dir=None):
    """Validate every shared block embedded in content.

    required is an iterable of block names that must be present.
    Returns a list of problem strings; empty means compliant.
    """
    problems = []
    embedded = extract_shared_blocks(content)
    seen = set()
    for name, version, text in embedded:
        seen.add(name)
        if text is None:
            problems.append(
                f'shared block {name} has no '
                f'{SHARED_BLOCK_END} marker'
            )
            continue
        canonical = load_canonical_block(name, blocks_dir=blocks_dir)
        if canonical is None:
            problems.append(
                f'unknown shared block {name} (no canonical copy in '
                f'templates/shared-blocks/)'
            )
            continue
        canonical_version, canonical_text = canonical
        if version != canonical_version:
            problems.append(
                f'shared block {name} is stale (v{version} embedded, '
                f'v{canonical_version} current)'
            )
        elif normalize_block(text) != normalize_block(canonical_text):
            problems.append(
                f'shared block {name} has drifted from the canonical '
                f'wording in templates/shared-blocks/{name}.md'
            )
    for name in (required or []):
        if name not in seen:
            problems.append(
                f'missing shared block {name} (copy it verbatim from '
                f'templates/shared-blocks/{name}.md in the '
                f'development repository)'
            )
    return problems


def check_push_audit(repo_path, props, blocks_dir=None):
    """Check the pre-push audit file name and its shared blocks.

    The pre-push audit runbook must be named PUSH-AUDIT.md (the
    historical PUSH-TEMPLATE.md name is flagged as legacy) and must
    embed the current readme-discipline, llm-doc-discipline,
    comment-proportion and plan-phase-references shared blocks.
    Repositories with no pre-push audit file at all are N/A --
    whether every project should have one is a separate decision.
    """
    has_new = check_file_exists(repo_path, 'PUSH-AUDIT.md')
    has_legacy = check_file_exists(repo_path, 'PUSH-TEMPLATE.md')
    if not has_new and not has_legacy:
        return {
            'id': 'push-audit',
            'status': 'not_applicable',
            'details': 'No pre-push audit file',
        }

    problems = []
    if has_legacy:
        problems.append(
            'legacy filename PUSH-TEMPLATE.md (rename to '
            'PUSH-AUDIT.md and update references)'
        )

    filename = 'PUSH-AUDIT.md' if has_new else 'PUSH-TEMPLATE.md'
    with open(
        os.path.join(repo_path, filename), 'r', errors='replace'
    ) as f:
        content = f.read()
    problems += validate_shared_blocks(
        content,
        required=[
            'readme-discipline', 'llm-doc-discipline',
            'comment-proportion', 'plan-phase-references',
        ],
        blocks_dir=blocks_dir,
    )

    if problems:
        return {
            'id': 'push-audit',
            'status': 'fail',
            'details': '; '.join(problems),
        }
    return {
        'id': 'push-audit',
        'status': 'pass',
        'details': (
            'PUSH-AUDIT.md carries current shared blocks'
        ),
    }


# The controlled vocabulary a plan status cell may use, canonically
# documented in templates/shared-blocks/plan-status-vocabulary.md. A
# test asserts the two agree, so the wording repositories are handed
# and the wording we enforce cannot drift apart.
PLAN_STATUSES = (
    'Proposed',
    'Not started',
    'In progress',
    'Blocked',
    'Complete',
    'Abandoned',
    'Superseded',
)

# The leading columns every docs/plans/index.md table must carry, in
# this order. Chronological order is the reading order for a plan
# index, and a fixed column order is what lets tooling find the status
# without guessing.
PLAN_INDEX_LEAD_COLUMNS = ('date', 'plan')

PLAN_INDEX_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# A phase plan is named after its master plan, so it is not itself an
# entry the index has to carry.
PLAN_PHASE_FILE_RE = re.compile(r'-phase-\d')

# Markdown decoration ignored when reading a table cell.
PLAN_CELL_DECORATION_RE = re.compile(r'[`*_~]')

PLAN_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

PLAN_TABLE_SEPARATOR_RE = re.compile(r'^[\s|:-]+$')

# How many offending items to name before summarising.
PLAN_INDEX_MAX_SHOWN = 5


def plan_index_cells(line):
    """The cells of a markdown table row, outer empties trimmed."""
    return [c.strip() for c in line.strip().strip('|').split('|')]


def plan_cell_text(cell):
    """A cell's text, with any markdown link collapsed to its label."""
    return PLAN_CELL_DECORATION_RE.sub(
        '', PLAN_LINK_RE.sub(r'\1', cell)
    ).strip()


def plan_index_summarise(label, items):
    """One problem string naming at most PLAN_INDEX_MAX_SHOWN items."""
    shown = ', '.join(items[:PLAN_INDEX_MAX_SHOWN])
    more = (
        '' if len(items) <= PLAN_INDEX_MAX_SHOWN
        else f' (+{len(items) - PLAN_INDEX_MAX_SHOWN} more)'
    )
    return f'{label}: {shown}{more}'


def check_plan_index(repo_path, props):
    """Check docs/plans/index.md layout, ordering and statuses.

    The index is the one place that answers "what has this repository
    planned, and what still wants attention". That only works if it is
    mechanically readable, so every table in it leads with a Date
    column and a Plan column, rows run in date order, every master
    plan is listed, and status cells are drawn from the shared
    vocabulary rather than written as prose.

    Repositories with no docs/plans/ directory are N/A. Whether every
    project should plan this way is a separate decision.
    """
    plans_dir = os.path.join(repo_path, 'docs', 'plans')
    if not os.path.isdir(plans_dir):
        return {
            'id': 'plan-index',
            'status': 'not_applicable',
            'details': 'No docs/plans/ directory',
        }

    masters = sorted(
        name for name in os.listdir(plans_dir)
        if name.endswith('.md')
        and name != 'index.md'
        and not PLAN_PHASE_FILE_RE.search(name)
        and os.path.isfile(os.path.join(plans_dir, name))
    )
    index_path = os.path.join(plans_dir, 'index.md')
    if not os.path.exists(index_path):
        if not masters:
            return {
                'id': 'plan-index',
                'status': 'not_applicable',
                'details': 'No plans in docs/plans/',
            }
        return {
            'id': 'plan-index',
            'status': 'fail',
            'details': (
                f'docs/plans/index.md is missing, so none of the '
                f'{len(masters)} plan(s) in docs/plans/ are registered'
            ),
        }

    with open(index_path, 'r', errors='replace') as f:
        content = f.read()

    linked = set()
    statuses = {s.lower() for s in PLAN_STATUSES}

    # Findings are gathered per table and only reported for tables that
    # turn out to list plans. An index may hold a table that is not a
    # plan listing at all, and holding a legend to the plan layout
    # would be a finding nobody could act on.
    tables = []
    current = None
    lines = content.splitlines()
    for offset, line in enumerate(lines):
        lineno = offset + 1
        for _, target in PLAN_LINK_RE.findall(line):
            name = target.split('#')[0].strip()
            if name.endswith('.md'):
                linked.add(os.path.basename(name))

        stripped = line.strip()
        if not stripped.startswith('|'):
            # Prose between tables ends the run of rows, so two
            # adjacent tables never compare dates across the boundary.
            current = None
            continue
        if PLAN_TABLE_SEPARATOR_RE.match(stripped):
            continue

        cells = plan_index_cells(stripped)

        # A header is the row the separator underlines. Recognising it
        # by its position rather than by "has no links" means a data
        # row that happens to carry no link cannot be mistaken for the
        # start of a new table.
        following = (
            lines[offset + 1].strip() if offset + 1 < len(lines) else ''
        )
        if (following.startswith('|')
                and PLAN_TABLE_SEPARATOR_RE.match(following)):
            columns = [plan_cell_text(c).lower() for c in cells]
            current = {
                'columns': columns,
                'lead_ok': (tuple(columns[:len(PLAN_INDEX_LEAD_COLUMNS)])
                            == PLAN_INDEX_LEAD_COLUMNS),
                'header': f'line {lineno} starts "{" | ".join(cells[:2])}"',
                'has_plans': False,
                'previous_date': None,
                'bad_dates': [],
                'unsorted': [],
                'bad_statuses': [],
            }
            tables.append(current)
            continue

        if current is None or len(current['columns']) < 2 or len(cells) < 2:
            continue
        if PLAN_LINK_RE.search(stripped):
            current['has_plans'] = True
        if not current['lead_ok']:
            # The column order is already reported; reading dates and
            # statuses out of the wrong columns would only add noise.
            continue

        plan = plan_cell_text(cells[1]) or f'line {lineno}'

        date = plan_cell_text(cells[0])
        if not PLAN_INDEX_DATE_RE.match(date):
            current['bad_dates'].append(f'{plan} ("{date}")')
        else:
            previous = current['previous_date']
            if previous is not None and date < previous:
                current['unsorted'].append(f'{plan} ({date} after {previous})')
            current['previous_date'] = date

        if 'status' in current['columns']:
            index = current['columns'].index('status')
            if index < len(cells):
                status = plan_cell_text(cells[index])
                if status.lower() not in statuses:
                    excerpt = (
                        status if len(status) <= 40 else status[:37] + '...'
                    )
                    current['bad_statuses'].append(f'{plan} ("{excerpt}")')

    plan_tables = [t for t in tables if t['has_plans']]

    problems = []
    if not plan_tables:
        problems.append(
            'index has no plan table (it must list plans in a table '
            'led by Date and Plan columns, not as prose or a bullet '
            'list)'
        )

    bad_columns = [t['header'] for t in plan_tables if not t['lead_ok']]
    if bad_columns:
        problems.append(plan_index_summarise(
            f'{len(bad_columns)} table(s) not led by Date then Plan '
            f'columns', bad_columns))

    bad_dates = [item for t in plan_tables for item in t['bad_dates']]
    if bad_dates:
        problems.append(plan_index_summarise(
            f'{len(bad_dates)} row(s) without a YYYY-MM-DD date',
            bad_dates))

    unsorted = [item for t in plan_tables for item in t['unsorted']]
    if unsorted:
        problems.append(plan_index_summarise(
            f'{len(unsorted)} row(s) out of date order', unsorted))

    bad_statuses = [item for t in plan_tables for item in t['bad_statuses']]
    if bad_statuses:
        problems.append(plan_index_summarise(
            f'{len(bad_statuses)} status cell(s) outside the shared '
            f'vocabulary ({", ".join(PLAN_STATUSES)})', bad_statuses))

    unregistered = [name for name in masters if name not in linked]
    if unregistered:
        problems.append(plan_index_summarise(
            f'{len(unregistered)} master plan(s) not listed in the '
            f'index', unregistered))

    if problems:
        return {
            'id': 'plan-index',
            'status': 'fail',
            'details': '; '.join(problems),
        }
    return {
        'id': 'plan-index',
        'status': 'pass',
        'details': (
            f'docs/plans/index.md lists {len(masters)} plan(s) in date '
            f'order with statuses from the shared vocabulary'
        ),
    }


# Shared blocks every PLAN-TEMPLATE.md must carry. The model roster
# is deliberately separate from the rest of the step guidance: it
# churns whenever a model ships or retires, and keeping it apart
# means the issue filed against a lagging repository names the
# roster rather than the surrounding prose.
PLAN_TEMPLATE_BLOCKS = [
    'plan-file-conventions',
    'plan-status-vocabulary',
    'subagent-execution-model',
    'plan-planning-effort',
    'subagent-step-guidance',
    'subagent-model-roster',
    'plan-review-checklist',
    'plan-closeout-sections',
]


def check_plan_template(repo_path, props, blocks_dir=None):
    """Check PLAN-TEMPLATE.md carries the current shared blocks.

    The generic half of a plan template -- phase file naming, the
    sub-agent execution model, the effort ladder, the model roster,
    the review checklist and the close-out sections -- is shared
    fleet-wide; only the project-specific half (what to read before
    planning, the success criteria) is written per repository.

    Repositories with no PLAN-TEMPLATE.md at all are N/A: whether
    every project should have one is a separate decision.
    """
    if not check_file_exists(repo_path, 'PLAN-TEMPLATE.md'):
        return {
            'id': 'plan-template',
            'status': 'not_applicable',
            'details': 'No PLAN-TEMPLATE.md',
        }

    with open(
        os.path.join(repo_path, 'PLAN-TEMPLATE.md'), 'r',
        errors='replace',
    ) as f:
        content = f.read()
    problems = validate_shared_blocks(
        content,
        required=PLAN_TEMPLATE_BLOCKS,
        blocks_dir=blocks_dir,
    )

    if problems:
        return {
            'id': 'plan-template',
            'status': 'fail',
            'details': '; '.join(problems),
        }
    return {
        'id': 'plan-template',
        'status': 'pass',
        'details': 'PLAN-TEMPLATE.md carries current shared blocks',
    }


# Scanners we accept, by the name they are invoked under. gitleaks
# is the reference implementation; the others are equivalent enough
# that requiring a specific one would be churn for no gain.
SECRET_SCANNERS = ['gitleaks', 'trufflehog', 'detect-secrets']


def check_secret_scanning_ci(repo_path, props):
    """Check a repository secret scanner runs in CI.

    Any of the scanners in SECRET_SCANNERS, invoked from any
    workflow, satisfies this. We deliberately do not check how it
    is invoked or on which triggers -- a scanner running at all is
    the step change, and pinning the invocation would make the
    check brittle against reasonable variation.

    Note this covers only the scanner. The credential handling
    patterns in audits/secret-handling.md are review criteria; a
    pass here does not mean a project keeps credentials out of its
    logs.
    """
    if props['is_docs_only']:
        return {
            'id': 'secret-scanning-ci',
            'status': 'not_applicable',
            'details': 'Documentation-only repository',
        }

    if not props['has_workflows_dir']:
        return {
            'id': 'secret-scanning-ci',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'secret-scanning-ci',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    for workflow in workflows:
        path = os.path.join(repo_path, '.github', 'workflows', workflow)
        try:
            with open(path, 'r', errors='replace') as f:
                content = f.read()
        except OSError:
            continue

        # Full-line comments do not count. Workflows routinely mention
        # a scanner in a header comment explaining that some other
        # workflow runs it, and matching those would report a project
        # as compliant for describing the thing it does not do.
        content = '\n'.join(
            line for line in content.splitlines()
            if not line.lstrip().startswith('#')
        )

        for scanner in SECRET_SCANNERS:
            if scanner in content:
                return {
                    'id': 'secret-scanning-ci',
                    'status': 'pass',
                    'details': f'{scanner} runs in {workflow}',
                }

    return {
        'id': 'secret-scanning-ci',
        'status': 'fail',
        'details': (
            f'No secret scanner in CI; expected one of '
            f'{", ".join(SECRET_SCANNERS)} in a workflow'
        ),
    }


# Directories whose markdown is loaded as agent context. A skill is a
# directory holding SKILL.md; a bare markdown file alongside them is
# inert, which is the failure this audit exists to catch.
SKILL_ROOTS = ('.claude/skills', '.codex/skills')

# Markdown that legitimately sits beside skill directories rather than
# being a skill itself.
ALLOWED_LOOSE_SKILL_FILES = ('readme.md', 'index.md')

# Files whose presence means a repository has agent context worth
# linting. A repository with none of these has nothing for skillsaw to
# look at, and reporting it either way would be noise.
AGENT_CONTEXT_MARKERS = (
    '.claude', '.codex', 'AGENTS.md', 'CLAUDE.md', 'GEMINI.md',
)

# How a repository is expected to invoke skillsaw. The pre-commit hook
# and the action both live in the upstream repository, so one string
# identifies either.
SKILLSAW_SOURCE = 'stbenjam/skillsaw'

# A CI job which runs pre-commit over the tree runs every hook the
# pre-commit config declares, skillsaw included. Requiring the linter
# to be named in a workflow as well would report those repositories as
# non-compliant for a wiring that does run it -- and would fail the
# reference invocation in this repository's own consistency-audit.yml,
# which installs skillsaw from PyPI and so never names the upstream
# repository either.
PRE_COMMIT_RUN_RE = re.compile(r'pre-commit\s+run\b')


def file_matches(filepath, pattern):
    """Does a file match a regex, outside of its comments?

    The comment handling matches file_mentions: a header comment
    describing what something else does must not count as doing it.
    """
    if not os.path.exists(filepath):
        return False
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            if line.lstrip().startswith('#'):
                continue
            if pattern.search(line):
                return True
    return False


def has_agent_context(repo_path):
    """Does this repository carry agent context files at all?"""
    return any(
        check_file_exists(repo_path, marker)
        for marker in AGENT_CONTEXT_MARKERS
    )


def orphan_skill_markdown(repo_path):
    """Markdown in a skills directory that will never load as a skill.

    A skill is ``<skills dir>/<name>/SKILL.md``. Markdown directly in
    the skills directory, or in a subdirectory with no SKILL.md, is
    read by nobody: the agent does not load it, and skillsaw does not
    lint it either, because it is never discovered as a skill. That
    combination is why this is checked here in Python rather than left
    to skillsaw -- a linter cannot report a file it cannot see, and
    the resulting clean run reads as a pass.
    """
    orphans = []
    for relative in SKILL_ROOTS:
        skills_dir = os.path.join(repo_path, relative)
        if not os.path.isdir(skills_dir):
            continue

        for entry in sorted(os.listdir(skills_dir)):
            path = os.path.join(skills_dir, entry)

            if os.path.isfile(path) and entry.lower().endswith('.md'):
                if entry.lower() in ALLOWED_LOOSE_SKILL_FILES:
                    continue
                orphans.append(f'{relative}/{entry}')
                continue

            if not os.path.isdir(path):
                continue
            if os.path.exists(os.path.join(path, 'SKILL.md')):
                continue
            stray = sorted(
                name for name in os.listdir(path)
                if name.lower().endswith('.md')
            )
            if stray:
                orphans.append(f'{relative}/{entry}/ (no SKILL.md)')

    return orphans


def skillsaw_errors(repo_path):
    """Error-severity skillsaw violations, or None if it cannot run.

    Only error severity is collected. skillsaw's warning and info
    tiers carry style opinions -- unlinked path references alone run
    to dozens per repository -- and an audit that reports them would
    spend more of our time than it saves. The error tier is the
    structural subset: invalid frontmatter, malformed manifests,
    embedded secrets, smuggled unicode.
    """
    try:
        result = subprocess.run(
            [
                'skillsaw', 'lint',
                '--no-progress', '--no-custom-rules',
                '--format', 'json',
                repo_path,
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    return [
        violation for violation in report.get('violations', [])
        if violation.get('severity') == 'error'
    ]


def check_llm_context_lint(repo_path, props):
    """Check the repository's agent context passes skillsaw cleanly.

    Two findings are combined because they answer the same question
    from opposite ends: skillsaw validates the context it can see, and
    orphan_skill_markdown() finds the context it cannot.

    A missing skillsaw reports not_applicable rather than fail. The
    binary is the audit harness's responsibility, not the audited
    repository's, and failing would file an issue against every
    project in the fleet for a problem none of them can fix. The
    consistency-audit workflow installs a pinned skillsaw, so this
    state should not arise; when it does, every row flipping to N/A at
    once is the signal that it has.
    """
    if not has_agent_context(repo_path):
        return {
            'id': 'llm-context-lint',
            'status': 'not_applicable',
            'details': 'No agent context files to lint',
        }

    problems = []

    orphans = orphan_skill_markdown(repo_path)
    if orphans:
        problems.append(
            f'Markdown that will never load as a skill: '
            f'{", ".join(orphans)}'
        )

    errors = skillsaw_errors(repo_path)
    if errors is None:
        return {
            'id': 'llm-context-lint',
            'status': 'not_applicable',
            'details': (
                'skillsaw is not available in the audit environment'
            ),
        }

    if errors:
        described = ', '.join(
            sorted({
                f'{violation.get("rule_id", "unknown")} '
                f'({violation.get("file_path", "?")})'
                for violation in errors
            })
        )
        problems.append(f'skillsaw errors: {described}')

    if problems:
        return {
            'id': 'llm-context-lint',
            'status': 'fail',
            'details': '; '.join(problems),
        }

    return {
        'id': 'llm-context-lint',
        'status': 'pass',
        'details': 'Agent context lints clean at error severity',
    }


def check_llm_context_lint_ci(repo_path, props):
    """Check skillsaw runs in pre-commit and in CI.

    The daily audit is a backstop, not the feedback loop. A malformed
    skill or a smuggled instruction should be caught by the commit
    that introduces it, so the audit checks that each repository runs
    the linter itself rather than waiting to be told once a day.

    As with the secret scanner check, how skillsaw is invoked is
    deliberately not pinned. Naming the upstream repository in a
    pre-commit config and in a workflow is the step change; requiring
    a particular rev or argument list would make the check brittle
    against reasonable variation.
    """
    if not has_agent_context(repo_path):
        return {
            'id': 'llm-context-lint-ci',
            'status': 'not_applicable',
            'details': 'No agent context files to lint',
        }

    missing = []

    pre_commit_config = os.path.join(repo_path, '.pre-commit-config.yaml')
    in_pre_commit = file_mentions(pre_commit_config, SKILLSAW_SOURCE)
    if not in_pre_commit:
        missing.append('.pre-commit-config.yaml')

    workflows = [
        os.path.join(repo_path, '.github', 'workflows', workflow)
        for workflow in list_workflow_files(repo_path)
    ]
    named_in_ci = any(
        file_mentions(workflow, SKILLSAW_SOURCE) for workflow in workflows
    )
    # A workflow which runs pre-commit runs the skillsaw hook with it,
    # so the linter reaches CI without the workflow naming it.
    via_pre_commit = in_pre_commit and any(
        file_matches(workflow, PRE_COMMIT_RUN_RE) for workflow in workflows
    )
    if not named_in_ci and not via_pre_commit:
        missing.append('a CI workflow')

    if missing:
        return {
            'id': 'llm-context-lint-ci',
            'status': 'fail',
            'details': (
                f'skillsaw does not run from {" or ".join(missing)}'
            ),
        }

    return {
        'id': 'llm-context-lint-ci',
        'status': 'pass',
        'details': 'skillsaw runs in pre-commit and in CI',
    }


# Repos with human review tracking deployed should keep the review
# backlog small enough that a session clears it. The value is a
# tuning knob: an absolute count rather than a percentage (agreed
# 2026-08-02), because "how much review work has piled up" does not
# scale with repository size.
REVIEW_BACKLOG_THRESHOLD = 5

REVIEW_TRACKING_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'review-tracking.py'
)


def check_review_coverage(repo_path, props):
    """Check the human review backlog in repos with review tracking.

    Applies only to repositories with the review tracking tooling
    deployed, detected by the presence of the scope config. Coverage
    is recomputed against HEAD by review-tracking.py status rather
    than trusted from the committed REVIEWS.md, so a missed prune
    cannot inflate it. We invoke our sibling copy of the script
    directly rather than the target repo's tools/ wrapper, which
    searches for a development clone the runner does not have.
    """
    if not check_file_exists(repo_path, '.vscode/review-scope.toml'):
        return {
            'id': 'review-coverage',
            'status': 'not_applicable',
            'details': (
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)'
            ),
        }

    try:
        result = subprocess.run(
            [sys.executable, REVIEW_TRACKING_SCRIPT, 'status', '--json'],
            cwd=repo_path, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {
            'id': 'review-coverage',
            'status': 'fail',
            'details': 'review-tracking.py status timed out',
        }
    if result.returncode != 0:
        return {
            'id': 'review-coverage',
            'status': 'fail',
            'details': (
                f'review-tracking.py status failed: '
                f'{result.stderr.strip()}'
            ),
        }
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            'id': 'review-coverage',
            'status': 'fail',
            'details': (
                'review-tracking.py status emitted unparseable JSON'
            ),
        }

    details = (
        f'{status["reviewed"]} of {status["in_scope"]} in-scope '
        f'files reviewed at HEAD; {status["needing_review"]} need '
        f'review (threshold {REVIEW_BACKLOG_THRESHOLD})'
    )
    if status['needing_review'] >= REVIEW_BACKLOG_THRESHOLD:
        # The issue machinery renders 'missing' as a bullet list, so
        # this becomes the review session's work queue.
        missing = (
            [f'stale: {p}' for p in status['stale']]
            + [f'never reviewed: {p}' for p in status['never_reviewed']]
        )
        return {
            'id': 'review-coverage',
            'status': 'fail',
            'details': details,
            'missing': missing,
        }
    return {
        'id': 'review-coverage',
        'status': 'pass',
        'details': details,
    }


# sfui (the Shaken Fist web UI design system) is vendored into
# consumers by its tools/vendor.sh, which stamps .sfui-commit in the
# vendored directory with the canonical commit the copy came from.
SFUI_CANONICAL_URL = 'https://github.com/shakenfist/sfui'


def find_sfui_vendored_dirs(repo_path):
    """Find directories holding a vendored sfui copy.

    A vendored copy is identified by its .sfui-commit provenance
    stamp. Hidden directories are pruned: as well as .git, local
    build state like .tox and .venv can hold site-packages copies
    of a consumer's static assets, which are installation artifacts
    rather than vendored copies. Returns repo-relative directory
    paths.
    """
    found = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        if '.sfui-commit' in files:
            found.append(os.path.relpath(root, repo_path))
    return sorted(found)


def check_sfui_vendor(repo_path, props, canonical_url=None):
    """Check vendored sfui copies are verbatim and current.

    Two failure modes, mirroring the shared-blocks rules: a copy
    that differs from its recorded canonical commit was edited in
    place (lost work -- the next sync silently discards it), and a
    copy behind canonical HEAD is stale (improvements have not
    propagated). The verbatim comparison runs the canonical
    repository's own tools/vendor.sh --check at the recorded
    commit, so the distributable file list always matches the
    commit the copy claims to be. Repositories with no vendored
    copy are N/A.
    """
    vendored = find_sfui_vendored_dirs(repo_path)
    if not vendored:
        return {
            'id': 'sfui-vendor',
            'status': 'not_applicable',
            'details': 'No vendored sfui copy (no .sfui-commit file)',
        }

    problems = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            canonical = os.path.join(tmp, 'sfui')
            clone = subprocess.run(
                [
                    'git', 'clone', '--quiet',
                    canonical_url or SFUI_CANONICAL_URL, canonical,
                ],
                capture_output=True, text=True, timeout=120,
            )
            if clone.returncode != 0:
                return {
                    'id': 'sfui-vendor',
                    'status': 'fail',
                    'details': (
                        f'Could not clone canonical sfui: '
                        f'{clone.stderr.strip()}'
                    ),
                }

            for rel in vendored:
                directory = os.path.join(repo_path, rel)
                with open(
                    os.path.join(directory, '.sfui-commit'), 'r',
                    errors='replace',
                ) as f:
                    sha = f.read().strip()
                if not re.fullmatch(r'[0-9a-f]{40}', sha):
                    problems.append(
                        f'{rel}: .sfui-commit does not contain a '
                        f'commit sha'
                    )
                    continue

                exists = subprocess.run(
                    [
                        'git', '-C', canonical, 'cat-file', '-e',
                        f'{sha}^{{commit}}',
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                if exists.returncode != 0:
                    problems.append(
                        f'{rel}: recorded commit {sha[:9]} is not in '
                        f'the canonical repository (vendored from a '
                        f'dirty or unpushed tree?)'
                    )
                    continue

                subprocess.run(
                    [
                        'git', '-C', canonical, 'checkout', '--quiet',
                        sha,
                    ],
                    capture_output=True, text=True, timeout=30,
                    check=True,
                )
                verbatim = subprocess.run(
                    [
                        'bash',
                        os.path.join(canonical, 'tools', 'vendor.sh'),
                        '--check', os.path.abspath(directory),
                    ],
                    capture_output=True, text=True, timeout=60,
                )
                if verbatim.returncode != 0:
                    problems.append(
                        f'{rel}: differs from recorded commit '
                        f'{sha[:9]} -- a vendored copy was edited in '
                        f'place; move the change to the canonical '
                        f'repository and re-vendor'
                    )

                behind = subprocess.run(
                    [
                        'git', '-C', canonical, 'rev-list', '--count',
                        f'{sha}..origin/HEAD',
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                if (behind.returncode == 0
                        and int(behind.stdout.strip()) > 0):
                    count = int(behind.stdout.strip())
                    problems.append(
                        f'{rel}: {count} commit(s) behind canonical; '
                        f're-run tools/vendor.sh from an up to date '
                        f'sfui checkout'
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError,
            subprocess.CalledProcessError) as e:
        return {
            'id': 'sfui-vendor',
            'status': 'fail',
            'details': f'Error checking vendored sfui: {e}',
        }

    if problems:
        return {
            'id': 'sfui-vendor',
            'status': 'fail',
            'details': '; '.join(problems),
        }
    return {
        'id': 'sfui-vendor',
        'status': 'pass',
        'details': (
            f'{len(vendored)} vendored sfui '
            f'{"copy" if len(vendored) == 1 else "copies"} verbatim '
            f'at canonical HEAD'
        ),
    }


def check_calls(repo_path, props, repo_name, org):
    """Pair every check id with the call that produces it.

    The calls are deferred so that a repository scoped with
    only_checks can skip a check without paying for it first: several
    checks query the GitHub API, and on a private repository some of
    those queries fail for reasons that have nothing to do with the
    repository's compliance.

    The id written here must be the id the check returns.
    test_audit_check.py asserts that for every entry, so a check that
    renames its id cannot silently become unschedulable.
    """
    return [
        ('llm-tooling',
         lambda: check_llm_tooling(repo_path, props)),
        ('llm-doc-structure',
         lambda: check_llm_doc_structure(repo_path, props)),
        ('llm-context-lint',
         lambda: check_llm_context_lint(repo_path, props)),
        ('llm-context-lint-ci',
         lambda: check_llm_context_lint_ci(repo_path, props)),
        ('release-process',
         lambda: check_release_process(repo_path, props)),
        ('ci-review-automation',
         lambda: check_ci_review_automation(repo_path, props)),
        ('renovate',
         lambda: check_renovate(repo_path, props)),
        ('pin-indirect-dependencies',
         lambda: check_pin_indirect_deps(repo_path, props)),
        ('dependency-name-normalization',
         lambda: check_dependency_name_normalization(repo_path, props)),
        ('export-repo-config',
         lambda: check_export_repo_config(repo_path, props)),
        ('default-branch-naming',
         lambda: check_default_branch(repo_path, props, repo_name, org)),
        ('github-security',
         lambda: check_github_security(repo_path, props, repo_name, org)),
        ('delete-branch-on-merge',
         lambda: check_delete_branch_on_merge(
             repo_path, props, repo_name, org)),
        ('merge-queue-config',
         lambda: check_merge_queue_config(repo_path, props, repo_name, org)),
        ('workflow-permissions',
         lambda: check_workflow_permissions(repo_path, props)),
        ('pre-commit-config',
         lambda: check_pre_commit_config(repo_path, props)),
        ('review-marks-pre-commit',
         lambda: check_review_marks_pre_commit(repo_path, props)),
        ('flake8wrap',
         lambda: check_flake8wrap(repo_path, props)),
        ('self-hosted-runners',
         lambda: check_self_hosted_runners(repo_path, props)),
        ('static-runner-tags',
         lambda: check_static_runner_tags(repo_path, props)),
        ('devpi-fallback',
         lambda: check_devpi_fallback(repo_path, props)),
        ('devpi-stale-ip',
         lambda: check_devpi_stale_ip(repo_path, props)),
        ('expensive-lane-path-filter',
         lambda: check_expensive_lane_path_filter(repo_path, props)),
        ('pyproject-usage',
         lambda: check_pyproject_usage(repo_path, props)),
        ('version-file-gitignore',
         lambda: check_version_file(repo_path, props)),
        ('rust-unwrap-lint',
         lambda: check_rust_unwrap_lint(repo_path, props)),
        ('readme-absolute-links',
         lambda: check_readme_absolute_links(repo_path, props)),
        ('docs-external-links',
         lambda: check_docs_external_links(repo_path, props)),
        ('readme-structure',
         lambda: check_readme_structure(repo_path, props)),
        ('plan-phase-references',
         lambda: check_plan_phase_references(repo_path, props)),
        ('plan-source-references',
         lambda: check_plan_source_references(repo_path, props)),
        ('plan-index',
         lambda: check_plan_index(repo_path, props)),
        ('push-audit',
         lambda: check_push_audit(repo_path, props)),
        ('plan-template',
         lambda: check_plan_template(repo_path, props)),
        ('secret-scanning-ci',
         lambda: check_secret_scanning_ci(repo_path, props)),
        ('review-coverage',
         lambda: check_review_coverage(repo_path, props)),
        ('sfui-vendor',
         lambda: check_sfui_vendor(repo_path, props)),
    ]


def run_all_checks(repo_path, repo_name, org):
    """Run all checks and return results.

    A repository scoped with an only_checks override runs just those
    checks. The rest are reported not_applicable rather than left out
    of the results: audit-update-docs.py renders a check it cannot
    find as "unknown", and out of scope is a decision we have made,
    not something we failed to measure.
    """
    props = detect_repo_properties(repo_path, repo_name)
    only = props['only_checks']

    checks = []
    for check_id, run_check in check_calls(repo_path, props, repo_name, org):
        if only and check_id not in only:
            checks.append({
                'id': check_id,
                'status': 'not_applicable',
                'details': (
                    f'{repo_name} is audited for '
                    f'{", ".join(sorted(only))} only'
                ),
            })
            continue
        checks.append(run_check())

    summary = {
        'total': len(checks),
        'pass': sum(1 for c in checks if c['status'] == 'pass'),
        'fail': sum(1 for c in checks if c['status'] == 'fail'),
        'not_applicable': sum(
            1 for c in checks if c['status'] == 'not_applicable'
        ),
    }

    return {
        'repo': repo_name,
        'org': org,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'checks': checks,
        'summary': summary,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Check a repo against consistency audit criteria'
    )
    parser.add_argument(
        '--repo-path', required=True,
        help='Path to the cloned repository',
    )
    parser.add_argument(
        '--repo-name', required=True,
        help='Repository name (e.g. occystrap)',
    )
    parser.add_argument(
        '--github-org', default='shakenfist',
        help='GitHub organization (default: shakenfist)',
    )
    args = parser.parse_args()

    if not os.path.isdir(args.repo_path):
        print(
            f'Error: {args.repo_path} is not a directory',
            file=sys.stderr,
        )
        sys.exit(1)

    results = run_all_checks(
        args.repo_path, args.repo_name, args.github_org,
    )
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
