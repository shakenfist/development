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

from audit import registry
from audit.files import check_file_exists
from audit.github import GhCli
from audit.repo import REPO_OVERRIDES, Repo, detect_repo_properties

# REPO_OVERRIDES and detect_repo_properties now live in audit/repo.py
# and are re-exported here: test_audit_check.py and several callers
# reach for them on this module, and moving the code is a separate
# question from moving the name.
__all__ = ['REPO_OVERRIDES', 'detect_repo_properties']


def _github(client):
    """The GitHub client to use, defaulting to the real one.

    Threaded through the checks that query the API rather than reached
    for globally, so a test can substitute audit.github.FakeGitHub.
    """
    return client if client is not None else GhCli()


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


def check_default_branch(repo_path, props, repo_name, org, github=None):
    """Check default branch is 'develop' via GitHub API."""
    try:
        result = _github(github).api(
            f'repos/{org}/{repo_name}', jq='.default_branch')
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


def check_github_security(repo_path, props, repo_name, org, github=None):
    """Check GitHub security settings and CodeQL workflow."""
    issues = []

    # Fetch visibility and security settings in one API call.
    # Visibility is queried live rather than hardcoded because repos
    # change visibility over time and a stale override would silently
    # skip the CodeQL check.
    is_private = props['is_private']
    security = None
    try:
        result = _github(github).api(
            f'repos/{org}/{repo_name}',
            jq='{private: .private, security: .security_and_analysis}')
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


def check_delete_branch_on_merge(repo_path, props, repo_name, org,
                                 github=None):
    """Check head branches are deleted automatically when a PR merges."""
    try:
        result = _github(github).api(
            f'repos/{org}/{repo_name}', jq='.delete_branch_on_merge')
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


def check_merge_queue_config(repo_path, props, repo_name, org,
                             github=None):
    """Check any merge queue on the default branch is serialized."""
    client = _github(github)
    try:
        result = client.api(
            f'repos/{org}/{repo_name}', jq='.default_branch')
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

        result = client.api(
            f'repos/{org}/{repo_name}/rules/branches/{branch}')
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


# Diagram format: a picture of structure or flow belongs in a mermaid
# fence, where GitHub and mkdocs both render it. See
# docs/audits/diagram-format.md, and
# templates/shared-blocks/diagram-discipline.md for the policy the
# push-audit reviewer applies to a diff.


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


def check_review_scope_completeness(repo_path, props):
    """Check that nothing leaves the review queue by omission.

    review-coverage measures the backlog against the scope. This
    measures the scope itself, and the two fail in opposite
    directions: narrowing `include` is the cheapest way to make a
    review-coverage issue close, and without this check nothing
    notices a repository that reaches full coverage by shrinking what
    counts.

    The rule is that every tracked file is either in scope or named by
    an `exclude` entry. Excluding a file is fine and often right --
    generated output, vendored trees, verbatim upstream text -- but it
    should be a decision with a comment beside it rather than the
    accident of an `include` list written before that file type
    existed. An empty `include` satisfies this trivially, which is the
    intended pressure: a repository either enumerates its file types
    and keeps doing so, or reviews everything it has not excluded.

    We ask review-tracking.py rather than parsing the scope config
    here, so that the audit and the tooling cannot disagree about what
    in-scope means -- the '!' re-include semantics and the built-in
    exclusion of the review state files both live in that script. As
    with review-coverage we invoke our sibling copy directly, since
    the target repo's tools/ wrapper looks for a development clone the
    runner does not have.
    """
    if not check_file_exists(repo_path, '.vscode/review-scope.toml'):
        return {
            'id': 'review-scope-completeness',
            'status': 'not_applicable',
            'details': (
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)'
            ),
        }

    try:
        result = subprocess.run(
            [sys.executable, REVIEW_TRACKING_SCRIPT, 'scope-orphans',
             '--json'],
            cwd=repo_path, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {
            'id': 'review-scope-completeness',
            'status': 'fail',
            'details': 'review-tracking.py scope-orphans timed out',
        }
    # Exit status 1 is the reportable outcome, not an error: the
    # subcommand exits non-zero precisely when there are orphans, so
    # only a status outside {0, 1} means the run itself broke.
    if result.returncode not in (0, 1):
        return {
            'id': 'review-scope-completeness',
            'status': 'fail',
            'details': (
                f'review-tracking.py scope-orphans failed: '
                f'{result.stderr.strip()}'
            ),
        }
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        # A crash in the script also exits 1, which is the status
        # orphans use, so this is where a broken run lands. Carry
        # stderr through or the report says the output was malformed
        # when what actually happened was a traceback.
        return {
            'id': 'review-scope-completeness',
            'status': 'fail',
            'details': (
                f'review-tracking.py scope-orphans emitted unparseable '
                f'JSON: {result.stderr.strip()}'
            ),
        }

    orphans = report.get('orphans', [])
    if not orphans:
        return {
            'id': 'review-scope-completeness',
            'status': 'pass',
            'details': (
                'Every tracked file is either in review scope or '
                'explicitly excluded'
            ),
        }

    # The whole list in 'missing', which audit-manage-issues.py
    # renders as bullets: the fix is per file, so a truncated list
    # would leave someone re-running the tool to find out what the
    # issue meant. The count alone goes in 'details', which is what
    # the compliance page prints in a table cell.
    return {
        'id': 'review-scope-completeness',
        'status': 'fail',
        'details': (
            f'{len(orphans)} tracked file(s) are out of review scope '
            f'only because no include pattern in '
            f'.vscode/review-scope.toml names them'
        ),
        'missing': orphans,
    }


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


def check_calls(repo_path, props, repo_name, org, github=None):
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
        ('export-repo-config',
         lambda: check_export_repo_config(repo_path, props)),
        ('default-branch-naming',
         lambda: check_default_branch(
             repo_path, props, repo_name, org, github)),
        ('github-security',
         lambda: check_github_security(
             repo_path, props, repo_name, org, github)),
        ('delete-branch-on-merge',
         lambda: check_delete_branch_on_merge(
             repo_path, props, repo_name, org, github)),
        ('merge-queue-config',
         lambda: check_merge_queue_config(
             repo_path, props, repo_name, org, github)),
        ('review-marks-pre-commit',
         lambda: check_review_marks_pre_commit(repo_path, props)),
        ('review-coverage',
         lambda: check_review_coverage(repo_path, props)),
        ('review-scope-completeness',
         lambda: check_review_scope_completeness(repo_path, props)),
        ('sfui-vendor',
         lambda: check_sfui_vendor(repo_path, props)),
    ]


def run_all_checks(repo_path, repo_name, org, github=None):
    """Run every check against a clone and return the results document.

    The scheduling, the only_checks scoping and the summary now live in
    audit/registry.py. This keeps its name and signature because
    check-audit-smoke.py, ci.yml and the tests all drive it.
    """
    repo = Repo(repo_path, repo_name, org, github=github)
    return registry.run_all(
        repo,
        legacy=check_calls(repo_path, repo.props, repo_name, org,
                           repo.github),
    )


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
