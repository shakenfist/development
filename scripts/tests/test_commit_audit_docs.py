#!/usr/bin/env python3

"""Tests for scripts/commit-audit-docs.sh, run rather than read.

The script pushes the regenerated compliance page to main with a token
that main's ruleset admits as a bypass actor, so what matters about it
is what reaches main and who sees the token. Neither is checkable by
reading it: a `git add` widened to the directory, or a header moved
into argv, still looks right. So these run it from a throwaway clone of
a local bare origin, the way test_ci_prune_reviews.py runs
ci-prune-reviews.sh.

Nothing here reaches the network. git is a wrapper on PATH that logs
every call and then runs the real git. For a push it also asks git
which extraheader applies to a github.com URL and to another host, in
the push's own environment: the origin here is a local path, which
never sends an HTTP header, so the log is what proves the header is
handed to git and scoped to github.com rather than that being assumed.

Run with: python3 scripts/tests/test_commit_audit_docs.py
"""

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import REPO_ROOT  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, 'scripts', 'commit-audit-docs.sh')

BRANCH = 'main'
PAGE_PATH = 'docs/audits/compliance.md'
TOKEN = 'test-push-token-4a5b6c'
HEADER = 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + TOKEN).encode()).decode()
COMMIT_MESSAGE = 'Regenerate the audit compliance page.\n\nAutomated commit by the consistency-audit workflow.'

# The page as committed, and as update-docs regenerates it: prose
# outside the markers carried over, the table between them rewritten.
SEEDED_PAGE = 'Prose v1.\n<!-- begin -->\ntable v1\n<!-- end -->\n'
REGENERATED_PAGE = 'Prose v1.\n<!-- begin -->\ntable v2\n<!-- end -->\n'

# Wraps the real git. It logs each call, one line apiece: the arguments
# (newlines escaped, since a commit message has them), then
# tab-separated, the git configuration variables the script sets,
# whether PUSH_TOKEN is in the environment, and for a push the
# extraheader git resolves for github.com and for another host.
GIT_STUB = '''#!/bin/bash
github=''
other=''
if [ "$1" = 'push' ]; then
    github="$('%(git)s' config --get-urlmatch http.extraheader https://github.com/shakenfist/development)"
    other="$('%(git)s' config --get-urlmatch http.extraheader https://example.com/shakenfist/development)"
fi
{
    printf '%%s' "${*//$'\\n'/\\\\n}"
    printf '\\t%%s' "${GIT_CONFIG_COUNT-}" "${GIT_CONFIG_KEY_0-}" "${GIT_CONFIG_VALUE_0-}" "${GIT_CONFIG_KEY_1-}" \\
        "${PUSH_TOKEN+set}" "${github}" "${other}"
    echo
} >> "${AUDIT_DOCS_TEST_GIT_LOG}"
exec '%(git)s' "$@"
'''
GIT_LOG_FIELDS = ('argv', 'count', 'key0', 'value0', 'key1', 'push_token', 'github', 'other')


def _write(path, content, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    if mode is not None:
        os.chmod(path, mode)


class CommitAuditDocsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, 'origin.git')
        self.checkout = os.path.join(self.tmp, 'checkout')
        self.git_log = os.path.join(self.tmp, 'git.log')

        # No global or system git configuration leaks in, and no GIT_
        # variable from whatever is running the suite -- pre-commit
        # sets some -- redirects these repositories.
        gitconfig = os.path.join(self.tmp, 'gitconfig')
        _write(gitconfig, (
            '[user]\n\tname = Test\n\temail = test@example.com\n'
            '[commit]\n\tgpgsign = false\n'
            '[init]\n\tdefaultBranch = %s\n'
        ) % BRANCH)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env['GIT_CONFIG_GLOBAL'] = gitconfig
        env['GIT_CONFIG_SYSTEM'] = os.devnull
        env.pop('PUSH_TOKEN', None)
        self.env = env

        # The harness runs the real git; only the script gets the wrapper.
        self.real_git = shutil.which('git', path=env['PATH'])
        stubs = os.path.join(self.tmp, 'stubs')
        _write(os.path.join(stubs, 'git'), GIT_STUB % {'git': self.real_git}, 0o755)
        self.script_env = dict(env, PATH=stubs + os.pathsep + env['PATH'], AUDIT_DOCS_TEST_GIT_LOG=self.git_log)

        # An origin holding the script and the page, and the runner's
        # checkout of it, detached at the commit the audit ran on.
        seed = os.path.join(self.tmp, 'seed')
        self.git('init', '-q', seed)
        os.makedirs(os.path.join(seed, 'scripts'))
        shutil.copy(SCRIPT, os.path.join(seed, 'scripts', 'commit-audit-docs.sh'))
        _write(os.path.join(seed, PAGE_PATH), SEEDED_PAGE)
        _write(os.path.join(seed, 'docs', 'audits', 'other.md'), 'Other prose.\n')
        self.git('-C', seed, 'add', '.')
        self.git('-C', seed, 'commit', '-q', '-m', 'Seed.')
        self.git('init', '-q', '--bare', self.origin)
        self.git('-C', seed, 'push', '-q', self.origin, 'HEAD:%s' % BRANCH)
        self.base = self.tip()
        self.git('clone', '-q', self.origin, self.checkout)
        self.git('-C', self.checkout, 'checkout', '-q', '--detach', self.base)

        self.page = os.path.join(self.tmp, 'page.md')
        _write(self.page, REGENERATED_PAGE)

    def git(self, *args):
        return subprocess.run([self.real_git, *args], env=self.env, check=True,
                              stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()

    def tip(self):
        return self.git('--git-dir', self.origin, 'rev-parse', BRANCH)

    def show(self, rev, path):
        return self.git('--git-dir', self.origin, 'show', '%s:%s' % (rev, path))

    def git_calls(self):
        """The script's git calls, as dicts of GIT_LOG_FIELDS."""
        if not os.path.exists(self.git_log):
            return []
        with open(self.git_log) as f:
            return [dict(zip(GIT_LOG_FIELDS, line.split('\t'))) for line in f.read().splitlines()]

    def run_script(self, args=None, token=TOKEN):
        """Run from the top of the checkout, as push-docs does; token=None leaves PUSH_TOKEN unset."""
        env = dict(self.script_env)
        if token is not None:
            env['PUSH_TOKEN'] = token
        if args is None:
            args = [self.page]
        return subprocess.run(
            [os.path.join('scripts', 'commit-audit-docs.sh'), *args],
            cwd=self.checkout, env=env, timeout=120,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def push_concurrent_commit(self):
        """A merge that lands on main while the audit runs, editing the page's prose."""
        other = os.path.join(self.tmp, 'other')
        self.git('clone', '-q', self.origin, other)
        _write(os.path.join(other, PAGE_PATH), SEEDED_PAGE.replace('Prose v1.', 'Prose v2.'))
        _write(os.path.join(other, 'docs', 'audits', 'other.md'), 'Other prose, edited.\n')
        self.git('-C', other, 'commit', '-q', '-am', 'A concurrent merge.')
        self.git('-C', other, 'push', '-q', 'origin', 'HEAD:%s' % BRANCH)
        return self.tip()

    def assert_nothing_happened(self):
        self.assertEqual(self.tip(), self.base)
        self.assertEqual(self.git('-C', self.checkout, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(self.git('-C', self.checkout, 'status', '--porcelain'), '')

    def test_an_empty_token_is_refused_before_anything_runs(self):
        # Checked first, so that a missing token fails every run and not
        # only the first one with something to push.
        result = self.run_script(token='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PUSH_TOKEN is empty', result.stderr)
        self.assertEqual(self.git_calls(), [])
        self.assert_nothing_happened()

    def test_an_unset_token_is_refused_before_anything_runs(self):
        result = self.run_script(token=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PUSH_TOKEN is empty', result.stderr)
        self.assertEqual(self.git_calls(), [])
        self.assert_nothing_happened()

    def test_a_missing_or_empty_page_is_refused(self):
        # Either means the artifact did not arrive, and committing it
        # would blank the published page.
        empty = os.path.join(self.tmp, 'empty.md')
        _write(empty, '')
        for page in (os.path.join(self.tmp, 'missing.md'), empty):
            with self.subTest(page=os.path.basename(page)):
                result = self.run_script([page])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('is missing or empty', result.stderr)
                self.assertEqual(self.git_calls(), [])
                self.assert_nothing_happened()

    def test_the_wrong_number_of_arguments_is_refused(self):
        for args in ([], [self.page, self.page]):
            with self.subTest(count=len(args)):
                result = self.run_script(args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Usage:', result.stderr)
                self.assertEqual(self.git_calls(), [])
                self.assert_nothing_happened()

    def test_an_unchanged_page_commits_and_pushes_nothing(self):
        _write(self.page, SEEDED_PAGE)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No changes to the audit compliance page.', result.stdout)
        verbs = [call['argv'].split(' ', 1)[0] for call in self.git_calls()]
        self.assertNotIn('commit', verbs)
        self.assertNotIn('push', verbs)
        self.assert_nothing_happened()

    def test_a_page_regenerated_behind_main_is_rebased_onto_it(self):
        """The page lands on top of what merged during the audit, keeping that merge's prose edit."""
        concurrent = self.push_concurrent_commit()
        # Anything else in the checkout stays out of the bot's commit:
        # everything else under docs/audits/ is prose under human review.
        _write(os.path.join(self.checkout, 'docs', 'audits', 'stray.md'), 'Not for main.\n')

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.tip()
        self.assertEqual(self.git('--git-dir', self.origin, 'rev-parse', tip + '^'), concurrent)
        self.assertEqual(self.git('--git-dir', self.origin, 'diff', '--name-only', concurrent, tip), PAGE_PATH)
        self.assertEqual(self.show(tip, PAGE_PATH), REGENERATED_PAGE.replace('Prose v1.', 'Prose v2.').strip())
        self.assertEqual(self.show(tip, 'docs/audits/other.md'), 'Other prose, edited.')
        self.assertEqual(self.git('--git-dir', self.origin, 'log', '-1', '--format=%an <%ae>', tip),
                         'shakenfist-bot <bot@shakenfist.com>')
        self.assertEqual(self.git('--git-dir', self.origin, 'log', '-1', '--format=%B', tip), COMMIT_MESSAGE)

    def test_the_token_reaches_only_the_push(self):
        """As a github.com extraheader in the push's environment: never in argv, .git/config, or another git."""
        self.push_concurrent_commit()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.git_calls()
        pushes = [call for call in calls if call['argv'].startswith('push ')]
        self.assertEqual([call['argv'] for call in pushes], ['push origin HEAD:%s' % BRANCH])
        push = pushes[0]
        self.assertEqual((push['count'], push['key0'], push['value0'], push['key1']),
                         ('1', 'http.https://github.com/.extraheader', HEADER, ''))
        # What git itself makes of that configuration: the header for
        # github.com, and nothing for any other host.
        self.assertEqual((push['github'], push['other']), (HEADER, ''))

        encoded = HEADER.split()[-1]
        for call in calls:
            self.assertNotIn(TOKEN, call['argv'])
            self.assertNotIn(encoded, call['argv'])
            self.assertEqual(call['push_token'], '', call['argv'])
            if call is not push:
                self.assertEqual((call['count'], call['value0']), ('', ''), call['argv'])

        with open(os.path.join(self.checkout, '.git', 'config')) as f:
            config = f.read()
        self.assertNotIn(TOKEN, config)
        self.assertNotIn(encoded, config)
        self.assertNotIn('extraheader', config)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
