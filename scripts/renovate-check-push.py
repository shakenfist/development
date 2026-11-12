#!/usr/bin/env python3
"""Check that Renovate's token can push to every repository it manages.

One central Renovate run holds one token for the whole organisation, so
a repository that token cannot write is a repository Renovate reads,
plans updates for, and then fails to act on -- once an hour, with
nothing but a line in a log to say so. This runs before Renovate on
every run and fails the job naming every repository that is not
writable, rather than stopping at the first.

The repository set is derived rather than listed, by the same rule
Renovate applies with `requireConfig: "required"`: every unarchived
repository in the organisation whose default branch carries a Renovate
configuration. A list of names here would be a second copy of the
fleet, and would be wrong the first time a repository was added or
retired.

Usage:
    RENOVATE_TOKEN=... python3 scripts/renovate-check-push.py --org shakenfist

Standard library only, like the audit scripts: the runner image has
python3 and git but no gh, and this runs hourly, so it installs
nothing.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


API_ROOT = 'https://api.github.com'

# The API version header pins the response shape. Without it GitHub
# serves whatever the current default is, so `permissions` could change
# underneath a probe whose whole job is to read one field of it.
API_VERSION = '2022-11-28'

# Long enough that a slow response is not a failed run, short enough
# that a hung connection cannot hold a VM for the job's whole timeout.
HTTP_TIMEOUT = 30

# `requireConfig: "required"` manages a repository that carries any of
# renovate.json, .json5 or .jsonc, the same three under .github/ or
# .gitlab/, .renovaterc, .renovaterc with those three suffixes, or a
# renovate key in package.json. Only renovate.json is checked here.
# Every one of the managed repositories carries it, because that is the
# file the `renovate` consistency audit requires of them, and reading
# part of the longer list would buy confidence the probe has not
# earned: a repository configured one of the other ways would be
# managed without being probed either way, and the audit criterion is
# what keeps that from happening unnoticed.
CONFIG_FILE = 'renovate.json'

# GitHub pages repository listings at 30 by default, and the
# organisation is larger than that: a probe that read only the first
# page would report a smaller fleet than it manages and pass.
PAGE_SIZE = 100

NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class ProbeError(Exception):
    """The probe could not get an answer, so it cannot report one."""


def api_get(url, token, allow_missing=False):
    """GET one API URL, returning (payload, link header).

    With allow_missing, a 404 returns (None, '') instead of raising:
    that is how a repository without a Renovate configuration is told
    apart from one the token cannot see, which is a 403 and an error.
    """
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'User-Agent': 'renovate-push-probe',
        'X-GitHub-Api-Version': API_VERSION,
    })
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.load(response), response.headers.get('Link', '')
    except urllib.error.HTTPError as e:
        if allow_missing and e.code == 404:
            return None, ''
        raise ProbeError(f'GET {url} returned HTTP {e.code} {e.reason}') from e
    except urllib.error.URLError as e:
        raise ProbeError(f'GET {url} failed: {e.reason}') from e


def api_get_all(url, token):
    """Every item of a paginated listing, following the Link header.

    The installation listing wraps its items in an object while the
    organisation listing is a bare array, so both shapes are unwrapped
    here rather than in each caller.
    """
    items = []
    while url:
        payload, link = api_get(url, token)
        items.extend(payload['repositories'] if isinstance(payload, dict) else payload)
        match = NEXT_LINK_RE.search(link)
        url = match.group(1) if match else None
    return items


def organisation_repositories(org, token):
    """Every repository in the organisation the token can see.

    Two endpoints, because which one answers is a property of whoever
    minted the token rather than of anything here: a personal access
    token lists an organisation's repositories, while a GitHub App
    installation token is refused there and lists its installation
    instead. The installation can span owners, so its answer is
    filtered back to this organisation.
    """
    try:
        return api_get_all(f'{API_ROOT}/orgs/{org}/repos?per_page={PAGE_SIZE}', token)
    except ProbeError as e:
        print(f'  the organisation listing did not answer ({e})', file=sys.stderr)
        print('  trying the installation listing instead', file=sys.stderr)
        return [repo for repo in api_get_all(f'{API_ROOT}/installation/repositories?per_page={PAGE_SIZE}', token)
                if (repo.get('owner') or {}).get('login') == org]


def has_renovate_config(full_name, default_branch, token):
    """True when the repository's default branch carries a Renovate config.

    Read at the default branch by name rather than at the API's own
    default, so that a configuration added on a branch -- which
    Renovate does not read either -- does not make a repository look
    managed.
    """
    ref = urllib.parse.quote(default_branch, safe='')
    payload, _ = api_get(f'{API_ROOT}/repos/{full_name}/contents/{CONFIG_FILE}?ref={ref}', token,
                         allow_missing=True)
    return payload is not None


def managed_repositories(org, token):
    """The repositories Renovate manages, in name order."""
    managed = []
    repositories = organisation_repositories(org, token)
    for repo in sorted(repositories, key=lambda repo: repo['full_name']):
        if repo.get('archived'):
            continue
        if has_renovate_config(repo['full_name'], repo['default_branch'], token):
            managed.append(repo['full_name'])
    print(f'{len(repositories)} repositories in {org}, {len(managed)} of them managed by Renovate')
    for full_name in managed:
        print(f'  {full_name}')
    return managed


def can_push(full_name, token):
    """True when the token has push access to the repository.

    Asked per repository rather than read from the listing above,
    because a single repository read is what a dispatch of this probe
    against one repository has to be able to do, and because a listing
    that omitted a repository would otherwise be reported as that
    repository being writable.
    """
    payload, _ = api_get(f'{API_ROOT}/repos/{full_name}', token)
    return (payload.get('permissions') or {}).get('push') is True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check Renovate can push to every repository it manages')
    parser.add_argument('--org', required=True, help='GitHub organisation to probe')
    args = parser.parse_args(argv)

    token = os.environ.get('RENOVATE_TOKEN')
    if not token:
        print('RENOVATE_TOKEN is not set, so there is nothing to probe', file=sys.stderr)
        return 1

    try:
        managed = managed_repositories(args.org, token)
        # An empty set is a failure rather than a pass. A token that can
        # see nothing, or a listing that quietly came back empty, would
        # otherwise report that every repository Renovate manages is
        # writable, which is true and useless.
        if not managed:
            print(f'no repository in {args.org} carries {CONFIG_FILE}, which cannot be right', file=sys.stderr)
            return 1
        unwritable = [full_name for full_name in managed if not can_push(full_name, token)]
    except ProbeError as e:
        print(f'{e}', file=sys.stderr)
        return 1

    if unwritable:
        print(f'the token cannot push to {len(unwritable)} of {len(managed)} repositories:', file=sys.stderr)
        for full_name in unwritable:
            print(f'  {full_name}', file=sys.stderr)
        return 1

    print(f'the token can push to all {len(managed)} repositories')
    return 0


if __name__ == '__main__':
    sys.exit(main())
