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
are stubs on PATH; the development clone is redirected to a local
repository with url.insteadOf; and tools/review-tracking.sh in the
checkout is a stub that stands in for prune and import. It writes
the imports file from HEAD, so the content of the landed commit says
which tree it was regenerated against.

Run with: python3 scripts/tests/test_ci_prune_reviews.py
"""

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

BRANCH = 'trunk'
DEVELOPMENT_URL = 'https://github.com/shakenfist/development'
COMMIT_MESSAGE = 'Prune and import review marks.\n\nAutomated commit by the prune-reviews workflow.'

# Stands in for tools/review-tracking.sh. It logs each subcommand with
# the HEAD it ran against; import writes the imports file from HEAD
# when asked to import anything; and import can push a concurrent
# commit to origin, a set number of times, which is the race the
# landing loop exists for.
REVIEW_TRACKING_STUB = '''#!/bin/bash
set -e
echo "$1 $(git rev-parse HEAD)" >> "${PRUNE_TEST_LOG}"
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
    git -C "${other}" commit --quiet -m 'A concurrent merge.'
    git -C "${other}" push --quiet origin "HEAD:${DEFAULT_BRANCH}"
    rm -rf "${other}"
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

UNAME_STUB = '#!/bin/sh\necho x86_64\n'
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
        gitconfig = os.path.join(self.tmp, 'gitconfig')
        _write(gitconfig, (
            '[user]\n\tname = Test\n\temail = test@example.com\n'
            '[commit]\n\tgpgsign = false\n'
            '[init]\n\tdefaultBranch = %s\n'
            '[url "%s"]\n\tinsteadOf = %s\n'
        ) % (BRANCH, os.path.join(self.tmp, 'development'), DEVELOPMENT_URL))
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env['GIT_CONFIG_GLOBAL'] = gitconfig
        env['GIT_CONFIG_SYSTEM'] = os.devnull
        self.env = env

        for name, content in (('curl', CURL_STUB), ('sha256sum', SHA256SUM_STUB),
                              ('uname', UNAME_STUB), ('sleep', SLEEP_STUB)):
            _write(os.path.join(stubs, name), content, 0o755)

        version, digest = _pinned()
        self.asset = 'gitsign_%s_linux_amd64' % version
        self.expected = '%s  %s' % (digest, self.asset)
        self.checksums = os.path.join(self.tmp, 'checksums.txt')
        self.races = os.path.join(self.tmp, 'races')
        self.set_checksums(['0' * 64 + '  gitsign_%s_darwin_amd64' % version, self.expected])
        self.set_races(0)

        self.logs = {name: os.path.join(logs, name) for name in ('review', 'curl', 'sha256', 'sleep')}
        self.env.update({
            'PATH': stubs + os.pathsep + env['PATH'],
            'DEFAULT_BRANCH': BRANCH,
            'RUNNER_TEMP': os.path.join(self.tmp, 'runner'),
            'PRUNE_TEST_LOG': self.logs['review'],
            'PRUNE_TEST_CURL_LOG': self.logs['curl'],
            'PRUNE_TEST_SHA256_LOG': self.logs['sha256'],
            'PRUNE_TEST_SLEEP_LOG': self.logs['sleep'],
            'PRUNE_TEST_CHECKSUMS': self.checksums,
            'PRUNE_TEST_RACES': self.races,
            'PRUNE_TEST_ORIGIN': self.origin,
        })
        os.makedirs(self.env['RUNNER_TEMP'])

        # What the development clone is redirected to. Its content is
        # irrelevant: the stub wrapper never looks at it.
        development = os.path.join(self.tmp, 'development')
        self.git('init', '-q', development)
        self.git('-C', development, 'commit', '-q', '--allow-empty', '-m', 'Development.')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env, check=True,
                              stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()

    def set_checksums(self, lines):
        _write(self.checksums, ''.join(line + '\n' for line in lines))

    def set_races(self, count):
        _write(self.races, '%d\n' % count)

    def make_repository(self, gitignore=None):
        """An origin holding an adopted repository, and the runner's checkout of it."""
        seed = os.path.join(self.tmp, 'seed')
        self.git('init', '-q', '--bare', self.origin)
        self.git('init', '-q', seed)
        _write(os.path.join(seed, 'tools', 'review-tracking.sh'), REVIEW_TRACKING_STUB, 0o755)
        shutil.copy(TEMPLATE_SCRIPT, os.path.join(seed, 'tools', 'ci-prune-reviews.sh'))
        _write(os.path.join(seed, '.vscode', 'mikal.weaudit-shas.json'), '{}\n')
        _write(os.path.join(seed, 'REVIEWS.md'), '# Reviews\n')
        if gitignore is not None:
            _write(os.path.join(seed, '.gitignore'), gitignore)
        self.git('-C', seed, 'add', '--force', '.')
        self.git('-C', seed, 'commit', '-q', '-m', 'Adopt review tracking.')
        self.git('-C', seed, 'push', '-q', self.origin, 'HEAD:%s' % BRANCH)
        self.git('clone', '-q', self.origin, self.checkout)
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

    def tip(self):
        return self.git('--git-dir', self.origin, 'rev-parse', BRANCH)

    def message(self, rev):
        return self.git('--git-dir', self.origin, 'log', '-1', '--format=%B', rev)

    def read_log(self, name):
        if not os.path.exists(self.logs[name]):
            return []
        with open(self.logs[name]) as f:
            return f.read().splitlines()

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

    def test_a_binary_that_does_not_hash_to_the_digest_fails(self):
        before = self.make_repository()
        result = self.run_script(PRUNE_TEST_IMPORT='1', PRUNE_TEST_SHA256_RC='1')
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('gitsign stub version', result.stdout)
        self.assertEqual(self.read_log('review'), [])
        self.assertEqual(self.tip(), before)


if __name__ == '__main__':
    unittest.main()
