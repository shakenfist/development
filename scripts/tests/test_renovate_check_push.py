#!/usr/bin/env python3

"""Tests for renovate-check-push.py.

Run with: python3 scripts/tests/test_renovate_check_push.py

Every test here is a way the probe could report success when the
cutover is not safe, because that is the only failure of this script
that costs anything: a job that wrongly fails is read by whoever
dispatched it, while a job that wrongly passes is read by nobody and
disables 23 workflows behind it.

No test reaches the network. api_get is the seam: the probe asks GitHub
through that one function, so a fake keyed by the exact URLs the probe
builds covers the listing, the configuration read and the permissions
read at once.
"""

import importlib.util
import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPT_DIR, 'renovate-check-push.py')

# renovate-check-push.py is not importable by name (the hyphens are not
# valid in a module identifier), so load it from its path.
_spec = importlib.util.spec_from_file_location('renovate_check_push', SCRIPT)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

ORG = 'shakenfist'
OTHER_ORG = 'somebodyelse'

# A 404 from the API, which the probe asks for with allow_missing on the
# configuration read and nowhere else.
MISSING = object()

# What the contents endpoint answers with when the file is there. The
# probe reads nothing out of it, only whether it exists.
CONFIG = {'name': 'renovate.json'}

# A repository the token can read but whose answer carries no
# permissions block at all, which is not the same as one carrying
# push: false and has to be treated the same way.
NO_PERMISSIONS = object()

# The URLs are built from the module's own constants rather than typed
# out, so changing a page size or a configuration file name does not
# leave these tests passing against URLs the probe no longer asks for.
ORG_LISTING = f'{probe.API_ROOT}/orgs/{ORG}/repos?per_page={probe.PAGE_SIZE}'
INSTALLATION_LISTING = f'{probe.API_ROOT}/installation/repositories?per_page={probe.PAGE_SIZE}'
NEXT_PAGE = f'{ORG_LISTING}&page=2'


def repository(name, owner=ORG, archived=False, default_branch='main'):
    """One row of a repository listing, with the fields the probe reads."""
    return {
        'full_name': f'{owner}/{name}',
        'archived': archived,
        'default_branch': default_branch,
        'owner': {'login': owner},
    }


def config_url(repo):
    return (f'{probe.API_ROOT}/repos/{repo["full_name"]}/contents/'
            f'{probe.CONFIG_FILE}?ref={repo["default_branch"]}')


def repo_url(repo):
    return f'{probe.API_ROOT}/repos/{repo["full_name"]}'


def forbidden(url):
    """What api_get raises for a 403, which allow_missing does not cover."""
    return probe.ProbeError(f'GET {url} returned HTTP 403 Forbidden')


def answers(rows, listing=ORG_LISTING, link=''):
    """Fake responses for a listing of (repository, config, push) rows.

    config is CONFIG, MISSING or a ProbeError; push is True, False,
    NO_PERMISSIONS, or None when the row is never expected to be read
    that far -- in which case asking for it is a KeyError naming the
    URL rather than a quiet pass.
    """
    responses = {listing: ([row[0] for row in rows], link)}
    for repo, config, push in rows:
        responses[config_url(repo)] = config if config is not CONFIG else (CONFIG, '')
        if push is NO_PERMISSIONS:
            responses[repo_url(repo)] = ({}, '')
        elif push is not None:
            responses[repo_url(repo)] = ({'permissions': {'push': push}}, '')
    return responses


class FakeApi:
    """Stands in for api_get, keyed by the exact URL the probe builds.

    A URL the probe asks for that a test did not set up is a KeyError
    naming it, rather than a default that would let a test pass against
    a request the probe never makes.
    """

    def __init__(self, responses):
        self.responses = dict(responses)
        self.urls = []

    def __call__(self, url, token, allow_missing=False):
        self.urls.append(url)
        answer = self.responses[url]
        if answer is MISSING:
            if allow_missing:
                return None, ''
            raise probe.ProbeError(f'GET {url} returned HTTP 404 Not Found')
        if isinstance(answer, probe.ProbeError):
            raise answer
        return answer


class RenovatePushProbeTest(unittest.TestCase):
    def run_probe(self, responses, token='a-token', org=ORG):
        """Run main() against fake responses, returning (code, out, err, api).

        Kept apart rather than concatenated, because the managed set is
        listed on stdout: a repository named in the failure report is
        also named in the listing above it, so an assertion against the
        two streams together would hold whether or not the report named
        it at all.
        """
        api = FakeApi(responses)
        environ = {'RENOVATE_TOKEN': token} if token is not None else {}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(probe, 'api_get', api), \
                mock.patch.dict(os.environ, environ, clear=True), \
                redirect_stdout(out), redirect_stderr(err):
            code = probe.main(['--org', org])
        return code, out.getvalue(), err.getvalue(), api

    def test_a_writable_fleet_passes(self):
        code, out, err, _ = self.run_probe(answers([
            (repository('one'), CONFIG, True),
            (repository('two'), CONFIG, True),
        ]))
        self.assertEqual(code, 0, out + err)
        self.assertIn('all 2 repositories', out)

    def test_a_token_that_finds_nothing_to_manage_fails(self):
        # A blind token, or a listing that quietly came back without the
        # repositories it should have, must not report that every
        # repository Renovate manages is writable.
        code, out, err, _ = self.run_probe(answers([
            (repository('one'), MISSING, None),
            (repository('two'), MISSING, None),
        ]))
        self.assertEqual(code, 1, out + err)
        self.assertIn(probe.CONFIG_FILE, err)

    def test_a_repository_without_push_is_named_and_fails(self):
        code, out, err, _ = self.run_probe(answers([
            (repository('writable'), CONFIG, True),
            (repository('readonly'), CONFIG, False),
        ]))
        self.assertEqual(code, 1, out + err)
        self.assertIn(f'{ORG}/readonly', err)
        self.assertNotIn(f'{ORG}/writable', err)

    def test_every_unwritable_repository_is_named(self):
        # Not just the first. The cutover disables 23 workflows on the
        # strength of this output, so a report naming one repository out
        # of three sends somebody back for another run per repository.
        code, out, err, _ = self.run_probe(answers([
            (repository('first'), CONFIG, False),
            (repository('middle'), CONFIG, True),
            (repository('last'), CONFIG, False),
        ]))
        self.assertEqual(code, 1, out + err)
        self.assertIn(f'{ORG}/first', err)
        self.assertIn(f'{ORG}/last', err)
        self.assertIn('2 of 3', err)

    def test_a_repository_with_no_renovate_config_is_not_managed(self):
        unmanaged = repository('unmanaged')
        code, out, err, api = self.run_probe(answers([
            (repository('managed'), CONFIG, True),
            (unmanaged, MISSING, None),
        ]))
        self.assertEqual(code, 0, out + err)
        self.assertIn('1 of them managed', out)
        self.assertNotIn(unmanaged['full_name'], out + err)
        self.assertNotIn(repo_url(unmanaged), api.urls)

    def test_a_forbidden_config_read_is_an_error_not_an_absence(self):
        # 403 is a token that cannot see the repository, which is the
        # finding this probe exists to report. Treating it like the 404
        # above would drop that repository out of the managed set and
        # pass.
        hidden = repository('hidden')
        code, out, err, _ = self.run_probe(answers([
            (repository('managed'), CONFIG, True),
            (hidden, forbidden(config_url(hidden)), None),
        ]))
        self.assertEqual(code, 1, out + err)
        self.assertIn('403', err)

    def test_a_response_with_no_permissions_block_is_unwritable(self):
        code, out, err, _ = self.run_probe(answers([
            (repository('opaque'), CONFIG, NO_PERMISSIONS),
        ]))
        self.assertEqual(code, 1, out + err)
        self.assertIn(f'{ORG}/opaque', err)

    def test_a_paginated_listing_is_followed_to_the_end(self):
        # The organisation is larger than one page, so a probe that read
        # only the first would report a smaller fleet than it manages
        # and pass on it.
        first = repository('first')
        second = repository('second')
        responses = answers([(first, CONFIG, True)],
                            link=f'<{NEXT_PAGE}>; rel="next"')
        responses.update(answers([(second, CONFIG, False)], listing=NEXT_PAGE))
        code, out, err, _ = self.run_probe(responses)
        self.assertEqual(code, 1, out + err)
        self.assertIn('2 repositories', out)
        self.assertIn(first['full_name'], out)
        self.assertIn(second['full_name'], err)

    def test_the_installation_listing_answers_when_the_organisation_one_does_not(self):
        # Which listing answers depends on how the token was minted, and
        # the fallback is the branch a GitHub App installation token
        # takes, so it is the one most likely to run untested.
        mine = repository('mine')
        theirs = repository('theirs', owner=OTHER_ORG)
        responses = answers([(mine, CONFIG, True), (theirs, CONFIG, True)],
                            listing=INSTALLATION_LISTING)
        responses[INSTALLATION_LISTING] = ({'repositories': [mine, theirs]}, '')
        responses[ORG_LISTING] = forbidden(ORG_LISTING)
        code, out, err, api = self.run_probe(responses)
        self.assertEqual(code, 0, out + err)
        self.assertIn(mine['full_name'], out)
        # Filtered back to the organisation asked about: the
        # installation can span owners, and a repository in another
        # organisation is not part of this fleet.
        self.assertNotIn(theirs['full_name'], out + err)
        self.assertNotIn(config_url(theirs), api.urls)

    def test_an_archived_repository_is_not_managed(self):
        archived = repository('archived', archived=True)
        code, out, err, api = self.run_probe(answers([
            (repository('live'), CONFIG, True),
            (archived, CONFIG, None),
        ]))
        self.assertEqual(code, 0, out + err)
        self.assertNotIn(archived['full_name'], out + err)
        self.assertNotIn(config_url(archived), api.urls)

    def test_a_missing_token_fails_before_anything_is_asked(self):
        code, out, err, api = self.run_probe(answers([
            (repository('one'), CONFIG, True),
        ]), token=None)
        self.assertEqual(code, 1, out + err)
        self.assertIn('RENOVATE_TOKEN', err)
        self.assertEqual(api.urls, [])


class FakeResponse(io.BytesIO):
    """Just enough of an HTTP response for json.load and a Link header."""

    def __init__(self, payload, link=''):
        super().__init__(json.dumps(payload).encode())
        self.headers = {'Link': link}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, reason):
    return urllib.error.HTTPError(API_URL, code, reason, {}, None)


API_URL = f'{probe.API_ROOT}/repos/{ORG}/development'


class ApiGetTest(unittest.TestCase):
    """api_get itself, with urlopen patched out.

    The tests above replace api_get, so the one thing they cannot prove
    is the distinction this function draws between a 404 and a 403: an
    absence and an error. That distinction is what decides whether a
    repository the token cannot see is reported, or quietly dropped
    from the managed set and passed over.
    """

    def api_get(self, answer, **kwargs):
        """Call api_get with urlopen answering once, however it answers."""
        def fake_urlopen(request, timeout=None):
            self.request = request
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(probe.urllib.request, 'urlopen', fake_urlopen):
            return probe.api_get(API_URL, 'a-token', **kwargs)

    def test_a_successful_read_returns_the_payload_and_the_link_header(self):
        payload, link = self.api_get(FakeResponse({'permissions': {'push': True}},
                                                  '<https://example/next>; rel="next"'))
        self.assertIs(payload['permissions']['push'], True)
        self.assertIn('rel="next"', link)

    def test_the_token_is_sent_as_a_bearer_credential(self):
        self.api_get(FakeResponse({}))
        self.assertEqual(self.request.get_header('Authorization'), 'Bearer a-token')

    def test_a_404_is_an_absence_when_the_caller_allows_one(self):
        self.assertEqual(self.api_get(http_error(404, 'Not Found'), allow_missing=True), (None, ''))

    def test_a_404_is_an_error_when_the_caller_does_not_allow_one(self):
        with self.assertRaises(probe.ProbeError) as raised:
            self.api_get(http_error(404, 'Not Found'))
        self.assertIn('404', str(raised.exception))

    def test_a_403_is_an_error_even_where_a_404_would_be_allowed(self):
        # A token that cannot see a repository is the finding the probe
        # exists to report. Treated as an absence, it would take that
        # repository out of the managed set and pass.
        with self.assertRaises(probe.ProbeError) as raised:
            self.api_get(http_error(403, 'Forbidden'), allow_missing=True)
        self.assertIn('403', str(raised.exception))

    def test_a_connection_that_fails_is_an_error(self):
        with self.assertRaises(probe.ProbeError):
            self.api_get(urllib.error.URLError('no route to host'), allow_missing=True)


if __name__ == '__main__':
    unittest.main()
