#!/usr/bin/env python3

"""Tests for audit/checks/distros.py.

Most of these are about what the criterion must *not* report. The
fleet writes the string "debian-12" into guest image filenames, cached
disk paths, upstream job names and explanatory comments, and a check
that reported those would be turned off within a week -- so each of
those forms has a test naming the real file it came from.

Run with: python3 scripts/tests/test_distros.py
"""

import contextlib
import datetime
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit import registry  # noqa: E402
from audit.checks import distros  # noqa: E402
from tests.base import (  # noqa: E402
    CheckTestCase, REPO_ROOT, repo_text,
)


class ImageReferenceTest(unittest.TestCase):
    """Reading a container image reference the way a runtime would."""

    def release(self, reference):
        found = distros.image_release(reference)
        return None if found is None else found.name

    def test_the_distribution_by_version(self):
        self.assertEqual('Debian 12 (bookworm)', self.release('debian:12'))
        self.assertEqual('Debian 11 (bullseye)', self.release('debian:11'))
        self.assertEqual('Ubuntu 20.04 LTS (focal)',
                         self.release('ubuntu:20.04'))

    def test_the_distribution_by_codename(self):
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('debian:bookworm'))
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('debian:bookworm-slim'))

    def test_a_point_release_is_the_same_release(self):
        self.assertEqual('Debian 12 (bookworm)', self.release('debian:12.11'))

    def test_a_derived_image_inherits_its_base(self):
        # Both spellings are in kerbside: loadtests/latency/Dockerfile
        # builds on rust:1.97-bookworm and rust/kerbside-proxy/Dockerfile
        # on rust:slim-bookworm. The proxy container the project ships
        # is a Debian 12 userland, which is exactly the dependency this
        # criterion exists to surface.
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('rust:1.97-bookworm'))
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('rust:slim-bookworm'))
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('python:3.13-bookworm'))

    def test_a_codename_is_read_in_any_image_not_only_the_distros(self):
        """Deliberate, and the reason rust:1.97-bookworm is caught.

        The criterion cannot tell a distribution's own image from
        something built on one, so it does not try; a project
        publishing derived artifacts marks them audit-ok instead.
        """
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('myapp:bookworm'))
        self.assertEqual('Debian 11 (bullseye)',
                         self.release('internal/tool:bullseye-builder'))

    def test_a_version_number_is_only_read_for_the_distribution_itself(self):
        """'12' means bookworm in debian:12 and nothing in rust:1.12."""
        self.assertIsNone(self.release('rust:1.12'))
        self.assertIsNone(self.release('rust:1.97'))
        self.assertIsNone(self.release('python:3.12'))

    def test_a_supported_release_is_not_a_finding(self):
        self.assertIsNone(self.release('debian:13'))
        self.assertIsNone(self.release('debian:trixie'))
        self.assertIsNone(self.release('debian:trixie-slim'))
        self.assertIsNone(self.release('ubuntu:24.04'))

    def test_an_untagged_reference_is_not_judged(self):
        """An implicit latest says nothing about what it resolves to."""
        self.assertIsNone(self.release('debian'))
        self.assertIsNone(self.release('rockylinux/rockylinux'))

    def test_a_registry_prefix_does_not_change_the_answer(self):
        self.assertEqual('Debian 12 (bookworm)',
                         self.release('docker.io/library/debian:12'))
        # instar's src/.devcontainer/Dockerfile: the tag is 'debian',
        # which names no release.
        self.assertIsNone(
            self.release('mcr.microsoft.com/devcontainers/base:debian'))

    def test_a_digest_is_stripped_before_the_tag_is_read(self):
        self.assertEqual(
            'Debian 12 (bookworm)',
            self.release('debian:12@sha256:' + 'a' * 64))
        self.assertIsNone(
            self.release('mcr.microsoft.com/devcontainers/base:debian@'
                         'sha256:' + 'b' * 64))

    def test_a_registry_port_is_not_a_tag(self):
        self.assertIsNone(self.release('localhost:5000/debian'))

    def test_an_expression_is_not_judged(self):
        self.assertIsNone(self.release('${{ matrix.image }}'))
        self.assertIsNone(self.release('$IMAGE'))


class RetiredReleaseTest(unittest.TestCase):
    """A release listed ahead of its date must not fail the fleet.

    The table is meant to be cheap to add to, which invites writing an
    entry while a migration is still being planned. Until the date
    arrives the entry is inert.
    """

    def test_todays_list_is_every_release_whose_date_has_passed(self):
        for release in distros.retired_releases(datetime.date(2099, 1, 1)):
            self.assertIn(release, distros.EOL_RELEASES)
        self.assertEqual(list(distros.EOL_RELEASES),
                         distros.retired_releases(datetime.date(2099, 1, 1)))

    def test_a_release_is_not_retired_before_its_date(self):
        # Debian 11 went end of life on 2026-08-31.
        names = [r.name for r in
                 distros.retired_releases(datetime.date(2026, 7, 1))]
        self.assertNotIn('Debian 11 (bullseye)', names)
        self.assertIn('Debian 12 (bookworm)', names)

    def test_the_date_itself_counts_as_retired(self):
        names = [r.name for r in
                 distros.retired_releases(datetime.date(2026, 8, 31))]
        self.assertIn('Debian 11 (bullseye)', names)

    def test_every_listed_date_parses(self):
        for release in distros.EOL_RELEASES:
            with self.subTest(release=release.name):
                datetime.date.fromisoformat(release.eol)


class RunnerLabelMatchTest(unittest.TestCase):
    """A retired label is a whole token, never a substring."""

    def labels(self, line):
        return [m.group(1) for m in distros.RUNNER_LABEL_RE.finditer(line)]

    def test_a_bare_label_matches(self):
        self.assertEqual(['debian-12'], self.labels('[self-hosted, vm, '
                                                    'debian-12, s]'))

    def test_a_variant_matches_as_itself(self):
        """Longest first, or debian-12-docker reports as debian-12."""
        self.assertEqual(
            ['debian-12-docker'],
            self.labels('[self-hosted, vm, debian-12-docker, s]'))

    def test_a_guest_image_filename_is_not_a_label(self):
        # instar's functional-tests.yml runs a chain test over
        # /images/debian-12-genericcloud-amd64.qcow2.
        self.assertEqual(
            [], self.labels('/images/debian-12-genericcloud-amd64.qcow2'))

    def test_an_upstream_job_name_is_not_a_label(self):
        # kerbside dispatches 'kolla-ansible-master-debian-12'. We do
        # not choose that gate's platform.
        self.assertEqual(
            [], self.labels("- '[\"kolla-ansible-master-debian-12\"]'"))

    def test_a_cached_disk_path_is_not_a_label(self):
        # kerbside's functional-tests.yml copies
        # /srv/ci/cached/debian-12-gnome-agents to the runner.
        self.assertEqual(
            [], self.labels('/srv/ci/cached/debian-12-gnome-agents \\'))

    def test_a_supported_release_does_not_match(self):
        self.assertEqual(
            [], self.labels('[self-hosted, vm, debian-13, s]'))
        self.assertEqual(
            [], self.labels('[self-hosted, vm, debian-13-docker, s]'))


class EolDistroTest(CheckTestCase):
    """The criterion end to end, against a fixture checkout."""

    check_class = distros.EolDistro

    def test_a_repository_with_nothing_to_read_does_not_apply(self):
        self.assert_skip(self.check(), containing='No workflows')

    def test_a_supported_runner_passes(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-13, s]\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_retired_runner_label_fails(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-12, s]\n')
        result = self.assert_fail(self.check(has_workflows_dir=True),
                                  containing='Debian 12 (bookworm)')
        self.assertEqual(['.github/workflows/ci.yml:3 (debian-12)'],
                         result['findings'])

    def test_the_finding_says_what_to_move_to(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-12, s]\n')
        result = self.check(has_workflows_dir=True)
        self.assertIn('2026-06-10', result['details'])
        self.assertIn('debian-13', result['details'])

    def test_the_finding_says_the_label_move_is_two_files(self):
        """Every recipient otherwise rediscovers the actionlint half.

        A repository's .github/actionlint.yaml lists the self-hosted
        labels its workflows may name, so a label swap that does not
        touch it trades this finding for a lint failure.
        """
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-12, s]\n')
        result = self.check(has_workflows_dir=True)
        self.assertIn('actionlint.yaml', result['details'])

    def test_a_matrix_value_feeding_runs_on_is_caught(self):
        """The label need not sit on the runs-on line to choose the OS."""
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    strategy:\n'
            '      matrix:\n'
            '        os:\n'
            '          - debian-12\n'
            '    runs-on: [self-hosted, vm, "${{ matrix.os }}", s]\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='1 reference(s)')

    def test_a_label_inside_a_comment_is_not_a_finding(self):
        # kerbside's release.yml explains a past mistake by quoting the
        # runs-on that caused it.
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    # `[self-hosted, debian-12, static]` matched no runner\n'
            '    runs-on: [self-hosted, vm, debian-13, s]\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_label_inside_a_shell_command_is_not_a_finding(self):
        # shakenfist/actions passes an image name as an argument.
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - run: |\n'
            '          "${setup} ubuntu-2004 /srv/ci/ubuntu:20.04 --shared"\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_yaml_extension_workflow_is_scanned(self):
        """Repo.workflows() returns both spellings; only one was tested."""
        self.fixture.workflow(
            'ci.yaml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-12, s]\n')
        result = self.assert_fail(self.check(has_workflows_dir=True))
        self.assertEqual(['.github/workflows/ci.yaml:3 (debian-12)'],
                         result['findings'])

    def test_an_image_key_written_by_a_heredoc_is_still_read(self):
        """A stated limit, not an accident -- see the spec.

        The key anchoring cannot tell a workflow's own container from
        one it writes into a compose file, and the dependency on the
        retired release is real either way.
        """
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - run: |\n'
            '          cat > compose.yml <<EOF\n'
            '          image: debian:12\n'
            '          EOF\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='Debian 12 (bookworm)')

    def test_an_image_run_from_a_shell_command_is_not_read(self):
        """The other half of the same limit: a gap, and a stated one."""
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - run: docker run --rm debian:12 true\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_container_image_key_fails(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    runs-on: [self-hosted, static]\n'
            '    container:\n'
            '      image: debian:bookworm\n')
        result = self.assert_fail(self.check(has_workflows_dir=True),
                                  containing='Debian 12 (bookworm)')
        self.assertEqual(['.github/workflows/ci.yml:5 (debian:bookworm)'],
                         result['findings'])

    def test_a_dockerfile_from_line_fails(self):
        self.fixture.write('rust/proxy/Dockerfile',
                           'FROM rust:slim-bookworm AS builder\n')
        self.assert_fail(self.check(), containing='Debian 12 (bookworm)')

    def test_an_image_key_with_a_trailing_comment_is_still_read(self):
        """A pinned line explaining its own pin is the stale one."""
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    container:\n'
            '      image: debian:12  # renovate pin\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='Debian 12 (bookworm)')

    def test_a_from_line_with_a_platform_flag_is_read(self):
        self.fixture.write(
            'Dockerfile',
            'FROM --platform=$BUILDPLATFORM debian:12 AS builder\n')
        self.assert_fail(self.check(), containing='Debian 12 (bookworm)')

    def test_several_from_flags_are_skipped(self):
        self.fixture.write(
            'Dockerfile',
            'FROM --platform=linux/amd64 --a=b debian:bookworm\n')
        self.assert_fail(self.check(), containing='Debian 12 (bookworm)')

    def test_a_workflow_template_is_scanned(self):
        """A template is copied into ten repositories verbatim."""
        self.fixture.write(
            'templates/mermaid-lint/mermaid-lint.yml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-12, s]\n')
        result = self.assert_fail(self.check(),
                                  containing='Debian 12 (bookworm)')
        self.assertEqual(
            ['templates/mermaid-lint/mermaid-lint.yml:3 (debian-12)'],
            result['findings'])

    def test_a_template_readme_is_prose_not_a_workflow(self):
        self.fixture.write(
            'templates/mermaid-lint/README.md',
            'Use `[self-hosted, vm, debian-12, s]`, not static.\n')
        self.assert_skip(self.check())

    def test_a_dockerfile_in_a_vendored_tree_is_not_ours(self):
        self.fixture.write('vendor/thing/Dockerfile', 'FROM debian:12\n')
        self.assert_skip(self.check())

    def test_a_marked_exception_on_the_line_passes(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    container:\n'
            "      image: 'debian:12'  # audit-ok: eol-distro -- test input\n")
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_marked_exception_on_the_line_above_passes(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    # audit-ok: eol-distro -- we measure old images\n'
            '    runs-on: [self-hosted, vm, debian-12, s]\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_marked_exception_in_a_dockerfile_passes(self):
        self.fixture.write(
            'Dockerfile',
            '# audit-ok: eol-distro -- the thing under test\n'
            'FROM debian:bookworm\n')
        self.assert_pass(self.check())

    def test_every_finding_is_reported_not_just_the_first(self):
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n'
            '  a:\n'
            '    runs-on: [self-hosted, vm, debian-12, s]\n'
            '  b:\n'
            '    runs-on: [self-hosted, vm, debian-11, s]\n')
        self.fixture.write('Dockerfile', 'FROM ubuntu:focal\n')
        result = self.assert_fail(self.check(has_workflows_dir=True),
                                  containing='3 reference(s)')
        self.assertEqual(3, len(result['findings']))
        for release in distros.EOL_RELEASES:
            self.assertIn(release.name, result['details'])


class DateGateTest(CheckTestCase):
    """An entry may be written before its date; scan() withholds it.

    retired_releases() is tested directly, but every entry in the table
    is already past today, so without these the filter in scan() is
    dead code as far as the suite is concerned and a refactor could
    drop it silently.
    """

    check_class = distros.EolDistro

    #: Debian 12's end of standard support, from EOL_RELEASES.
    BOOKWORM_EOL = datetime.date(2026, 6, 10)

    def setUp(self):
        super().setUp()
        self.fixture.workflow(
            'ci.yml',
            'jobs:\n  a:\n    runs-on: [self-hosted, vm, debian-12, s]\n')

    def scan(self, today):
        return distros.scan(self.repo(has_workflows_dir=True), today=today)

    def test_the_day_before_the_date_reports_nothing(self):
        self.assertEqual(
            [], self.scan(self.BOOKWORM_EOL - datetime.timedelta(days=1)))

    def test_the_date_itself_reports_the_same_tree(self):
        found = self.scan(self.BOOKWORM_EOL)
        self.assertEqual(1, len(found))
        self.assertEqual('debian-12', found[0][1])

    def test_a_year_earlier_reports_nothing_at_all(self):
        """Nothing in the table had retired a year before bookworm did."""
        self.assertEqual(
            [], self.scan(datetime.date(2025, 1, 1)))


class SpecificationTest(unittest.TestCase):
    """The table in the specification and the table in the code.

    Nothing can reach across from here to the CI conductor's image
    list, so the specification page is where a reader learns which
    releases are retired -- and a release added to EOL_RELEASES without
    a row there is a finding nobody can look up.
    """

    def setUp(self):
        with open(os.path.join(REPO_ROOT, 'docs', 'audits',
                               'eol-distro.md')) as f:
            self.spec = f.read()

    def test_every_retired_release_is_documented(self):
        for release in distros.EOL_RELEASES:
            with self.subTest(release=release.name):
                self.assertIn(release.name, self.spec)
                self.assertIn(release.eol, self.spec)

    def test_every_retired_runner_label_is_documented(self):
        for release in distros.EOL_RELEASES:
            for label in release.runner_labels:
                with self.subTest(label=label):
                    self.assertIn(f'`{label}`', self.spec)

    def test_the_specification_names_the_exception_marker(self):
        self.assertIn('audit-ok: eol-distro', self.spec)

    def test_no_generated_marker_block(self):
        """A spec page must stay reviewable; see docs/audits/README.md."""
        self.assertNotIn('consistency-audit:begin', self.spec)

    def test_the_criterion_is_listed_in_the_index(self):
        with open(os.path.join(REPO_ROOT, 'docs', 'audits',
                               'README.md')) as f:
            index = f.read()
        self.assertIn('[eol-distro.md](eol-distro.md)', index)


class ExceptionMarkerTest(unittest.TestCase):
    """The marker is the same shape as the runner criteria's markers."""

    def test_the_marker_tolerates_the_spellings_the_fleet_writes(self):
        for line in ('# audit-ok: eol-distro',
                     '#audit-ok:eol-distro',
                     'runs-on: [a]  # audit-ok: eol-distro -- reason'):
            with self.subTest(line=line):
                self.assertTrue(distros.EXCEPTION_RE.search(line))

    def test_the_marker_is_not_matched_by_a_neighbouring_criterion(self):
        self.assertIsNone(
            distros.EXCEPTION_RE.search('# audit-ok: vm-runner-size'))


class DockerfileNamingTest(unittest.TestCase):
    def test_the_spellings_that_build_an_image(self):
        for name in ('Dockerfile', 'Dockerfile.ci', 'ci.dockerfile',
                     'Containerfile', 'Containerfile.dev'):
            with self.subTest(name=name):
                self.assertTrue(distros.is_dockerfile(name))

    def test_the_spellings_that_do_not(self):
        for name in ('Dockerfilename', 'dockerfile.md',
                     'docker-compose.yml', 'README.md'):
            with self.subTest(name=name):
                self.assertFalse(distros.is_dockerfile(name))


class RegressionGuardTest(unittest.TestCase):
    """The label regex must keep its token lookarounds.

    Rewriting them as \\b passes every positive test above and silently
    reports three classes of false positive, because a hyphen is not a
    word character. This asserts the shape rather than the behaviour so
    that the reason survives a refactor.
    """

    def test_the_pattern_bounds_labels_on_hyphens_and_dots(self):
        pattern = distros.RUNNER_LABEL_RE.pattern
        self.assertIn(r'(?<![\w.-])', pattern)
        self.assertIn(r'(?![\w.-])', pattern)
        self.assertNotIn(r'\b', pattern)

    def test_variants_are_ordered_longest_first(self):
        """Longest first, or debian-12-docker reports as debian-12.

        The capture excludes nested parentheses deliberately. Matching
        from the first '(' in the pattern picks up the '(?<!' lookbehind
        instead, and the first split element becomes the lookbehind glued
        to the first label -- which still satisfies a longest-first
        assertion by accident, because that mangled string is the longest
        of them. The first assertion below is what makes a mis-capture
        fail loudly rather than pass quietly.
        """
        alternatives = re.search(
            r'\(([^()]*)\)\(\?!', distros.RUNNER_LABEL_RE.pattern)
        self.assertIsNotNone(alternatives)
        labels = alternatives.group(1).split('|')
        self.assertEqual(re.escape('debian-11-docker'), labels[0])
        self.assertEqual(sorted(labels, key=lambda x: (-len(x), x)), labels)


#: A producer definition in the shape private-ci writes one, with a
#: comment naming a label that was removed. Both producer modules
#: carry exactly this: `imagebuilder.py` explains above its entries
#: that "ubuntu-2004 was dropped 2026-07-11", so a grep of the live
#: table reports a label nothing has offered for months.
PRODUCER = """\
# This matrix decides what the fleet can boot.
# ubuntu-2004 was dropped 2026-07-11: nothing boots it any more.
IMAGE_BUILDS = [
    {
        'name': 'debian-13',
        'base_image': 'debian:13',
        'label': 'debian-13',
    },
    {
        'name': 'rocky-9',
        'base_image': 'rocky:9',
        'label': 'rocky-9',
    },
]
"""


class ProducerDefinitionTest(unittest.TestCase):
    """Reading a producer definition out of Python with ast."""

    def labels(self, source, name='IMAGE_BUILDS'):
        return distros.definition_labels(source, name)

    def test_the_labels_an_entry_offers_come_back_with_their_lines(self):
        self.assertEqual([('debian-13', 7), ('rocky-9', 12)],
                         self.labels(PRODUCER))

    def test_a_label_named_only_in_a_comment_is_not_offered(self):
        """The false positive a grep-shaped criterion inherits.

        'ubuntu-2004' is in PRODUCER, on the comment line that records
        its removal, and a retired label's obituary must not read as
        an offer. The abstract syntax tree has no comments in it, so
        this holds by construction rather than by a pattern.
        """
        self.assertNotIn('ubuntu-2004',
                         [label for label, _ in self.labels(PRODUCER)])

    def test_a_missing_assignment_is_not_an_empty_producer(self):
        """"Not read" and "offers nothing" must not render the same."""
        with self.assertRaises(distros.ProducerParseError):
            self.labels(PRODUCER, name='CI_IMAGES')

    def test_a_definition_that_is_not_a_list_is_a_parse_error(self):
        with self.assertRaises(distros.ProducerParseError):
            self.labels("IMAGE_BUILDS = build_the_table()\n")

    def test_entries_with_no_label_key_are_a_parse_error(self):
        """The reshape this check would otherwise pass vacuously on."""
        with self.assertRaises(distros.ProducerParseError):
            self.labels(
                "IMAGE_BUILDS = [{'runner_label': 'debian-12'}]\n")

    def test_unparseable_python_is_a_parse_error(self):
        with self.assertRaises(distros.ProducerParseError):
            self.labels("IMAGE_BUILDS = [{'label': \n")

    def test_an_empty_table_offers_nothing_without_raising(self):
        """A producer can legitimately be emptied out."""
        self.assertEqual([], self.labels("IMAGE_BUILDS = []\n"))

    def test_a_computed_field_beside_the_label_does_not_hide_it(self):
        """Only the label has to be a literal, not the whole entry."""
        self.assertEqual(
            [('debian-12', 1)],
            self.labels(
                "IMAGE_BUILDS = [{'label': 'debian-12', "
                "'size': 4 * GB}]\n"))

    def test_an_annotated_assignment_is_read(self):
        self.assertEqual(
            [('debian-12', 1)],
            self.labels(
                "IMAGE_BUILDS: list = [{'label': 'debian-12'}]\n"))

    def test_one_unreadable_entry_is_a_parse_error(self):
        """All or nothing: the literal entries beside it do not pass it.

        Skipping the entry this cannot read would pass the table on
        the ones it can, which is an unread producer reported clean
        one entry at a time.
        """
        unreadable = {
            'dict call': "dict(label='debian-12')",
            'helper call': "make_entry('debian-12')",
            'starred': "*OTHER_BUILDS",
            'spread': "{**BASE, 'label': 'debian-13'}",
            'computed key': "{LABEL_KEY: 'debian-12'}",
            'constant label': "{'label': DEBIAN_12}",
            'f-string label': "{'label': f'debian-{VERSION}'}",
            'two labels': "{'label': 'debian-13', 'label': 'debian-12'}",
        }
        for shape, entry in unreadable.items():
            with self.subTest(shape=shape):
                with self.assertRaises(distros.ProducerParseError):
                    self.labels(
                        "IMAGE_BUILDS = [\n"
                        "    {'label': 'debian-13'},\n"
                        f"    {entry},\n"
                        "]\n")

    def test_a_change_after_the_assignment_is_a_parse_error(self):
        """The literal read is not the whole table once it is changed."""
        changes = {
            'augmented': "IMAGE_BUILDS += [{'label': 'debian-12'}]",
            'append': "IMAGE_BUILDS.append({'label': 'debian-12'})",
            'extend': "IMAGE_BUILDS.extend(MORE)",
            'rebound': "IMAGE_BUILDS = [{'label': 'debian-12'}]",
            'entry store': "IMAGE_BUILDS[0]['label'] = 'debian-12'",
            'entry update': "IMAGE_BUILDS[0].update(label='debian-12')",
            'deleted': "del IMAGE_BUILDS[0]",
            'conditional': "if OLD:\n    IMAGE_BUILDS = OLD_BUILDS",
            'class body': "class Old:\n    IMAGE_BUILDS.append(OLD)",
        }
        for shape, change in changes.items():
            with self.subTest(shape=shape):
                with self.assertRaises(distros.ProducerParseError):
                    self.labels(
                        "IMAGE_BUILDS = [{'label': 'debian-13'}]\n"
                        f"{change}\n")

    def test_reading_the_table_after_the_assignment_is_not_a_change(self):
        """The provisioner builds ALL_CI_IMAGE_LABELS this way."""
        self.assertEqual(
            [('debian-13', 1)],
            self.labels(
                "IMAGE_BUILDS = [{'label': 'debian-13'}]\n"
                "LABELS = [i['label'] for i in IMAGE_BUILDS]\n"
                "FIRST = IMAGE_BUILDS[0]\n"))

    def test_a_change_inside_a_function_is_out_of_reach(self):
        """It runs when called, not at import, so it is not read."""
        self.assertEqual(
            [('debian-13', 1)],
            self.labels(
                "IMAGE_BUILDS = [{'label': 'debian-13'}]\n"
                "def later():\n"
                "    IMAGE_BUILDS.append({'label': 'debian-12'})\n"
                "handler = lambda: IMAGE_BUILDS.clear()\n"))


class RetiredLabelTest(unittest.TestCase):
    """The banned set is the table's, filtered by today."""

    def test_every_retired_releases_labels_are_banned(self):
        banned = distros.retired_labels()
        for release in distros.retired_releases():
            for label in release.runner_labels:
                with self.subTest(label=label):
                    self.assertEqual(release, banned[label])

    def test_a_release_is_not_banned_before_its_date(self):
        """Nothing in the table had retired by the start of 2025."""
        self.assertEqual({}, distros.retired_labels(
            datetime.date(2025, 1, 1)))


class EolProducersTest(CheckTestCase):
    """The producer criterion end to end, against a fixture checkout."""

    check_class = distros.EolProducers

    BUILDER = 'conductor/imagebuilder.py'
    PROVISIONER = 'conductor/provisioner.py'

    def builder(self, *labels):
        entries = ''.join(
            "    {'label': '%s'},\n" % label for label in labels)
        return 'IMAGE_BUILDS = [\n%s]\n' % entries

    def provisioner(self, *labels):
        entries = ''.join(
            "    {'label': '%s'},\n" % label for label in labels)
        return 'CI_IMAGES = [\n%s]\n' % entries

    def test_a_repository_with_no_producer_does_not_apply(self):
        """not_applicable, not pass: nothing here was measured.

        Every repository in the fleet but one has no producer, so a
        pass here would report the whole organisation green on a
        question none of them was asked.
        """
        self.assert_skip(self.check(),
                         containing='No runner-label producer')

    def test_a_producer_offering_supported_labels_passes(self):
        self.fixture.write(self.BUILDER,
                           self.builder('debian-13', 'rocky-10'))
        self.assert_pass(self.check())

    def test_a_producer_offering_a_retired_label_fails(self):
        self.fixture.write(
            self.BUILDER, self.builder('debian-12', 'debian-13'))
        result = self.assert_fail(self.check(),
                                  containing='Debian 12 (bookworm)')
        self.assertEqual(['conductor/imagebuilder.py:2 (debian-12)'],
                         result['findings'])

    def test_the_findings_read_down_the_file(self):
        """Definition order, not sorted: ':123' sorts above ':62'."""
        self.fixture.write(
            self.BUILDER,
            self.builder(*(['debian-13'] * 8 + ['debian-11',
                                                'debian-12'])))
        self.assertEqual(
            ['conductor/imagebuilder.py:10 (debian-11)',
             'conductor/imagebuilder.py:11 (debian-12)'],
            self.check()['findings'])

    def test_both_definitions_are_read(self):
        self.fixture.write(self.BUILDER, self.builder('debian-11'))
        self.fixture.write(self.PROVISIONER,
                           self.provisioner('debian-12-docker'))
        result = self.assert_fail(self.check())
        self.assertEqual(
            ['conductor/imagebuilder.py:2 (debian-11)',
             'conductor/provisioner.py:2 (debian-12-docker)'],
            result['findings'])

    def test_one_definition_present_is_enough_to_measure(self):
        """The provisioner alone, with no image builder beside it."""
        self.fixture.write(self.PROVISIONER,
                           self.provisioner('debian-13'))
        result = self.assert_pass(self.check())
        self.assertIn('CI_IMAGES', result['details'])
        self.assertNotIn('IMAGE_BUILDS', result['details'])

    def test_a_retired_label_in_a_comment_is_not_a_finding(self):
        """The whole reason this parses Python instead of grepping."""
        self.fixture.write(self.BUILDER, PRODUCER)
        self.assert_pass(self.check())

    def test_the_finding_says_which_release_and_what_to_offer(self):
        self.fixture.write(self.BUILDER, self.builder('debian-11'))
        result = self.check()
        self.assertIn('2026-08-31', result['details'])
        self.assertIn('debian-13', result['details'])

    def test_the_finding_says_the_consumers_move_first(self):
        """A label deleted under a live consumer queues forever."""
        self.fixture.write(self.BUILDER, self.builder('debian-12'))
        self.assertIn('eol-distro', self.check()['details'])

    def test_a_producer_that_cannot_be_read_is_not_a_pass(self):
        """An error, which files nothing and fails the audit leg."""
        self.fixture.write(self.BUILDER, 'IMAGE_BUILDS = compute()\n')
        with self.assertRaises(distros.ProducerParseError):
            distros.scan_producers(self.repo())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = registry.run_check(self.check_class(), self.repo())
        self.assertEqual('error', result['status'])
        self.assertIn('ProducerParseError', stderr.getvalue())

    def test_a_label_retired_in_the_future_is_not_yet_offered_wrongly(self):
        """scan_producers() asks the table what is retired today."""
        self.fixture.write(self.BUILDER, self.builder('debian-12'))
        self.assertEqual([], distros.scan_producers(
            self.repo(), today=datetime.date(2025, 1, 1)))


class ProducerSpecificationTest(unittest.TestCase):
    """The producer definitions named in the code and in the page.

    Nothing here can reach into private-ci to check that the files
    still exist, so the specification page is where a reader learns
    which two definitions are read -- and a producer added to
    PRODUCER_DEFINITIONS without a row there is a finding nobody can
    look up.
    """

    def setUp(self):
        self.spec = repo_text('docs', 'audits', 'eol-producers.md')

    def test_every_producer_definition_is_documented(self):
        for path, name in distros.PRODUCER_DEFINITIONS:
            with self.subTest(definition=name):
                self.assertIn(f'`{path}`', self.spec)
                self.assertIn(f'`{name}`', self.spec)

    def test_the_label_key_is_documented(self):
        self.assertIn(f'`{distros.PRODUCER_LABEL_KEY}`', self.spec)

    def test_the_page_says_the_definitions_are_parsed_not_grepped(self):
        """The one decision a later reader must not quietly undo."""
        self.assertIn('`ast`', self.spec)
        self.assertIn('grep', self.spec)

    def test_the_page_carries_the_label_less_fleet_generalisation(self):
        """Decision 6.4 of PLAN-image-supply-chain lives here.

        A static fleet advertising only `self-hosted` and `static` is
        invisible to a label-based audit, and that sentence was in a
        closed issue rather than anywhere a reader of the criterion
        would find it.
        """
        self.assertIn('structurally invisible', self.spec)

    def test_the_page_does_not_restate_the_retired_list(self):
        """A second copy of the banned set is the defect to avoid.

        The dates belong to eol-distro's table. A release name may be
        discussed in prose here, but a date means the list has been
        copied.
        """
        for release in distros.EOL_RELEASES:
            with self.subTest(release=release.name):
                self.assertNotIn(release.eol, self.spec)

    def test_no_generated_marker_block(self):
        """A spec page must stay reviewable; see docs/audits/README.md."""
        self.assertNotIn('consistency-audit:begin', self.spec)

    def test_the_criterion_is_listed_in_the_index(self):
        index = repo_text('docs', 'audits', 'README.md')
        self.assertIn('[eol-producers.md](eol-producers.md)', index)


if __name__ == '__main__':
    unittest.main()
