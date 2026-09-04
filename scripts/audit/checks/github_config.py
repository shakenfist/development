"""The criteria about a repository's settings on GitHub.

The default branch, the security features, whether merged branches are
deleted, whether the merge queue is serialised, and whether the
settings are exported into the repository where a diff can show them
moving.

Four of the five ask the GitHub API rather than the checkout, which is
why they were the last family to move and why none of them had a test
before the client seam existed. export-repo-config is here for what it
is about rather than how it works: it reads the filesystem.

scope-coverage is here because it asks the API too, but it is the odd
one in the package: every other check measures the repository it was
handed, and that one measures the organisation the repository belongs
to.
"""

import json
import re
import subprocess

from audit import scope
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


def http_status(stderr):
    """The HTTP status `gh` reported, or None if it did not.

    `gh api` writes "gh: Not Found (HTTP 404)" on failure. The status
    is the difference between a repository that is gone and one this
    token cannot see or ask about, and those want opposite fixes.
    """
    match = re.search(r'HTTP (\d{3})', stderr or '')
    return int(match.group(1)) if match else None


def describe_failure(result):
    """A short reason for a resolution that neither succeeded nor 404ed."""
    status = http_status(result.stderr)
    if status is not None:
        return f'HTTP {status}'
    lines = (result.stderr or '').strip().splitlines()
    return lines[0] if lines else 'no error reported'


#: How many repositories to ask for. `gh repo list` returns 30 by
#: default, which silently loses eight of the organisation's 38 and
#: reports them as undecided; the check refuses a listing that reaches
#: this number rather than trusting a page that may have been cut.
ORG_LISTING_LIMIT = 1000


class ScopeCoverage(Check):
    id = 'scope-coverage'
    spec = 'docs/audits/scope-coverage.md'
    template = None
    issue_title = 'Audit scope against the organisation'

    #: The repository that states the scope, and so the only one where
    #: this criterion can be measured.
    SCOPE_REPO = 'development'

    def applies(self, repo):
        """Only the repository that holds the lists can be asked.

        Scope is stated in development: the matrix in
        .github/workflows/consistency-audit.yml and the two lists in
        docs/audits/README.md. Everywhere else there is nothing to
        read, and the API call would be paid for an answer that could
        not be compared to anything.
        """
        if repo.name != self.SCOPE_REPO:
            return (f'Audit scope is stated in the {self.SCOPE_REPO} '
                    f'repository, and is measured there')
        return None

    def run(self, repo):
        """Check the audit scope and the organisation still agree.

        Scope is written down three times and a test holds those three
        to each other, but until this existed nothing compared any of
        them to the organisation. A repository in none of them was not
        audited, not documented as excluded, and produced no finding
        anywhere: the only signal was a repository missing from a list
        nobody diffs.

        Two directions, and both are the same missing check. A
        repository in the organisation and in neither list is one
        nobody has decided about -- that it should be audited, or that
        it should not, are both fine, and having made neither decision
        is what this reports. A name in a list that no longer resolves
        is harmless in itself, but it is the same reconciliation
        failing the other way, and it is how a list comes to describe a
        fleet that has moved on.

        Archived repositories are not exempt. isArchived is the obvious
        filter and it is deliberately not used: every archived
        repository is already on the excluded list, so requiring a
        decision for all of them costs nothing, and a dormant
        repository that nobody has archived -- which is what the
        filter would have missed -- is exactly the case worth naming.
        """
        try:
            matrix = set(scope.matrix_repos(repo.path))
            excluded = set(scope.documented_excluded(repo.path))
        except scope.ScopeParseError as e:
            return self.fail(f'Could not read the audit scope: {e}')
        except OSError as e:
            return self.fail(f'Could not read the audit scope: {e}')

        try:
            result = repo.github.run(
                ['repo', 'list', repo.org, '--limit',
                 str(ORG_LISTING_LIMIT), '--json', 'name,isPrivate'],
                timeout=60)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Could not list the {repo.org} organisation: {e}')
        if result.returncode != 0:
            return self.fail(
                f'Could not list the {repo.org} organisation: '
                f'{result.stderr.strip()}')
        try:
            listing = json.loads(result.stdout)
        except json.JSONDecodeError:
            return self.fail(
                f'Could not parse the {repo.org} organisation listing')
        # Valid JSON of the wrong shape would raise out of the check,
        # and registry.run_all() has no per-check exception handling:
        # an exception here aborts the development leg, which
        # manage-issues and update-docs are needs-gated on, and stops
        # issue filing and the compliance page for the whole fleet.
        if not isinstance(listing, list) or not all(
                isinstance(entry, dict) and 'name' in entry
                for entry in listing):
            return self.fail(
                f'Could not parse the {repo.org} organisation listing: '
                f'expected a list of repositories')

        # Counted before deduplication: a duplicate name would leave the
        # set one short of the limit, and a genuinely truncated listing
        # would sail through the guard it exists to trip.
        if len(listing) >= ORG_LISTING_LIMIT:
            return self.fail(
                f'The {repo.org} listing returned {len(listing)} '
                f'repositories, which is the limit asked for: it may '
                f'have been truncated, and a truncated listing reports '
                f'repositories as undecided that are merely unlisted')

        if not listing:
            return self.fail(
                f'The {repo.org} listing returned no repositories at '
                f'all, so nothing here can be reconciled: check the '
                f'token')

        organisation = {entry['name'] for entry in listing}
        # Whether the token can see private repositories at all. GitHub
        # answers 404 rather than 403 for a private repository a token
        # cannot see, so a blind token is indistinguishable from a
        # deletion one name at a time; this is the signal that
        # distinguishes them in bulk.
        sees_private = any(entry.get('isPrivate') for entry in listing)

        undecided = organisation - matrix - excluded
        unlisted = sorted((matrix | excluded) - organisation)

        # A name missing from the listing is not evidence the repository
        # is gone, and "gone" is the one conclusion whose suggested fix
        # -- delete the entry -- is destructive. So each one is asked
        # about directly, and a resolution that fails for any reason
        # other than a 404 is reported as a failure to resolve rather
        # than as a deletion. gh_canonical_repo() falls back rather than
        # concluding anything from a failed call, for the same reason.
        #
        # Where the listing saw no private repositories at all, a 404
        # carries no information: this token answers 404 for every
        # private repository in the organisation, exactly as it does
        # for a deleted one. Nothing is called gone in that state. The
        # finding names the token instead, which is the only edit that
        # can clear it -- a red row nobody can act on is one people
        # learn to skip.
        gone, renamed, invisible, unresolvable = [], [], [], []
        for name in unlisted:
            # Per name rather than around the loop: the undecided set is
            # already computed and needs no API access, and one slow
            # call must not discard the finding this criterion exists
            # for.
            try:
                result = repo.github.api(
                    f'repos/{repo.org}/{name}', jq='.full_name')
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                unresolvable.append((name, f'{type(e).__name__}'))
                continue
            if result.returncode != 0:
                status = http_status(result.stderr)
                if status != 404:
                    unresolvable.append((name, describe_failure(result)))
                elif sees_private:
                    gone.append(name)
                else:
                    unresolvable.append(
                        (name, 'a 404 this token cannot distinguish from '
                               'a private repository it cannot see'))
                continue
            canonical = result.stdout.strip()
            if not canonical:
                # Exit zero and no answer. gh_canonical_repo() guards
                # the same case; without this the finding reads
                # "renamed to " and names no destination.
                unresolvable.append((name, 'the API returned no name'))
            elif canonical.lower() != f'{repo.org}/{name}'.lower():
                renamed.append((name, canonical))
            else:
                invisible.append(name)

        # Item of noise rather than a wrong answer: a repository renamed
        # from a name the scope still carries is also, under its new
        # name, in the organisation and in neither list. Both findings
        # ask for the same single edit, so the rename is the one that
        # says it.
        undecided -= {canonical.split('/')[-1] for _, canonical in renamed}

        problems = []
        missing = []
        if undecided:
            problems.append(
                f'{len(undecided)} repository(s) in the organisation are '
                f'in neither the audit matrix nor the excluded list')
            missing.extend(f'{name} (in the organisation, decided nowhere)'
                           for name in sorted(undecided))
        if gone:
            problems.append(
                f'{len(gone)} name(s) in the audit matrix or the '
                f'excluded list no longer exist')
            missing.extend(f'{name} (no such repository)' for name in gone)
        if renamed:
            problems.append(
                f'{len(renamed)} name(s) in the audit scope have been '
                f'renamed')
            missing.extend(f'{name} -> {canonical} (renamed)'
                           for name, canonical in renamed)
        if invisible:
            # Not a scope finding at all: the lists are right and the
            # listing is short. Reported rather than passed over,
            # because a listing that cannot see part of the
            # organisation cannot answer the undecided question either.
            problems.append(
                f'{len(invisible)} repository(s) named by the audit '
                f'scope exist but are missing from the organisation '
                f'listing, which is therefore incomplete -- check the '
                f'token before trusting anything else here')
            missing.extend(f'{name} (exists, but not in the listing)'
                           for name in invisible)
        if unresolvable:
            blind = '' if sees_private else (
                ', and the listing returned no private repositories at '
                'all, so this token cannot answer the question: grant '
                'it private-repository read')
            problems.append(
                f'{len(unresolvable)} name(s) in the audit scope could '
                f'not be resolved either way{blind}')
            missing.extend(f'{name} (unresolved: {reason})'
                           for name, reason in unresolvable)
        if problems:
            return self.fail('; '.join(problems), missing=missing)

        return self.ok(
            f'Every one of the {len(organisation)} repositories in '
            f'{repo.org} is either audited or documented as excluded, '
            f'and every name in the scope still resolves')
