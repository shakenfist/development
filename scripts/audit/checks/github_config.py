"""The criteria about a repository's settings on GitHub.

The default branch, the security features, whether merged branches are
deleted, whether the merge queue is serialised, whether the environment
a release waits in is protected, and whether the settings are exported
into the repository where a diff can show them moving.

All but one ask the GitHub API rather than the checkout, which is
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
import urllib.parse

from audit import scope
from audit.check import Check, ToolFailure
from audit.files import workflows_inheriting_secrets_into
from audit.text.workflows import (
    indented_block, job_level_keys, mapping_entries, workflow_job_blocks,
)


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
        """Check for repo config export workflow.

        The shared export-repo-config.yml reads no secrets: it
        authenticates with github.token under the caller's permissions:
        block. So "secrets: inherit" on the calling job buys nothing and
        hands every secret the repository holds to a workflow in another
        repository, called at a moving @main. The template carried that
        line until September 2026, which is how every caller came to,
        so it is checked rather than left to the template being right
        from here on -- the same reasoning, and the same matcher, as the
        pr-auto-review.yml finding under ci-review-automation.
        """
        if not repo.isfile('.github/workflows/export-repo-config.yml'):
            return self.fail('Missing .github/workflows/export-repo-config.yml')
        inheriting = workflows_inheriting_secrets_into(
            repo, 'export-repo-config.yml')
        if inheriting:
            return self.fail(
                'Passing "secrets: inherit" to export-repo-config.yml: '
                f'{", ".join(inheriting)}. The shared workflow reads no '
                'secrets and authenticates with github.token, so every '
                'secret this repository holds is handed to a workflow in '
                'another repository for no benefit. Delete the line.')
        return self.ok('export-repo-config.yml exists and no caller inherits secrets')


class DefaultBranchNaming(Check):
    id = 'default-branch-naming'
    spec = 'docs/audits/default-branch-naming.md'
    template = None
    issue_title = 'Default branch naming'

    def applies(self, repo):
        """Exempt docs-only repositories and documented exceptions.

        Both may use main: a documented exception is a reason in
        REPO_OVERRIDES. Decided here rather than after the API call, because neither
        exemption depends on the answer: asking first would make an
        exempt repository's verdict hostage to GitHub being reachable.
        """
        if repo.props['is_docs_only']:
            return 'Docs-only repo (exception allowed)'
        if repo.props['default_branch_exception']:
            return f'Exempt: {repo.props["default_branch_exception"]}'
        return None

    def run(self, repo):
        """Check default branch is 'develop' via GitHub API."""
        result = repo.github.api_answer(
            f'repos/{repo.org}/{repo.name}', 'the default branch',
            jq='.default_branch')
        branch = result.stdout.strip()
        if branch != 'develop':
            return self.fail(
                f'Default branch is "{branch}", '
                f'expected "develop"')
        return self.ok('Default branch is "develop"')


class GithubSecurity(Check):
    id = 'github-security'
    spec = 'docs/audits/github-security.md'
    template = 'templates/codeql/'
    issue_title = 'GitHub security settings'

    def run(self, repo):
        """Check GitHub security settings and CodeQL workflow.

        A query that fails, or answers without the settings, is an
        error rather than a verdict. Read as "no settings", either
        would pass wherever the CodeQL workflow is present -- there
        would be nothing to find wrong with settings nobody saw -- so a
        token that cannot see them would report secret scanning as
        compliant everywhere at once. GitHub returns
        security_and_analysis as null to a token without admin access,
        which is the realistic way to arrive there.
        """
        issues = []

        # Fetch visibility and security settings in one API call.
        # Visibility is queried live rather than hardcoded because repos
        # change visibility over time and a stale override would silently
        # skip the CodeQL check.
        result = repo.github.api_answer(
            f'repos/{repo.org}/{repo.name}', 'the security settings',
            jq='{private: .private, security: .security_and_analysis}')
        try:
            repo_info = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ToolFailure(
                f'Could not parse the security settings response: {e}') from e
        if not isinstance(repo_info, dict):
            raise ToolFailure(
                'Could not parse the security settings response: '
                'expected an object')
        is_private = repo_info.get('private')
        security = repo_info.get('security')
        if not isinstance(is_private, bool):
            raise ToolFailure(
                'GitHub did not say whether the repository is private')
        if not isinstance(security, dict):
            raise ToolFailure(
                'GitHub returned no security_and_analysis settings; the '
                'token may lack admin access to the repository')

        # Check CodeQL workflow (file-based, not API)
        if is_private:
            pass  # Private repos can't use CodeQL without GHAS
        elif repo.props['is_docs_only']:
            pass  # No code to scan
        elif not repo.isfile('.github/workflows/codeql-analysis.yml'):
            issues.append('Missing .github/workflows/codeql-analysis.yml')

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
        """Check head branches are deleted automatically when a PR merges.

        A null answer is an error, not a fail: the API omits the field
        for a token without push access, which says nothing about the
        setting.
        """
        result = repo.github.api_answer(
            f'repos/{repo.org}/{repo.name}', 'the delete branch on merge setting',
            jq='.delete_branch_on_merge')
        setting = result.stdout.strip()

        if setting == 'true':
            return self.ok('Delete branch on merge is enabled')
        if setting == 'false':
            return self.fail('Delete branch on merge is not enabled')
        raise ToolFailure(
            f'Could not determine delete branch on merge setting '
            f'(API returned "{setting or "null"}"; the token may '
            f'lack push access)')


class MergeQueueConfig(Check):
    id = 'merge-queue-config'
    spec = 'docs/audits/merge-queue-config.md'
    template = None
    issue_title = 'Merge queue reasonability'

    def run(self, repo):
        """Check any merge queue on the default branch is serialized."""
        client = repo.github
        result = client.api_answer(
            f'repos/{repo.org}/{repo.name}', 'the default branch',
            jq='.default_branch')
        branch = result.stdout.strip()

        # A branch with no rules answers an empty list rather than an
        # error, so every failure here is GitHub not answering.
        result = client.api_answer(
            f'repos/{repo.org}/{repo.name}/rules/branches/{branch}',
            'branch rules')
        try:
            rules = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ToolFailure(
                f'Could not parse the branch rules response: {e}') from e
        if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
            raise ToolFailure(
                'Could not parse the branch rules response: expected a list of rules')

        problems = evaluate_merge_queue_rules(rules)
        if problems is None:
            return self.skip(f'No merge queue on default branch "{branch}"')
        if problems:
            return self.fail('; '.join(problems))
        return self.ok(
            f'Merge queue on "{branch}" is serialized '
            f'(max_entries_to_build 1, min_entries_to_merge 1)')


# The flow-mapping spelling of a job's environment,
# `environment: {name: release, url: ...}`, reduced to its name.
FLOW_ENVIRONMENT_NAME_RE = re.compile(r'^\{.*?\bname\s*:\s*([^,}]+)')


def unquote(value):
    """A YAML scalar without the quotes around it."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
        return value[1:-1]
    return value


def job_environment(body):
    """The environment a job names, as written, or None.

    `environment:` takes a name, or a mapping whose `name:` is the
    name, in either block or flow form. A mapping without a name is
    returned as the empty string rather than None: the job has the key,
    and a caller that read it as absent would stop asking about it. A
    value holding an expression is returned whole, because cutting a
    name out of it would stop at the expression's own braces.
    """
    keys = job_level_keys(body)
    if 'environment' not in keys:
        return None
    value = keys['environment']
    if not value:
        return unquote(mapping_entries(indented_block(body, 'environment')).get('name', ''))
    if '${{' in value:
        return value
    match = FLOW_ENVIRONMENT_NAME_RE.match(value)
    if match:
        return unquote(match.group(1))
    if value.startswith('{'):
        return ''
    return unquote(value)


def parse_json_answer(result, what):
    """The JSON object GitHub answered with, or ToolFailure."""
    try:
        answer = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ToolFailure(f'Could not parse {what}: {e}') from e
    if not isinstance(answer, dict):
        raise ToolFailure(f'Could not parse {what}: expected an object')
    return answer


class ReleaseEnvironmentProtection(Check):
    id = 'release-environment-protection'
    spec = 'docs/audits/release-environment-protection.md'
    template = None
    issue_title = 'Release environment protection'

    WORKFLOW = 'release.yml'
    SETUP = 'RELEASE-SETUP.md'

    #: More deployment policies than this on one environment is a page
    #: that may have been cut, and an unread policy could be a branch.
    POLICY_PAGE = 100

    #: Tag patterns that match every tag, so restrict nothing.
    ANY_TAG = frozenset({'*', '**'})

    def applies(self, repo):
        """Only a repository that releases through release.yml has a gate to check."""
        if repo.workflow(self.WORKFLOW) is None:
            return f'No .github/workflows/{self.WORKFLOW}, so nothing is released through an environment'
        return None

    def run(self, repo):
        """Check every environment release.yml names waits for a reviewer and a tag.

        The environment is the only thing between a tag push and a
        published release, and nothing in the checkout shows whether it
        is configured: GitHub creates a referenced environment that
        does not exist, with no protection at all, the first time a job
        names it.
        """
        content = repo.workflow(self.WORKFLOW)
        environments = []
        seen = set()
        problems = []
        for job, body in workflow_job_blocks(content):
            name = job_environment(body)
            if name is None:
                continue
            if not name:
                problems.append(
                    f'the {job} job has an environment with no name, so it cannot be checked')
            elif '${{' in name:
                # Resolved at run time, so the audit cannot say which
                # environment the job deploys to. It could be one that
                # does not exist yet, which GitHub would create
                # unprotected, so it is reported rather than passed.
                problems.append(
                    f'the {job} job names its environment with an expression ({name}), so the audit '
                    'cannot confirm it is protected; name it literally')
            elif name.casefold() not in seen:
                # GitHub matches environment names without regard to
                # case, so release and Release are one environment.
                seen.add(name.casefold())
                environments.append(name)

        if not environments and not problems:
            return self.fail(
                f'{self.WORKFLOW} names no environment, so nothing in a release waits for a '
                f'reviewer; gate the publishing jobs behind one as {self.SETUP} describes')

        for name in environments:
            problems.extend(self.environment_problems(repo, name))

        if problems:
            return self.fail('; '.join(problems) + f' (see {self.SETUP})')
        listed = ', '.join(f'"{name}"' for name in environments)
        return self.ok(
            f'Every environment {self.WORKFLOW} names ({listed}) has a required reviewer and deploys '
            'only from tags')

    def environment_problems(self, repo, name):
        """The ways one environment falls short, as a list of strings."""
        path = f'repos/{repo.org}/{repo.name}/environments/{urllib.parse.quote(name, safe="")}'
        what = f'the {name} environment'
        try:
            result = repo.github.api(path)
        except subprocess.TimeoutExpired as e:
            raise ToolFailure(f'Could not query GitHub for {what}: timed out') from e
        except OSError as e:
            raise ToolFailure(f'Could not query GitHub for {what}: {e}') from e
        if result.returncode != 0:
            if http_status(result.stderr) == 404:
                return [f'the "{name}" environment does not exist, so GitHub would create it '
                        'unprotected the first time a release job ran']
            raise ToolFailure(f'Could not query GitHub for {what}: {describe_failure(result)}')
        environment = parse_json_answer(result, what)

        problems = []
        rules = environment.get('protection_rules') or []
        if not any(isinstance(rule, dict) and rule.get('type') == 'required_reviewers'
                   and rule.get('reviewers') for rule in rules):
            problems.append(f'the "{name}" environment has no required reviewer')

        policy = environment.get('deployment_branch_policy')
        if not isinstance(policy, dict):
            problems.append(f'the "{name}" environment can be deployed from any branch or tag')
            return problems
        if policy.get('custom_branch_policies') is not True:
            problems.append(
                f'the "{name}" environment is limited to protected branches rather than to tags')
            return problems

        what = f'the deployment policies of the {name} environment'
        result = repo.github.api_answer(
            f'{path}/deployment-branch-policies?per_page={self.POLICY_PAGE}', what)
        answer = parse_json_answer(result, what)
        policies = answer.get('branch_policies')
        if not isinstance(policies, list) or not all(isinstance(p, dict) for p in policies):
            raise ToolFailure(f'Could not parse {what}: expected a list of policies')
        if answer.get('total_count', len(policies)) != len(policies):
            raise ToolFailure(
                f'Could not read {what}: {answer.get("total_count")} policies, but '
                f'{len(policies)} on the page')
        if not policies:
            problems.append(
                f'the "{name}" environment allows custom deployment policies but has none, so no tag can release')
        branches = sorted(str(p.get('name')) for p in policies if p.get('type') != 'tag')
        if branches:
            problems.append(
                f'the "{name}" environment can be deployed from branches as well as tags '
                f'({", ".join(branches)})')
        everything = sorted(str(p.get('name')) for p in policies
                            if p.get('type') == 'tag' and p.get('name') in self.ANY_TAG)
        if everything:
            problems.append(
                f'the "{name}" environment deploys from any tag at all ({", ".join(everything)}), '
                'not only release tags')
        return problems


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

        # Everything from here to the classification is the listing
        # failing to answer -- an error, a malformed reply, a page that
        # may have been cut, an empty organisation -- and each is
        # raised rather than failed: none of them is a fact about the
        # scope, and a fail would file an issue on development for a
        # token problem.
        result = repo.github.answer(
            ['repo', 'list', repo.org, '--limit',
             str(ORG_LISTING_LIMIT), '--json', 'name,isPrivate'],
            f'the {repo.org} organisation listing', timeout=60)
        try:
            listing = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ToolFailure(
                f'Could not parse the {repo.org} organisation listing: '
                f'{e}') from e
        if not isinstance(listing, list) or not all(
                isinstance(entry, dict) and 'name' in entry
                for entry in listing):
            raise ToolFailure(
                f'Could not parse the {repo.org} organisation listing: '
                f'expected a list of repositories')

        # Counted before deduplication: a duplicate name would leave the
        # set one short of the limit, and a genuinely truncated listing
        # would sail through the guard it exists to trip.
        if len(listing) >= ORG_LISTING_LIMIT:
            raise ToolFailure(
                f'The {repo.org} listing returned {len(listing)} '
                f'repositories, which is the limit asked for: it may '
                f'have been truncated, and a truncated listing reports '
                f'repositories as undecided that are merely unlisted')

        if not listing:
            raise ToolFailure(
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
        # other than a 404 is raised as GitHub not answering rather
        # than read as a deletion. gh_canonical_repo() falls back rather
        # than concluding anything from a failed call, for the same
        # reason.
        #
        # Where the listing saw no private repositories at all, a 404
        # carries no information: this token answers 404 for every
        # private repository in the organisation, exactly as it does
        # for a deleted one. Nothing is called gone in that state. The
        # finding names the token instead, which is the only edit that
        # can clear it -- a red row nobody can act on is one people
        # learn to skip.
        gone, renamed, moved, invisible, unresolvable = [], [], [], [], []
        # Both classification tests below are case-insensitive, and the
        # trailing '/' is load-bearing: without it 'shakenfist-extra/foo'
        # reads as a repository still in the organisation.
        prefix = f'{repo.org}/'.lower()
        for name in unlisted:
            # A failure on one name raises, and so costs the undecided
            # finding computed above for this run. That is the policy
            # rather than an oversight: an error leaves the open issue
            # as it is, so a finding already filed survives an outage,
            # while a fail made of unresolved names would file one on
            # development for GitHub being unreachable.
            what = f'the repository {repo.org}/{name}'
            try:
                result = repo.github.api(
                    f'repos/{repo.org}/{name}', jq='.full_name')
            except subprocess.TimeoutExpired as e:
                raise ToolFailure(
                    f'Could not query GitHub for {what}: timed out') from e
            except OSError as e:
                raise ToolFailure(
                    f'Could not query GitHub for {what}: {e}') from e
            if result.returncode != 0:
                status = http_status(result.stderr)
                if status != 404:
                    raise ToolFailure(
                        f'Could not query GitHub for {what}: '
                        f'{describe_failure(result)}')
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
                raise ToolFailure(
                    f'Could not query GitHub for {what}: the API '
                    f'returned no name')
            if canonical.lower() == prefix + name.lower():
                invisible.append(name)
            elif canonical.lower().startswith(prefix):
                renamed.append((name, canonical))
            else:
                # Transferred out of the organisation rather than
                # renamed inside it, and the two need different advice.
                # What a rename says -- write the new name in the matrix
                # or the list -- is not expressible here: the audit
                # workflow clones {org}/${{ matrix.repo }}, so a foreign
                # owner cannot go in the matrix at all. The entry has to
                # be removed instead.
                moved.append((name, canonical))

        # Item of noise rather than a wrong answer: a repository renamed
        # from a name the scope still carries is also, under its new
        # name, in the organisation and in neither list. Both findings
        # ask for the same single edit, so the rename is the one that
        # says it.
        #
        # `renamed` holds only canonical names still in this
        # organisation, because the classification above sorts a
        # transfer out into `moved` instead. That distinction is what
        # keeps this line honest: a repository transferred out
        # redirects to its new owner indefinitely, and subtracting on
        # the basename alone silenced the genuine "decided nowhere"
        # finding for any repository still here that happened to share
        # that name -- restoring the third state this criterion exists
        # to remove.
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
        if moved:
            problems.append(
                f'{len(moved)} name(s) in the audit scope have moved '
                f'out of the {repo.org} organisation')
            missing.extend(
                f'{name} -> {canonical} (moved out of {repo.org}: '
                f'remove the entry)' for name, canonical in moved)
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
            # Only the blind token's 404 arrives here: every other
            # failure to resolve a name has raised. It stays a finding
            # because GitHub did answer, and the answer is about how
            # this repository's audit is configured.
            problems.append(
                f'{len(unresolvable)} name(s) in the audit scope could '
                f'not be resolved either way, and the listing returned '
                f'no private repositories at all, so this token cannot '
                f'answer the question: grant it private-repository read')
            missing.extend(f'{name} (unresolved: {reason})'
                           for name, reason in unresolvable)
        if problems:
            return self.fail('; '.join(problems), missing=missing)

        return self.ok(
            f'Every one of the {len(organisation)} repositories in '
            f'{repo.org} is either audited or documented as excluded, '
            f'and every name in the scope still resolves')
