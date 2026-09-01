"""The criteria about human review of the code.

Whether whole-file review is keeping up, whether the scope list still
covers everything, whether a pre-commit hook is quietly rewriting the
files a review mark attests to -- and whether a vendored copy of sfui
still matches the commit it claims.

sfui-vendor is here because it is the same question in a different
shape: content somebody attested to, checked against what it was
attested from.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

from audit.check import Check


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


# Repos with human review tracking deployed should keep the review
# backlog small enough that a session clears it. The value is a
# tuning knob: an absolute count rather than a percentage (agreed
# 2026-08-02), because "how much review work has piled up" does not
# scale with repository size.
REVIEW_BACKLOG_THRESHOLD = 5


REVIEW_TRACKING_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))),
    'review-tracking.py',
)


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


class ReviewMarksPreCommit(Check):
    id = 'review-marks-pre-commit'
    spec = 'docs/audits/workflow-standards.md'
    template = None
    issue_title = 'Workflow standards (review marks)'
    column = 'Review marks'

    def run(self, repo):
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
        if not repo.exists('.vscode/review-scope.toml'):
            return self.skip(
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)')
        if not repo.exists('.pre-commit-config.yaml'):
            return self.skip('No .pre-commit-config.yaml')
        rewriting = pre_commit_rewriting_hooks(repo.path)
        if not rewriting:
            return self.skip(
                'No file-rewriting pre-commit hooks, so nothing '
                'rewrites the review marks')
        if not pre_commit_excludes_review_marks(repo.path):
            return self.fail(
                f'{", ".join(rewriting)} rewrite(s) the review marks; '
                r'add exclude: ^\.vscode/.*\.weaudit to those hooks')
        return self.ok(f'Review marks excluded from {", ".join(rewriting)}')


class ReviewCoverage(Check):
    id = 'review-coverage'
    spec = 'docs/audits/review-coverage.md'
    template = None
    issue_title = 'Human review coverage'

    def run(self, repo):
        """Check the human review backlog in repos with review tracking.

        Applies only to repositories with the review tracking tooling
        deployed, detected by the presence of the scope config. Coverage
        is recomputed against HEAD by review-tracking.py status rather
        than trusted from the committed REVIEWS.md, so a missed prune
        cannot inflate it. We invoke our sibling copy of the script
        directly rather than the target repo's tools/ wrapper, which
        searches for a development clone the runner does not have.
        """
        if not repo.exists('.vscode/review-scope.toml'):
            return self.skip(
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)')

        try:
            result = subprocess.run(
                [sys.executable, REVIEW_TRACKING_SCRIPT, 'status', '--json'],
                cwd=repo.path, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return self.fail('review-tracking.py status timed out')
        if result.returncode != 0:
            return self.fail(
                f'review-tracking.py status failed: '
                f'{result.stderr.strip()}')
        try:
            status = json.loads(result.stdout)
        except json.JSONDecodeError:
            return self.fail('review-tracking.py status emitted unparseable JSON')

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
            return self.fail(details, missing=missing)
        return self.ok(details)


class ReviewScopeCompleteness(Check):
    id = 'review-scope-completeness'
    spec = 'docs/audits/review-scope-completeness.md'
    template = None
    issue_title = 'Human review scope completeness'

    def run(self, repo):
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
        if not repo.exists('.vscode/review-scope.toml'):
            return self.skip(
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)')

        try:
            result = subprocess.run(
                [sys.executable, REVIEW_TRACKING_SCRIPT, 'scope-orphans',
                 '--json'],
                cwd=repo.path, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return self.fail('review-tracking.py scope-orphans timed out')
        # Exit status 1 is the reportable outcome, not an error: the
        # subcommand exits non-zero precisely when there are orphans, so
        # only a status outside {0, 1} means the run itself broke.
        if result.returncode not in (0, 1):
            return self.fail(
                f'review-tracking.py scope-orphans failed: '
                f'{result.stderr.strip()}')
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            # A crash in the script also exits 1, which is the status
            # orphans use, so this is where a broken run lands. Carry
            # stderr through or the report says the output was malformed
            # when what actually happened was a traceback.
            return self.fail(
                f'review-tracking.py scope-orphans emitted unparseable '
                f'JSON: {result.stderr.strip()}')

        orphans = report.get('orphans', [])
        if not orphans:
            return self.ok(
                'Every tracked file is either in review scope or '
                'explicitly excluded')

        # The whole list in 'missing', which audit-manage-issues.py
        # renders as bullets: the fix is per file, so a truncated list
        # would leave someone re-running the tool to find out what the
        # issue meant. The count alone goes in 'details', which is what
        # the compliance page prints in a table cell.
        return self.fail(
            f'{len(orphans)} tracked file(s) are out of review scope '
            f'only because no include pattern in '
            f'.vscode/review-scope.toml names them', missing=orphans)


class SfuiVendor(Check):
    id = 'sfui-vendor'
    spec = 'docs/audits/sfui-vendor.md'
    template = None
    issue_title = 'sfui vendored copy'

    def __init__(self, canonical_url=None):
        """Where the canonical sfui lives.

        A parameter so the tests can point the check at a fixture
        repository instead of cloning over the network. It was a
        keyword argument on the old function; a Check is built once and
        run many times, so it belongs to the instance.
        """
        self.canonical_url = canonical_url

    def run(self, repo):
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
        vendored = find_sfui_vendored_dirs(repo.path)
        if not vendored:
            return self.skip('No vendored sfui copy (no .sfui-commit file)')

        problems = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                canonical = os.path.join(tmp, 'sfui')
                clone = subprocess.run(
                    [
                        'git', 'clone', '--quiet',
                        self.canonical_url or SFUI_CANONICAL_URL, canonical,
                    ],
                    capture_output=True, text=True, timeout=120,
                )
                if clone.returncode != 0:
                    return self.fail(
                        f'Could not clone canonical sfui: '
                        f'{clone.stderr.strip()}')

                for rel in vendored:
                    directory = os.path.join(repo.path, rel)
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
            return self.fail(f'Error checking vendored sfui: {e}')

        if problems:
            return self.fail('; '.join(problems))
        return self.ok(
            f'{len(vendored)} vendored sfui '
            f'{"copy" if len(vendored) == 1 else "copies"} verbatim '
            f'at canonical HEAD')
