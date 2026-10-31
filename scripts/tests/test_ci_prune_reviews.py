#!/usr/bin/env python3

"""Tests for templates/review-tracking/ci-prune-reviews.sh, run rather than read.

The script lands generated review state on a default branch that
humans are merging to at the same time, and it installs a binary it
downloads. Neither is checkable by reading it: a landing loop that
pushes a stale commit, or a digest check that no longer runs, still
looks right. So these run the template copy -- the one the fleet
gets, and which ReviewTrackingDeploymentTest pins this repository's
tools/ copy to -- against a throwaway origin and checkout, the way
MermaidLintScriptTest runs mermaid-lint.sh.

Nothing here reaches the network. curl, sha256sum, uname and sleep
are stubs on PATH; so is git, a wrapper that logs every call and
redirects the development clone to a local repository; and
tools/review-tracking.sh in the checkout is a stub that stands in for
prune and import. It writes the imports file from HEAD, so the
content of the landed commit says which tree it was regenerated
against.

The redirect is a git on PATH rather than the url.insteadOf in git
configuration these tests once used, or an environment variable the
script would honour. The script now ignores global and system git
configuration, precisely so that a runner's ~/.gitconfig cannot
redirect that clone, and an override the script read from its
environment would be a second way to do the same thing, kept in the
shipped script only for the tests' sake. Replacing git on PATH needs
nothing from the script, and whoever can do it on a runner already
controls every command the job runs.

Run with: python3 scripts/tests/test_ci_prune_reviews.py
"""

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import REPO_ROOT  # noqa: E402

TEMPLATE_SCRIPT = os.path.join(REPO_ROOT, 'templates', 'review-tracking', 'ci-prune-reviews.sh')
TEMPLATE_WORKFLOW = os.path.join(REPO_ROOT, 'templates', 'review-tracking', 'prune-reviews.yml')

BRANCH = 'trunk'
DEVELOPMENT_URL = 'https://github.com/shakenfist/development'
REPOSITORY = 'shakenfist/adopted'
TARGET_URL = 'https://github.com/' + REPOSITORY
TOKEN = 'test-push-token-1d2e3f'
WORKFLOW_STEP_SEPARATOR = '\n      - name: '
COMMIT_MESSAGE = 'Prune and import review marks.\n\nAutomated commit by the prune-reviews workflow.'

# Stands in for tools/review-tracking.sh. It logs each subcommand with
# the HEAD it ran against; prune generates REVIEWS.md if it is missing,
# as the real one regenerates it, and when asked to prune something
# empties the reviewer's sidecar and rewrites REVIEWS.md; import writes
# the imports file from HEAD when asked to import anything; import can
# push a concurrent commit to origin, a set number of times, which is
# the race the landing loop exists for; and after all that import can
# fail, a set number of times, having scribbled on REVIEWS.md.
REVIEW_TRACKING_STUB = '''#!/bin/bash
set -e
echo "$1 $(git rev-parse HEAD)" >> "${PRUNE_TEST_LOG}"
echo "$1 TUF_ROOT=${TUF_ROOT-} PUSH_TOKEN=${PUSH_TOKEN+set}" >> "${PRUNE_TEST_ENV_LOG}"
if [ "$1" = 'prune' ] && [ ! -e REVIEWS.md ]; then
    echo '# Reviews' > REVIEWS.md
fi
if [ "$1" = 'prune' ] && [ -n "${PRUNE_TEST_PRUNE:-}" ]; then
    echo '{"pruned": true}' > .vscode/mikal.weaudit-shas.json
    echo '# Reviews, pruned' > REVIEWS.md
fi
if [ "$1" != 'import' ]; then
    exit 0
fi
if [ -n "${PRUNE_TEST_IMPORT:-}" ]; then
    git rev-parse HEAD > .vscode/imports.weaudit-shas.json
fi
races="$(cat "${PRUNE_TEST_RACES}")"
if [ "${races}" -gt 0 ]; then
    echo "$((races - 1))" > "${PRUNE_TEST_RACES}"
    other="$(mktemp -d)"
    git clone --quiet "${PRUNE_TEST_ORIGIN}" "${other}"
    echo "${races}" >> "${other}/concurrent.txt"
    git -C "${other}" add concurrent.txt
    git -C "${other}" -c user.name=Merger -c user.email=merger@example.com commit --quiet -m 'A concurrent merge.'
    git -C "${other}" push --quiet origin "HEAD:${DEFAULT_BRANCH}"
    rm -rf "${other}"
fi
failures="$(cat "${PRUNE_TEST_IMPORT_FAILURES}")"
if [ "${failures}" -gt 0 ]; then
    echo "$((failures - 1))" > "${PRUNE_TEST_IMPORT_FAILURES}"
    echo 'half an import' >> REVIEWS.md
    echo 'Traceback: import fell over' >&2
    exit 3
fi
'''

# Writes the release's checksums.txt from a fixture, and anything else
# as a gitsign that answers `gitsign version`.
CURL_STUB = '''#!/bin/bash
out=''
url=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift ;;
        http*) url="$1" ;;
    esac
    shift
done
echo "${url}" >> "${PRUNE_TEST_CURL_LOG}"
if [ -n "${PUSH_TOKEN+set}" ]; then
    echo 'curl saw PUSH_TOKEN' >> "${PRUNE_TEST_CURL_LOG}"
fi
case "${url}" in
    */checksums.txt) cp "${PRUNE_TEST_CHECKSUMS}" "${out}" ;;
    *) printf '#!/bin/sh\\necho gitsign stub version\\n' > "${out}" ;;
esac
'''

# The downloaded stub cannot hash to the pinned digest, so the hash
# check itself is stubbed: it records what it was asked to check, and
# fails on demand.
SHA256SUM_STUB = '''#!/bin/bash
{ echo "$*"; cat; } >> "${PRUNE_TEST_SHA256_LOG}"
exit "${PRUNE_TEST_SHA256_RC:-0}"
'''

UNAME_STUB = '#!/bin/sh\necho "${PRUNE_TEST_ARCH:-x86_64}"\n'

# Wraps the real git. It logs each call, one line apiece: the
# arguments (newlines escaped, since a commit message has them), then
# tab-separated, the git configuration variables the script sets,
# whether PUSH_TOKEN is in the environment, and GIT_CEILING_DIRECTORIES. And it redirects the
# development clone, and the clone of the repository being landed on,
# to local repositories.
GIT_STUB = '''#!/bin/bash
{
    printf '%%s' "${*//$'\\n'/\\\\n}"
    printf '\\t%%s' "${GIT_CONFIG_GLOBAL-}" "${GIT_CONFIG_NOSYSTEM-}" "${GIT_CONFIG_COUNT-}" \\
        "${GIT_CONFIG_KEY_0-}" "${GIT_CONFIG_VALUE_0-}" "${GIT_CONFIG_KEY_1-}" "${GIT_CONFIG_VALUE_1-}" \\
        "${PUSH_TOKEN+set}" "${GIT_CEILING_DIRECTORIES-}"
    echo
} >> "${PRUNE_TEST_GIT_LOG}"
args=()
for arg in "$@"; do
    if [ "${arg}" = '%(url)s' ]; then
        arg="${PRUNE_TEST_DEVELOPMENT}"
    elif [ "${arg}" = '%(target_url)s' ]; then
        # file:// so that --depth applies, as it does to the real URL:
        # git ignores it for a plain path.
        arg="file://${PRUNE_TEST_ORIGIN}"
    fi
    args+=("${arg}")
done
exec '%(git)s' "${args[@]}"
'''
GIT_LOG_FIELDS = ('argv', 'global', 'nosystem', 'count', 'key0', 'value0', 'key1', 'value1', 'push_token', 'ceiling')
SLEEP_STUB = '#!/bin/sh\necho "$@" >> "${PRUNE_TEST_SLEEP_LOG}"\n'


def _pinned():
    """The version and digest the template pins."""
    with open(TEMPLATE_SCRIPT) as f:
        script = f.read()
    version = re.search(r"^GITSIGN_VERSION='([^']+)'$", script, re.MULTILINE).group(1)
    digest = re.search(r"^GITSIGN_SHA256='([0-9a-f]{64})'$", script, re.MULTILINE).group(1)
    return version, digest


def _write(path, content, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    if mode is not None:
        os.chmod(path, mode)


class CiPruneReviewsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, 'origin.git')
        self.checkout = os.path.join(self.tmp, 'checkout')
        stubs = os.path.join(self.tmp, 'stubs')
        logs = os.path.join(self.tmp, 'logs')
        os.makedirs(logs)

        # No global or system git configuration leaks in, and no GIT_
        # variable from whatever is running the suite -- pre-commit
        # sets some -- redirects these repositories.
        # This is the harness's own configuration; the script ignores
        # it.
        gitconfig = os.path.join(self.tmp, 'gitconfig')
        _write(gitconfig, (
            '[user]\n\tname = Test\n\temail = test@example.com\n'
            '[commit]\n\tgpgsign = false\n'
            '[init]\n\tdefaultBranch = %s\n'
        ) % BRANCH)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env['GIT_CONFIG_GLOBAL'] = gitconfig
        env['GIT_CONFIG_SYSTEM'] = os.devnull
        self.env = env

        # The harness runs the real git; only the script gets the wrapper.
        self.real_git = shutil.which('git', path=env['PATH'])
        git_stub = GIT_STUB % {'url': DEVELOPMENT_URL, 'target_url': TARGET_URL, 'git': self.real_git}
        for name, content in (('curl', CURL_STUB), ('sha256sum', SHA256SUM_STUB),
                              ('uname', UNAME_STUB), ('sleep', SLEEP_STUB), ('git', git_stub)):
            _write(os.path.join(stubs, name), content, 0o755)

        version, digest = _pinned()
        self.asset = 'gitsign_%s_linux_amd64' % version
        self.expected = '%s  %s' % (digest, self.asset)
        self.checksums = os.path.join(self.tmp, 'checksums.txt')
        self.races = os.path.join(self.tmp, 'races')
        self.import_failures = os.path.join(self.tmp, 'import-failures')
        self.set_checksums(['0' * 64 + '  gitsign_%s_darwin_amd64' % version, self.expected])
        self.set_races(0)
        self.set_import_failures(0)

        self.logs = {name: os.path.join(logs, name) for name in ('review', 'env', 'curl', 'sha256', 'sleep', 'git')}
        self.development = os.path.join(self.tmp, 'development')
        self.env.update({
            'PATH': stubs + os.pathsep + env['PATH'],
            'DEFAULT_BRANCH': BRANCH,
            'GITHUB_REPOSITORY': REPOSITORY,
            'RUNNER_TEMP': os.path.join(self.tmp, 'runner'),
            'PRUNE_TEST_LOG': self.logs['review'],
            'PRUNE_TEST_ENV_LOG': self.logs['env'],
            'PRUNE_TEST_GIT_LOG': self.logs['git'],
            'PRUNE_TEST_DEVELOPMENT': self.development,
            'PUSH_TOKEN': TOKEN,
            'PRUNE_TEST_CURL_LOG': self.logs['curl'],
            'PRUNE_TEST_SHA256_LOG': self.logs['sha256'],
            'PRUNE_TEST_SLEEP_LOG': self.logs['sleep'],
            'PRUNE_TEST_CHECKSUMS': self.checksums,
            'PRUNE_TEST_RACES': self.races,
            'PRUNE_TEST_IMPORT_FAILURES': self.import_failures,
            'PRUNE_TEST_ORIGIN': self.origin,
        })
        os.makedirs(self.env['RUNNER_TEMP'])

        # What the development clone is redirected to. Its content is
        # irrelevant to the stub wrapper, which never looks at it, but
        # its one commit tells it apart from an impostor.
        self.git('init', '-q', self.development)
        self.git('-C', self.development, 'commit', '-q', '--allow-empty', '-m', 'Development.')

    def git(self, *args, cwd=None):
        return subprocess.run([self.real_git, *args], cwd=cwd, env=self.env, check=True,
                              stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()

    def set_checksums(self, lines):
        _write(self.checksums, ''.join(line + '\n' for line in lines))

    def set_races(self, count):
        _write(self.races, '%d\n' % count)

    def set_import_failures(self, count):
        _write(self.import_failures, '%d\n' % count)

    def make_repository(self, gitignore=None, reviews_md=True, pre_receive=None):
        """An origin holding an adopted repository, and the runner's checkout of it."""
        seed = os.path.join(self.tmp, 'seed')
        self.git('init', '-q', '--bare', self.origin)
        self.git('init', '-q', seed)
        _write(os.path.join(seed, 'tools', 'review-tracking.sh'), REVIEW_TRACKING_STUB, 0o755)
        shutil.copy(TEMPLATE_SCRIPT, os.path.join(seed, 'tools', 'ci-prune-reviews.sh'))
        _write(os.path.join(seed, '.vscode', 'mikal.weaudit-shas.json'), '{}\n')
        if reviews_md:
            _write(os.path.join(seed, 'REVIEWS.md'), '# Reviews\n')
        if gitignore is not None:
            _write(os.path.join(seed, '.gitignore'), gitignore)
        self.git('-C', seed, 'add', '--force', '.')
        self.git('-C', seed, 'commit', '-q', '-m', 'Adopt review tracking.')
        self.git('-C', seed, 'push', '-q', self.origin, 'HEAD:%s' % BRANCH)
        self.git('clone', '-q', self.origin, self.checkout)
        if pre_receive is not None:
            _write(os.path.join(self.origin, 'hooks', 'pre-receive'), pre_receive, 0o755)
        return self.tip()

    def push_concurrent_commit(self):
        """A merge that lands after the checkout, before the run."""
        other = os.path.join(self.tmp, 'other')
        self.git('clone', '-q', self.origin, other)
        _write(os.path.join(other, 'merged.txt'), 'merged\n')
        self.git('-C', other, 'add', 'merged.txt')
        self.git('-C', other, 'commit', '-q', '-m', 'A merge.')
        self.git('-C', other, 'push', '-q', 'origin', 'HEAD:%s' % BRANCH)
        return self.tip()

    def show(self, rev, path):
        return self.git('--git-dir', self.origin, 'show', '%s:%s' % (rev, path))

    def tip(self):
        return self.git('--git-dir', self.origin, 'rev-parse', BRANCH)

    def message(self, rev):
        return self.git('--git-dir', self.origin, 'log', '-1', '--format=%B', rev)

    def target_clone(self):
        """Where the script clones the repository it lands on."""
        return os.path.join(self.env['RUNNER_TEMP'], 'target')

    def read_log(self, name):
        if not os.path.exists(self.logs[name]):
            return []
        with open(self.logs[name]) as f:
            return f.read().splitlines()

    def git_calls(self):
        """The script's git calls, and its stubs', as dicts of GIT_LOG_FIELDS."""
        return [dict(zip(GIT_LOG_FIELDS, line.split('\t'))) for line in self.read_log('git')]

    def run_script(self, **extra):
        env = dict(self.env)
        env.update(extra)
        # From outside the checkout, as the script anchors itself.
        return subprocess.run(
            [os.path.join(self.checkout, 'tools', 'ci-prune-reviews.sh')],
            cwd=self.tmp, env=env, timeout=120,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def test_nothing_to_prune_or_import_commits_nothing(self):
        before = self.make_repository()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No review marks to prune or import.', result.stdout)
        self.assertEqual(self.tip(), before)
        self.assertEqual(self.read_log('review'), ['prune %s' % before, 'import %s' % before])

    def test_changed_state_lands_as_one_commit(self):
        before = self.make_repository()
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.tip()
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', tip + '^'), before)
        self.assertEqual(self.message(tip), COMMIT_MESSAGE)
        self.assertEqual(self.git('--git-dir', self.origin, 'log', '-1', '--format=%an <%ae>', tip),
                         'shakenfist-bot <bot@shakenfist.com>')
        # The new, untracked imports file is committed, not left behind.
        self.assertEqual(self.git('--git-dir', self.origin, 'show', tip + ':.vscode/imports.weaudit-shas.json'),
                         before)
        self.assertIn('gitsign stub version', result.stdout)
        self.assertEqual(self.read_log('sleep'), [])

    def test_the_run_lands_on_the_fetched_tip_not_the_checkout(self):
        """The checkout is of the triggering commit, which may already be behind."""
        self.make_repository()
        newer = self.push_concurrent_commit()
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', self.tip() + '^'), newer)
        self.assertEqual(self.read_log('review'), ['prune %s' % newer, 'import %s' % newer])

    def test_a_rejected_push_is_regenerated_on_the_new_tip(self):
        """Not rebased: the landed commit is prune and import run again against the merge."""
        before = self.make_repository()
        self.set_races(1)
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Landing attempt 1 of 3 was rejected; retrying.', result.stdout)

        tip = self.tip()
        concurrent = self.git('--git-dir', self.origin, 'rev-parse', tip + '^')
        self.assertEqual(self.message(concurrent), 'A concurrent merge.')
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', concurrent + '^'), before)
        self.assertEqual(self.message(tip), COMMIT_MESSAGE)
        self.assertEqual(self.git('--git-dir', self.origin, 'show', tip + ':.vscode/imports.weaudit-shas.json'),
                         concurrent)
        self.assertEqual(self.read_log('review'), [
            'prune %s' % before, 'import %s' % before,
            'prune %s' % concurrent, 'import %s' % concurrent,
        ])
        self.assertEqual(self.read_log('sleep'), ['5'])

    def test_three_rejections_fail_the_run(self):
        self.make_repository()
        self.set_races(3)
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Could not land the review state after 3 attempts.', result.stderr)
        self.assertIn('Landing attempt 2 of 3 was rejected; retrying.', result.stdout)
        # Only a retry that is actually coming is announced or slept for.
        self.assertNotIn('Landing attempt 3 of 3', result.stdout)
        self.assertEqual(self.read_log('sleep'), ['5', '5'])
        self.assertEqual(self.message(self.tip()), 'A concurrent merge.')
        self.assertEqual(len([line for line in self.read_log('review') if line.startswith('import ')]), 3)

    def test_a_push_refused_for_another_reason_is_not_retried(self):
        """A token or ruleset problem is not a race, and regenerating will not fix it."""
        before = self.make_repository(
            pre_receive='#!/bin/sh\necho "Changes must be made through a pull request."\nexit 1\n')
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Changes must be made through a pull request.', result.stderr)
        self.assertIn('not because a merge landed first', result.stderr)
        self.assertNotIn('retrying', result.stdout)
        self.assertNotIn('Could not land the review state', result.stderr)
        self.assertEqual(self.read_log('sleep'), [])
        self.assertEqual(len([line for line in self.read_log('review') if line.startswith('import ')]), 1)
        self.assertEqual(self.tip(), before)

    def test_a_failed_import_still_lands_prunes_result(self):
        """Prune is what stops a changed file reading as reviewed, so an import failure must not lose it."""
        before = self.make_repository()
        self.set_import_failures(1)
        result = self.run_script(PRUNE_TEST_PRUNE='1', PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Traceback: import fell over', result.stderr)
        self.assertIn('import failed with exit 3. Discarding', result.stderr)
        # Said again last, after the push, where a reader of the log looks.
        self.assertTrue(result.stderr.rstrip().endswith("has landed. See import's output above."),
                        result.stderr)

        tip = self.tip()
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', tip + '^'), before)
        self.assertEqual(self.message(tip), COMMIT_MESSAGE)
        self.assertEqual(self.show(tip, '.vscode/mikal.weaudit-shas.json'), '{"pruned": true}')
        # What the failed import wrote is not landed with it.
        self.assertEqual(self.show(tip, 'REVIEWS.md'), '# Reviews, pruned')
        self.assertNotIn('imports.weaudit-shas.json',
                         self.git('--git-dir', self.origin, 'ls-tree', '-r', '--name-only', tip))

    def test_a_failed_import_with_nothing_pruned_still_fails(self):
        before = self.make_repository()
        self.set_import_failures(1)
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('No review marks to prune or import.', result.stdout)
        self.assertIn('import failed with exit 3', result.stderr)
        self.assertEqual(self.tip(), before)

    def test_an_import_that_recovers_on_a_retry_lands_everything(self):
        """Only the last attempt's import counts."""
        self.make_repository()
        self.set_races(1)
        self.set_import_failures(1)
        result = self.run_script(PRUNE_TEST_PRUNE='1', PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Landing attempt 1 of 3 was rejected; retrying.', result.stdout)
        tip = self.tip()
        concurrent = self.git('--git-dir', self.origin, 'rev-parse', tip + '^')
        self.assertEqual(self.message(concurrent), 'A concurrent merge.')
        self.assertEqual(self.show(tip, '.vscode/imports.weaudit-shas.json'), concurrent)
        self.assertEqual(self.show(tip, '.vscode/mikal.weaudit-shas.json'), '{"pruned": true}')

    def test_the_token_reaches_only_the_push(self):
        """As an extraheader in the push's environment: never in argv, .git/config, or anything else run."""
        self.make_repository()
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        header = 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + TOKEN).encode()).decode()

        calls = self.git_calls()
        pushes = [call for call in calls if call['argv'].startswith('push ')]
        self.assertEqual([call['argv'] for call in pushes], ['push origin HEAD:%s' % BRANCH])
        self.assertEqual((pushes[0]['count'], pushes[0]['key0'], pushes[0]['value0'], pushes[0]['key1']),
                         ('1', 'http.https://github.com/.extraheader', header, ''))
        for call in calls:
            self.assertNotIn(TOKEN, call['argv'])
            self.assertNotIn(header.split()[-1], call['argv'])
            self.assertEqual(call['push_token'], '', call['argv'])
            if call not in pushes:
                self.assertEqual((call['count'], call['value0']), ('', ''), call['argv'])

        for repo in (self.checkout, self.target_clone()):
            with open(os.path.join(repo, '.git', 'config')) as f:
                self.assertNotIn(TOKEN, f.read())
        self.assertNotIn('curl saw PUSH_TOKEN', self.read_log('curl'))
        self.assertEqual([line.split()[-1] for line in self.read_log('env')],
                         ['PUSH_TOKEN=', 'PUSH_TOKEN='])
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_the_workspace_git_state_is_never_used(self):
        """A static runner reuses the workspace, .git included, so an earlier job can plant hooks and config there."""
        before = self.make_repository()
        marker = os.path.join(self.tmp, 'planted-code-ran')
        attacker_origin = os.path.join(self.tmp, 'attacker-origin.git')
        self.git('init', '-q', '--bare', attacker_origin)
        hooks = os.path.join(self.checkout, '.git', 'hooks')
        for hook in ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit', 'reference-transaction',
                     'post-checkout', 'pre-push'):
            _write(os.path.join(hooks, hook), '#!/bin/sh\necho %s >> %s\n' % (hook, marker), 0o755)
        fsmonitor = os.path.join(self.tmp, 'fsmonitor')
        _write(fsmonitor, '#!/bin/sh\necho fsmonitor >> %s\n' % marker, 0o755)
        included = os.path.join(self.tmp, 'included')
        _write(included, '[url "%s"]\n\tinsteadOf = %s\n' % (attacker_origin, self.origin))
        self.git('-C', self.checkout, 'config', 'core.fsmonitor', fsmonitor)
        self.git('-C', self.checkout, 'config', 'remote.origin.pushurl', attacker_origin)
        self.git('-C', self.checkout, 'config', 'include.path', included)
        workspace_head = self.git('-C', self.checkout, 'rev-parse', 'HEAD')

        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', self.tip() + '^'), before)
        self.assertEqual(self.git('--git-dir', attacker_origin, 'for-each-ref'), '')
        self.assertEqual(self.git('-C', self.checkout, 'rev-parse', 'HEAD'), workspace_head)
        # Landed from a fresh, shallow clone of the URL GITHUB_REPOSITORY names.
        clones = [call['argv'] for call in self.git_calls() if call['argv'].startswith('clone ')]
        self.assertEqual(clones[-1], 'clone --quiet --depth 1 --branch %s %s %s'
                         % (BRANCH, TARGET_URL, self.target_clone()))
        self.assertEqual(self.git('-C', self.target_clone(), 'rev-parse', '--is-shallow-repository'), 'true')
        self.assertEqual(self.git('-C', self.target_clone(), 'rev-list', '--count', 'HEAD'), '2')

    def test_a_run_without_github_repository_is_refused(self):
        before = self.make_repository()
        env = dict(self.env)
        del env['GITHUB_REPOSITORY']
        result = subprocess.run([os.path.join(self.checkout, 'tools', 'ci-prune-reviews.sh')], cwd=self.tmp,
                                env=env, timeout=120, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('GITHUB_REPOSITORY must name the repository', result.stderr)
        self.assertEqual(self.git_calls(), [])
        self.assertEqual(self.tip(), before)

    def test_runner_git_configuration_is_ignored(self):
        """A poisoned ~/.gitconfig or system gitconfig can redirect neither the development clone nor the push."""
        before = self.make_repository()
        attacker = os.path.join(self.tmp, 'attacker')
        self.git('init', '-q', attacker)
        self.git('-C', attacker, 'commit', '-q', '--allow-empty', '-m', 'Attacker.')
        attacker_origin = os.path.join(self.tmp, 'attacker-origin.git')
        self.git('init', '-q', '--bare', attacker_origin)
        poisoned_global = os.path.join(self.tmp, 'poisoned-global')
        _write(poisoned_global, '[url "%s"]\n\tinsteadOf = %s\n' % (attacker, self.development))
        poisoned_system = os.path.join(self.tmp, 'poisoned-system')
        _write(poisoned_system, '[url "%s"]\n\tpushInsteadOf = %s\n' % (attacker_origin, self.origin))

        result = self.run_script(PRUNE_TEST_IMPORT='1', GIT_CONFIG_GLOBAL=poisoned_global,
                                 GIT_CONFIG_SYSTEM=poisoned_system)
        self.assertEqual(result.returncode, 0, result.stderr)
        clone = os.path.join(self.env['RUNNER_TEMP'], 'development')
        self.assertEqual(self.git('-C', clone, 'log', '-1', '--format=%s'), 'Development.')
        # Plain and full, with the remote-tracking ref import walks.
        self.git('-C', clone, 'rev-parse', '--verify', 'refs/remotes/origin/%s' % BRANCH)
        self.assertEqual(self.git('-C', clone, 'rev-parse', '--is-shallow-repository'), 'false')
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', self.tip() + '^'), before)
        self.assertEqual(self.git('--git-dir', attacker_origin, 'for-each-ref'), '')

    def test_git_never_looks_for_a_repository_above_runner_temp(self):
        """_work, the runner directory and home are writable by an earlier job, so a .git there must stay unseen."""
        self.make_repository()
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.git_calls()
        self.assertTrue(calls)
        ceiling = os.path.dirname(self.env['RUNNER_TEMP'])
        for call in calls:
            self.assertEqual(call['ceiling'], ceiling, call['argv'])

    def test_gitsign_gets_a_fresh_tuf_cache(self):
        """Not the shared ~/.sigstore, which an earlier job on the runner could have poisoned."""
        self.make_repository()
        tuf_root = os.path.join(self.env['RUNNER_TEMP'], 'sigstore-root')
        _write(os.path.join(tuf_root, 'remote.json'), '{"mirror": "https://attacker.example"}\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[1] for line in self.read_log('env')], ['TUF_ROOT=' + tuf_root] * 2)
        self.assertEqual(os.listdir(tuf_root), [])

    def workflow_steps(self):
        """The template workflow's steps, by name, with comments dropped.

        Split on the step separator rather than parsed, since nothing
        else here needs a YAML library; the exact set of names asserted
        below is what stops a format change passing silently.
        """
        with open(TEMPLATE_WORKFLOW) as f:
            lines = [line for line in f.read().splitlines() if not line.lstrip().startswith('#')]
        text = '\n'.join(lines) + '\n'
        steps = {step.split('\n', 1)[0]: step for step in text.split(WORKFLOW_STEP_SEPARATOR)[1:]}
        self.assertEqual(sorted(steps), ['Fetch the prune script', 'Mint the push token',
                                         'Prune and import review marks'])
        return text, steps

    def test_the_workflow_never_checks_out_the_workspace(self):
        """No checkout, and the key and push token reach only the steps that need them.

        A runner left dirty by an earlier job, as a static one can be, then has no hook
        through which to rewrite the script, and no step but the push sees the token.
        """
        text, steps = self.workflow_steps()
        self.assertNotIn('actions/checkout', text)
        for name, step in steps.items():
            if name == 'Mint the push token':
                # A uses: step runs no shell, so it has no directory to run in.
                self.assertNotIn('working-directory', step)
            else:
                self.assertIn('\n        working-directory: ${{ runner.temp }}\n', step)

        # The key is named twice, by the presence guard and by the step
        # that mints from it, and no other secret is named at all.
        key_guard = "\n      HAS_REVIEW_MARKS_KEY: ${{ secrets.REVIEW_MARKS_APP_KEY != '' }}\n"
        key_input = '\n          private-key: ${{ secrets.REVIEW_MARKS_APP_KEY }}\n'
        self.assertEqual(text.count('secrets.REVIEW_MARKS_APP_KEY'), 2)
        self.assertEqual(text.count('secrets.'), 2)
        self.assertIn(key_guard, text.split(WORKFLOW_STEP_SEPARATOR)[0])
        self.assertIn(key_input, steps['Mint the push token'])

        # The push token reaches the script step alone, through PUSH_TOKEN.
        push_token = '\n          PUSH_TOKEN: ${{ steps.app-token.outputs.token || github.token }}\n'
        self.assertIn(push_token, steps['Prune and import review marks'])
        for name in ('Fetch the prune script', 'Prune and import review marks'):
            step = steps[name].replace(push_token, '\n').lower()
            self.assertNotIn('secrets.', step, name)
            self.assertNotIn('token', step, name)

    def test_the_push_token_is_minted_from_the_app_on_a_vm_runner(self):
        """Only the default branch can read the key, and only where it is present is a token minted."""
        text, steps = self.workflow_steps()
        job = text.split(WORKFLOW_STEP_SEPARATOR)[0]
        self.assertEqual(list(steps), ['Fetch the prune script', 'Mint the push token',
                                       'Prune and import review marks'])

        self.assertIn('\n    runs-on: [self-hosted, vm, debian-13, s]\n', job)
        self.assertEqual(job.count('runs-on:'), 1)
        self.assertIn('\n    environment:\n      name: review-marks\n      deployment: false\n', job)
        self.assertIn('\n    permissions:\n      contents: write\n', job)
        self.assertIn('\n    timeout-minutes: 30\n', job)

        mint = steps['Mint the push token']
        self.assertIn('\n        id: app-token\n', mint)
        self.assertIn("\n        if: env.HAS_REVIEW_MARKS_KEY == 'true'\n", mint)
        self.assertRegex(mint, r'\n        uses: actions/create-github-app-token@[0-9a-f]{40} # v\d+\.\d+\.\d+\n')
        self.assertIn('\n          client-id: ${{ vars.REVIEW_MARKS_APP_CLIENT_ID }}\n', mint)
        self.assertIn('\n          permission-contents: write\n', mint)
        # The vm runners set a proxy, and the action will not start
        # behind one unless Node.js is told to use it.
        self.assertIn("\n        env:\n          NODE_USE_ENV_PROXY: '1'\n", mint)
        # Naming the owner, or repositories, would widen the token to
        # every repository the App is installed on.
        self.assertNotRegex(mint, r'\n\s+(owner|repositories):')
        self.assertNotIn('app-id:', mint)

    def test_the_workflow_steps_land_the_review_state(self):
        """Run as the workflow runs them: fetch the script into RUNNER_TEMP, then run it from there."""
        before = self.make_repository()
        _, steps = self.workflow_steps()
        fetch = steps['Fetch the prune script'].split('run: >-\n', 1)[1]
        fetch = ' '.join(line.strip() for line in fetch.splitlines() if line.strip())
        run = steps['Prune and import review marks'].split('run: ', 1)[1].split('\n', 1)[0]
        env = dict(self.env, GITHUB_REPOSITORY=REPOSITORY)
        fetch_env = {k: v for k, v in env.items() if k != 'PUSH_TOKEN'}
        subprocess.run(['bash', '-ec', fetch], cwd=env['RUNNER_TEMP'], env=fetch_env, check=True)
        clone = [call for call in self.git_calls() if call['argv'].startswith('clone ')][0]
        self.assertEqual(clone['argv'], 'clone --quiet --depth 1 --branch %s %s prune-script' % (BRANCH, TARGET_URL))
        self.assertEqual((clone['global'], clone['nosystem'], clone['ceiling']),
                         (os.devnull, '1', os.path.dirname(env['RUNNER_TEMP'])))
        result = subprocess.run(['bash', '-ec', run], cwd=env['RUNNER_TEMP'], env=dict(env, PRUNE_TEST_IMPORT='1'),
                                timeout=120, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', self.tip() + '^'), before)

    def test_a_gitignored_reviews_md_fails_the_run(self):
        """A REVIEWS.md ignored before it was first generated can never be committed either."""
        before = self.make_repository(gitignore='REVIEWS.md\n', reviews_md=False)
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('REVIEWS.md\nREVIEWS.md must not be ignored', result.stderr)
        self.assertNotIn('imports.weaudit-shas.json', result.stderr)
        self.assertEqual(self.tip(), before)

    def test_a_gitignored_imports_file_fails_the_run(self):
        """Otherwise every run imports, commits nothing, and goes green."""
        before = self.make_repository(gitignore='.vscode/\n')
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('.vscode/imports.weaudit-shas.json', result.stderr)
        self.assertIn('step 1 of', result.stderr)
        # A tracked sidecar under an ignored directory is fine: git add
        # stages it anyway.
        self.assertNotIn('mikal.weaudit-shas.json', result.stderr)
        self.assertNotIn('No review marks', result.stdout)
        self.assertEqual(self.tip(), before)

    def test_the_gitignore_exception_is_enough(self):
        """As step 1 writes it: .vscode/* rather than .vscode/, whose contents no exception can reach."""
        before = self.make_repository(gitignore='.vscode/*\n!.vscode/*.weaudit-shas.json\n')
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', self.tip() + '^'), before)

    def test_gitsign_is_checked_against_the_pinned_digest(self):
        self.make_repository()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_log('sha256'), ['--check --strict', self.expected])
        self.assertEqual([url.rsplit('/', 1)[1] for url in self.read_log('curl')],
                         [self.asset, 'checksums.txt'])

    def test_a_checksums_file_without_the_pinned_digest_fails(self):
        """Which is what a version bumped without its digest looks like."""
        before = self.make_repository()
        self.set_checksums(['f' * 64 + '  ' + self.asset])
        result = self.run_script(PRUNE_TEST_IMPORT='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('does not list', result.stderr)
        self.assertEqual(self.read_log('review'), [])
        self.assertEqual(self.tip(), before)

    def test_a_runner_that_is_not_x86_64_is_refused(self):
        """The pinned digest is the linux_amd64 binary's, so nothing else could be installed."""
        before = self.make_repository()
        result = self.run_script(PRUNE_TEST_IMPORT='1', PRUNE_TEST_ARCH='aarch64')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('This script installs the linux_amd64 gitsign, but this runner is aarch64.', result.stderr)
        # Refused before any download, clone, fetch or push.
        self.assertEqual(self.read_log('curl'), [])
        self.assertEqual(self.read_log('sha256'), [])
        self.assertEqual(self.read_log('review'), [])
        self.assertEqual(self.git_calls(), [])
        self.assertFalse(os.path.exists(os.path.join(self.env['RUNNER_TEMP'], 'development')))
        self.assertFalse(os.path.exists(os.path.join(self.env['RUNNER_TEMP'], 'gitsign')))
        self.assertEqual(self.tip(), before)

    def test_a_binary_that_does_not_hash_to_the_digest_fails(self):
        before = self.make_repository()
        result = self.run_script(PRUNE_TEST_IMPORT='1', PRUNE_TEST_SHA256_RC='1')
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('gitsign stub version', result.stdout)
        self.assertEqual(self.read_log('review'), [])
        self.assertEqual(self.tip(), before)


if __name__ == '__main__':
    unittest.main()
