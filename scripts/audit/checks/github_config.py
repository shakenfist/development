"""The criteria about a repository's settings on GitHub.

The default branch, the security features, whether merged branches are
deleted, whether the merge queue is serialised, and whether the
settings are exported into the repository where a diff can show them
moving.

Four of the five ask the GitHub API rather than the checkout, which is
why they were the last family to move and why none of them had a test
before the client seam existed. export-repo-config is here for what it
is about rather than how it works: it reads the filesystem.
"""

import json
import subprocess

from audit.check import Check
from audit.files import check_file_exists


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


class ExportRepoConfig(Check):
    id = 'export-repo-config'
    spec = 'docs/audits/export-repo-config.md'
    template = 'templates/export-repo-config/'
    issue_title = 'Export repo config'

    def run(self, repo):
        """Check for repo config export workflow."""
        if not check_file_exists(
            repo.path, '.github/workflows/export-repo-config.yml'
        ):
            return self.fail('Missing .github/workflows/export-repo-config.yml')
        return self.ok('export-repo-config.yml exists')


class DefaultBranchNaming(Check):
    id = 'default-branch-naming'
    spec = 'docs/audits/default-branch-naming.md'
    template = None
    issue_title = 'Default branch naming'

    def run(self, repo):
        """Check default branch is 'develop' via GitHub API."""
        try:
            result = repo.github.api(
                f'repos/{repo.org}/{repo.name}', jq='.default_branch')
            if result.returncode != 0:
                return self.fail(
                    f'Could not query GitHub API: '
                    f'{result.stderr.strip()}')
            branch = result.stdout.strip()

            # Exceptions: docs-only repos, and repositories carrying a
            # documented reason in REPO_OVERRIDES, may use main
            if repo.props['is_docs_only']:
                return self.skip(
                    f'Docs-only repo (current: {branch}, '
                    f'exception allowed)')

            if repo.props['default_branch_exception']:
                return self.skip(
                    f'Exempt: {repo.props["default_branch_exception"]} '
                    f'(current: {branch})')

            if branch != 'develop':
                return self.fail(
                    f'Default branch is "{branch}", '
                    f'expected "develop"')
            return self.ok('Default branch is "develop"')
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Error checking default branch: {e}')


class GithubSecurity(Check):
    id = 'github-security'
    spec = 'docs/audits/github-security.md'
    template = 'templates/codeql/'
    issue_title = 'GitHub security settings'

    def run(self, repo):
        """Check GitHub security settings and CodeQL workflow."""
        issues = []

        # Fetch visibility and security settings in one API call.
        # Visibility is queried live rather than hardcoded because repos
        # change visibility over time and a stale override would silently
        # skip the CodeQL check.
        is_private = repo.props['is_private']
        security = None
        try:
            result = repo.github.api(
                f'repos/{repo.org}/{repo.name}',
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
        elif repo.props['is_docs_only']:
            pass  # No code to scan
        elif not check_file_exists(
            repo.path, '.github/workflows/codeql-analysis.yml'
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
            return self.fail('; '.join(issues))
        return self.ok('Security settings and CodeQL are compliant')


class DeleteBranchOnMerge(Check):
    id = 'delete-branch-on-merge'
    spec = 'docs/audits/delete-branch-on-merge.md'
    template = None
    issue_title = 'Delete branch on merge'

    def run(self, repo):
        """Check head branches are deleted automatically when a PR merges."""
        try:
            result = repo.github.api(
                f'repos/{repo.org}/{repo.name}', jq='.delete_branch_on_merge')
            if result.returncode != 0:
                return self.fail(
                    f'Could not query GitHub API: '
                    f'{result.stderr.strip()}')
            setting = result.stdout.strip()

            if setting == 'true':
                return self.ok('Delete branch on merge is enabled')
            if setting == 'false':
                return self.fail('Delete branch on merge is not enabled')
            # The API omits this field (returns null) when the token
            # lacks push access to the repository.
            return self.fail(
                f'Could not determine delete branch on merge setting '
                f'(API returned "{setting or "null"}"; the token may '
                f'lack push access)')
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Error checking delete branch on merge: {e}')


class MergeQueueConfig(Check):
    id = 'merge-queue-config'
    spec = 'docs/audits/merge-queue-config.md'
    template = None
    issue_title = 'Merge queue reasonability'

    def run(self, repo):
        """Check any merge queue on the default branch is serialized."""
        client = repo.github
        try:
            result = client.api(
                f'repos/{repo.org}/{repo.name}', jq='.default_branch')
            if result.returncode != 0:
                return self.fail(
                    f'Could not query GitHub API for the default '
                    f'branch: {result.stderr.strip()}')
            branch = result.stdout.strip()

            result = client.api(
                f'repos/{repo.org}/{repo.name}/rules/branches/{branch}')
            if result.returncode != 0:
                return self.fail(
                    f'Could not query GitHub API for branch rules: '
                    f'{result.stderr.strip()}')
            try:
                rules = json.loads(result.stdout)
            except json.JSONDecodeError:
                return self.fail('Could not parse branch rules response')

            problems = evaluate_merge_queue_rules(rules)
            if problems is None:
                return self.skip(f'No merge queue on default branch "{branch}"')
            if problems:
                return self.fail('; '.join(problems))
            return self.ok(
                f'Merge queue on "{branch}" is serialized '
                f'(max_entries_to_build 1, min_entries_to_merge 1)')
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Error checking merge queue config: {e}')
