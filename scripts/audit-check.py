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
from audit.files import (
    any_workflow_contains, check_file_contains, check_file_exists,
    list_workflow_files, workflow_has_permissions,
)
from audit.github import GhCli
from audit.text.workflows import (
    RUNS_ON_RE, STATIC_ALLOWED_LABELS, parse_runner_labels,
)
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

RETIRED_ADDRESSER_SCRIPTS = (
    'address-comments-with-claude.sh',
    'render-review.py',
    'review-schema.json',
)

# The whole chain, matched by name anywhere in the tree rather than only
# under tools/ and .github/workflows/. Those are the canonical homes, but
# deployments put them elsewhere -- the check this replaced found a
# contrib/ copy, and a template directory carries the copy of the
# workflow the next project installs -- and a dead file is dead wherever
# it sits. Every copy is named because the remediation is to remove
# everything the finding names in one commit: naming only the installed
# workflow would leave the template behind, after which the repository
# passes forever while still handing the chain to the next project. The
# workflow's basename comes off the path above so the two cannot drift.
RETIRED_ADDRESSER_FILES = (
    os.path.basename(RETIRED_ADDRESSER_WORKFLOW),
) + RETIRED_ADDRESSER_SCRIPTS

# Except where they are a composite action's own source. shakenfist/actions
# is in the audit matrix and is the canonical home of
# review-pr-with-claude/render-review.py and its schema -- the copies every
# project's reviewer actually runs, and the ones this retirement points
# projects at instead of their own. Searching by bare filename cannot tell
# those from a deployed leftover, and the finding tells the maintainer to
# remove the whole chain in one commit, so acting on it would delete the
# renderer out from under the reviewer in every repository at once. An
# action manifest beside the file is what distinguishes the two: it means
# the directory is the action, not a copy of somebody else's. Both
# spellings, because Actions accepts both and the cost of missing one is
# the false finding this exists to prevent.
COMPOSITE_ACTION_MANIFESTS = ('action.yml', 'action.yaml')

ADDRESSER_RETIRED_PREFIX = (
    'the retired comment addresser is still deployed (%s); it is unused, '
)

# Two tails, because the finding is the entire content of an auto-filed
# issue on another repository and the maintainer goes looking for what it
# names. A copy at .github/workflows/pr-address-comments.yml is a live
# workflow holding contents: write on the pull request branch, which is
# the urgent case. Anything else -- leftover scripts, a template copy of
# the workflow -- does not run, and asserting a privileged workflow there
# sends the maintainer hunting for one that was already deleted.
ADDRESSER_RETIRED_LIVE_TAIL = (
    'and its workflow holds contents: write on the pull request branch'
)
ADDRESSER_RETIRED_LEFTOVER_TAIL = (
    'and these are dead weight the next project copies'
)


def addresser_retired_detail(deployed):
    """Render the finding naming the retired addresser files found."""
    if RETIRED_ADDRESSER_WORKFLOW in deployed:
        tail = ADDRESSER_RETIRED_LIVE_TAIL
    else:
        tail = ADDRESSER_RETIRED_LEFTOVER_TAIL
    return (ADDRESSER_RETIRED_PREFIX % ', '.join(deployed)) + tail


def carries_retired_comment_addresser(repo_path):
    """Return the retired addresser's files which are still deployed.

    Reported as one finding naming every file found, not one finding per
    file: they are a single chain, they are removed in a single commit,
    and a repository which deletes the workflow but keeps the scripts has
    not finished the job.

    All four names are matched by basename anywhere in the tree. A
    workflow only runs from .github/workflows/, so a copy elsewhere is
    inert -- but a template directory's copy is what the next project
    installs, and the finding has to name it or the maintainer removes
    the scripts, leaves the template, and passes the audit thereafter.
    Which of the two the finding is about is what
    addresser_retired_detail() decides.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        # .git holds whatever another branch left behind, which is not
        # something this repository can act on.
        dirnames[:] = [d for d in dirnames if d != '.git']
        # See COMPOSITE_ACTION_MANIFESTS: the action's own source is not
        # a deployed copy of the retired chain.
        if any(m in filenames for m in COMPOSITE_ACTION_MANIFESTS):
            continue
        for name in RETIRED_ADDRESSER_FILES:
            if name in filenames:
                found.append(
                    os.path.relpath(
                        os.path.join(dirpath, name), repo_path
                    )
                )
    return sorted(found)


# The three things check_ci_review_automation() can name in a finding
# beyond a missing file. A maintainer following an audit issue has to
# land on a spec page that states the thing they are being measured
# against, and a rewrite of that page dropped the shared action while
# the check went on filing it -- so the names live here and
# CiReviewAutomationSpecTest asserts the page carries every one.
CI_REVIEW_DEVELOPER_WORKFLOWS = ('pr-re-review.yml', 'pr-retest.yml')
CI_REVIEW_SHARED_ACTION = 'review-pr-with-claude@main'
CI_REVIEW_TRIGGER_ACTION = 'shakenfist/actions/pr-bot-trigger@main'


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
    return not check_file_contains(
        repo_path, path, re.escape(CI_REVIEW_TRIGGER_ACTION.split('/')[-1]))


# Quoting and a trailing comment are both forms GitHub Actions treats
# as identical to a bare "secrets: inherit", and the commented form is
# the realistic evasion: a maintainer who reads the template text or
# receives the audit issue is more likely to write
# "secrets: inherit  # TODO: drop once migrated" than to delete the
# line. Anchoring on end-of-line let both through, and a security guard
# reporting pass while the exposure stands is worse than no guard --
# the compliance page then positively asserts the repository is clean.
# The explicit mapping form ("secrets:" followed by named entries) is
# still deliberately not matched: that caller passes what it names.
SECRETS_INHERIT_RE = re.compile(
    r"""\s*secrets:\s*['"]?inherit['"]?\s*(#.*)?$""")


def pr_auto_review_callers_inheriting_secrets(repo_path):
    """Find reviewer jobs which hand the shared workflow every secret.

    pr-auto-review.yml declares no secrets and reads none: it and
    review-pr-with-claude authenticate with github.token, which comes
    from the calling job's permissions: block. "secrets: inherit" on
    that job therefore buys the caller nothing, while passing every
    secret the repository holds -- publishing tokens included -- to a
    workflow which lives in another repository. The exposure is latent
    rather than active, but it means a bad change landing in
    shakenfist/actions would already have those secrets within reach,
    which is the situation to avoid rather than to detect afterwards.

    The shared workflow's own header tells callers not to do this. An
    earlier version of templates/ci-review-automation/README.md told
    them to, which is how nine repositories came to, so this is checked
    rather than left to the template being right from here on. Run
    against fresh clones of the whole matrix on 2026-08-22 it reported
    two of them rather than zero, so it is a guard which has seen real
    repositories and not only fixtures. Which repositories are
    outstanding today is the ci-review-automation section of
    docs/audits/compliance.md, which regenerates daily. That is
    deliberately not restated here: one of the two the survey named had
    merged its own removal within the day, and this change is the
    other.

    Returns the workflow files whose reviewer job still inherits.
    """
    offenders = []
    for wf in list_workflow_files(repo_path):
        filepath = os.path.join(repo_path, '.github', 'workflows', wf)
        with open(filepath, 'r', errors='replace') as f:
            content = f.read()
        for _, body in workflow_job_blocks(content):
            lines = [line for line in body.splitlines()
                     if not line.lstrip().startswith('#')]
            if not any(re.search(r'uses:.*pr-auto-review\.yml', line)
                       for line in lines):
                continue
            if any(SECRETS_INHERIT_RE.match(line) for line in lines):
                offenders.append(wf)
                break
    return sorted(offenders)


def secrets_inherit_issues(repo_path):
    """Findings for reviewer jobs which inherit secrets needlessly."""
    return [
        f'{wf} passes "secrets: inherit" to pr-auto-review.yml, which '
        'reads no secrets and authenticates with github.token, so '
        'every secret this repository holds is handed to a workflow '
        'in another repository for no benefit'
        for wf in pr_auto_review_callers_inheriting_secrets(repo_path)
    ]


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
                'details': addresser_retired_detail(deployed),
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
        inheriting = secrets_inherit_issues(repo_path)
        if inheriting:
            return {
                'id': 'ci-review-automation',
                'status': 'fail',
                'details': '; '.join(inheriting),
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
    for wf in CI_REVIEW_DEVELOPER_WORKFLOWS:
        if not check_file_exists(
            repo_path, f'.github/workflows/{wf}'
        ):
            issues.append(f'Missing {wf}')

    # Check that at least one workflow uses the shared review action
    if not any_workflow_contains(
        repo_path, re.escape(CI_REVIEW_SHARED_ACTION)
    ):
        issues.append(
            f'No workflow uses shared action {CI_REVIEW_SHARED_ACTION}'
        )

    # A hand-rolled pr-re-review.yml misses the shared action's fork
    # guard. See the helper's docstring.
    if pr_re_review_open_codes_the_trigger(repo_path):
        issues.append(
            f'pr-re-review.yml does not use {CI_REVIEW_TRIGGER_ACTION}, '
            f'so it hand-rolls the trigger handling and does not '
            f"inherit the action's fork pull request guard"
        )

    # "secrets: inherit" on the reviewer job hands every secret this
    # repository holds to a workflow in another one, for a workflow
    # which reads none. See the helper's docstring.
    issues.extend(secrets_inherit_issues(repo_path))

    # The comment addresser is retired. See the helper's docstring.
    deployed = carries_retired_comment_addresser(repo_path)
    if deployed:
        issues.append(addresser_retired_detail(deployed))

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
    to read the human-written text a filter would skip -- a secret,
    or an instruction aimed at an agent, lands in docs or review
    notes as easily as in code. That is an argument about the
    scanner job, not about the file it happens to live in, so it
    only carries the whole workflow when the whole workflow is
    scanners.

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
    """Does a job body invoke a content scanner, outside comments?

    CONTENT_SCANNERS rather than SECRET_SCANNERS: what earns the
    exemption is that the job has to read the text a path filter
    would skip, which is as true of the agent-context linter as it
    is of the credential scanner. CONTENT_SCANNERS is defined next
    to SECRET_SCANNERS further down this file, and is read at call
    time rather than at import.

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
        if any(scanner in line for scanner in CONTENT_SCANNERS):
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
    unfiltered workflow all of whose jobs invoke a CONTENT_SCANNERS
    tool -- are exempt: their whole point is to read the
    human-written text a filter would skip, since a secret, or an
    instruction smuggled into an agent's context, lands in docs or
    review marks as easily as in code. That is the same reasoning that keeps
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
            if any(s in content for s in CONTENT_SCANNERS):
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


# On a merge_group event github.ref is the per-attempt queue branch,
# gh-readonly-queue/<base>/pr-<N>-<SHA>, and GitHub mints a fresh SHA
# every time it rebuilds the group. A concurrency group keyed on it is
# therefore unique per rebuild, cancel-in-progress never matches, and
# superseded runs build whole clouds nobody is waiting on. See
# docs/audits/merge-group-cancellation.md.
MERGE_GROUP_TRIGGER_RE = re.compile(
    r'^\s{1,4}merge_group:\s*$', re.MULTILINE
)
MERGE_GROUP_FLOW_RE = re.compile(
    r'^on:\s*\[[^\]]*\bmerge_group\b', re.MULTILINE
)
WORKFLOW_CALL_TRIGGER_RE = re.compile(
    r'^\s{1,4}workflow_call:\s*$', re.MULTILINE
)

# A deliberate exception, ideally with a reason beside it.
MERGE_GROUP_CANCEL_EXCEPTION_RE = re.compile(
    r'audit-ok:\s*merge-group-cancellation'
)

# The queue ref, and the contexts that resolve to something GitHub
# mints afresh on every rebuild of a merge group. Any of these in a
# concurrency group makes the group unique per rebuild, so the
# superseding group never joins it. These are substring tests, so
# 'github.ref' also covers github.ref_name and github.ref_type (the
# same queue branch without the refs/heads/ prefix), and
# 'github.event.merge_group.head_commit' covers its .id.
#
# github.sha is here because on merge_group it is the SHA of the
# per-attempt merge commit, not of the pull request head: it is as
# unique per rebuild as the queue branch, and it is the key somebody
# reaches for when they notice github.ref is wrong.
QUEUE_REF_CONTEXTS = (
    'github.ref',
    'github.sha',
    'github.run_id',
    'github.run_number',
    'github.run_attempt',
    'github.event.merge_group.head_ref',
    'github.event.merge_group.head_sha',
    'github.event.merge_group.head_commit',
)

# The branch that makes a github.ref-based key safe: the expression
# picks a different key when the event is a merge group.
MERGE_GROUP_BRANCH_RE = re.compile(
    r"github\.event_name\s*[=!]=\s*'merge_group'"
)

# The key that has to vary between the lanes of one matrix, and
# between two jobs that call the same reusable workflow. Without a
# matrix context in the group, a matrix's own lanes share a group and
# cancel each other -- which is not a wasted run but a cancelled
# required check, and a cancelled required check ejects the pull
# request from the queue.
MATRIX_CONTEXT_RE = re.compile(r'\bmatrix\.[A-Za-z_]')
INPUTS_CONTEXT_RE = re.compile(r'\binputs\.[A-Za-z_]')

# The fleet convention for telling one invocation of a reusable
# workflow from another on the same ref. A callee cannot see its
# caller's job name, so the caller has to say which invocation it is;
# shakenfist/actions' smoke-cluster.yml declares this input for
# exactly that reason. Required only where a callee is invoked more
# than once per ref, which is the only time invocations can collide.
CONCURRENCY_KEY_INPUT = 'concurrency_key'

# Callees this audit can actually see. An in-repo callee is read from
# the clone; a shakenfist/ callee is a fleet repository the audit runs
# against in its own right. Anything else is a concurrency group
# nobody here has checked, and the caller cannot fix it.
AUDITED_CALLEE_PREFIXES = ('./', 'shakenfist/')

USES_WORKFLOW_RE = re.compile(
    r'^\s*uses:\s*(\S+\.github/workflows/\S+)\s*$'
)


def strip_yaml_comments(text):
    """Drop full-line comments from a block of YAML.

    Concurrency keys and `if:` conditions are routinely explained by
    a comment directly above them that quotes the very expression
    being warned about, so matching comment text would read those
    explanations as the code they describe.
    """
    return '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('#')
    )


def indented_block(body, key):
    """Extract the `key:` mapping from a job or workflow body.

    Returns the block's text (without the key line), or None when
    there is none. The block runs from the key to the next line at or
    below its indentation, which covers both the inline and the
    folded (`>-`) forms the fleet uses.
    """
    lines = strip_yaml_comments(body).splitlines()
    collected = None
    indent = None
    for line in lines:
        if collected is None:
            match = re.match(r'^(\s*)' + re.escape(key) + r':\s*$', line)
            if match:
                collected = []
                indent = len(match.group(1))
            continue
        if not line.strip():
            collected.append(line)
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        collected.append(line)
    if collected is None:
        return None
    return '\n'.join(collected)


def concurrency_block(body):
    """Extract a `concurrency:` mapping from a job or workflow body."""
    return indented_block(body, 'concurrency')


def cancels_superseded_merge_groups(block):
    """Does a concurrency block cancel a superseded merge group?

    Two requirements: cancel-in-progress must be on, and the group
    must not be keyed on anything the queue mints afresh per rebuild
    unless the expression branches on the event name and uses
    something stable there.
    """
    if block is None:
        return False, 'no concurrency block'
    if not re.search(r'cancel-in-progress:\s*true', block):
        return False, 'cancel-in-progress is not true'
    if not any(ctx in block for ctx in QUEUE_REF_CONTEXTS):
        return True, None
    if MERGE_GROUP_BRANCH_RE.search(block):
        return True, None
    return False, 'group is keyed on the per-attempt queue ref'


EVENT_NAME_EQUALITY_RE = re.compile(
    r"github\.event_name\s*==\s*'([a-z_]+)'"
)


def job_excluded_from_merge_group(body):
    """Does a job's `if:` keep it off merge_group events?

    Two forms count, and both appear in the fleet:

    * an inequality against 'merge_group' -- ryll's smoke tier, and
      kerbside's whole-lane skip, are written this way;
    * a condition that names one or more events by equality, none of
      which is 'merge_group' -- instar's ci-tooling is
      `github.event_name == 'pull_request' && ...`.

    A job whose condition cannot be read either way is treated as
    reachable, which errs toward auditing it. The second form is read
    as constraining the whole condition, so a job would be wrongly
    exempted by an `if:` that ORs an event equality with a term that
    does not mention the event at all. Nothing in the fleet is
    written that way, and the alternative -- parsing the boolean
    structure of a GitHub expression -- buys accuracy that is not
    yet needed.
    """
    body = strip_yaml_comments(body)
    if re.search(r"github\.event_name\s*!=\s*'merge_group'", body):
        return True
    events = EVENT_NAME_EQUALITY_RE.findall(body)
    return bool(events) and 'merge_group' not in events


def job_uses_workflow(body):
    """The reusable workflow a job delegates to, or None.

    Such a job has no runner of its own; the concurrency group that
    matters lives in the callee, which this audit checks where it is
    defined. What is checked here instead is that two invocations of
    one callee can be told apart.
    """
    for line in strip_yaml_comments(body).splitlines():
        match = USES_WORKFLOW_RE.match(line)
        if match:
            return match.group(1)
    return None


def job_matrix_block(body):
    """The `matrix:` mapping of a job, or None when it has none."""
    strategy = indented_block(body, 'strategy')
    if strategy is None:
        return None
    return indented_block(strategy, 'matrix')


def job_with_inputs(body):
    """The `with:` inputs a job passes, as a name -> value mapping.

    Only the scalar `name: value` form is read, which is every input
    the fleet passes. A folded or block scalar is recorded as the
    empty string: it is present, which is what the collision checks
    below ask about.
    """
    block = indented_block(body, 'with')
    if block is None:
        return {}
    inputs = {}
    base = None
    for line in block.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if base is None:
            base = indent
        if indent != base:
            continue
        match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$', line)
        if match:
            inputs[match.group(1)] = match.group(2).strip()
    return inputs


def job_runs_on_scarce_runner(body):
    """Does a job hold a scarce self-hosted runner while it runs?

    Every self-hosted pool except `static` qualifies. The path-filter
    audit uses the narrower 'vm' label, which is right for the
    question it asks -- 'vm' marks the lanes that build clouds -- but
    wrong for this one: instar's ephemeral runners are tagged
    [self-hosted, debian-12, xl] with no 'vm' label at all, and an
    abandoned merge group holds one of those just as firmly. The
    static pool is the exception because it is always-on and shared,
    and its jobs (path filters, gates) are seconds long.

    A `runs-on:` that is an expression is resolved as far as the
    matrix it reads: `runs-on: ${{ matrix.os }}` over a matrix whose
    values are self-hosted label lists is a scarce job, and reading
    it as unresolvable -- which is what parse_runner_labels alone
    does -- would drop a whole matrix of cloud builds out of the
    audit. An expression over a matrix with no self-hosted value in
    it, ryll's GitHub-hosted Windows and macOS builds, stays out of
    scope: there is no fleet runner to starve.
    """
    matrix = job_matrix_block(body)
    for line in strip_yaml_comments(body).splitlines():
        match = RUNS_ON_RE.match(line)
        if not match:
            continue
        labels = parse_runner_labels(match.group(1))
        if labels is None:
            # An expression. It can only be scarce if the matrix it
            # reads names a self-hosted pool other than static.
            if matrix is None or 'self-hosted' not in matrix:
                continue
            for value in re.findall(r'\[[^\]]*\]', matrix):
                resolved = parse_runner_labels(value)
                if not resolved or 'self-hosted' not in resolved:
                    continue
                if set(resolved) - STATIC_ALLOWED_LABELS:
                    return True
            continue
        if 'self-hosted' not in labels:
            continue
        if set(labels) - STATIC_ALLOWED_LABELS:
            return True
    return False


def workflow_header(content):
    """Everything in a workflow file above its `jobs:` key."""
    return content.split('\njobs:')[0]


def workflow_declares_inputs(content):
    """Does a reusable workflow declare any `workflow_call` inputs?"""
    call = indented_block(workflow_header(content), 'workflow_call')
    if call is None:
        return False
    return indented_block(call, 'inputs') is not None


def lane_key_is_distinct(block, has_matrix):
    """Does a matrix job's concurrency group vary between its lanes?

    A matrix whose lanes share one group is not a wasted run: the
    lanes cancel each other, the queue sees a cancelled required
    check, and the pull request is ejected. Which makes this the more
    expensive half of getting concurrency wrong, and the half a group
    keyed on github.ref accidentally got right.
    """
    if not has_matrix or block is None:
        return True
    return bool(MATRIX_CONTEXT_RE.search(block))


def reusable_invocation_offenders(wf, invocations):
    """Check that two invocations of one callee can be told apart.

    A job that calls a reusable workflow has no concurrency group of
    its own -- the callee's group is the one that matters, and this
    audit checks it where the callee is defined. What cannot be seen
    from there is how many times one ref invokes it. shakenfist's
    merge tier calls smoke-cluster.yml five times, four of them the
    lanes of one matrix, and every one of those invocations lands in
    the callee's group; they are distinct only because the caller
    passes a per-invocation concurrency_key. Drop it and the lanes
    cancel each other, which ejects the pull request from the queue.

    So the requirement lands on the caller, and only where
    invocations can collide: a callee invoked once per ref, with no
    matrix behind it, has nothing to be distinct from.
    """
    problems = []
    for callee, calls in sorted(invocations.items()):
        if not callee.startswith(AUDITED_CALLEE_PREFIXES):
            names = ', '.join(sorted(name for name, _, _ in calls))
            problems.append(
                f'{wf}:{names} (calls {callee}, whose concurrency '
                f'group is outside the audited fleet)'
            )
            continue
        if len(calls) == 1 and not calls[0][2]:
            continue
        seen = {}
        for name, inputs, has_matrix in sorted(calls):
            key = inputs.get(CONCURRENCY_KEY_INPUT)
            if key is None:
                problems.append(
                    f'{wf}:{name} (invokes {callee} more than once '
                    f'per ref with no {CONCURRENCY_KEY_INPUT} input '
                    f'to tell the invocations apart)'
                )
                continue
            if has_matrix and not MATRIX_CONTEXT_RE.search(key):
                problems.append(
                    f'{wf}:{name} ({CONCURRENCY_KEY_INPUT} is the '
                    f'same for every matrix lane)'
                )
                continue
            if key in seen:
                problems.append(
                    f'{wf}:{name} (passes the same '
                    f'{CONCURRENCY_KEY_INPUT} as {seen[key]})'
                )
                continue
            seen[key] = name
    return problems


def merge_queue_is_serial(repo_name, org, github=None):
    """Is the default branch's merge queue building one entry at a time?

    Returns True, False, or None when GitHub could not be asked. The
    base_ref key this audit requires aliases every live entry in a
    queue, which is safe only while the queue builds one at a time --
    with speculative stacking, entry N+1's run would be cancelled by
    entry N's, and a cancelled required check ejects it. The
    merge-queue-config audit is what holds the fleet to
    max_entries_to_build: 1, and it is also what reports an
    unreachable API, so a None here is left to that check to explain
    rather than failing this one twice.
    """
    try:
        client = _github(github)
        branch = client.api(
            f'repos/{org}/{repo_name}', jq='.default_branch')
        if branch.returncode != 0:
            return None
        rules = client.api(
            f'repos/{org}/{repo_name}/rules/branches/'
            f'{branch.stdout.strip()}')
        if rules.returncode != 0:
            return None
        parsed = json.loads(rules.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError):
        return None

    entries = [
        (r.get('parameters') or {}).get('max_entries_to_build')
        for r in parsed if r.get('type') == 'merge_queue'
    ]
    if not entries:
        return None
    return all(e == 1 for e in entries)


def check_merge_group_cancellation(repo_path, props, repo_name, org,
                                   github=None):
    """Check merge group runs cancel exactly what they should.

    A job reachable on a merge_group event that holds a scarce
    self-hosted runner must be in a concurrency group that a
    superseding merge group joins, and that its own siblings do not.
    Both halves have teeth, and they pull in opposite directions.

    Cancelling too little is what this audit was written for.
    github.ref does not group a job with its successor: on
    merge_group it is the per-attempt queue branch
    gh-readonly-queue/<base>/pr-<N>-<SHA>, GitHub mints a fresh SHA
    on every rebuild of the group, and so every rebuild lands in a
    group of its own and nothing is ever cancelled. The superseded
    runs build complete clouds against a queue branch GitHub has
    already abandoned, on an under-cloud shared with every other
    repository in the fleet. github.sha, github.run_id and the
    merge_group head contexts are the same mistake wearing a
    different name.

    Cancelling too much is worse, and is what the rest of the checks
    are for. Two lanes of one matrix, or two jobs invoking one
    reusable workflow, that share a concurrency group cancel each
    other inside a single run -- and a cancelled required check does
    not merely waste a runner, it ejects the pull request from the
    queue. So a matrix job's group must carry a matrix context, and
    a caller that invokes one reusable workflow more than once per
    ref must pass a per-invocation concurrency_key.

    Reusable (workflow_call) workflows are in scope, and
    unconditionally: they inherit the caller's event, so a callee
    keyed on github.ref carries the defect on behalf of every caller,
    and a callee published for the fleet cannot know what event it
    will see. Inferring reachability from in-repo callers was tried
    and is wrong -- it exempted shakenfist/actions' smoke-cluster.yml,
    which every shakenfist merge group runs four nested clusters
    through, on the strength of a scheduled canary calling it too. A
    callee that declares inputs must also key its group on one of
    them, because a group made only of caller contexts is the same
    group for every invocation on a ref.

    The cost of that conservatism is reusable workflows that really
    cannot see a merge group -- the fleet's test-drift-fix.yml is
    called only from pr-fix-tests.yml on issue_comment -- which carry
    the audit-ok marker with that as the stated reason. The marker is
    read per job, and only exempts a whole file when it appears above
    the jobs: key: a workflow file here runs to eight hundred lines
    and fifteen jobs, and one job's stated exception should not
    quietly stop the other fourteen being measured.

    Cancelling is only safe at all because the merge-queue-config
    audit holds the fleet to max_entries_to_build: 1, which makes any
    other in-flight merge_group run superseded by definition. Where
    that is not true, the base_ref key this audit asks for would
    alias two live entries, so the precondition is checked here
    rather than left as a note in the specification.
    """
    if not props['has_workflows_dir']:
        return {
            'id': 'merge-group-cancellation',
            'status': 'not_applicable',
            'details': 'No .github/workflows/ directory',
        }

    workflows = list_workflow_files(repo_path)
    if not workflows:
        return {
            'id': 'merge-group-cancellation',
            'status': 'not_applicable',
            'details': 'No workflow files found',
        }

    offenders = []
    audited = 0
    saw_merge_group = False
    keyed_on_base_ref = False
    for wf in sorted(workflows):
        filepath = os.path.join(
            repo_path, '.github', 'workflows', wf
        )
        with open(filepath, 'r', errors='replace') as f:
            content = f.read()

        triggered = bool(
            MERGE_GROUP_TRIGGER_RE.search(content)
            or MERGE_GROUP_FLOW_RE.search(content)
        )
        reusable = bool(WORKFLOW_CALL_TRIGGER_RE.search(content))
        if not (triggered or reusable):
            continue
        saw_merge_group = True

        header = workflow_header(content)
        if MERGE_GROUP_CANCEL_EXCEPTION_RE.search(header):
            continue

        workflow_block = concurrency_block(header)
        needs_input_key = reusable and workflow_declares_inputs(content)
        invocations = {}
        for name, body in workflow_job_blocks(content):
            if MERGE_GROUP_CANCEL_EXCEPTION_RE.search(body):
                continue
            if triggered and job_excluded_from_merge_group(body):
                continue

            callee = job_uses_workflow(body)
            if callee:
                invocations.setdefault(callee, []).append((
                    name,
                    job_with_inputs(body),
                    job_matrix_block(body) is not None,
                ))
                continue

            if not job_runs_on_scarce_runner(body):
                continue
            audited += 1

            block = concurrency_block(body)
            if block is None:
                block = workflow_block
            ok, reason = cancels_superseded_merge_groups(block)
            if not ok:
                offenders.append(f'{wf}:{name} ({reason})')
                continue
            if MERGE_GROUP_BRANCH_RE.search(block):
                keyed_on_base_ref = True
            if not lane_key_is_distinct(
                    block, job_matrix_block(body) is not None):
                offenders.append(
                    f'{wf}:{name} (every matrix lane shares one '
                    f'concurrency group, so the lanes cancel each '
                    f'other)'
                )
                continue
            if needs_input_key and not INPUTS_CONTEXT_RE.search(block):
                offenders.append(
                    f'{wf}:{name} (a reusable workflow group made '
                    f'only of caller contexts is the same group for '
                    f'every invocation on a ref)'
                )

        offenders.extend(
            reusable_invocation_offenders(wf, invocations)
        )
        audited += sum(len(c) for c in invocations.values())

    if not saw_merge_group:
        return {
            'id': 'merge-group-cancellation',
            'status': 'not_applicable',
            'details': (
                f'No merge_group or reusable workflow among '
                f'{len(workflows)} workflow(s)'
            ),
        }

    if (keyed_on_base_ref
            and merge_queue_is_serial(repo_name, org, github) is False):
        offenders.append(
            'the merge queue on the default branch builds more than '
            'one entry at a time, so a group keyed on '
            'merge_group.base_ref aliases live entries and would '
            'cancel one of them (see docs/audits/merge-queue-config.md)'
        )

    if offenders:
        return {
            'id': 'merge-group-cancellation',
            'status': 'fail',
            'details': (
                f'{len(offenders)} problem(s) with merge_group '
                f'cancellation: {", ".join(offenders)}. A group must '
                f'be shared with the run that supersedes it -- key it '
                f'on github.event.merge_group.base_ref rather than '
                f'anything minted per rebuild -- and not shared with '
                f'anything running beside it. See '
                f'docs/audits/merge-group-cancellation.md, or mark a '
                f'deliberate exception with an "audit-ok: '
                f'merge-group-cancellation" comment'
            ),
        }

    return {
        'id': 'merge-group-cancellation',
        'status': 'pass',
        'details': (
            f'{audited} job(s) reachable on merge_group cancel '
            f'superseded queue entries without cancelling each other'
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


# Diagram format: a picture of structure or flow belongs in a mermaid
# fence, where GitHub and mkdocs both render it. See
# docs/audits/diagram-format.md, and
# templates/shared-blocks/diagram-discipline.md for the policy the
# push-audit reviewer applies to a diff.


# Scanners we accept, by the name they are invoked under. gitleaks
# is the reference implementation; the others are equivalent enough
# that requiring a specific one would be churn for no gain.
SECRET_SCANNERS = ['gitleaks', 'trufflehog', 'detect-secrets']

# Tools whose job is reading the repository's own human-written
# content for something dangerous, used by the path-filter exemption
# in check_expensive_lane_path_filter(). A superset of
# SECRET_SCANNERS, because the exemption's argument is not specific to
# credentials: skillsaw lints the agent context for instructions
# smuggled into text an agent will load, and a prompt aimed at an
# agent lands in a document at least as readily as a key does.
#
# Kept separate from SECRET_SCANNERS so the two questions stay
# separate. check_secret_scanning_ci() asks whether this repository
# scans for credentials at all, and skillsaw is not an answer to that.
CONTENT_SCANNERS = SECRET_SCANNERS + ['skillsaw']


def check_secret_scanning_ci(repo_path, props):
    """Check a repository secret scanner runs in CI.

    Any of the scanners in SECRET_SCANNERS, invoked from any
    workflow, satisfies this. We deliberately do not check how it
    is invoked or on which triggers -- a scanner running at all is
    the step change, and pinning the invocation would make the
    check brittle against reasonable variation.

    Note this covers only the scanner. The credential handling
    patterns in docs/audits/secret-handling.md are review criteria; a
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
        ('ci-review-automation',
         lambda: check_ci_review_automation(repo_path, props)),
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
        ('workflow-permissions',
         lambda: check_workflow_permissions(repo_path, props)),
        ('pre-commit-config',
         lambda: check_pre_commit_config(repo_path, props)),
        ('review-marks-pre-commit',
         lambda: check_review_marks_pre_commit(repo_path, props)),
        ('devpi-fallback',
         lambda: check_devpi_fallback(repo_path, props)),
        ('devpi-stale-ip',
         lambda: check_devpi_stale_ip(repo_path, props)),
        ('expensive-lane-path-filter',
         lambda: check_expensive_lane_path_filter(repo_path, props)),
        ('merge-group-cancellation',
         lambda: check_merge_group_cancellation(
             repo_path, props, repo_name, org, github)),
        ('secret-scanning-ci',
         lambda: check_secret_scanning_ci(repo_path, props)),
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
