"""The criteria about human review of the code.

Whether whole-file review is keeping up, whether the scope list still
covers everything, whether a pre-commit hook is quietly rewriting the
files a review mark attests to, whether a change to the scope list is
itself reviewed, whether the review tracking CI files are still the
templates they were copied from -- and whether a vendored copy of sfui
still matches the commit it claims.

sfui-vendor is here because it is the same question in a different
shape: content somebody attested to, checked against what it was
attested from.
"""

import json
import os
import re
import sys
import tempfile

from audit.check import Check, ToolFailure, run_tool
from audit.github import environment_without_credentials
from audit.repo import READ_MAX_BYTES, read_within
from audit.text.patterns import bounded_search


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


_BLOCK_SCALAR = re.compile(r'([|>])([+-]?)(\d?)([+-]?)\s*(?:#.*)?$')
_SINGLE_QUOTED = re.compile(r"'((?:[^']|'')*)'")
_DOUBLE_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _block_scalar_text(match, lines):
    """The string a YAML block scalar (`|`, `>`, `|-`, `>+`...) holds.

    `lines` are the raw continuation lines. The content indent is taken
    as that of the least indented non-blank line, which is how every
    config in the fleet writes one; an explicit indentation digit is
    accepted but not needed for that.
    """
    body = list(lines)
    while body and not body[-1].strip():
        body.pop()
    trailing = len(lines) - len(body)
    if not body:
        return ''
    indent = min(len(line) - len(line.lstrip(' ')) for line in body if line.strip())
    body = [line[indent:] if line.strip() else '' for line in body]
    if match.group(1) == '|':
        text = '\n'.join(body)
    else:
        text = body[0]
        for previous, line in zip(body, body[1:]):
            if not line:
                text += '\n'
            elif not previous:
                text += line
            else:
                text += ' ' + line
    chomp = match.group(2) or match.group(4)
    if chomp == '-':
        return text
    return text + '\n' * (1 + trailing if chomp == '+' else 1)


def _scalar_value(value):
    """The string a plain or quoted YAML scalar on one line holds."""
    for quoted, unescape in ((_SINGLE_QUOTED, lambda s: s.replace("''", "'")),
                             (_DOUBLE_QUOTED, lambda s: re.sub(r'\\(["\\])', r'\1', s))):
        match = quoted.match(value)
        if match:
            return unescape(match.group(1))
    return re.sub(r'\s+#.*$', '', value).strip()


def scan_pre_commit_config(text):
    """Find the `exclude:` patterns in a .pre-commit-config.yaml.

    Returns (top_level_excludes, hooks), where hooks is a list of
    (hook_id, excludes) with one entry per `- id:` in the file. An
    exclude belongs to the hook whose mapping it is a key of -- found
    from indentation, since pre-commit applies a top-level exclude to
    every hook but a hook's own to that hook alone. A block scalar
    contributes the text it holds, never its `|` or `>` indicator, and
    its lines are never read as keys.

    Line-based rather than YAML-parsed, matching the rest of this
    file, which avoids a PyYAML dependency. Flow-style mappings,
    multi-line quoted scalars and a hook written as a bare `-` line
    with its `id:` on the next are not understood; none of the fleet's
    configs use them.
    """
    lines = text.splitlines()
    top = []
    hooks = []
    open_items = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.strip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        while open_items and indent <= open_items[-1]['dash']:
            item = open_items.pop()
            if item['id'] is not None:
                hooks.append((item['id'], item['excludes']))

        body = raw.strip()
        column = indent
        dash = re.match(r'-\s+', body)
        if dash:
            column = indent + dash.end()
            open_items.append({'dash': indent, 'column': column, 'id': None, 'excludes': []})
            body = body[dash.end():]

        key = re.match(r'([\w-]+):(?:\s+(.*?))?\s*$', body)
        if not key:
            continue
        name, value = key.group(1), key.group(2) or ''

        block = _BLOCK_SCALAR.match(value)
        if block:
            content = []
            while i < len(lines) and (not lines[i].strip()
                                      or len(lines[i]) - len(lines[i].lstrip(' ')) > column):
                content.append(lines[i])
                i += 1
            value = _block_scalar_text(block, content)
        else:
            value = _scalar_value(value)

        if name == 'exclude' and column == 0:
            top.append(value)
        elif name in ('id', 'exclude'):
            owner = open_items[-1] if open_items else None
            if owner and owner['column'] == column:
                if name == 'id':
                    owner['id'] = value
                else:
                    owner['excludes'].append(value)
    for item in open_items:
        if item['id'] is not None:
            hooks.append((item['id'], item['excludes']))
    return top, hooks


def pre_commit_rewriting_hooks(repo):
    """Which file-rewriting hooks does .pre-commit-config.yaml run?"""
    _, hooks = scan_pre_commit_config(repo.read('.pre-commit-config.yaml') or '')
    return sorted({hook_id for hook_id, _ in hooks if hook_id in FILE_REWRITING_HOOK_IDS})


def pre_commit_unprotected_rewriting_hooks(repo):
    """Which file-rewriting hooks would still be handed the review marks?

    A hook is protected by an `exclude:` on its own entry or at the
    top level of the file, whichever matches both sample paths -- the
    regex pre-commit would apply. An exclude on any other hook does
    nothing for this one, and each entry is judged separately because
    pre-commit runs each separately. A value we cannot compile, or whose
    search does not finish (see bounded_search()), protects nothing
    rather than raising: an unrelated malformed pattern is
    pre-commit's problem to report, not this audit's.
    """
    top, hooks = scan_pre_commit_config(repo.read('.pre-commit-config.yaml') or '')

    def protects(pattern):
        found = bounded_search(pattern, REVIEW_MARK_SAMPLE_PATHS)
        return found is not None and all(found)

    if any(protects(pattern) for pattern in top):
        return []
    return sorted({hook_id for hook_id, excludes in hooks
                   if hook_id in FILE_REWRITING_HOOK_IDS
                   and not any(protects(pattern) for pattern in excludes)})


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


# This repository's checkout, whose templates the copies elsewhere are
# compared with.
DEVELOPMENT_ROOT = os.path.realpath(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
REVIEW_TRACKING_TEMPLATE_DIR = 'templates/review-tracking'

# Each review tracking CI template, and where an adopting repository
# keeps its copy. templates/review-tracking/README.md says why a copy
# must be byte-identical: a review mark attests to a blob SHA, so only
# an identical copy imports this repository's review of the template.
REVIEW_TRACKING_TEMPLATE_COPIES = (
    ('prune-reviews.yml', '.github/workflows/prune-reviews.yml'),
    ('ci-prune-reviews.sh', 'tools/ci-prune-reviews.sh'),
    ('review-tracking.sh', 'tools/review-tracking.sh'),
)

# The one copy that deliberately differs: development's own wrapper
# runs the script in its own tree rather than searching for a clone of
# itself (see the README's "This repository").
REVIEW_TRACKING_TEMPLATE_EXCEPTIONS = {
    ('development', 'tools/review-tracking.sh'),
}


# sfui (the Shaken Fist web UI design system) is vendored into
# consumers by its tools/vendor.sh, which stamps .sfui-commit in the
# vendored directory with the canonical commit the copy came from.
SFUI_CANONICAL_URL = 'https://github.com/shakenfist/sfui'


def find_sfui_vendored_dirs(repo):
    """Find directories holding a vendored sfui copy.

    A vendored copy is identified by its .sfui-commit provenance
    stamp, and only a stamp repo.isfile() accepts counts: a dangling
    link, a directory or a link out of the clone named .sfui-commit
    is no stamp, so the run() that reads it cannot find it missing.
    Hidden directories are pruned: as well as .git, local build state
    like .tox and .venv can hold site-packages copies of a consumer's
    static assets, which are installation artifacts rather than
    vendored copies. Returns repo-relative directory paths.
    """
    found = []
    for root, dirs, files in os.walk(repo.path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        rel = repo.relative(root)
        if ('.sfui-commit' in files
                and repo.isfile(os.path.join(rel, '.sfui-commit'))):
            found.append(rel)
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
        detected the same way ReviewCoverage does, and then only where a
        rewriting hook is actually configured. The weAudit state
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
        if not repo.isfile('.pre-commit-config.yaml'):
            return self.skip('No .pre-commit-config.yaml')
        rewriting = pre_commit_rewriting_hooks(repo)
        if not rewriting:
            return self.skip(
                'No file-rewriting pre-commit hooks, so nothing '
                'rewrites the review marks')
        unprotected = pre_commit_unprotected_rewriting_hooks(repo)
        if unprotected:
            return self.fail(
                f'{", ".join(unprotected)} rewrite(s) the review marks; '
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

        # Every way this can go wrong is the tooling failing rather than
        # the repository, so each raises and the result is `error`. A
        # missing review-tracking.py makes the interpreter exit non-zero
        # and lands in the returncode branch, as does a review state file
        # the script refuses to read (a link out of the checkout, say),
        # which exits 2 with the reason on stderr; a repo.path that
        # failed to clone or was cleaned up mid-run fails as cwd, inside
        # run_tool(), before anything runs.
        #
        # The script runs inside the audited checkout and needs no
        # credential, so, as for every child but gh, it is given none.
        result = run_tool(
            [sys.executable, REVIEW_TRACKING_SCRIPT, 'status', '--json'],
            'review-tracking.py status', timeout=60, cwd=repo.path,
            env=environment_without_credentials(),
        )
        if result.returncode != 0:
            raise ToolFailure(
                f'review-tracking.py status failed: '
                f'{result.stderr.strip()}')
        try:
            status = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ToolFailure(
                'review-tracking.py status emitted unparseable JSON') from e
        shape = {'reviewed': int, 'in_scope': int, 'needing_review': int,
                 'stale': list, 'never_reviewed': list}
        if not isinstance(status, dict) or not all(
                isinstance(status.get(key), kind) for key, kind in shape.items()):
            raise ToolFailure(
                'review-tracking.py status emitted JSON without the counts '
                'and lists it reports')

        # Imported reviews count: they attest to the same bytes, and
        # import verified the attestation when it recorded them. Named
        # anyway, so a jump in coverage from an import is not mistaken
        # for a review session.
        imported = status.get('imported', 0)
        imported_note = (
            f' ({imported} imported from shakenfist/development)'
            if imported else ''
        )
        details = (
            f'{status["reviewed"]} of {status["in_scope"]} in-scope '
            f'files reviewed at HEAD{imported_note}; '
            f'{status["needing_review"]} need '
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

        # Without a credential, for the reason ReviewCoverage gives.
        result = run_tool(
            [sys.executable, REVIEW_TRACKING_SCRIPT, 'scope-orphans',
             '--json'],
            'review-tracking.py scope-orphans', timeout=60, cwd=repo.path,
            env=environment_without_credentials(),
        )
        # Exit status 1 is the reportable outcome, not an error: the
        # subcommand exits non-zero precisely when there are orphans, so
        # only a status outside {0, 1} means the run itself broke.
        if result.returncode not in (0, 1):
            raise ToolFailure(
                f'review-tracking.py scope-orphans failed: '
                f'{result.stderr.strip()}')
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            # A crash in the script also exits 1, which is the status
            # orphans use, so this is where a broken run lands. Carry
            # stderr through or the error says the output was malformed
            # when what actually happened was a traceback.
            raise ToolFailure(
                f'review-tracking.py scope-orphans emitted unparseable '
                f'JSON: {result.stderr.strip()}') from e

        # The script always writes the key, so a report without it is
        # not "no orphans": read that way, it would pass.
        if not isinstance(report, dict) or not isinstance(
                report.get('orphans'), list):
            raise ToolFailure(
                'review-tracking.py scope-orphans emitted JSON without an '
                'orphans list')
        orphans = report['orphans']
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


class ReviewTrackingTemplates(Check):
    id = 'review-tracking-templates'
    spec = 'docs/audits/review-tracking-templates.md'
    template = 'templates/review-tracking/'
    issue_title = 'Review tracking templates'

    def run(self, repo):
        """Check the review tracking CI files are byte-identical to the templates.

        Applies to repositories with review tracking deployed, detected
        the same way ReviewCoverage does. Bytes rather than text,
        because what matters is the blob: a copy that differs only in
        its line endings reads as the same text, and still cannot import
        this repository's review of the template. A copy that is
        missing fails too, since the adoption procedure installs all
        three.
        """
        if not repo.exists('.vscode/review-scope.toml'):
            return self.skip(
                'Human review tracking not deployed '
                '(no .vscode/review-scope.toml)')

        problems = []
        compared = 0
        for name, destination in REVIEW_TRACKING_TEMPLATE_COPIES:
            if (repo.name, destination) in REVIEW_TRACKING_TEMPLATE_EXCEPTIONS:
                continue
            template = f'{REVIEW_TRACKING_TEMPLATE_DIR}/{name}'
            canonical = read_within(
                DEVELOPMENT_ROOT, os.path.join(DEVELOPMENT_ROOT, template),
                binary=True)
            if canonical is None:
                raise ToolFailure(
                    f'Could not read {template} in the audit\'s own checkout')
            compared += 1
            copy = repo.read_blob(destination)
            # lexists rather than repo.exists(), which hides a link out
            # of the checkout: here the name being there is the point.
            if copy is None and os.path.lexists(repo.join(destination)):
                problems.append(
                    f'{destination} is not a regular file the audit will '
                    f'read (a dangling link, a link out of the checkout, a '
                    f'directory, or over {READ_MAX_BYTES // (1024 * 1024)} MiB)')
            elif copy is None:
                problems.append(f'{destination} is missing')
            elif copy != canonical:
                problems.append(f'{destination} differs from {template}')

        if problems:
            return self.fail(
                f'{"; ".join(problems)}. Copy them byte for byte from '
                f'{REVIEW_TRACKING_TEMPLATE_DIR}/ in shakenfist/development')
        return self.ok(
            f'{compared} review tracking file(s) match '
            f'{REVIEW_TRACKING_TEMPLATE_DIR}/')


class SfuiVendor(Check):
    id = 'sfui-vendor'
    spec = 'docs/audits/sfui-vendor.md'
    template = None
    issue_title = 'sfui vendored copy'

    def __init__(self, canonical_url=None):
        """Where the canonical sfui lives.

        A parameter so the tests can point the check at a fixture
        repository instead of cloning over the network. A Check is built
        once and run many times, so it belongs to the instance.
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

        Running that script is running code an audited repository
        chose, because the commit comes from its .sfui-commit. So the
        commit must be on the canonical default branch -- merged, and
        so reviewed -- before anything is checked out, rather than
        merely present: a full clone also holds every pushed branch.
        And no subprocess here sees a GitHub credential.
        """
        env = environment_without_credentials()
        vendored = find_sfui_vendored_dirs(repo)
        if not vendored:
            return self.skip('No vendored sfui copy (no .sfui-commit file)')

        # A clone, a checkout or a history query that fails is the
        # audit's tooling failing, and raises. The statuses that are
        # answers -- a commit that is not there, not merged, or a copy
        # vendor.sh says differs -- are findings.
        problems = []
        with tempfile.TemporaryDirectory() as tmp:
            canonical = os.path.join(tmp, 'sfui')
            clone = run_tool(
                [
                    'git', 'clone', '--quiet',
                    self.canonical_url or SFUI_CANONICAL_URL, canonical,
                ],
                'git clone of canonical sfui', timeout=120, env=env,
            )
            if clone.returncode != 0:
                raise ToolFailure(
                    f'Could not clone canonical sfui: '
                    f'{clone.stderr.strip()}')

            def git(*args, what):
                return run_tool(
                    ['git', '-C', canonical] + list(args),
                    f'git {what} in canonical sfui', timeout=30, env=env)

            for rel in vendored:
                directory = os.path.join(repo.path, rel)
                sha = (repo.read(os.path.join(rel, '.sfui-commit'))
                       or '').strip()
                if not re.fullmatch(r'[0-9a-f]{40}', sha):
                    problems.append(
                        f'{rel}: .sfui-commit does not contain a '
                        f'commit sha'
                    )
                    continue

                exists = git('cat-file', '-e', f'{sha}^{{commit}}',
                             what='cat-file')
                if exists.returncode != 0:
                    problems.append(
                        f'{rel}: recorded commit {sha[:9]} is not in '
                        f'the canonical repository (vendored from a '
                        f'dirty or unpushed tree?)'
                    )
                    continue

                merged = git('merge-base', '--is-ancestor', sha,
                             'origin/HEAD', what='merge-base')
                if merged.returncode == 1:
                    problems.append(
                        f'{rel}: recorded commit {sha[:9]} is not on '
                        f'the canonical default branch; vendor from a '
                        f'merged commit'
                    )
                    continue
                if merged.returncode != 0:
                    raise ToolFailure(
                        f'git merge-base in canonical sfui failed: '
                        f'{merged.stderr.strip()}')

                checkout = git('checkout', '--quiet', sha, what='checkout')
                if checkout.returncode != 0:
                    raise ToolFailure(
                        f'git checkout in canonical sfui failed: '
                        f'{checkout.stderr.strip()}')
                verbatim = run_tool(
                    [
                        'bash',
                        os.path.join(canonical, 'tools', 'vendor.sh'),
                        '--check', os.path.abspath(directory),
                    ],
                    'tools/vendor.sh --check', timeout=60, env=env,
                )
                if verbatim.returncode != 0:
                    problems.append(
                        f'{rel}: differs from recorded commit '
                        f'{sha[:9]} -- a vendored copy was edited in '
                        f'place; move the change to the canonical '
                        f'repository and re-vendor'
                    )

                behind = git('rev-list', '--count', f'{sha}..origin/HEAD',
                             what='rev-list')
                if behind.returncode != 0:
                    raise ToolFailure(
                        f'git rev-list in canonical sfui failed: '
                        f'{behind.stderr.strip()}')
                try:
                    count = int(behind.stdout.strip())
                except ValueError as e:
                    raise ToolFailure(
                        f'git rev-list in canonical sfui returned '
                        f'{behind.stdout.strip()!r}, not a count') from e
                if count > 0:
                    problems.append(
                        f'{rel}: {count} commit(s) behind canonical; '
                        f're-run tools/vendor.sh from an up to date '
                        f'sfui checkout'
                    )

        if problems:
            return self.fail('; '.join(problems))
        return self.ok(
            f'{len(vendored)} vendored sfui '
            f'{"copy" if len(vendored) == 1 else "copies"} verbatim '
            f'at canonical HEAD')
