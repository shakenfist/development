#!/usr/bin/env python3

"""Tests for audit-check.py checks.

Run with: python3 scripts/test_audit_check.py
"""

# audit-ok: plan-reference-file
#
# Every plan path in this file is a fixture, not a pointer. The plan
# checks are tested by writing plans into a temporary directory and
# naming them, and their failing cases exist precisely to name plans
# that do not resolve. None of it is a trail a reader would follow
# into docs/plans/, and marking fifty individual lines would bury the
# lines the per-line marker is actually meant for.

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'audit-check.py'
)

# audit_common lives beside audit-check.py, which is only on sys.path
# by accident of how this suite happens to be invoked. Inserted
# explicitly, the same way test_audit_update_docs.py does it.
sys.path.insert(0, os.path.dirname(SCRIPT))

from audit_common import AUDIT_METADATA, ISSUE_TITLES  # noqa: E402

# This repository, for the tests that check a check against the spec
# page or the canonical template it is supposed to agree with.
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

# These tests drive fixture git repositories, and the pre-commit hook
# runs them during `git commit`, when git exports GIT_INDEX_FILE and
# friends to hooks. Inherited by the fixture git subprocesses, those
# variables point git at the outer repository's index, so the tests
# wreck the real index instead of exercising their fixtures. Scrub
# them from this process so every child starts clean.
for _variable in [name for name in os.environ if name.startswith('GIT_')]:
    del os.environ[_variable]

# audit-check.py is not importable by name (the hyphen is not a valid
# module identifier), so load it from its path.
_spec = importlib.util.spec_from_file_location('audit_check', SCRIPT)
audit_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_check)


class CanonicalNameTest(unittest.TestCase):
    def test_collapses_separators_and_case(self):
        canonical = audit_check.canonical_dependency_name
        self.assertEqual(canonical('typing_extensions'), 'typing-extensions')
        self.assertEqual(canonical('typing-extensions'), 'typing-extensions')
        self.assertEqual(canonical('Zope.Interface'), 'zope-interface')
        self.assertEqual(canonical('prometheus__client'), 'prometheus-client')


class DependencyNameNormalizationTest(unittest.TestCase):
    def _check(self, pyproject_body):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write(pyproject_body)
            return audit_check.check_dependency_name_normalization(
                tmp, {'has_pyproject_toml': True}
            )

    def test_not_applicable_without_pyproject(self):
        result = audit_check.check_dependency_name_normalization(
            '/nonexistent', {'has_pyproject_toml': False}
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_clean_dependencies_pass(self):
        body = (
            'dependencies = [\n'
            '    "typing-extensions==4.16.0",\n'
            '    "requests==2.34.2",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_hyphen_underscore_duplicate_fails(self):
        body = (
            'dependencies = [\n'
            '    "typing-extensions==4.15.0",\n'
            '    "typing_extensions==4.15.0",\n'
            ']\n'
        )
        result = self._check(body)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('typing-extensions', result['details'])

    def test_diverged_exact_versions_fail(self):
        body = (
            'dependencies = [\n'
            '    "typing-extensions==4.15.0",\n'
            '    "typing_extensions==4.16.0",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'fail')

    def test_floor_plus_exact_pin_passes(self):
        # A direct floor constraint plus the exact pin appended by the
        # indirect-dependency workflow is intentional.
        body = (
            'dependencies = [\n'
            '    "psutil>=5.9.4",\n'
            '    "psutil==7.2.2",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_base_plus_extras_same_version_passes(self):
        body = (
            'dependencies = [\n'
            '    "gunicorn[gevent]==25.3.0",\n'
            '    "gunicorn==25.3.0",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_base_plus_extras_conflicting_version_fails(self):
        body = (
            'dependencies = [\n'
            '    "gunicorn[gevent]==25.3.0",\n'
            '    "gunicorn==26.0.0",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'fail')

    def test_same_name_across_separate_arrays_passes(self):
        # A name pinned in the main array and in an optional group is
        # two separate install-time scopes, not a conflict.
        body = (
            '[project]\n'
            'dependencies = [\n'
            '    "requests==2.34.2",\n'
            ']\n'
            '[project.optional-dependencies]\n'
            'pinned = [\n'
            '    "requests==2.34.2",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_non_dependency_quoted_strings_ignored(self):
        # URLs, script entry points and classifiers must not be parsed
        # as dependency pins.
        body = (
            '[project]\n'
            'dependencies = [\n'
            '    "typing-extensions==4.16.0",\n'
            ']\n'
            '[project.urls]\n'
            '"Homepage" = "https://shakenfist.com"\n'
            '[project.scripts]\n'
            'sf-ctl = "shakenfist.client.ctl:cli"\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')


class StripMarkdownCodeTest(unittest.TestCase):
    def test_strips_fenced_blocks(self):
        stripped = audit_check.strip_markdown_code(
            'before\n```\n[x](y)\n```\nafter\n'
        )
        self.assertNotIn('[x](y)', stripped)
        self.assertIn('before', stripped)
        self.assertIn('after', stripped)

    def test_strips_inline_span(self):
        self.assertNotIn(
            '[x](y)', audit_check.strip_markdown_code('see `[x](y)` here')
        )

    def test_strips_span_wrapped_across_lines(self):
        # Prose wrapped at 65 columns splits code spans all the time.
        stripped = audit_check.strip_markdown_code(
            'the guard read `if a.shared and requestor not in\n'
            "[a.namespace, 'system']: 404`, which is inverted\n"
        )
        self.assertNotIn('[a.namespace', stripped)
        self.assertIn('which is inverted', stripped)

    def test_unpaired_backtick_does_not_swallow_later_paragraphs(self):
        stripped = audit_check.strip_markdown_code(
            'a stray ` backtick\n\n[real](../README.md)\n'
        )
        self.assertIn('[real](../README.md)', stripped)


class DocsExternalLinksTest(unittest.TestCase):
    def _check(self, files=None, props=None):
        """Run the check over a docs/ tree built from {path: content}.

        Paths are repo-relative. A None content creates an empty file,
        which is enough for link resolution.
        """
        with tempfile.TemporaryDirectory() as tmp:
            for path, content in (files or {}).items():
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write(content or '')
            return audit_check.check_docs_external_links(tmp, props or {})

    def test_not_applicable_without_docs(self):
        self.assertEqual(self._check()['status'], 'not_applicable')

    def test_internal_relative_link_passes(self):
        result = self._check({
            'docs/index.md': '[guide](guide.md) and [up](../docs/guide.md)\n',
            'docs/guide.md': None,
        })
        self.assertEqual(result['status'], 'pass')

    def test_generated_compliance_block_links_are_not_scanned(self):
        # Same reasoning as the plan-phase-references case: a detail
        # string harvested from another repository can carry a
        # markdown link, and it is that repository's link to get
        # wrong, not ours.
        result = self._check({
            'docs/audits/renovate.md': (
                '# Audit: renovate\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                'Details for non-compliant projects:\n'
                '\n'
                '- ryll: see [the config](../renovate.json) for why\n'
                '<!-- consistency-audit:end -->\n'
            ),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_markers_shown_inside_a_fence_do_not_open_a_block(self):
        """A document may show what a generated block looks like.

        docs/audits/README.md does exactly that, and is safe only
        because both fence delimiters happen to fall outside the
        marker pair. When the closing fence falls between them instead,
        blanking erases the fence delimiter, the caller's fence pass
        never sees the block close, and every link in the rest of the
        file is treated as code and skipped -- an invisible exemption,
        which is the one direction this function must never fail in.
        Blanking therefore tracks fences itself rather than relying on
        the order in which each caller composes its passes.
        """
        result = self._check({
            'docs/audits/README.md': (
                '# Audit index\n'
                '\n'
                'Each audit file follows this structure:\n'
                '\n'
                '```markdown\n'
                '## Projects\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '```\n'
                '<!-- consistency-audit:end -->\n'
                '\n'
                'Our own [bad link](../tools/x.sh).\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'a fence delimiter blanked from inside a marker pair left '
            'the rest of the file unscanned',
        )
        self.assertIn('../tools/x.sh', result['details'])

    def test_prose_naming_a_marker_does_not_open_a_real_block(self):
        # Same shape as the plan-phase-references case: the prose
        # sentence and the real table are both normal things for a
        # spec page to contain, and a loose begin match joins them
        # into one exemption covering the file's own prose.
        result = self._check({
            'docs/audits/renovate.md': (
                '# Audit: renovate\n'
                '\n'
                'The `<!-- consistency-audit:begin -->` marker opens '
                'the table.\n'
                '\n'
                'Our own [bad link](../tools/x.sh).\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '| ryll | PASS |\n'
                '<!-- consistency-audit:end -->\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'prose naming the begin marker was closed by the real end '
            'marker further down, exempting everything between',
        )
        self.assertIn('../tools/x.sh', result['details'])

    def test_a_link_after_a_generated_block_is_still_scanned(self):
        result = self._check({
            'docs/audits/renovate.md': (
                '<!-- consistency-audit:begin -->\n'
                '- ryll: see [x](../renovate.json)\n'
                '<!-- consistency-audit:end -->\n'
                '\n'
                'Our own [bad link](../tools/x.sh).\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('../tools/x.sh', result['details'])

    def test_absolute_and_anchor_links_pass(self):
        result = self._check({
            'docs/index.md': (
                '[ci](https://github.com/shakenfist/x/blob/develop/'
                '.github/workflows/ci.yml)\n'
                '[top](#overview)\n'
                '[cdn](//example.com/x)\n'
                '[mail](mailto:someone@example.com)\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_escaping_link_fails(self):
        result = self._check({
            'docs/releasing.md': '[wf](../.github/workflows/release.yml)\n',
            '.github/workflows/release.yml': None,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('../.github/workflows/release.yml', result['details'])

    def test_escaping_link_from_subdirectory_fails(self):
        result = self._check({
            'docs/plans/PLAN-x.md': '[app](../../src/app.rs)\n',
            'src/app.rs': None,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/plans/PLAN-x.md', result['details'])

    def test_repo_root_relative_link_fails(self):
        # Written as if from the repository root, so it resolves to
        # docs/plans/src/app.rs, which does not exist. Dead on GitHub
        # too, and the fix is the same absolute URL.
        result = self._check({
            'docs/plans/PLAN-x.md': '[app](src/app.rs)\n',
            'src/app.rs': None,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('src/app.rs', result['details'])

    def test_site_root_absolute_link_passes(self):
        # The mkdocs convention for another page of the same site.
        result = self._check({
            'docs/index.md': '[locks](/operator_guide/locks/)\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_fragment_is_not_part_of_the_path(self):
        result = self._check({
            'docs/index.md': '[guide](guide.md#setup)\n',
            'docs/guide.md': None,
        })
        self.assertEqual(result['status'], 'pass')

    def test_escaping_link_in_code_block_is_ignored(self):
        result = self._check({
            'docs/index.md': '```\n[wf](../.github/workflows/ci.yml)\n```\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_percent_encoded_target_resolves(self):
        result = self._check({
            'docs/index.md': '[note](my%20note.md)\n',
            'docs/my note.md': None,
        })
        self.assertEqual(result['status'], 'pass')

    def test_reference_definition_is_checked(self):
        result = self._check({
            'docs/index.md': 'See [wf].\n\n[wf]: ../.github/workflows/ci.yml\n',
        })
        self.assertEqual(result['status'], 'fail')

    def test_doc_content_excludes_are_skipped(self):
        files = {
            'docs/components/ryll/index.md': '[app](../../../ryll/src/app.rs)\n',
        }
        self.assertEqual(self._check(files)['status'], 'fail')
        self.assertEqual(
            self._check(
                files, props={'doc_content_excludes': ['docs/components/']}
            )['status'],
            'pass',
        )


class ReadmeStructureTest(unittest.TestCase):
    PITCH = (
        '# Project\n\nA short pitch.\n\n'
        '[docs](https://github.com/shakenfist/x/blob/develop/'
        'docs/index.md)\n'
    )

    def _check(self, readme=None, with_docs_dir=False):
        with tempfile.TemporaryDirectory() as tmp:
            if readme is not None:
                with open(os.path.join(tmp, 'README.md'), 'w') as f:
                    f.write(readme)
            if with_docs_dir:
                os.mkdir(os.path.join(tmp, 'docs'))
            return audit_check.check_readme_structure(tmp, {})

    def test_not_applicable_without_readme(self):
        self.assertEqual(self._check()['status'], 'not_applicable')

    def test_short_readme_with_docs_link_passes(self):
        result = self._check(self.PITCH, with_docs_dir=True)
        self.assertEqual(result['status'], 'pass')

    def test_too_many_lines_fails(self):
        readme = self.PITCH + ('filler\n' * 200)
        result = self._check(readme, with_docs_dir=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('lines', result['details'])

    def test_too_many_words_fails(self):
        # Few lines, but far over the word cap.
        readme = self.PITCH + (('word ' * 300) + '\n') * 5
        result = self._check(readme, with_docs_dir=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('words', result['details'])

    def test_missing_docs_link_fails_when_docs_exist(self):
        result = self._check(
            '# Project\n\nA short pitch.\n', with_docs_dir=True
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no link into docs/', result['details'])

    def test_docs_link_not_required_without_docs_dir(self):
        result = self._check('# Project\n\nA short pitch.\n')
        self.assertEqual(result['status'], 'pass')

    def test_docs_link_in_code_block_does_not_count(self):
        readme = (
            '# Project\n\nA short pitch.\n\n'
            '```\n[docs](docs/index.md)\n```\n'
        )
        result = self._check(readme, with_docs_dir=True)
        self.assertEqual(result['status'], 'fail')


class LlmDocStructureTest(unittest.TestCase):
    AGENTS = (
        '# AGENTS.md\n\n## Conventions\n\nSingle quotes everywhere.\n\n'
        'Usage is documented in `docs/usage.md`.\n'
    )
    ARCHITECTURE = (
        '# Architecture\n\n## Overview\n\nA daemon and a client.\n\n'
        '[the docs](https://github.com/shakenfist/x/blob/develop/'
        'docs/usage.md)\n'
    )

    def _check(self, agents=None, architecture=None, docs=None):
        """docs maps docs/-relative filenames to content."""
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in (('AGENTS.md', agents),
                                  ('ARCHITECTURE.md', architecture)):
                if content is not None:
                    with open(os.path.join(tmp, name), 'w') as f:
                        f.write(content)
            if docs is not None:
                os.mkdir(os.path.join(tmp, 'docs'))
                for name, content in docs.items():
                    with open(os.path.join(tmp, 'docs', name), 'w') as f:
                        f.write(content)
            return audit_check.check_llm_doc_structure(tmp, {})

    def test_not_applicable_without_either_file(self):
        self.assertEqual(self._check()['status'], 'not_applicable')

    def test_summary_sized_files_pass(self):
        result = self._check(
            self.AGENTS, self.ARCHITECTURE, docs={'usage.md': 'Frob.\n'}
        )
        self.assertEqual(result['status'], 'pass')

    def test_one_file_alone_is_still_checked(self):
        result = self._check(agents=self.AGENTS + ('filler\n' * 400))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('AGENTS.md is', result['details'])

    def test_agents_line_cap_is_tighter_than_architecture(self):
        # 400 lines: over the AGENTS.md cap, under the
        # ARCHITECTURE.md one. AGENTS.md is loaded into every
        # session, so it pays for its length on every task.
        body = '\n'.join(f'line {n}' for n in range(400)) + '\n'
        self.assertEqual(
            self._check(agents=self.AGENTS + body)['status'], 'fail'
        )
        self.assertEqual(
            self._check(architecture=self.ARCHITECTURE + body)['status'],
            'pass',
        )

    def test_word_cap_fails_on_few_lines(self):
        result = self._check(agents=self.AGENTS + ('word ' * 3000))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('words', result['details'])

    def test_missing_docs_reference_fails_when_docs_exist(self):
        result = self._check(
            agents='# AGENTS.md\n\nNo pointers here.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('references no page under docs/', result['details'])

    def test_backticked_docs_path_counts_as_a_reference(self):
        # These files are read on GitHub and by agents, not rendered
        # off-site, so a backticked path points as well as a link.
        result = self._check(
            agents='# AGENTS.md\n\nSee `docs/usage.md`.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assertEqual(result['status'], 'pass')

    def test_plan_reference_alone_does_not_count(self):
        # A plan is a design record, not the documentation these
        # files should be delegating to.
        result = self._check(
            agents='# AGENTS.md\n\nSee `docs/plans/PLAN-frob.md`.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assertEqual(result['status'], 'fail')

    def test_docs_reference_not_required_without_docs_dir(self):
        result = self._check(agents='# AGENTS.md\n\nNo pointers here.\n')
        self.assertEqual(result['status'], 'pass')

    def test_docs_holding_only_plans_is_not_a_docs_dir(self):
        # client-python's docs/ contains nothing but plans/, so there
        # is no documentation page to delegate to.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'docs', 'plans'))
            with open(
                os.path.join(tmp, 'docs', 'plans', 'PLAN-frob.md'), 'w'
            ) as f:
                f.write('# Plan\n')
            with open(os.path.join(tmp, 'AGENTS.md'), 'w') as f:
                f.write('# AGENTS.md\n\nNo pointers here.\n')
            result = audit_check.check_llm_doc_structure(tmp, {})
        self.assertEqual(result['status'], 'pass')

    def test_shared_heading_between_files_fails(self):
        result = self._check(
            self.AGENTS + '\n## Code Organisation\n\nCrates.\n',
            self.ARCHITECTURE + '\n## code organisation\n\nCrates.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('share the headings', result['details'])
        self.assertIn('code organisation', result['details'])

    def test_shared_heading_can_be_suppressed(self):
        marker = audit_check.LLM_DOC_STRUCTURE_OK
        result = self._check(
            self.AGENTS + f'\n## Testing {marker}\n\nHow to run.\n',
            self.ARCHITECTURE + '\n## Testing\n\nWhere they live.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assertEqual(result['status'], 'pass')

    def test_heading_restating_a_docs_page_fails(self):
        result = self._check(
            architecture=self.ARCHITECTURE + '\n## Configuration\n\n.vv.\n',
            docs={'configuration.md': 'Every flag.\n'},
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/configuration.md', result['details'])

    def test_docs_page_heading_match_ignores_hyphens_and_case(self):
        result = self._check(
            agents=self.AGENTS + '\n## Control Socket\n\nVerbs.\n',
            docs={'control-socket.md': 'The wire protocol.\n'},
        )
        self.assertEqual(result['status'], 'fail')

    def test_docs_index_heading_is_allowed(self):
        # "## Index" pointing at docs/index.md is the behaviour the
        # audit wants, not a duplication finding.
        result = self._check(
            agents=self.AGENTS + '\n## Index\n\nStart here.\n',
            docs={'index.md': 'Contents.\n'},
        )
        self.assertEqual(result['status'], 'pass')

    def test_headings_in_code_blocks_are_ignored(self):
        fenced = '\n```markdown\n## Configuration\n```\n'
        result = self._check(
            agents=self.AGENTS + fenced,
            architecture=self.ARCHITECTURE + fenced,
            docs={'configuration.md': 'Every flag.\n'},
        )
        self.assertEqual(result['status'], 'pass')


class PlanPhaseReferencesTest(unittest.TestCase):
    def _check(self, files, props=None):
        """files maps repo-relative paths to content."""
        with tempfile.TemporaryDirectory() as tmp:
            for rel, content in files.items():
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path) or tmp,
                            exist_ok=True)
                with open(path, 'w') as f:
                    f.write(content)
            return audit_check.check_plan_phase_references(
                tmp, props or {}
            )

    def test_not_applicable_without_readme_or_docs(self):
        self.assertEqual(
            self._check({})['status'], 'not_applicable'
        )

    def test_clean_docs_pass(self):
        result = self._check({
            'README.md': '# Project\n\nA pitch.\n',
            'docs/usage.md': 'The frobnicator frobs on demand.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_phase_reference_in_docs_fails_with_location(self):
        result = self._check({
            'docs/usage.md': (
                'Frobbing.\n\n'
                'Frobnication was implemented in phase 5.\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/usage.md:3', result['details'])

    def test_phase_reference_in_readme_fails(self):
        result = self._check({
            'README.md': 'Since Phase 3, frobbing is default.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('README.md:1', result['details'])

    def test_generated_compliance_block_is_not_scanned(self):
        """A harvested detail must not fail this repository's own audit.

        The compliance tables on docs/audits/compliance.md are written
        by audit-update-docs.py from detail strings collected in other
        repositories, and rendered as bare prose. The plan-index check
        quotes an offending status cell verbatim, and the canonical
        example of one is 'Complete (phases 1-5 and 2b, 2026-08-15)'.
        Once the audits tree moved under docs/ those files entered this
        check's scope, so without the exclusion the bot writes that
        phrase into docs/audits/plan-index.md one morning and this
        repository fails its own audit the next, having committed
        nothing.
        """
        result = self._check({
            'docs/audits/plan-index.md': (
                '# Audit: plan index\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '| Project | Status |\n'
                '|---------|--------|\n'
                '| ryll | FAIL |\n'
                '\n'
                'Details for non-compliant projects:\n'
                '\n'
                '- ryll: 1 plan has a freeform status cell '
                '(PLAN-x.md ("Complete (phases 1-5 and 2b, '
                '2026-08-15): shipped"))\n'
                '<!-- consistency-audit:end -->\n'
            ),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_prose_naming_the_markers_exempts_nothing(self):
        """Naming a marker in prose must not exempt anything.

        The first version of this exclusion matched
        'consistency-audit:begin' as a bare substring, so a sentence
        describing the markers triggered it. Documents that write the
        pair as one token -- '<!-- consistency-audit:begin/end -->' --
        matched the begin and never the end, blanking the rest of the
        file: 148 of 309 lines of one plan and 96 of 159 of another
        silently left docs-external-links. Both spellings below are
        taken from the real files that did it.
        """
        for prose in (
            'The `<!-- consistency-audit:begin/end -->` marker block so',
            'empty `<!-- consistency-audit:begin/end -->` block. Pairs with',
            'rewrites the table between the `<!-- consistency-audit:begin',
        ):
            result = self._check({
                'docs/notes.md': (
                    '# Notes\n'
                    '\n'
                    + prose + '\n'
                    '\n'
                    'This was wired up in phase 6.\n'
                ),
            })
            self.assertEqual(
                result['status'], 'fail',
                f'prose naming the markers exempted the rest of the '
                f'file: {prose!r}',
            )
            self.assertIn('docs/notes.md:5', result['details'])

    def test_prose_naming_both_markers_exempts_nothing_between_them(self):
        """Exact whole-line matching, pinned by the case that needs it.

        This is the one the substring test really got wrong and that
        the unterminated case cannot prove: docs/consistency-audits.md
        names the begin marker on one line and the end marker two
        lines later while explaining them, so a substring test found a
        matched pair and blanked the prose between. Only a whole-line
        match in the exact spelling audit-update-docs.py emits
        distinguishes that from a real block.
        """
        result = self._check({
            'docs/notes.md': (
                '# Notes\n'
                '\n'
                'audit-update-docs.py rewrites the table between the\n'
                '`<!-- consistency-audit:begin -->` and\n'
                'This part was wired up in phase 6.\n'
                '`<!-- consistency-audit:end -->` markers.\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'prose naming both markers exempted the lines between them',
        )
        self.assertIn('docs/notes.md:5', result['details'])

    def test_prose_naming_a_marker_does_not_open_a_real_block(self):
        """Prose and a real table in one file is the shape that bites.

        The earlier tests here put prose naming the markers in a file
        with no real block, so nothing could ever close what the prose
        loosely opened and the exemption stayed empty. A spec page that
        both explains the markers and carries a table has a real end
        marker further down, which closes the block the prose opened --
        blanking every line between the sentence and the table. That is
        a larger and more plausible exemption than the one measured in
        the plan files, and it is invisible in exactly the same way.
        """
        result = self._check({
            'docs/audits/plan-index.md': (
                '# Audit: plan index\n'
                '\n'
                'The `<!-- consistency-audit:begin -->` marker opens '
                'the table.\n'
                '\n'
                'This was wired up in phase 6.\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '| ryll | PASS |\n'
                '<!-- consistency-audit:end -->\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'prose naming the begin marker was closed by the real end '
            'marker further down, exempting everything between',
        )
        self.assertIn('docs/audits/plan-index.md:5', result['details'])

    def test_an_unterminated_block_exempts_nothing(self):
        # Failing towards more scanning: a hidden exemption is
        # invisible, a false positive is not.
        result = self._check({
            'docs/notes.md': (
                '<!-- consistency-audit:begin -->\n'
                'Wired up in phase 6.\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/notes.md:2', result['details'])

    def test_a_phase_reference_after_a_generated_block_still_fails(self):
        # The exclusion must end at the end marker, and must not shift
        # the reported line numbers: blanked lines are kept, not
        # dropped. Without both, this is a hole rather than a filter.
        result = self._check({
            'docs/audits/plan-index.md': (
                '# Audit: plan index\n'
                '\n'
                '<!-- consistency-audit:begin -->\n'
                '- ryll: shipped in phase 4\n'
                '<!-- consistency-audit:end -->\n'
                '\n'
                'This was wired up in phase 6.\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('docs/audits/plan-index.md:7', result['details'])

    def test_markers_shown_inside_a_fence_do_not_open_a_block(self):
        # The same hazard as the docs-external-links case, and worth
        # pinning per caller: each one runs its own fence pass, and
        # both ran it after blanking. A closing fence blanked from
        # between a marker pair leaves the fence open for the rest of
        # the file, so the phase reference below escapes.
        result = self._check({
            'docs/audits/README.md': (
                '# Audit index\n'
                '\n'
                '```markdown\n'
                '<!-- consistency-audit:begin -->\n'
                '```\n'
                '<!-- consistency-audit:end -->\n'
                '\n'
                'This was wired up in phase 6.\n'
            ),
        })
        self.assertEqual(
            result['status'], 'fail',
            'a fence delimiter blanked from inside a marker pair left '
            'the rest of the file unscanned',
        )
        self.assertIn('docs/audits/README.md:8', result['details'])

    def test_plural_phases_fails(self):
        result = self._check({
            'docs/usage.md': 'Delivered across phases 2 and 3.\n',
        })
        self.assertEqual(result['status'], 'fail')

    def test_plans_directory_is_ignored(self):
        result = self._check({
            'docs/plans/PLAN-frob.md': '## Phase 1: frob\n',
            'docs/usage.md': 'Frobbing.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_nested_plans_directory_is_ignored(self):
        result = self._check({
            'docs/parts/plans/PLAN-frob.md': '## Phase 2: frob\n',
            'docs/usage.md': 'Frobbing.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_doc_content_excludes_are_skipped(self):
        # shakenfist's docs/components/ is an automated import of
        # other repositories' documentation; findings there must be
        # fixed at the source, not double-reported.
        files = {
            'docs/components/ryll/notes.md': 'As of phase 2.\n',
            'docs/usage.md': 'Frobbing.\n',
        }
        result = self._check(
            files,
            props={'doc_content_excludes': ['docs/components/']},
        )
        self.assertEqual(result['status'], 'pass')
        # Without the override the same tree fails, so the exclude
        # is doing the work.
        self.assertEqual(self._check(files)['status'], 'fail')

    def test_code_blocks_do_not_count(self):
        result = self._check({
            'docs/usage.md': (
                'Frobbing.\n\n'
                '```\nlog line: entering phase 3\n```\n\n'
                'A `phase 3` inline span does not count either.\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_audit_ok_marker_suppresses_the_line(self):
        result = self._check({
            'docs/electrics.md': (
                'A phase 3 supply feeds the rack. '
                '<!-- audit-ok: phase-reference -->\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_phase_without_a_number_passes(self):
        result = self._check({
            'docs/usage.md': (
                'Two-phase commit is used for the frob step.\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_non_markdown_files_are_ignored(self):
        result = self._check({
            'docs/notes.txt': 'Implemented in phase 4.\n',
            'docs/usage.md': 'Frobbing.\n',
        })
        self.assertEqual(result['status'], 'pass')


class DiagramFormatTest(unittest.TestCase):
    """The interesting cases are the ones that must NOT be flagged.

    Every "passes" case below is a real block from this fleet that an
    earlier draft of the heuristic reported. They are kept as tests
    rather than as a note in the spec because each one is a different
    reason, and a future loosening of the rule will break exactly the
    one it should.
    """

    def _check(self, files, props=None):
        """files maps repo-relative paths to content."""
        with tempfile.TemporaryDirectory() as tmp:
            for rel, content in files.items():
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path) or tmp, exist_ok=True)
                with open(path, 'w') as f:
                    f.write(content)
            return audit_check.check_diagram_format(tmp, props or {})

    def _fenced(self, body):
        return f'# Page\n\nText.\n\n```\n{body}```\n'

    def test_not_applicable_without_docs(self):
        self.assertEqual(
            self._check({})['status'], 'not_applicable'
        )

    def test_prose_passes(self):
        result = self._check({
            'README.md': '# Project\n\nA pitch, with no pictures.\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_mermaid_fence_passes(self):
        result = self._check({
            'ARCHITECTURE.md': (
                '# Shape\n\n'
                '```mermaid\n'
                'flowchart TB\n'
                '    a["One"] --> b["Two"]\n'
                '```\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_ascii_box_diagram_fails_with_location(self):
        result = self._check({
            'ARCHITECTURE.md': self._fenced(
                '+-------------+     +-------------+\n'
                '| Config      |---->| Engine      |\n'
                '+-------------+     +-------------+\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ARCHITECTURE.md:5', result['details'])

    def test_unicode_box_diagram_fails(self):
        result = self._check({
            'docs/design.md': self._fenced(
                '┌─────────────┐\n'
                '│  Front end  │\n'
                '└─────────────┘\n'
                '       │\n'
                '       ▼\n'
                '┌─────────────┐\n'
                '│  Back end   │\n'
                '└─────────────┘\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')

    def test_boxless_sequence_diagram_fails(self):
        """Two parties and labelled arrows, drawn with bare verticals."""
        result = self._check({
            'docs/protocol.md': self._fenced(
                'VMM                          Guest\n'
                ' │                            │\n'
                ' │ ──── VmmConfig ──────────> │\n'
                ' │ <──── InitMessage ──────── │\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')

    def test_file_tree_passes(self):
        """A tree has tees and elbows but no corner and no edge.

        The arrow in a comment is the trap: ryll's ARCHITECTURE.md
        annotates a src/ listing with "egui::Key -> LogicalKey", and
        counting a thin arrow anywhere in the block flagged the whole
        98-line tree.
        """
        result = self._check({
            'ARCHITECTURE.md': self._fenced(
                'src/\n'
                '├── main.rs              # CLI entry\n'
                '├── app.rs               # egui App, event loop\n'
                '│                        #   and reconnect\n'
                '├── input_egui.rs        # egui::Key → LogicalKey\n'
                '└── web/                 # --web mode\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_memory_map_passes(self):
        result = self._check({
            'docs/guest.md': self._fenced(
                'Address         Size    Region\n'
                '──────────────  ──────  ──────────────────\n'
                '0x0000_1000             GDT\n'
                '0x0000_2000             Page tables\n'
                '0x0001_0000    128 KiB  core.bin\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_bit_field_with_caret_callouts_passes(self):
        """A caret points up at a field; it is not a flow connector."""
        result = self._check({
            'docs/qcow2.md': self._fenced(
                ' 63  62  61          csize_shift           0\n'
                '+---+---+------------+----------------------+\n'
                '| 0 | 1 | Sectors    |  Compressed Offset   |\n'
                '+---+---+------------+----------------------+\n'
                '      ^        ^                ^\n'
                '      |        |                |\n'
                '      |        +-- 512B sectors +-- Byte offset\n'
                '      +-- COMPRESSED = 1\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_ring_buffer_with_thin_arrow_callouts_passes(self):
        result = self._check({
            'docs/video.md': self._fenced(
                '┌───────────────────────────────┐\n'
                '│          Ring Buffer          │\n'
                '│  ┌─────┬─────┬─────┬─────┐    │\n'
                '│  │cmd 1│cmd 2│cmd 3│     │    │\n'
                '│  └─────┴─────┴─────┴─────┘    │\n'
                '│     ↑                 ↑       │\n'
                '│   (tail)            (head)    │\n'
                '└───────────────────────────────┘\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_register_map_with_thin_arrow_annotations_passes(self):
        result = self._check({
            'docs/mmio.md': self._fenced(
                '┌────────────────────────────────────────┐\n'
                '│ Control Registers (4KB, MMIO)          │\n'
                '│   0x00: command (u32)                  │\n'
                '│   0x08: data_gpa (u64)  ← guest phys   │\n'
                '│   0x28: doorbell (u32)  ← triggers work│\n'
                '└────────────────────────────────────────┘\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_audit_ok_marker_exempts_a_block(self):
        drawn = (
            '+-------------+     +-------------+\n'
            '| Config      |---->| Engine      |\n'
            '+-------------+     +-------------+\n'
        )
        self.assertEqual(
            self._check({'docs/x.md': self._fenced(drawn)})['status'],
            'fail',
        )
        result = self._check({
            'docs/x.md': (
                '# Page\n\n'
                '<!-- audit-ok: diagram-format -->\n'
                f'```\n{drawn}```\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_audit_ok_marker_survives_a_blank_line(self):
        """A blank line after an HTML comment is ordinary style.

        A one-line window would mean the natural way to write the
        exemption is the way that silently does not work.
        """
        result = self._check({
            'docs/x.md': (
                '# Page\n\n'
                '<!-- audit-ok: diagram-format -->\n'
                '\n'
                '```\n'
                '+---+     +---+\n'
                '| a |---->| b |\n'
                '+---+     +---+\n'
                '```\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_audit_ok_marker_is_not_inherited_from_a_paragraph_above(self):
        result = self._check({
            'docs/x.md': (
                '# Page\n\n'
                '<!-- audit-ok: diagram-format -->\n'
                '\n'
                'A paragraph about the exempt diagram further up.\n'
                '\n'
                '```\n'
                '+---+     +---+\n'
                '| a |---->| b |\n'
                '+---+     +---+\n'
                '```\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')

    def test_mermaid_fence_with_an_info_string_passes(self):
        result = self._check({
            'docs/x.md': (
                '# Page\n\n'
                '```mermaid title=flow\n'
                'flowchart TB\n'
                '    a --> b\n'
                '```\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_plans_are_out_of_scope(self):
        result = self._check({
            'docs/plans/PLAN-x.md': self._fenced(
                '+-------------+     +-------------+\n'
                '| Config      |---->| Engine      |\n'
                '+-------------+     +-------------+\n'
            ),
        })
        self.assertEqual(result['status'], 'not_applicable')

    def test_doc_content_excludes_are_skipped(self):
        """shakenfist's docs/components/ is synced from elsewhere.

        Flagging it would file an issue against the repository that
        cannot fix it: the next sync-external-docs run reverts any
        conversion made there.
        """
        files = {
            'docs/components/ryll/x.md': self._fenced(
                '+-------------+     +-------------+\n'
                '| Config      |---->| Engine      |\n'
                '+-------------+     +-------------+\n'
            ),
        }
        self.assertEqual(self._check(files)['status'], 'fail')
        result = self._check(
            files, {'doc_content_excludes': ['docs/components/']}
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_unterminated_fence_yields_nothing(self):
        result = self._check({
            'docs/x.md': (
                '# Page\n\n```\n'
                '+---+\n| a |---->\n+---+\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')


class MermaidLintCiTest(unittest.TestCase):
    def _check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            for rel, content in files.items():
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path) or tmp, exist_ok=True)
                with open(path, 'w') as f:
                    f.write(content)
            return audit_check.check_mermaid_lint_ci(tmp, {})

    DIAGRAM = (
        '# Shape\n\n```mermaid\nflowchart TB\n  a --> b\n```\n'
    )
    WORKFLOW = (
        'name: Mermaid lint\non:\n  pull_request:\n'
        'permissions:\n  contents: read\njobs:\n'
        '  lint:\n    runs-on: [self-hosted, vm, debian-12-docker, s]\n'
        '    steps:\n      - run: ./tools/mermaid-lint.sh\n'
    )

    def test_not_applicable_without_mermaid(self):
        result = self._check({'README.md': '# Project\n\nNo pictures.\n'})
        self.assertEqual(result['status'], 'not_applicable')

    def test_fails_without_the_script(self):
        result = self._check({
            'ARCHITECTURE.md': self.DIAGRAM,
            '.github/workflows/mermaid-lint.yml': self.WORKFLOW,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('tools/mermaid-lint.sh', result['details'])

    def test_fails_without_a_workflow(self):
        result = self._check({
            'ARCHITECTURE.md': self.DIAGRAM,
            'tools/mermaid-lint.sh': '#!/bin/bash\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('CI workflow', result['details'])

    def test_passes_with_both(self):
        result = self._check({
            'ARCHITECTURE.md': self.DIAGRAM,
            'tools/mermaid-lint.sh': '#!/bin/bash\n',
            '.github/workflows/mermaid-lint.yml': self.WORKFLOW,
        })
        self.assertEqual(result['status'], 'pass')

    def test_a_workflow_that_only_mentions_it_in_a_comment_fails(self):
        """Describing what something else does is not doing it."""
        result = self._check({
            'ARCHITECTURE.md': self.DIAGRAM,
            'tools/mermaid-lint.sh': '#!/bin/bash\n',
            '.github/workflows/ci.yml': (
                '# Diagrams are linted by tools/mermaid-lint.sh in\n'
                '# its own workflow.\nname: CI\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')

    def test_a_diagram_in_a_plan_still_needs_the_linter(self):
        """The linter renders every tracked markdown file.

        diagram-format ignores plans, but a broken diagram in one
        still breaks a page, so applicability is the whole tree.
        """
        result = self._check({'docs/plans/PLAN-x.md': self.DIAGRAM})
        self.assertEqual(result['status'], 'fail')

    def test_a_tilde_fence_does_not_make_it_applicable(self):
        """mmdc recognises backtick fences only.

        It finds no chart in a ~~~mermaid block and exits zero, so
        calling such a repository applicable would mark it covered for
        a diagram its linter never renders. The audit matches the same
        narrow form the script greps for.
        """
        result = self._check({
            'docs/x.md': (
                '# Page\n\n~~~mermaid\nflowchart TB\n  a --> b\n~~~\n'
            ),
        })
        self.assertEqual(result['status'], 'not_applicable')

    def test_vendored_trees_do_not_make_it_applicable(self):
        """A Rust registry cache holds other people's diagrams."""
        result = self._check({
            'README.md': '# Project\n',
            '.cargo-cache/registry/src/x/README.md': self.DIAGRAM,
        })
        self.assertEqual(result['status'], 'not_applicable')


class MermaidLintDeploymentTest(unittest.TestCase):
    """This repository's copies must match the template exactly.

    templates/mermaid-lint/README.md promises byte-identity, and the
    promise is load-bearing for the shell script in particular:
    .pre-commit-config.yaml scopes shellcheck to ^(scripts|tools)/, so
    the template copy -- the one that goes out to the fleet -- is only
    linted by proxy through its tools/ twin. If the two drift, the
    shipped copy is the one nothing checks.
    """

    def _repo(self, *parts):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, *parts), 'rb') as f:
            return f.read()

    def test_script_matches_the_template(self):
        self.assertEqual(
            self._repo('tools', 'mermaid-lint.sh'),
            self._repo('templates', 'mermaid-lint', 'mermaid-lint.sh'),
        )

    def test_workflow_matches_the_template(self):
        self.assertEqual(
            self._repo('.github', 'workflows', 'mermaid-lint.yml'),
            self._repo('templates', 'mermaid-lint', 'mermaid-lint.yml'),
        )


class CiReviewAutomationSpecTest(unittest.TestCase):
    """The check and its spec page name the same requirements.

    A rewrite of the page condensed the "What we check" list and
    dropped review-pr-with-claude@main from it, while the check went
    on filing "No workflow uses shared action
    review-pr-with-claude@main" against repositories -- so a
    maintainer following the issue link landed on a page that did not
    state the thing they were being measured against. Deriving the
    agreement is what stops that recurring in a new guise.
    """

    def _measured(self):
        # The "Measured" subsection only. Asserting against the whole
        # page passes on the strength of the auto-generated compliance
        # table at the bottom, which quotes the issue message verbatim
        # -- so the assertion would hold precisely while a repository
        # was being failed for a requirement the page never states.
        # And asserting against all of "What we check" would let a
        # requirement satisfy it from the list the check does *not*
        # measure, which is the opposite claim.
        with open(os.path.join(
                REPO_ROOT, 'docs', 'audits',
                'ci-review-automation.md')) as f:
            spec = f.read()
        start = spec.index('### Measured')
        return spec[start:spec.index('\n### ', start + 1)]

    def test_the_spec_names_every_requirement(self):
        spec = self._measured()
        for requirement in (
            audit_check.CI_REVIEW_DEVELOPER_WORKFLOWS
            + (audit_check.CI_REVIEW_SHARED_ACTION,
               audit_check.CI_REVIEW_TRIGGER_ACTION)
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, spec)


class PushAuditTest(unittest.TestCase):
    def setUp(self):
        # A private canonical blocks directory so the tests do not
        # depend on the real templates/shared-blocks/ content.
        self._blocks = tempfile.TemporaryDirectory()
        self.addCleanup(self._blocks.cleanup)
        self.readme_block = (
            '<!-- shared-block: readme-discipline v2 -->\n'
            'Canonical wording.\n'
            '<!-- shared-block-end -->\n'
        )
        # A different version number, so the tests prove versions are
        # tracked per block rather than globally.
        self.comment_block = (
            '<!-- shared-block: comment-proportion v3 -->\n'
            'Comment wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.phase_block = (
            '<!-- shared-block: plan-phase-references v1 -->\n'
            'Phase wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.llm_doc_block = (
            '<!-- shared-block: llm-doc-discipline v1 -->\n'
            'Agent doc wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.path_block = (
            '<!-- shared-block: path-traversal-review v1 -->\n'
            'Path wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.python_block = (
            '<!-- shared-block: python-version-discipline v1 -->\n'
            'Python wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.tests_block = (
            '<!-- shared-block: functional-test-coverage v1 -->\n'
            'Testing wording.\n'
            '<!-- shared-block-end -->\n'
        )
        self.diagram_block = (
            '<!-- shared-block: diagram-discipline v1 -->\n'
            'Diagram wording.\n'
            '<!-- shared-block-end -->\n'
        )
        for name, block in (
            ('readme-discipline', self.readme_block),
            ('llm-doc-discipline', self.llm_doc_block),
            ('diagram-discipline', self.diagram_block),
            ('comment-proportion', self.comment_block),
            ('plan-phase-references', self.phase_block),
            ('path-traversal-review', self.path_block),
            ('python-version-discipline', self.python_block),
            ('functional-test-coverage', self.tests_block),
        ):
            with open(
                os.path.join(self._blocks.name, f'{name}.md'), 'w'
            ) as f:
                f.write(block)
        self.canonical = (
            f'{self.readme_block}\n{self.llm_doc_block}\n'
            f'{self.diagram_block}\n{self.comment_block}\n'
            f'{self.phase_block}\n{self.path_block}\n'
            f'{self.python_block}\n{self.tests_block}'
        )

    def _check(self, files):
        # A referencing AGENTS.md is supplied unless the case brings
        # its own, so the block-validation cases below keep testing
        # block validation instead of all failing on the reference
        # check. The reference check has its own cases.
        files = dict(files)
        named = None
        for candidate in ('PUSH-AUDIT.md', 'PUSH-TEMPLATE.md'):
            # `is not None` rather than `in`, so a case using the
            # absent-file sentinel selects the same filename the
            # check will.
            if files.get(candidate) is not None:
                named = candidate
                break
        if named and 'AGENTS.md' not in files:
            files['AGENTS.md'] = f'# Agents\n\nSee {named}.\n'
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
                # None means "this file is absent", which is how a
                # case opts out of the default AGENTS.md above.
                if content is None:
                    continue
                with open(os.path.join(tmp, name), 'w') as f:
                    f.write(content)
            return audit_check.check_push_audit(
                tmp, {}, blocks_dir=self._blocks.name
            )

    def test_not_applicable_without_file(self):
        self.assertEqual(self._check({})['status'], 'not_applicable')

    def test_current_block_passes(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_legacy_filename_fails(self):
        result = self._check({
            'PUSH-TEMPLATE.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])

    def test_missing_block_fails(self):
        result = self._check({'PUSH-AUDIT.md': '# Audit\n'})
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block readme-discipline',
            result['details'],
        )
        self.assertIn(
            'missing shared block comment-proportion',
            result['details'],
        )

    def test_missing_comment_proportion_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.readme_block}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block comment-proportion',
            result['details'],
        )

    def test_missing_llm_doc_discipline_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': (
                f'# Audit\n\n{self.readme_block}\n'
                f'{self.comment_block}\n{self.phase_block}\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block llm-doc-discipline',
            result['details'],
        )

    def test_missing_plan_phase_references_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': (
                f'# Audit\n\n{self.readme_block}\n'
                f'{self.comment_block}\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block plan-phase-references',
            result['details'],
        )

    def test_stale_version_fails(self):
        stale = self.canonical.replace('v2', 'v1')
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{stale}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('stale (v1 embedded, v2 current)',
                      result['details'])

    def test_drifted_content_fails(self):
        drifted = self.canonical.replace(
            'Canonical wording.', 'Mutated wording.'
        )
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{drifted}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('drifted', result['details'])

    def test_trailing_whitespace_is_ignored(self):
        padded = self.canonical.replace(
            'Canonical wording.', 'Canonical wording.   '
        )
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{padded}\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_unknown_block_fails(self):
        content = (
            f'# Audit\n\n{self.canonical}\n'
            '<!-- shared-block: no-such-block v1 -->\n'
            'Words.\n'
            '<!-- shared-block-end -->\n'
        )
        result = self._check({'PUSH-AUDIT.md': content})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('unknown shared block no-such-block',
                      result['details'])

    def test_missing_end_marker_fails(self):
        content = (
            '# Audit\n\n'
            '<!-- shared-block: readme-discipline v2 -->\n'
            'Canonical wording.\n'
        )
        result = self._check({'PUSH-AUDIT.md': content})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no <!-- shared-block-end -->',
                      result['details'])

    def test_both_files_fails_even_with_current_block(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'PUSH-TEMPLATE.md': '# Old\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])

    def test_unreferenced_audit_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': '# Agents\n\nNothing about the audit here.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'AGENTS.md does not reference PUSH-AUDIT.md',
            result['details'],
        )

    def test_missing_agents_file_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': None,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no AGENTS.md to reference', result['details'])

    def test_legacy_name_reference_names_the_legacy_file(self):
        # A repository still on the old name gets told to rename it,
        # not told twice about a file it does not have.
        result = self._check({
            'PUSH-TEMPLATE.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': '# Agents\n\nSee PUSH-TEMPLATE.md.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])
        self.assertNotIn('does not reference', result['details'])

    def test_reference_is_reported_alongside_block_problems(self):
        # Both failures at once, so a repository fixing one is not
        # surprised by the other on the next daily run.
        result = self._check({
            'PUSH-AUDIT.md': '# Audit\n',
            'AGENTS.md': '# Agents\n\nNothing here.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('missing shared block', result['details'])
        self.assertIn('does not reference', result['details'])

    def test_both_files_with_only_the_legacy_name_referenced(self):
        # The code resolves filename to the new name when both are
        # present, so an AGENTS.md naming only the legacy file gets
        # both a rename message and a reference message. Pinning the
        # behaviour rather than asserting it is desirable.
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'PUSH-TEMPLATE.md': '# Old\n',
            'AGENTS.md': '# Agents\n\nSee PUSH-TEMPLATE.md.\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])
        self.assertIn(
            'AGENTS.md does not reference PUSH-AUDIT.md',
            result['details'],
        )

    def test_shallowness_is_deliberate(self):
        # The spec says the check looks for the filename and not for
        # particular wording, so a mention inside a fenced code block
        # counts. That is a known false positive, kept on purpose:
        # this test exists so a later tightening is a deliberate
        # decision rather than a quiet one.
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'AGENTS.md': (
                '# Agents\n\n```\ncat PUSH-AUDIT.md\n```\n'
            ),
        })
        self.assertEqual(result['status'], 'pass')

    def test_pass_details_mention_the_reference(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'pass')
        self.assertIn('referenced from AGENTS.md', result['details'])

    def test_every_required_block_has_a_canonical_copy(self):
        # A name in the list with no file under
        # templates/shared-blocks would report every repository as
        # carrying an unknown block.
        for name in audit_check.PUSH_AUDIT_BLOCKS:
            with self.subTest(block=name):
                self.assertTrue(os.path.exists(os.path.join(
                    REPO_ROOT, 'templates', 'shared-blocks',
                    f'{name}.md')))

    def test_every_required_block_is_named_in_the_spec(self):
        # A criterion spans four files that must stay in sync. A
        # block required here but absent from the spec page files a
        # fleet issue naming something that page never mentions,
        # which is exactly the cross-file drift this suite exists to
        # catch.
        with open(os.path.join(
                REPO_ROOT, 'docs', 'audits', 'push-audit.md')) as f:
            spec = f.read()
        for name in audit_check.PUSH_AUDIT_BLOCKS:
            with self.subTest(block=name):
                self.assertIn(name, spec)

    def test_the_fixture_covers_every_required_block(self):
        # Otherwise a block added to the list is never exercised
        # here: self.canonical would simply be missing it and every
        # case in this class would fail for the same reason.
        self.assertEqual(
            sorted(audit_check.PUSH_AUDIT_BLOCKS),
            sorted(os.path.splitext(name)[0]
                   for name in os.listdir(self._blocks.name)),
        )


class PinIndirectDepsScopeTest(unittest.TestCase):
    """Tests that indirect pinning only applies to projects which pin.

    A project that exactly pins its own direct dependencies is declaring
    it controls its runtime environment, which is the condition under
    which pinning transitive versions is safe. A library that constrains
    loosely is deliberately leaving resolution to its consumers, and
    pinning on their behalf takes that away.
    """

    def _check(self, dependencies, files=None):
        with tempfile.TemporaryDirectory() as tmp:
            body = 'dependencies = [\n'
            for dependency in dependencies:
                body += f'    "{dependency}",\n'
            body += ']\n'
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write('[project]\nname = "example"\n' + body)
            for path in files or []:
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write('')
            return audit_check.check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )

    def test_not_applicable_without_pyproject(self):
        result = audit_check.check_pin_indirect_deps(
            '/nonexistent', {'has_pyproject_toml': False}
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_library_with_loose_constraints_is_out_of_scope(self):
        result = self._check([
            'click>=8.0.0', 'distro', 'psutil>5.9.0', 'grpcio>=1.70.0',
        ])
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('library', result['details'])

    def test_bare_name_is_not_a_pin(self):
        result = self._check(['python-debian'])
        self.assertEqual(result['status'], 'not_applicable')

    def test_application_with_exact_pins_is_in_scope(self):
        # In scope, and missing the tooling, so it fails rather than
        # dropping out as not applicable.
        result = self._check(['click==8.4.2', 'requests==2.34.2'])
        self.assertEqual(result['status'], 'fail')

    def test_extras_on_an_exact_pin_still_count(self):
        result = self._check(['gunicorn[gevent]==26.0.0'])
        self.assertEqual(result['status'], 'fail')

    def test_a_few_loose_pins_do_not_exempt_an_application(self):
        # shakenfist and kerbside each leave a couple of dependencies
        # loose on purpose; that must not read as a library.
        result = self._check([
            'psutil>=5.9.4', 'uv>=0.8.0', 'click==8.4.2',
            'requests==2.34.2', 'PyYAML==6.0.3',
        ])
        self.assertEqual(result['status'], 'fail')

    def test_in_scope_project_with_the_tooling_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, '.github', 'workflows'))
            os.makedirs(os.path.join(tmp, 'tools'))
            open(os.path.join(
                tmp, '.github', 'workflows',
                'pin-indirect-dependencies.yml'), 'w').close()
            open(os.path.join(
                tmp, 'tools', 'pin-indirect-dependencies.sh'), 'w').close()
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write(
                    '[project]\nname = "example"\ndependencies = [\n'
                    '    "click==8.4.2",\n'
                    '    # START_OF_INDIRECT_DEPS\n'
                    '    # END_OF_INDIRECT_DEPS\n'
                    ']\n'
                )
            result = audit_check.check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )
        self.assertEqual(result['status'], 'pass')

    def test_unparseable_pyproject_is_out_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write('this is not = valid toml [\n')
            result = audit_check.check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )
        self.assertEqual(result['status'], 'not_applicable')


class ReviewCoverageTest(unittest.TestCase):
    """Tests check_review_coverage against fixture git repositories.

    The check shells out to review-tracking.py status, which needs a
    real repository: committed files so blob SHAs resolve, weAudit
    state, and a stamped sidecar.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test User')
        self.git('config', 'commit.gpgsign', 'false')
        os.mkdir(os.path.join(self.repo, '.vscode'))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git'] + list(args), cwd=self.repo, check=True,
                              capture_output=True, text=True)

    def write(self, path, content):
        with open(os.path.join(self.repo, path), 'w') as f:
            f.write(content)

    def review_tracking(self, *args):
        script = os.path.join(os.path.dirname(SCRIPT), 'review-tracking.py')
        return subprocess.run([sys.executable, script] + list(args),
                              cwd=self.repo, capture_output=True, text=True)

    def make_reviewed_repo(self, files, reviewed):
        """Create and commit files, mark some reviewed, stamp, commit."""
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')
        for path in files:
            self.write(path, f'# {path}\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'initial')
        if reviewed:
            self.write('.vscode/testuser.weaudit', json.dumps({
                'auditedFiles': [{'path': p, 'author': 'testuser'} for p in reviewed],
                'partiallyAuditedFiles': [],
            }))
            self.review_tracking('stamp')
            self.git('add', '-A')
            self.git('commit', '-m', 'reviews')

    def make_stale(self, files):
        for path in files:
            self.write(path, f'# {path} changed\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'changes')

    def check(self):
        return audit_check.check_review_coverage(self.repo, {})

    def test_not_applicable_without_scope_config(self):
        self.write('a.py', 'a = 1\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'initial')
        result = self.check()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('review-scope.toml', result['details'])

    def test_backlog_under_threshold_passes(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files)
        self.make_stale(files[:4])
        result = self.check()
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('4 need review (threshold 5)', result['details'])
        self.assertNotIn('missing', result)

    def test_backlog_at_threshold_fails_with_work_queue(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files)
        self.make_stale(files[:5])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('5 need review (threshold 5)', result['details'])
        self.assertEqual(result['missing'],
                         [f'stale: f{i}.py' for i in range(5)])

    def test_never_reviewed_files_count(self):
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('0 of 5 in-scope files reviewed', result['details'])
        self.assertEqual(result['missing'],
                         [f'never reviewed: f{i}.py' for i in range(5)])


class ReviewMarksPreCommitTest(unittest.TestCase):
    """Tests check_review_marks_pre_commit against config fixtures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        os.mkdir(os.path.join(self.repo, '.vscode'))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, content):
        with open(os.path.join(self.repo, path), 'w') as f:
            f.write(content)

    def adopt(self):
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')

    def check(self):
        return audit_check.check_review_marks_pre_commit(self.repo, {})

    def config(self, body, hooks='end-of-file-fixer'):
        """Write a config running `hooks`, prefixed by `body`."""
        hook_lines = ''.join(
            f'      - id: {h}\n' for h in hooks.split() if h
        )
        self.write(
            '.pre-commit-config.yaml',
            f'{body}repos:\n  - repo: local\n    hooks:\n{hook_lines}'
        )

    def test_not_applicable_without_scope_config(self):
        self.config('')
        self.assertEqual(self.check()['status'], 'not_applicable')

    def test_not_applicable_without_pre_commit_config(self):
        self.adopt()
        self.assertEqual(self.check()['status'], 'not_applicable')

    def test_not_applicable_without_a_rewriting_hook(self):
        """ryll's shape: scanners and linters, but no formatter.

        Nothing rewrites the marks, so there is nothing to exclude --
        and demanding a blanket exclude here would hide review prose
        from gitleaks and the bidi scanner.
        """
        self.adopt()
        self.config('', hooks='gitleaks bidi-check shellcheck')
        result = self.check()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('No file-rewriting', result['details'])

    def test_top_level_exclude_passes(self):
        self.adopt()
        self.config('exclude: ^\\.vscode/.*\\.weaudit\n\n')
        self.assertEqual(self.check()['status'], 'pass')

    def test_per_hook_exclude_passes(self):
        """A hook-level exclude protects the files just as well."""
        self.adopt()
        self.write('.pre-commit-config.yaml',
                   'repos:\n'
                   '  - repo: local\n'
                   '    hooks:\n'
                   '      - id: end-of-file-fixer\n'
                   '        exclude: ^\\.vscode/.*\\.weaudit\n')
        self.assertEqual(self.check()['status'], 'pass')

    def test_quoted_exclude_passes(self):
        self.adopt()
        self.config("exclude: '^\\.vscode/.*\\.weaudit'\n\n")
        self.assertEqual(self.check()['status'], 'pass')

    def test_trailing_whitespace_hook_also_counts(self):
        self.adopt()
        self.config('', hooks='trailing-whitespace')
        result = self.check()
        self.assertEqual(result['status'], 'fail')
        self.assertIn('trailing-whitespace', result['details'])

    def test_no_exclude_fails(self):
        self.adopt()
        self.config('')
        result = self.check()
        self.assertEqual(result['status'], 'fail')
        self.assertIn('weaudit', result['details'])

    def test_exclude_missing_the_sidecar_fails(self):
        """An anchored pattern catches the weaudit file but not its json."""
        self.adopt()
        self.config('exclude: ^\\.vscode/.*\\.weaudit$\n\n')
        self.assertEqual(self.check()['status'], 'fail')

    def test_unrelated_exclude_fails(self):
        self.adopt()
        self.config('exclude: ^kerbside/api/static/\n\n')
        self.assertEqual(self.check()['status'], 'fail')

    def test_uncompilable_exclude_is_skipped_not_raised(self):
        self.adopt()
        self.config('exclude: ^[unclosed\n\n')
        self.assertEqual(self.check()['status'], 'fail')


class CanonicalSharedBlocksTest(unittest.TestCase):
    def test_real_canonical_blocks_parse(self):
        # Every canonical file in templates/shared-blocks/ must
        # contain a block whose name matches the filename.
        blocks_dir = audit_check.SHARED_BLOCKS_DIR
        names = [
            f[:-3] for f in os.listdir(blocks_dir)
            if f.endswith('.md') and f != 'README.md'
        ]
        self.assertIn('readme-discipline', names)
        self.assertIn('comment-proportion', names)
        for name in names:
            canonical = audit_check.load_canonical_block(name)
            self.assertIsNotNone(
                canonical,
                f'templates/shared-blocks/{name}.md has no '
                f'shared-block marker matching its filename',
            )


class PlanSourceReferenceTest(unittest.TestCase):
    """Plan pointers written into source and configuration."""

    def _repo(self, tmp, files):
        for relative, content in files.items():
            path = os.path.join(tmp, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        subprocess.run(['git', 'init', '--quiet', '-b', 'main'], cwd=tmp,
                       check=True)
        subprocess.run(['git', 'add', '-A'], cwd=tmp, check=True)
        return audit_check.check_plan_source_references(tmp, {})

    def test_a_resolving_reference_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'docs/plans/PLAN-frob.md': '# Frob\n',
                'src/frob.py': '# See docs/plans/PLAN-frob.md.\n',
            })
        self.assertEqual(result['status'], 'pass')

    def test_a_rotted_reference_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'docs/plans/PLAN-frob.md': '# Frob\n',
                'src/frob.py': '# See docs/plans/PLAN-gone.md.\n',
            })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-gone.md', result['details'])

    def test_a_rotted_reference_in_a_test_still_fails(self):
        # Test files carry prose pointers like any other source, and
        # they rot the same way -- instar's tests/test_adversarial.py
        # cites a plan that no longer exists in its module docstring.
        # Skipping a file because its name looks like a test would
        # hide exactly that.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'tests/test_frob.py': '"""See PLAN-gone.md."""\n',
            })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-gone.md', result['details'])

    def test_the_file_marker_exempts_a_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'tests/test_frob.py': (
                    '# audit-ok: plan-reference-file\n'
                    '"""See PLAN-gone.md and PLAN-also-gone.md."""\n'
                ),
            })
        self.assertEqual(result['status'], 'not_applicable')

    def test_the_line_marker_exempts_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'docs/plans/PLAN-frob.md': '# Frob\n',
                'src/frob.py': (
                    "PATTERN = 'PLAN-*.md'  # audit-ok: plan-reference\n"
                    '# See docs/plans/PLAN-gone.md.\n'
                ),
            })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-gone.md', result['details'])
        self.assertNotIn('PATTERN', result['details'])

    def test_plan_template_is_not_a_plan_reference(self):
        # PLAN-TEMPLATE.md lives at the repository root, not in
        # docs/plans/, and the plan-template audit is what holds it
        # there. Naming it is not a pointer that can rot.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'tools/check.sh': 'grep x PLAN-TEMPLATE.md\n',
            })
        self.assertEqual(result['status'], 'not_applicable')

    def test_an_absolute_url_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'src/frob.py': (
                    '# See https://github.com/shakenfist/ryll/blob/'
                    'develop/docs/plans/PLAN-gone.md.\n'
                ),
            })
        self.assertEqual(result['status'], 'not_applicable')


class AuditScopeIsStatedOnceTest(unittest.TestCase):
    """The three places that say who is audited must agree.

    Scope is written down three times: the matrix in
    .github/workflows/consistency-audit.yml is what actually runs,
    and the in-scope and excluded lists in docs/audits/README.md are
    what a reader is told. Nothing else ties them together, so a
    repository added to the matrix alone is audited while the
    documentation says it is not -- and one dropped from the matrix
    alone silently stops being measured while the documentation says
    it is.

    Reading two of the three means splitting prose on a literal
    phrase, so this class also holds those phrases to their job. See
    bulleted_block() for what a phrase has to keep doing to stay a
    usable anchor.
    """

    root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )

    # Each list below is found by splitting a file on a literal phrase:
    # two sentences of prose and one line of YAML indentation, in files
    # nobody edits with a parser in mind. A start phrase that gets
    # reworded away is loud, because the split raises. An end phrase
    # that gets reworded away is the dangerous one -- the block simply
    # runs on to the end of the file and collects every bullet after
    # it, which the comparisons here can still pass on. So the phrases
    # are named constants and bulleted_block() asserts they still
    # delimit a list of repository names before anything trusts them.
    EXCLUDED_DOC = 'docs/audits/README.md'
    EXCLUDED_START = 'are **excluded**'
    EXCLUDED_END = 'The `actions` repository'
    EXCLUDED_BULLET = '* '

    IN_SCOPE_DOC = 'docs/audits/README.md'
    IN_SCOPE_START = '## In-scope projects'
    IN_SCOPE_END = 'One project is in scope'
    IN_SCOPE_BULLET = '- '

    MATRIX_WORKFLOW = '.github/workflows/consistency-audit.yml'
    MATRIX_START = '        repo:\n'
    MATRIX_BULLET = '          - '

    # What a GitHub repository in any of these lists looks like. The
    # point is not to validate the name but to notice a parse that has
    # started collecting prose: a swallowed paragraph brings back
    # bullets like "The configured version file path must be covered".
    #
    # Anchored at both ends. assertRegex is re.search, so an
    # end-anchor alone matches any sentence closing on a lowercase
    # word -- including that exact example, which is what this guard
    # exists to reject.
    REPO_NAME = re.compile(r'^[a-z0-9][a-z0-9.-]*$')

    def read(self, relative):
        with open(os.path.join(self.root, relative)) as f:
            return f.read()

    def bulleted_block(self, path, start, end, bullet):
        """Return the bullet list delimited by two literal phrases.

        Every assertion here is about the parse rather than the
        content, so that a reworded document fails with the phrase it
        needs to carry rather than with a comparison of two sets of
        repository names that no longer means anything.
        """
        text = self.read(path)
        self.assertEqual(
            text.count(start), 1,
            f'{path} must contain the phrase "{start}" exactly once: '
            f'it is where this suite starts reading the list that '
            f'follows it',
        )
        after = text.split(start, 1)[1]
        self.assertEqual(
            after.count(end), 1,
            f'{path} must contain the phrase "{end}" exactly once '
            f'after "{start}": it is where this suite stops reading, '
            f'and without it the parse runs to the end of the file',
        )
        block = after.split(end, 1)[0]
        # Any heading level, not just '## '. The excluded-projects
        # list this guards sits under a '### ', so a '###' subsection
        # inserted inside the block would have slipped past a check
        # for '## ' alone.
        self.assertIsNone(
            re.search(r'^#{1,6} ', block, re.MULTILINE),
            f'the list after "{start}" in {path} now runs past a '
            f'heading, so "{end}" is no longer the end of it',
        )
        entries = [
            line[len(bullet):].strip() for line in block.splitlines()
            if line.startswith(bullet)
        ]
        self.assertTrue(
            entries,
            f'no "{bullet}" bullets between "{start}" and "{end}" in '
            f'{path}; the list has moved or changed its bullet style',
        )
        for entry in entries:
            self.assertRegex(
                entry, self.REPO_NAME,
                f'"{entry}" was read as a repository name from the '
                f'list after "{start}" in {path}, so the parse is '
                f'picking up something that is not that list',
            )
        return entries

    def matrix_repos(self):
        text = self.read(self.MATRIX_WORKFLOW)
        self.assertEqual(
            text.count(self.MATRIX_START), 1,
            f'{self.MATRIX_WORKFLOW} must contain the matrix key '
            f'"{self.MATRIX_START.strip()}" at exactly one '
            f'indentation this suite recognises',
        )
        block = text.split(self.MATRIX_START, 1)[1]
        repos = []
        for line in block.splitlines():
            if line.startswith(self.MATRIX_BULLET):
                repos.append(line[len(self.MATRIX_BULLET):].strip())
            elif line.strip() and not line.lstrip().startswith('#'):
                break
        self.assertTrue(
            repos,
            f'no matrix entries read from {self.MATRIX_WORKFLOW}; the '
            f'list is indented differently to "{self.MATRIX_BULLET}"',
        )
        for repo in repos:
            self.assertRegex(
                repo, self.REPO_NAME,
                f'"{repo}" was read as a repository name from the '
                f'audit matrix, so the parse has overrun the list',
            )
        return repos

    def documented_in_scope(self):
        return self.bulleted_block(
            self.IN_SCOPE_DOC, self.IN_SCOPE_START, self.IN_SCOPE_END,
            self.IN_SCOPE_BULLET,
        )

    def documented_excluded(self):
        return self.bulleted_block(
            self.EXCLUDED_DOC, self.EXCLUDED_START, self.EXCLUDED_END,
            self.EXCLUDED_BULLET,
        )

    def partially_scoped(self):
        return {
            name for name, overrides
            in audit_check.REPO_OVERRIDES.items()
            if overrides.get('only_checks')
        }

    def test_a_parse_that_overruns_its_list_is_rejected(self):
        """The REPO_NAME guard must fire, not merely exist.

        Reading an assertion cannot distinguish one that holds from one
        that cannot fail, so this hands bulleted_block() the failure it
        was written for. The loud cases are already covered by the
        count assertions: a start or end phrase that vanishes raises
        naming the phrase. The quiet case is an end phrase that has
        drifted further down the page, so the block still terminates
        but now spans a prose list on the way -- with no heading
        crossed, REPO_NAME is the only thing left to notice.

        The bullet used here is the example named in the comment above
        REPO_NAME, which an end-anchored pattern accepted: re.search
        found 'covered' at the end of it and passed.
        """
        overrun = (
            'Two repositories are **excluded** from the conventions:\n'
            '\n'
            '* imago\n'
            '* ryll\n'
            '\n'
            'Some criterion, described in a paragraph that grew a list:\n'
            '\n'
            '* The configured version file path must be covered\n'
            '\n'
            'The `actions` repository is a library of composite actions.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'drifted.md'), 'w') as f:
                f.write(overrun)
            self.root = tmp
            with self.assertRaisesRegex(
                    AssertionError,
                    'The configured version file path must be covered'):
                self.bulleted_block(
                    'drifted.md', self.EXCLUDED_START, self.EXCLUDED_END,
                    self.EXCLUDED_BULLET,
                )

    def test_repo_name_rejects_a_sentence_ending_in_a_word(self):
        # assertRegex is re.search, so this is the whole point of the
        # leading anchor. Kept separate from the parse above because it
        # is the property, not the plumbing: if REPO_NAME ever loses
        # its '^' again, this is the test that says so in one line.
        self.assertIsNone(
            self.REPO_NAME.search(
                'The configured version file path must be covered'),
            'REPO_NAME matched a sentence, so it is not anchored at '
            'the start and cannot notice a parse collecting prose',
        )
        for name in ['shakenfist', 'client-python', 'kerbside-patches']:
            self.assertIsNotNone(
                self.REPO_NAME.search(name),
                f'REPO_NAME no longer matches the repository name '
                f'"{name}"',
            )

    def test_a_subsection_heading_inside_the_block_is_caught(self):
        # The list this guards sits under a '### ', so a guard that
        # only knew '## ' would not have noticed a '###' subsection
        # appearing inside the parsed span.
        drifted = (
            'Two repositories are **excluded** from the conventions:\n'
            '\n'
            '* imago\n'
            '\n'
            '### Some new subsection\n'
            '\n'
            '* ryll\n'
            '\n'
            'The `actions` repository is a library of composite actions.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'drifted.md'), 'w') as f:
                f.write(drifted)
            self.root = tmp
            with self.assertRaisesRegex(AssertionError, 'runs past a heading'):
                self.bulleted_block(
                    'drifted.md', self.EXCLUDED_START, self.EXCLUDED_END,
                    self.EXCLUDED_BULLET,
                )

    def test_the_parse_anchors_still_delimit_their_lists(self):
        # The comparisons below are worth no more than the parses that
        # feed them, and all three parses are anchored to phrases in
        # documents that get rewritten for reasons that have nothing
        # to do with this suite -- the page holding both lists was
        # rewritten wholesale more than once already. Run
        # them here on their own so that a reworded anchor fails as a
        # reworded anchor, naming the phrase and the file, rather than
        # as a mysterious disagreement about which repositories are
        # audited. Each parse asserts its own delimiting; this test is
        # what makes sure all three are exercised even if a comparison
        # below is one day rewritten not to call them.
        self.matrix_repos()
        self.documented_in_scope()
        self.documented_excluded()

    def test_matrix_matches_the_documented_scope(self):
        matrix = set(self.matrix_repos())
        self.assertIn('development', matrix)
        self.assertEqual(
            matrix - self.partially_scoped(),
            set(self.documented_in_scope()),
            'the audit matrix and the in-scope list in '
            'docs/audits/README.md disagree',
        )

    def test_no_audited_repo_is_also_documented_as_excluded(self):
        # A repository scoped to a subset of the checks is the one
        # exception: private-ci is excluded from the conventions but
        # audited for sfui-vendor, and both statements are true.
        overlap = (
            set(self.matrix_repos())
            & set(self.documented_excluded())
            - self.partially_scoped()
        )
        self.assertEqual(
            overlap, set(),
            'docs/audits/README.md lists these as excluded but '
            'the audit matrix runs every check against them',
        )


class ExpensiveLanePathFilterTest(unittest.TestCase):
    """Which expensive lanes are allowed to skip a path filter."""

    LINT_JOB = """  lint:
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
    SCAN_JOB = """  gitleaks:
    runs-on: [self-hosted, vm, debian-13, s]
    steps:
      - run: gitleaks detect
"""
    SKILLSAW_JOB = """  agent-context:
    runs-on: [self-hosted, vm]
    steps:
      - run: pre-commit run skillsaw --all-files
"""

    def _repo(self, tmp, workflows, docs=True):
        wdir = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(wdir)
        if docs:
            os.makedirs(os.path.join(tmp, 'docs'))
            with open(os.path.join(tmp, 'docs', 'index.md'), 'w') as f:
                f.write('# Docs\n')
        for name, content in workflows.items():
            with open(os.path.join(wdir, name), 'w') as f:
                f.write(content)
        return audit_check.check_expensive_lane_path_filter(
            tmp, {'has_workflows_dir': True}
        )

    def test_dedicated_scanner_workflow_needs_no_filter(self):
        # Reading the text a filter would skip is the whole job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'gitleaks.yml': (
                'on:\n  pull_request:\njobs:\n' + self.SCAN_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_agent_context_lint_is_a_content_scanner(self):
        # skillsaw reads the text a filter would skip for the same
        # reason gitleaks does: a prompt aimed at an agent lands in a
        # document as readily as a credential.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'supply-chain.yml': (
                'on:\n  pull_request:\njobs:\n' + self.SKILLSAW_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_a_scanner_and_a_context_lint_together_are_exempt(self):
        # The shape client-python arrived at: one ungated workflow
        # holding the credential scan and the context lint, and
        # nothing else.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'supply-chain.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SCAN_JOB + self.SKILLSAW_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_a_context_lint_does_not_exempt_the_lanes_beside_it(self):
        # Widening the scanner list must not widen the hole the
        # per-job rule exists to close.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SKILLSAW_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('beside it', result['details'])

    def test_a_context_lint_named_only_in_a_comment_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + """  lint:
    # skillsaw runs in the supply chain workflow, not here.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
            )})
        self.assertEqual(result['status'], 'fail')

    def test_a_scanner_does_not_exempt_the_lanes_beside_it(self):
        # shakenfist/actions ran lint, unit tests and the LLM
        # reviewer on ephemeral VMs for every documentation typo,
        # and passed this check, because a gitleaks job sat beside
        # them in the same unfiltered workflow.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SCAN_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertIn('beside it', result['details'])

    def test_a_scanner_named_only_in_a_comment_does_not_count(self):
        # Otherwise one comment in an unrelated lane makes a whole
        # workflow look like a dedicated scanner.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + """  lint:
    # gitleaks-scan.sh is a separate workflow's business.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
            )})
        self.assertEqual(result['status'], 'fail')

    def test_a_filtered_mixed_workflow_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\n    paths-ignore:\n'
                "      - 'docs/**'\njobs:\n"
                + self.SCAN_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_an_unfiltered_lane_with_no_scanner_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'functional-tests.yml': (
                'on:\n  pull_request:\njobs:\n' + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')

    def test_static_runner_lanes_are_not_expensive(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                '  lint:\n'
                '    runs-on: [self-hosted, static]\n'
                '    steps:\n      - run: tox -e pep8\n'
            )})
        self.assertEqual(result['status'], 'pass')


class RunnerLabelParsingTest(unittest.TestCase):
    """The two label parsers must agree about what an expression is.

    They answer different questions -- parse_runner_labels() gives up
    on a line it cannot fully resolve, literal_runner_labels() drops
    what it cannot resolve and keeps the rest -- but they must not
    disagree about which *elements* are unresolvable. Nothing tested
    these before, which is how a refactor changed one of them without
    a failure.
    """

    def test_an_expression_in_a_trailing_comment_is_not_a_label(self):
        # The comment is stripped before the expression test, so a
        # perfectly literal runs-on stays judgeable no matter what its
        # comment mentions. Testing the raw value here would skip the
        # line, and a skip in check_static_runner_tags() reports pass.
        value = '[self-hosted, static, s]  # was ${{ matrix.runner }}'
        self.assertEqual(
            ['self-hosted', 'static', 's'],
            audit_check.parse_runner_labels(value))
        self.assertEqual(
            ['self-hosted', 'static', 's'],
            audit_check.literal_runner_labels(value))

    def test_a_bare_expression_is_unjudgeable(self):
        self.assertIsNone(
            audit_check.parse_runner_labels('${{ matrix.runner }}'))

    def test_one_expression_element_makes_the_line_unjudgeable(self):
        self.assertIsNone(audit_check.parse_runner_labels(
            "[self-hosted, '${{ matrix.os }}', s]"))

    def test_a_comma_inside_an_expression_is_not_a_separator(self):
        # Splitting on it would leave fragments which no longer carry
        # the '${{' marking them unresolvable, so literal_runner_labels
        # would take them for labels -- and a fragment which happened
        # to read 'm' would excuse a sizeless job.
        value = ("[self-hosted, vm, "
                 "'${{ format('{0},{1}', matrix.a, matrix.b) }}']")
        self.assertEqual(
            ['self-hosted', 'vm'],
            audit_check.literal_runner_labels(value))
        self.assertIsNone(audit_check.parse_runner_labels(value))


class VmRunnerSizeTest(unittest.TestCase):
    """Every 'vm' runs-on has to name a size, and 'xs' is an answer."""

    # The phrases delimiting the size list in the specification. Named
    # rather than inlined so a reword fails with an explanation.
    SIZE_SENTENCE_START = '* Sizes are '
    SIZE_SENTENCE_END = 'variants.'

    def _repo(self, tmp, workflows):
        wdir = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(wdir)
        for name, content in workflows.items():
            with open(os.path.join(wdir, name), 'w') as f:
                f.write(content)
        return audit_check.check_vm_runner_size(
            tmp, {'has_workflows_dir': True}
        )

    def _job(self, runs_on):
        return (
            'on:\n  pull_request:\njobs:\n  build:\n'
            '    runs-on: %s\n'
            '    steps:\n      - run: true\n' % runs_on
        )

    def test_a_sized_vm_job_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12, s]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_sizeless_vm_job_is_a_finding(self):
        # The defect this check exists for: no size element, so the
        # conductor falls back to xs and nobody chose it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])

    def test_a_vm_job_with_no_os_label_is_still_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm]')})
        self.assertEqual('fail', result['status'])

    def test_xs_counts_as_naming_a_size(self):
        # The rule is that the size is chosen, not that it is large.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12, xs]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_bigdisk_variants_count_as_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, 'debian-13', 'xl-bigdisk']")})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_literal_size_beside_a_matrix_expression_passes(self):
        # kerbside-patches' shape: the OS comes from the matrix, the
        # size is written literally beside it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, '${{ matrix.test.runs_on }}', 'xl']")})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_matrix_expression_alone_does_not_excuse_a_missing_size(self):
        # The sibling job in the same file writes the size literally,
        # so an expression here is not evidence a size arrives -- and
        # skipping the line would hide a real sizeless deploy job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, '${{ matrix.test.runs_on }}']")})
        self.assertEqual('fail', result['status'])

    def test_static_jobs_are_not_in_scope(self):
        # A static runner must name no size; that is the complementary
        # check's business, and this one must not contradict it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, static]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_the_offending_line_is_named(self):
        # A finding has to say where, because the fix is per-line.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'a.yml': self._job('[self-hosted, vm, debian-12]'),
                'b.yml': self._job('[self-hosted, vm, debian-12, m]'),
            })
        self.assertEqual('fail', result['status'])
        self.assertIn('a.yml:5', result['details'])
        self.assertNotIn('b.yml', result['details'])

    def test_a_repository_without_workflows_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_check.check_vm_runner_size(
                tmp, {'has_workflows_dir': False})
        self.assertEqual('not_applicable', result['status'])

    def test_a_trailing_comment_does_not_hide_the_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]  # sized later')})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])

    def test_every_offending_line_is_reported(self):
        # The fix is per-line, so a file with two sizeless jobs has to
        # name both -- stopping at the first would hide the second
        # until the next run.
        job = ('    runs-on: [self-hosted, vm, debian-12]\n'
               '    steps:\n      - run: true\n')
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n' + job +
                '  deploy:\n' + job)})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])
        self.assertIn('ci.yml:9', result['details'])

    def test_the_offender_names_the_labels_it_found(self):
        # Matching check_static_runner_tags(): the issue body is what
        # somebody fixing this reads, and the labels say which job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        self.assertIn('(self-hosted, vm, debian-12)', result['details'])

    def test_the_remediation_names_every_size_accepted(self):
        # A job which needs the disk must be able to find its answer
        # in the issue body, not only in the specification.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        for label in audit_check.VM_SIZE_LABELS:
            self.assertIn(label, result['details'])

    def test_an_audit_ok_marker_exempts_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]  '
                '# audit-ok: vm-runner-size, sized by the caller')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_an_audit_ok_marker_on_the_line_above_exempts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n'
                '    # audit-ok: vm-runner-size, sized by the caller\n'
                '    runs-on: [self-hosted, vm, debian-12]\n'
                '    steps:\n      - run: true\n')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_block_sequence_runs_on_is_not_examined(self):
        # A documented limitation rather than a behaviour we want:
        # RUNS_ON_RE needs a value on the same line, so this shape is
        # invisible to the check. Nothing in scope writes one. If that
        # changes, this test is the place the decision is recorded.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n'
                '    runs-on:\n      - self-hosted\n      - vm\n'
                '      - debian-12\n'
                '    steps:\n      - run: true\n')})
        self.assertEqual('pass', result['status'])

    def test_the_size_vocabulary_matches_the_specification(self):
        # VM_SIZE_LABELS is a copy of CI_SIZES in shakenfist/private-ci
        # which this repository cannot reach, so the least it can do is
        # keep its own two copies in step.
        spec = os.path.join(
            REPO_ROOT, 'docs', 'audits', 'workflow-standards.md')
        with open(spec) as f:
            content = f.read()
        start = content.find(self.SIZE_SENTENCE_START)
        self.assertNotEqual(
            -1, start,
            f'{self.SIZE_SENTENCE_START!r} no longer introduces the '
            f'size list in workflow-standards.md')
        end = content.find(self.SIZE_SENTENCE_END, start)
        self.assertNotEqual(
            -1, end,
            f'{self.SIZE_SENTENCE_END!r} no longer ends the size list '
            f'in workflow-standards.md')
        documented = set(re.findall(r'`([^`]+)`', content[start:end]))
        self.assertEqual(set(audit_check.VM_SIZE_LABELS), documented)


class WorkflowJobBlocksTest(unittest.TestCase):
    def test_jobs_are_split_at_top_level_keys(self):
        blocks = audit_check.workflow_job_blocks(
            'name: CI\n'
            'on:\n  pull_request:\n'
            'jobs:\n'
            '  lint:\n    runs-on: a\n'
            '  test:\n    runs-on: b\n'
        )
        self.assertEqual([name for name, _ in blocks], ['lint', 'test'])
        self.assertIn('runs-on: a', blocks[0][1])
        self.assertIn('runs-on: b', blocks[1][1])

    def test_keys_outside_jobs_are_not_jobs(self):
        # 'pull_request:' under 'on:' is indented exactly like a job
        # key, so a naive split would invent a job called
        # pull_request and decide the workflow is not all scanners.
        blocks = audit_check.workflow_job_blocks(
            'on:\n  pull_request:\n'
            'jobs:\n  lint:\n    runs-on: a\n'
        )
        self.assertEqual([name for name, _ in blocks], ['lint'])

    def test_a_workflow_with_no_jobs_is_not_a_scanner(self):
        self.assertFalse(
            audit_check.is_dedicated_scanner_workflow('on:\n  push:\n')
        )


class RepoOverridesTest(unittest.TestCase):
    def test_actions_repo_properties(self):
        # The actions repository carries Python helper scripts but has
        # nothing to package, and keeps "main" because every consumer
        # pins to @main.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'actions'
        )
        self.assertTrue(props['not_python'])
        self.assertIn('@main', props['default_branch_exception'])

    def test_development_audits_itself(self):
        # development holds the audit tooling and is audited by it.
        # Its Python is never packaged, and it publishes no releases,
        # so it has no release branch for "develop" to be distinct
        # from -- but the exemption has to be a stated reason, not an
        # absence from the matrix.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'development'
        )
        self.assertTrue(props['not_python'])
        self.assertIn('releases', props['default_branch_exception'])

    def test_ordinary_repo_has_no_default_branch_exception(self):
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['default_branch_exception'], '')

    def test_shakenfist_excludes_imported_docs(self):
        # docs/components/ is an automated import of the other
        # repositories' documentation directories.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'shakenfist'
        )
        self.assertEqual(
            props['doc_content_excludes'], ['docs/components/']
        )

    def test_ordinary_repo_has_no_doc_content_excludes(self):
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['doc_content_excludes'], [])

    def test_ordinary_repo_is_scoped_to_no_checks(self):
        # An empty only_checks means the whole audit applies, so the
        # override cannot narrow a repository by accident.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['only_checks'], [])

    def test_private_ci_is_scoped_to_the_sfui_check(self):
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'private-ci'
        )
        self.assertEqual(props['only_checks'], ['sfui-vendor'])


class CheckScopeTest(unittest.TestCase):
    """The only_checks scoping in run_all_checks."""

    def _ids(self):
        return [
            check_id for check_id, _ in audit_check.check_calls(
                tempfile.mkdtemp(), {}, 'occystrap', 'shakenfist'
            )
        ]

    def test_every_scheduled_id_is_a_known_check(self):
        # A typo in the id table would make a check unschedulable
        # while still reporting a plausible looking result, so the
        # table has to agree with the issue title map. ISSUE_TITLES is
        # that map itself rather than a copy of it: audit-manage-issues
        # reads it as .get(check_id, check_id), so an id missing from it
        # files under the bare check id and orphans every open issue for
        # that check across the fleet, and audit-update-docs subscripts
        # it directly, so the same omission raises KeyError during docs
        # regeneration.
        #
        # AUDIT_METADATA is the third corner of the same triangle:
        # audit-update-docs iterates it to emit one compliance section
        # per check, and audit-manage-issues reads it for the spec link
        # in each filed issue. Asserting both closes the loop, so a new
        # check cannot be scheduled while missing from either map.
        ids = self._ids()
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertEqual(
            sorted(ids), sorted(ISSUE_TITLES.keys())
        )
        self.assertEqual(
            sorted(ids), sorted(AUDIT_METADATA.keys())
        )

    def test_scoped_repo_runs_only_its_check(self):
        # private-ci is scoped to sfui-vendor. Every other check must
        # be reported not_applicable with the scoping reason, and must
        # not have run: a check that ran would have written its own
        # details, and several of them would reach for the network.
        with tempfile.TemporaryDirectory() as tmp:
            results = audit_check.run_all_checks(
                tmp, 'private-ci', 'shakenfist'
            )

        reason = 'private-ci is audited for sfui-vendor only'
        by_id = {c['id']: c for c in results['checks']}
        self.assertEqual(len(by_id), len(ISSUE_TITLES))

        for check_id, check in by_id.items():
            if check_id == 'sfui-vendor':
                self.assertNotEqual(check['details'], reason)
                continue
            self.assertEqual(check['status'], 'not_applicable')
            self.assertEqual(check['details'], reason)

        # Nothing is dropped from the results, because a check missing
        # from the JSON renders as "unknown" in the docs/audits/ tables.
        self.assertEqual(
            results['summary']['total'], len(ISSUE_TITLES)
        )
        self.assertEqual(results['summary']['fail'], 0)

    def test_unscoped_repo_schedules_everything(self):
        # The scoping is opt in: with no override, no check is
        # replaced by the not_applicable stand-in.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertFalse(props['only_checks'])


class SfuiVendorTest(unittest.TestCase):
    """Exercise check_sfui_vendor against fixture repositories.

    The canonical fixture is a tiny git repo carrying a stand-in
    tools/vendor.sh that honours the real script's --check contract
    (diff the distributable files, exit non-zero on difference); the
    real script lives in shakenfist/sfui and is not vendored here.
    """

    TOKENS = ':root { --sf-bg: #000; }\n'

    def _git(self, repo, *args):
        subprocess.run(
            [
                'git', '-C', repo,
                '-c', 'user.name=test',
                '-c', 'user.email=test@example.com',
            ] + list(args),
            check=True, capture_output=True,
        )

    def _head(self, repo):
        return subprocess.run(
            ['git', '-C', repo, 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _make_canonical(self, tmp):
        repo = os.path.join(tmp, 'canonical')
        os.makedirs(os.path.join(repo, 'tools'))
        with open(os.path.join(repo, 'tokens.css'), 'w') as f:
            f.write(self.TOKENS)
        with open(os.path.join(repo, 'tools', 'vendor.sh'), 'w') as f:
            f.write(
                '#!/bin/bash\n'
                'src="$(cd "$(dirname "$0")/.." && pwd)"\n'
                '[ "$1" = "--check" ] || exit 2\n'
                'diff -u "$2/tokens.css" "$src/tokens.css"\n'
            )
        self._git(repo, 'init', '--quiet')
        self._git(repo, 'add', '-A')
        self._git(repo, 'commit', '--quiet', '-m', 'initial')
        return repo

    def _make_consumer(self, tmp, sha, tokens=None):
        consumer = os.path.join(tmp, 'consumer')
        vendored = os.path.join(consumer, 'static', 'sfui')
        os.makedirs(vendored)
        with open(os.path.join(vendored, 'tokens.css'), 'w') as f:
            f.write(tokens if tokens is not None else self.TOKENS)
        with open(os.path.join(vendored, '.sfui-commit'), 'w') as f:
            f.write(sha + '\n')
        return consumer

    def test_not_applicable_without_sfui_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            consumer = os.path.join(tmp, 'consumer')
            os.makedirs(consumer)
            result = audit_check.check_sfui_vendor(consumer, {})
            self.assertEqual(result['status'], 'not_applicable')

    def test_verbatim_copy_at_head_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, self._head(canonical))
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'pass')
            self.assertIn('verbatim', result['details'])

    def test_edited_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(
                tmp, self._head(canonical),
                tokens=':root { --sf-bg: #fff; }\n',
            )
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn('edited in place', result['details'])

    def test_copy_behind_canonical_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, self._head(canonical))
            with open(
                os.path.join(canonical, 'tokens.css'), 'a'
            ) as f:
                f.write('/* a change the consumer lacks */\n')
            self._git(canonical, 'commit', '--quiet', '-am', 'more')
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn('behind canonical', result['details'])

    def test_unknown_commit_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, '0' * 40)
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn(
                'not in the canonical repository', result['details']
            )

    def test_malformed_stamp_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, 'not-a-sha')
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn(
                'does not contain a commit sha', result['details']
            )


class MergeQueueConfigTest(unittest.TestCase):
    def _rule(self, **params):
        return {'type': 'merge_queue', 'parameters': params}

    def test_no_merge_queue_rule_returns_none(self):
        self.assertIsNone(audit_check.evaluate_merge_queue_rules([]))
        self.assertIsNone(audit_check.evaluate_merge_queue_rules(
            [{'type': 'deletion'}, {'type': 'non_fast_forward'}]
        ))

    def test_serialized_queue_passes(self):
        problems = audit_check.evaluate_merge_queue_rules([
            {'type': 'pull_request'},
            self._rule(
                max_entries_to_build=1, min_entries_to_merge=1,
                max_entries_to_merge=5,
                min_entries_to_merge_wait_minutes=5,
            ),
        ])
        self.assertEqual(problems, [])

    def test_speculative_stacking_fails(self):
        problems = audit_check.evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=2, min_entries_to_merge=1),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('max_entries_to_build is 2', problems[0])

    def test_batched_merging_fails(self):
        problems = audit_check.evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=1, min_entries_to_merge=2),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('min_entries_to_merge is 2', problems[0])

    def test_missing_parameters_flags_both(self):
        problems = audit_check.evaluate_merge_queue_rules([
            {'type': 'merge_queue'},
        ])
        self.assertEqual(len(problems), 2)


class RenovatePreCommitManagerTest(unittest.TestCase):
    """The pre-commit manager is opt-in, so its absence is a finding."""

    REMOTE_HOOKS = (
        'repos:\n'
        '  - repo: https://github.com/rhysd/actionlint\n'
        '    rev: v1.7.12\n'
        '    hooks:\n'
        '      - id: actionlint\n'
    )

    LOCAL_HOOKS = (
        'repos:\n'
        '  - repo: local\n'
        '    hooks:\n'
        '      - id: rust-check\n'
        '        entry: ./scripts/check-rust.sh\n'
        '        language: script\n'
    )

    def _repo(self, tmp, renovate, pre_commit=None):
        os.makedirs(os.path.join(tmp, '.github', 'workflows'))
        open(
            os.path.join(tmp, '.github', 'workflows', 'renovate.yml'), 'w'
        ).close()
        with open(os.path.join(tmp, 'renovate.json'), 'w') as f:
            f.write(json.dumps(renovate))
        if pre_commit is not None:
            with open(
                os.path.join(tmp, '.pre-commit-config.yaml'), 'w'
            ) as f:
                f.write(pre_commit)
        return audit_check.check_renovate(tmp, {})

    def test_remote_hooks_without_the_manager_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {}, self.REMOTE_HOOKS)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pre-commit manager', result['details'])

    def test_explicit_manager_block_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(
                tmp, {'pre-commit': {'enabled': True}}, self.REMOTE_HOOKS
            )
        self.assertEqual(result['status'], 'pass')

    def test_enabled_managers_list_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(
                tmp,
                {'enabledManagers': ['cargo', 'pre-commit']},
                self.REMOTE_HOOKS,
            )
        self.assertEqual(result['status'], 'pass')

    def test_preset_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(
                tmp, {'extends': [':enablePreCommit']}, self.REMOTE_HOOKS
            )
        self.assertEqual(result['status'], 'pass')

    def test_manager_disabled_explicitly_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(
                tmp, {'pre-commit': {'enabled': False}}, self.REMOTE_HOOKS
            )
        self.assertEqual(result['status'], 'fail')

    def test_no_pre_commit_config_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {})
        self.assertEqual(result['status'], 'pass')

    def test_local_only_hooks_pass(self):
        # A repo: local hook runs a script from the tree and carries no
        # revision, so there is nothing for renovate to bump.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {}, self.LOCAL_HOOKS)
        self.assertEqual(result['status'], 'pass')

    def test_missing_files_still_reported_first(self):
        # The manager check must not mask the more basic finding.
        with tempfile.TemporaryDirectory() as tmp:
            with open(
                os.path.join(tmp, '.pre-commit-config.yaml'), 'w'
            ) as f:
                f.write(self.REMOTE_HOOKS)
            result = audit_check.check_renovate(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('renovate.json', result['missing'])


class PlanIndexTest(unittest.TestCase):
    """docs/plans/index.md layout, ordering, statuses and coverage."""

    HEADER = (
        '| Date | Plan | Intent | Status |\n'
        '|------|------|--------|--------|\n'
    )

    def _check(self, plans=None, index=None):
        """Run the check over a docs/plans/ built from the arguments.

        plans is a list of file names to create; index is the content
        of index.md, or None to leave it out entirely.
        """
        with tempfile.TemporaryDirectory() as tmp:
            plans_dir = os.path.join(tmp, 'docs', 'plans')
            os.makedirs(plans_dir)
            for name in plans or []:
                with open(os.path.join(plans_dir, name), 'w') as f:
                    f.write('# A plan\n')
            if index is not None:
                with open(os.path.join(plans_dir, 'index.md'), 'w') as f:
                    f.write(index)
            return audit_check.check_plan_index(tmp, {})

    def test_not_applicable_without_plans_directory(self):
        result = audit_check.check_plan_index('/nonexistent', {})
        self.assertEqual(result['status'], 'not_applicable')

    def test_not_applicable_with_no_plans_and_no_index(self):
        self.assertEqual(self._check()['status'], 'not_applicable')

    def test_missing_index_with_plans_fails(self):
        result = self._check(plans=['PLAN-thing.md'], index=None)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('index.md is missing', result['details'])

    def test_well_formed_index_passes(self):
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-two.md'],
            index=(
                '# Plans index\n\n' + self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
                '| 2026-02-01 | [Two](PLAN-two.md) | Do two | In progress |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_phase_plans_need_no_index_row(self):
        # Phase files are named after their master plan and tracked in
        # it, so the index carries the master plan alone.
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-one-phase-01-start.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_plan_first_columns_fail(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Plan | Phase | Status |\n'
                '|------|-------|--------|\n'
                '| [One](PLAN-one.md) | 1. Start | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('not led by Date then Plan', result['details'])

    def test_wrong_columns_suppress_date_and_status_findings(self):
        # Reading a date out of a column that holds something else
        # would bury the finding that actually needs fixing.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Plan | Phase | Status |\n'
                '|------|-------|--------|\n'
                '| [One](PLAN-one.md) | 1. Start | Whenever |\n'
            ),
        )
        self.assertNotIn('date', result['details'])
        self.assertNotIn('vocabulary', result['details'])

    def test_rows_out_of_date_order_fail(self):
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-two.md'],
            index=(
                self.HEADER +
                '| 2026-02-01 | [Two](PLAN-two.md) | Do two | Complete |\n'
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('out of date order', result['details'])
        self.assertIn('One', result['details'])

    def test_dates_are_not_compared_across_tables(self):
        # Each table is its own chronological run, so a second table
        # starting earlier than the first ended is not a finding.
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-two.md'],
            index=(
                '## Master plans\n\n' + self.HEADER +
                '| 2026-02-01 | [Two](PLAN-two.md) | Do two | Complete |\n'
                '\n## Standalone plans\n\n' + self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_malformed_date_fails(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| April 2026 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('YYYY-MM-DD', result['details'])

    def test_status_outside_the_vocabulary_fails(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                'Code landed; awaiting verification |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('vocabulary', result['details'])

    def test_qualified_status_fails(self):
        # The whole point of the vocabulary: "Complete (phases 1-5,
        # 2026-08-15): every merge to develop..." is how a status
        # column turns into prose.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                'Complete (phases 1-5) |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('vocabulary', result['details'])

    def test_status_matching_is_case_insensitive(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                'In Progress |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_decorated_status_passes(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | '
                '**Complete** |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_table_without_a_status_column_is_fine(self):
        # Standalone plan listings carry no status, and that is not a
        # defect: they are registered, just not tracked.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Date | Plan | Intent |\n'
                '|------|------|--------|\n'
                '| 2026-01-01 | [One](PLAN-one.md) | Do one |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_unregistered_master_plan_fails(self):
        result = self._check(
            plans=['PLAN-one.md', 'PLAN-orphan.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PLAN-orphan.md', result['details'])

    def test_bullet_list_index_fails(self):
        result = self._check(
            plans=['PLAN-one.md'],
            index='# Plans\n\n* [One](PLAN-one.md).\n',
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no plan table', result['details'])
        # Linked plans still count as registered, so the only finding
        # is the missing table.
        self.assertNotIn('not listed in the index', result['details'])

    def test_table_without_plan_rows_is_ignored(self):
        # An index may explain itself with a table that lists no
        # plans. Judging its columns would be a finding nobody could
        # act on.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                '| Term | Meaning |\n'
                '|------|---------|\n'
                '| Blocked | Waiting on something else |\n'
                '\n' + self.HEADER +
                '| 2026-01-01 | [One](PLAN-one.md) | Do one | Complete |\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_link_free_row_is_not_read_as_a_header(self):
        # A header is the row the separator underlines. A data row
        # with no link must not reset the column mapping, or the rows
        # after it go unchecked.
        result = self._check(
            plans=['PLAN-one.md'],
            index=(
                self.HEADER +
                '| 2026-01-01 | Not yet written up | Do it | Complete |\n'
                '| 2026-02-01 | [One](PLAN-one.md) | Do one | Whenever |\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('vocabulary', result['details'])
        self.assertNotIn('not led by Date then Plan', result['details'])


class PlanStatusVocabularyBlockTest(unittest.TestCase):
    def test_canonical_block_lists_exactly_the_enforced_statuses(self):
        # The block is the wording repositories are handed and
        # PLAN_STATUSES is what the audit enforces. If they drift,
        # projects get told one thing and measured against another.
        canonical = audit_check.load_canonical_block(
            'plan-status-vocabulary'
        )
        self.assertIsNotNone(canonical)
        _, text = canonical
        documented = re.findall(r'^- `([^`]+)`', text, re.MULTILINE)
        self.assertEqual(
            sorted(documented), sorted(audit_check.PLAN_STATUSES)
        )

    def test_plan_templates_must_carry_the_block(self):
        self.assertIn(
            'plan-status-vocabulary', audit_check.PLAN_TEMPLATE_BLOCKS
        )


# The rules of plan-push-audit-phase that this repository reads by
# phrase, per the "a named constant and an assertion" rule in
# AGENTS.md. Each is the shortest fragment that cannot survive the
# rule being dropped: a reword should keep them, a retraction should
# not. Grep for this list before rewording the canonical block.
PUSH_AUDIT_BLOCK_RULES = {
    'where a table records it': '`Merged` column',
    'where prose records it': '`Merged:` line',
    'not in the status cell': '`Status` column keeps',
    'one commit is not a range': 'only ever enough when it is a merge commit',
    'complete plans are not reopened': 'not reopened to acquire',
}


class PushAuditPhaseBlockTest(unittest.TestCase):
    """The canonical block is the only statement of where a phase's
    landing commit is recorded, and of what counts as a range.

    Nothing mechanical reads a plan's Execution table, so if a later
    revision renames the column or shortens away one of these rules,
    the first thing to notice would be a sweep already halfway
    through thirty-six plans. That the block is required of every
    PLAN-TEMPLATE.md is asserted by PlanTemplateTest, which owns that
    invariant.
    """

    def test_canonical_block_still_states_each_rule(self):
        canonical = audit_check.load_canonical_block(
            'plan-push-audit-phase'
        )
        self.assertIsNotNone(canonical)
        _, text = canonical
        # Collapse whitespace first. These are prose wrapped at
        # seventy columns, so matching raw text would fail on a
        # reflow that changed no meaning -- and a test that fails on
        # cosmetic reflow teaches people to edit the test, which is
        # the reflex this tripwire exists to prevent.
        flat = ' '.join(text.split())
        for rule, phrase in PUSH_AUDIT_BLOCK_RULES.items():
            with self.subTest(rule=rule):
                self.assertIn(phrase, flat)


class PlanTemplateTest(unittest.TestCase):
    """Tests for check_plan_template.

    The check had no direct coverage at all, which matters once
    PLAN_TEMPLATE_BLOCKS gains an entry: adding a name to that list
    marks every repository carrying the previous set non-compliant
    on the next daily run, and nothing asserted either the list's
    contents or that the check reads it.
    """

    def setUp(self):
        self._blocks = tempfile.TemporaryDirectory()
        self.addCleanup(self._blocks.cleanup)
        self.blocks = {}
        for name in audit_check.PLAN_TEMPLATE_BLOCKS:
            block = (
                f'<!-- shared-block: {name} v1 -->\n'
                f'Canonical {name} wording.\n'
                '<!-- shared-block-end -->\n'
            )
            self.blocks[name] = block
            with open(
                os.path.join(self._blocks.name, f'{name}.md'), 'w'
            ) as f:
                f.write(block)

    def _check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
                with open(os.path.join(tmp, name), 'w') as f:
                    f.write(content)
            return audit_check.check_plan_template(
                tmp, {}, blocks_dir=self._blocks.name
            )

    def _template(self, omit=None):
        return '# Plan template\n\n' + '\n'.join(
            block for name, block in self.blocks.items()
            if name != omit
        )

    def test_not_applicable_without_template(self):
        self.assertEqual(self._check({})['status'], 'not_applicable')

    def test_all_blocks_passes(self):
        result = self._check({'PLAN-TEMPLATE.md': self._template()})
        self.assertEqual(result['status'], 'pass')

    def test_missing_push_audit_phase_block_fails(self):
        result = self._check({
            'PLAN-TEMPLATE.md': self._template(
                omit='plan-push-audit-phase'),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block plan-push-audit-phase',
            result['details'],
        )

    def test_stale_push_audit_phase_block_fails(self):
        # Take the marker from the fixture setUp built rather than
        # naming a version. These fixtures are deliberately
        # independent of templates/shared-blocks/, so a literal here
        # would be a version this test does not otherwise track.
        marker = self.blocks['plan-push-audit-phase'].splitlines()[0]
        stale = self._template().replace(
            marker,
            '<!-- shared-block: plan-push-audit-phase v0 -->',
        )
        result = self._check({'PLAN-TEMPLATE.md': stale})
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'shared block plan-push-audit-phase is stale',
            result['details'],
        )

    def test_push_audit_phase_is_required(self):
        # The line of this change with the widest fleet consequence:
        # naming the block here is what marks four currently
        # compliant repositories non-compliant.
        self.assertIn(
            'plan-push-audit-phase', audit_check.PLAN_TEMPLATE_BLOCKS
        )

    def test_every_required_block_has_a_canonical_copy(self):
        # A name in the list with no file under
        # templates/shared-blocks would report every repository as
        # carrying an unknown block.
        for name in audit_check.PLAN_TEMPLATE_BLOCKS:
            with self.subTest(block=name):
                self.assertTrue(os.path.exists(os.path.join(
                    REPO_ROOT, 'templates', 'shared-blocks',
                    f'{name}.md')))


class ConsoleLoggingTest(unittest.TestCase):
    """The console-logging check.

    The two false positives these guard against are the ones the
    first version of the check actually produced: the file that
    *defines* setup_console(), and the twenty-four occystrap modules
    that call it at import time without being an entry point.
    """

    PYPROJECT = (
        '[project]\n'
        'name = "thing"\n'
        '\n'
        '[project.scripts]\n'
        'thing = "thing.main:cli"\n'
    )

    COMPLIANT = (
        'from shakenfist_utilities import logs\n'
        'import logging\n'
        '\n'
        'LOG = logs.setup_console(__name__)\n'
        'logging.basicConfig(level=logging.INFO)\n'
        'logging.getLogger(__name__).propagate = False\n'
    )

    # Non-compliant on purpose. A test that a declaration spelling is
    # read wants a *finding* out of the file it names: not_applicable
    # and pass are both what you get when the file was never opened.
    NO_BASIC_CONFIG = (
        'from shakenfist_utilities import logs\n'
        'LOG = logs.setup_console(__name__)\n'
        'LOG.propagate = False\n'
    )

    def _check(self, files, pyproject=None):
        with tempfile.TemporaryDirectory() as tmp:
            if pyproject is not False:
                with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                    f.write(pyproject or self.PYPROJECT)
            for path, content in files.items():
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write(content)
            return audit_check.check_console_logging(tmp, {})

    def test_a_commented_out_basic_config_does_not_satisfy_it(self):
        # The state of any file somebody was debugging, and the exact
        # misconfiguration this check exists to catch. Grepping the
        # file rather than the code reported it as compliant.
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'LOG = logs.setup_console(__name__)\n'
            'LOG.propagate = False\n'
            '# logging.basicConfig(level=logging.INFO)\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('basicConfig', result['details'])

    def test_a_marker_in_a_docstring_does_not_exempt(self):
        # The mirror of the masking defect above: that half stopped a
        # docstring making a file a caller, this half stops one making
        # it exempt. Prose about the marker is not the marker.
        result = self._check({'thing/main.py': (
            '"""We do not use audit-ok: console-logging here."""\n'
            + self.NO_BASIC_CONFIG
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_a_marker_in_a_string_constant_does_not_exempt(self):
        result = self._check({'thing/main.py': (
            'DOC = "audit-ok: console-logging"\n' + self.NO_BASIC_CONFIG
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_a_marker_in_a_commented_out_string_still_exempts(self):
        # The marker view keeps comments and blanks strings, so a
        # quote inside a comment must not open a literal and swallow
        # the marker that follows it.
        result = self._check({'thing/main.py': (
            "# don't reconfigure: audit-ok: console-logging\n"
            + self.NO_BASIC_CONFIG
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_an_unresolved_entry_point_withholds_the_pass(self):
        # A mixed layout reported pass on the entry points it could
        # find and dropped the one it could not, which is the same
        # clean bill for a file nobody opened that the check refuses
        # when nothing resolves at all.
        result = self._check(
            {'thing/main.py': self.COMPLIANT},
            pyproject=(
                '[project]\n'
                'name = "thing"\n'
                '\n'
                '[project.scripts]\n'
                'thing = "thing.main:cli"\n'
                'lost = "elsewhere.cli:main"\n'
            ),
        )
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])
        self.assertIn('elsewhere.cli', result['details'])

    def test_an_unresolved_entry_point_is_named_in_a_failure(self):
        # A demonstrated violation stays a failure, but the entry
        # point nobody could look at is still named: fixing the one
        # that was found does not make the report complete.
        result = self._check(
            {'thing/main.py': self.NO_BASIC_CONFIG},
            pyproject=(
                '[project]\n'
                'name = "thing"\n'
                '\n'
                '[project.scripts]\n'
                'thing = "thing.main:cli"\n'
                'lost = "elsewhere.cli:main"\n'
            ),
        )
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('basicConfig', result['details'])
        self.assertIn('elsewhere.cli', result['details'])

    def test_a_docstring_mention_does_not_make_it_a_caller(self):
        # And the same defect pointing the other way: a fleet issue
        # filed against a module whose only setup_console( is prose
        # saying it deliberately does not call one.
        result = self._check({'thing/main.py': (
            '"""Logging is configured by the caller, not by\n'
            'logs.setup_console( ) here."""\n'
            'def cli():\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_an_earlier_setup_console_does_not_take_the_receiver(self):
        # Receivers used to come from whichever call was first, so an
        # entry point configuring somebody else's logger before its
        # own was failed for a line it has.
        result = self._check({'thing/main.py': (
            'import logging\n'
            'from shakenfist_utilities import logs\n'
            "OTHER = logs.setup_console('other')\n"
            'LOG = logs.setup_console(__name__)\n'
            'LOG.propagate = False\n'
            'logging.basicConfig(level=logging.INFO)\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_silencing_someone_elses_logger_is_still_not_enough(self):
        # The other half of the same change: accepting *any* call's
        # receiver would let this stand in for silencing your own.
        result = self._check({'thing/main.py': (
            'import logging\n'
            'from shakenfist_utilities import logs\n'
            "OTHER = logs.setup_console('other')\n"
            'LOG = logs.setup_console(__name__)\n'
            'OTHER.propagate = False\n'
            'logging.basicConfig(level=logging.INFO)\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('propagate', result['details'])

    def test_an_attribute_receiver_is_read(self):
        # self.LOG = setup_console(...) is silenced by writing
        # self.LOG.propagate, which the whole-name lookbehind
        # rejected when only the bare name had been captured.
        result = self._check({'thing/main.py': (
            'import logging\n'
            'from shakenfist_utilities import logs\n'
            'class App:\n'
            '    def __init__(self):\n'
            '        self.LOG = logs.setup_console(__name__)\n'
            '        self.LOG.propagate = False\n'
            'logging.basicConfig(level=logging.INFO)\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_declared_but_unresolved_entry_points_are_named(self):
        # A repository laying its packages out under lib/ declares
        # entry points and resolves none of them. Reporting that as
        # "none declared" is a false statement about a file nobody
        # looked at.
        result = self._check({'lib/thing/main.py': self.NO_BASIC_CONFIG})
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('resolved to no file', result['details'])
        self.assertIn('thing.main', result['details'])

    def test_a_malformed_scripts_table_does_not_abort_the_run(self):
        # Valid TOML, invalid PEP 621. The AttributeError this raised
        # propagated out of run_checks and cost the repository every
        # other check as well. Reported rather than dropped: a
        # declaration nobody could read is not the same fact as no
        # declaration, and saying the second is a clean bill for a
        # file nobody looked at.
        for pyproject, expected in (
            ('[project]\nname = "thing"\nscripts = "thing.main:cli"\n',
             '[project.scripts] is a str'),
            ('[project]\nname = "thing"\nentry-points = "oops"\n',
             '[project.entry-points] is a str'),
            ('project = "x"\n', '[project] is a str'),
        ):
            with self.subTest(pyproject=pyproject):
                result = self._check({}, pyproject=pyproject)
                self.assertEqual(result['status'], 'not_applicable')
                if expected:
                    self.assertIn(expected, result['details'])

    def test_a_non_string_target_is_reported_not_dropped(self):
        result = self._check(
            {}, pyproject='[project]\nname = "thing"\n'
                          '[project.scripts]\nthing = 3\n')
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('does not name a module', result['details'])

    def test_compliant_entry_point_passes(self):
        result = self._check({'thing/main.py': self.COMPLIANT})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_missing_basic_config_fails(self):
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'LOG = logs.setup_console(__name__)\n'
            'LOG.propagate = False\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('basicConfig', result['details'])

    def test_missing_propagate_fails(self):
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'import logging\n'
            'LOG = logs.setup_console(__name__)\n'
            'logging.basicConfig(level=logging.INFO)\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('propagate', result['details'])

    def test_no_pyproject_is_not_applicable(self):
        result = self._check({'thing/main.py': self.COMPLIANT},
                             pyproject=False)
        self.assertEqual(result['status'], 'not_applicable')

    def test_no_console_scripts_is_not_applicable(self):
        result = self._check(
            {'thing/main.py': self.COMPLIANT},
            pyproject='[project]\nname = "thing"\n',
        )
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('No console or GUI entry points', result['details'])

    def test_a_gui_script_is_an_entry_point(self):
        # [project.scripts] is what the fleet declares today, but a
        # gui-scripts table names an entry point just as much, and
        # reading only the first reported the package as declaring
        # none -- a clean bill for a file nobody looked at.
        result = self._check(
            {'thing/main.py': self.NO_BASIC_CONFIG},
            pyproject=(
                '[project]\nname = "thing"\n\n'
                '[project.gui-scripts]\nthing = "thing.main:cli"\n'
            ),
        )
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_an_explicit_console_scripts_table_is_an_entry_point(self):
        result = self._check(
            {'thing/main.py': self.NO_BASIC_CONFIG},
            pyproject=(
                '[project]\nname = "thing"\n\n'
                '[project.entry-points."console_scripts"]\n'
                'thing = "thing.main:cli"\n'
            ),
        )
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_a_non_string_entry_point_does_not_abort_the_run(self):
        # One exception loses every other check's result for the
        # repository, so a malformed declaration is stepped over
        # rather than allowed to propagate out of the walk.
        result = self._check(
            {'thing/main.py': self.NO_BASIC_CONFIG},
            pyproject=(
                '[project]\nname = "thing"\n\n'
                '[project.scripts]\nbroken = 1\n'
                'thing = "thing.main:cli"\n'
            ),
        )
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_entry_point_not_using_the_helper_is_not_applicable(self):
        result = self._check({'thing/main.py': 'def cli():\n    pass\n'})
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('none calling', result['details'])

    def test_a_non_entry_point_module_is_not_examined(self):
        # occystrap calls logs.setup_console(__name__) at the top of
        # all 24 of its modules. Only the entry point is the subject
        # of this rule, so a bare call anywhere else is not a finding.
        result = self._check({
            'thing/main.py': self.COMPLIANT,
            'thing/util.py': (
                'from shakenfist_utilities import logs\n'
                'LOG = logs.setup_console(__name__)\n'
            ),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_helpers_own_definition_is_not_a_call(self):
        # library-utilities defines setup_console(); it does not use
        # it, and has no console scripts either.
        result = self._check(
            {'shakenfist_utilities/logs.py': (
                'def setup_console(name):\n'
                '    return logging.getLogger(name)\n'
            )},
            pyproject='[project]\nname = "shakenfist-utilities"\n',
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_audit_ok_marker_exempts_a_file(self):
        result = self._check({'thing/main.py': (
            '# audit-ok: console-logging -- logging set up by the caller\n'
            'from shakenfist_utilities import logs\n'
            'LOG = logs.setup_console(__name__)\n'
        )})
        self.assertEqual(result['status'], 'not_applicable')

    def test_all_entry_points_exempt_says_so(self):
        # The 'none calling setup_console()' wording would tell a
        # reader the opposite of what is true, and send them looking
        # for a call that is right there.
        result = self._check({'thing/main.py': (
            '# audit-ok: console-logging -- logging set up by the caller\n'
            'from shakenfist_utilities import logs\n'
            'LOG = logs.setup_console(__name__)\n'
        )})
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('exempt by audit-ok marker', result['details'])
        self.assertNotIn('none calling', result['details'])

    def test_propagate_on_a_foreign_logger_is_not_enough(self):
        # The precise defect this rule exists to catch: the entry
        # point's own INFO lines are still emitted twice.
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'import logging\n'
            'LOG = logs.setup_console(__name__)\n'
            'logging.basicConfig(level=logging.INFO)\n'
            "logging.getLogger('urllib3').propagate = False\n"
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('propagate', result['details'])

    def test_a_foreign_logger_named_after_the_entry_points_is_not_enough(
            self):
        # URLLIB_LOG.propagate contains LOG.propagate as a substring,
        # so an unanchored receiver read this file as having silenced
        # its own logger. The third-party lines stop; the entry
        # point's own INFO lines are still emitted twice.
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'import logging\n'
            'LOG = logs.setup_console(__name__)\n'
            'logging.basicConfig(level=logging.INFO)\n'
            "URLLIB_LOG = logging.getLogger('urllib3')\n"
            'URLLIB_LOG.propagate = False\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('propagate', result['details'])

    def test_propagate_on_an_attribute_of_something_else_is_not_enough(self):
        # wrapper.LOG is not this module's LOG either.
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'import logging\n'
            'LOG = logs.setup_console(__name__)\n'
            'logging.basicConfig(level=logging.INFO)\n'
            'wrapper.LOG.propagate = False\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('propagate', result['details'])

    def test_propagate_via_get_logger_on_the_same_name_passes(self):
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'import logging\n'
            "LOG = logs.setup_console('thing')\n"
            'logging.basicConfig(level=logging.INFO)\n'
            "logging.getLogger('thing').propagate = False\n"
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_propagate_on_a_separately_fetched_logger_passes(self):
        # The entry point that fetches its logger by name instead of
        # keeping what setup_console() handed back is still setting
        # propagate on its own logger.
        result = self._check({'thing/main.py': (
            'from shakenfist_utilities import logs\n'
            'import logging\n'
            'logs.setup_console(__name__)\n'
            'LOG = logging.getLogger(__name__)\n'
            'logging.basicConfig(level=logging.INFO)\n'
            'LOG.propagate = False\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_package_entry_point_resolves_to_its_init(self):
        result = self._check(
            {'thing/__init__.py': self.COMPLIANT},
            pyproject=(
                '[project]\n'
                'name = "thing"\n'
                '\n'
                '[project.scripts]\n'
                'thing = "thing:cli"\n'
            ),
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_src_layout_entry_point_resolves(self):
        result = self._check({'src/thing/main.py': self.COMPLIANT})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_only_the_offending_entry_point_is_named(self):
        result = self._check(
            {
                'thing/good.py': self.COMPLIANT,
                'thing/bad.py': (
                    'from shakenfist_utilities import logs\n'
                    'LOG = logs.setup_console(__name__)\n'
                ),
            },
            pyproject=(
                '[project]\n'
                'name = "thing"\n'
                '\n'
                '[project.scripts]\n'
                'good = "thing.good:cli"\n'
                'bad = "thing.bad:cli"\n'
            ),
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('thing/bad.py', result['details'])
        self.assertNotIn('thing/good.py', result['details'])
        self.assertIn('1 of 2', result['details'])


class MaskCommentsAndStringsTest(unittest.TestCase):
    """Masking is only usable if it preserves every offset.

    Class positions, reported line numbers and the window an
    audit-ok marker is read from are all offsets taken against the
    masked text and used against the original. A mask that changed a
    single length would keep working on every fixture here -- the
    class is still found -- while reporting the wrong line and
    reading the marker window off the wrong part of the file.
    """

    SOURCES = (
        'a = 1  # class Handler(X):\n',
        'S = """\nclass H(Base):\n    pass\n"""\nc = 3\n',
        'D = \'\'\'\nclass H(Base):\n    pass\n\'\'\'\n',
        'class H(make("#"), Base):\n    pass\n',
        "x = 'a\\'b' + 1\n",
        'y = f"{a}"  # tail\n',
        'z = "never closed\n',
        'w = "trailing backslash \\\\"\n',
    )

    def test_masking_preserves_length_and_line_breaks(self):
        for source in self.SOURCES:
            with self.subTest(source=source):
                masked = audit_check.mask_comments_and_strings(source)
                self.assertEqual(len(masked), len(source))
                self.assertEqual(
                    [i for i, c in enumerate(masked) if c == '\n'],
                    [i for i, c in enumerate(source) if c == '\n'],
                )

    def test_code_outside_comments_and_strings_is_untouched(self):
        source = 'import os  # noqa\nPATH = "/tmp/x"\nclass A(B):\n'
        masked = audit_check.mask_comments_and_strings(source)
        self.assertIn('import os', masked)
        self.assertIn('class A(B):', masked)
        self.assertNotIn('noqa', masked)
        self.assertNotIn('/tmp/x', masked)


class MaskStringsTest(unittest.TestCase):
    """The view an audit-ok marker is read from.

    The complement of the code view: string bodies are blanked and
    comments survive, because a marker is a comment. Reading it from
    the file instead let `DOC = "audit-ok: header-sanitization"`
    exempt the class below it from a CWE-113 check.
    """

    def test_comments_survive_and_string_bodies_do_not(self):
        source = 'X = "audit-ok: x"  # audit-ok: y\n'
        masked = audit_check.mask_strings(source)
        self.assertIn('# audit-ok: y', masked)
        self.assertNotIn('audit-ok: x', masked)

    def test_a_hash_inside_a_string_does_not_start_a_comment(self):
        source = 'X = "# audit-ok: x"\nY = 1\n'
        masked = audit_check.mask_strings(source)
        self.assertNotIn('audit-ok', masked)
        self.assertIn('Y = 1', masked)

    def test_masking_preserves_length_and_line_breaks(self):
        for source in MaskCommentsAndStringsTest.SOURCES:
            with self.subTest(source=source):
                masked = audit_check.mask_strings(source)
                self.assertEqual(len(masked), len(source))
                self.assertEqual(
                    [i for i, c in enumerate(masked) if c == '\n'],
                    [i for i, c in enumerate(source) if c == '\n'],
                )


class HeaderSanitizationTest(unittest.TestCase):
    """The header-sanitization check.

    Position in the bases is the property under test: a subclass
    that lists SafeHeaderMixin after BaseHTTPRequestHandler reaches
    the base send_header() through the MRO and the override never
    runs, which is indistinguishable from not having the mixin.
    """

    def _check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            for path, content in files.items():
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write(content)
            subprocess.run(['git', '-C', tmp, 'add', '-A'], check=True)
            return audit_check.check_header_sanitization(tmp, {})

    def test_a_marker_in_a_string_constant_does_not_exempt(self):
        # A false clean bill on a security check, produced by an
        # ordinary string constant on the line above the class. The
        # marker window was read from the file rather than from a
        # view in which only comments survive.
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            'DOC = "audit-ok: header-sanitization"\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_a_marker_in_a_docstring_does_not_exempt(self):
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            '"""audit-ok: header-sanitization"""\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_an_apostrophe_in_the_marker_comment_does_not_break_it(self):
        # Comments survive in the marker view, so the scanner has to
        # keep recognising them: an apostrophe in one would otherwise
        # open a literal and blank the marker after it.
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            "# don't wrap this one: audit-ok: header-sanitization\n"
            'class Handler(BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_a_multi_line_aliased_import_is_examined(self):
        # An import list long enough to be wrapped is exactly where
        # an alias hides. The capture stopped at the newline, so the
        # alias resolved to nothing and the class using it was
        # dropped without a word.
        result = self._check({'a.py': (
            'from http.server import (\n'
            '    BaseHTTPRequestHandler as BHR,\n'
            ')\n'
            'class Handler(BHR):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('does not inherit SafeHeaderMixin',
                      result['details'])

    def test_a_backslash_continued_aliased_import_is_examined(self):
        result = self._check({'a.py': (
            'from http.server import \\\n'
            '    BaseHTTPRequestHandler as BHR\n'
            'class Handler(BHR):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_a_generic_class_is_examined(self):
        # PEP 695 puts a type parameter list between the name and the
        # bases, which read as a class with no base list at all --
        # silently out of scope on a security check.
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            'class Handler[T](BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_a_generic_class_with_a_bracketed_bound_is_examined(self):
        # A bound may itself hold brackets, so the parameter list is
        # walked rather than matched to the first "]".
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            'class Handler[T: dict[str, int]](BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_an_unclosed_type_parameter_list_is_reported_not_skipped(self):
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            'class Handler[T(BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('could not read the base list', result['details'])

    def test_an_aliased_handler_base_is_examined(self):
        # The import line carries the name, so the file was admitted
        # and then every class in it dropped -- reported as a
        # repository with no raw HTTP server in it at all.
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler as BHR\n'
            'class Handler(BHR):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin',
                      result['details'])

    def test_an_aliased_base_behind_the_mixin_is_still_ordered(self):
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler as BHR\n'
            'class Handler(BHR, SafeHeaderMixin):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('listed after', result['details'])

    def test_a_name_merely_containing_a_base_is_out_of_scope(self):
        # Bases are compared as whole names, not searched for as
        # substrings: this class has no reason to carry the mixin and
        # was being failed for not carrying it.
        result = self._check({'a.py': (
            'from x import MyBaseHTTPRequestHandlerWrapper\n'
            'class Handler(MyBaseHTTPRequestHandlerWrapper):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_a_class_inside_a_string_is_not_a_class(self):
        # A code sample in a module docstring, an embedded template,
        # a fixture built as a string: all produced a finding against
        # a class that does not exist.
        result = self._check({'a.py': (
            'SAMPLE = """\n'
            'send a "header like this:\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            '    pass\n'
            '"""\n'
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_a_hash_inside_a_base_list_string_does_not_hide_it(self):
        # The paren walk treated it as starting a comment and ran to
        # the end of the line, so the class was reported as having a
        # base list nobody could read.
        result = self._check({'a.py': (
            'from http.server import BaseHTTPRequestHandler\n'
            'class Handler(make_base("#"), BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin',
                      result['details'])

    def test_the_reported_line_survives_a_masked_preamble(self):
        # The line is counted in the original from an offset taken
        # against the masked text, so a mask that did not preserve
        # length would report a real class at the wrong line.
        result = self._check({'a.py': (
            'DOC = """\n'
            'a long\n'
            'embedded sample\n'
            '"""\n'
            '# and a comment\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('a.py:6 (Handler)', result['details'])

    def test_a_marker_survives_a_masked_preamble(self):
        # And the marker window is read out of the original at that
        # same offset, so an off-by-anything reads the wrong line.
        result = self._check({'a.py': (
            'DOC = """\n'
            'a long\n'
            'embedded sample\n'
            '"""\n'
            '# audit-ok: header-sanitization -- fixture\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_no_handler_is_not_applicable(self):
        result = self._check({'a.py': 'x = 1\n'})
        self.assertEqual(result['status'], 'not_applicable')

    def test_mixin_first_passes(self):
        result = self._check({'a.py': (
            'class Handler(\n'
            '        SafeHeaderMixin,\n'
            '        http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_missing_mixin_fails(self):
        result = self._check({'a.py': (
            'class Handler(http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin', result['details'])

    def test_mixin_after_the_base_class_fails(self):
        result = self._check({'a.py': (
            'class Handler(http.server.BaseHTTPRequestHandler,\n'
            '              SafeHeaderMixin):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('listed after', result['details'])

    def test_a_simple_http_request_handler_subclass_is_examined(self):
        # SimpleHTTPRequestHandler inherits the same unsanitized
        # send_header(), and a module subclassing it never mentions
        # the root class -- so naming only the root class reported a
        # genuine CWE-113 exposure as having no handler in it at all.
        result = self._check({'a.py': (
            'import http.server\n'
            '\n'
            '\n'
            'class Handler(http.server.SimpleHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin', result['details'])

    def test_a_cgi_http_request_handler_subclass_is_examined(self):
        result = self._check({'a.py': (
            'class Handler(CGIHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin', result['details'])

    def test_the_mixin_after_a_subclass_base_names_that_base(self):
        result = self._check({'a.py': (
            'class Handler(http.server.SimpleHTTPRequestHandler,\n'
            '              SafeHeaderMixin):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'listed after SimpleHTTPRequestHandler', result['details'])

    def test_a_call_in_the_base_list_is_still_parsed(self):
        # The base list used to be closed with \(([^)]*)\), which
        # stops at the first ")" -- so this class matched nothing,
        # handlers stayed empty and the result was not_applicable.
        result = self._check({'a.py': (
            'class Handler(make_base(),\n'
            '              http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin', result['details'])

    def test_a_closing_paren_in_a_base_list_comment_does_not_hide_it(self):
        # The paren walk used to close here, on the ")" inside the
        # comment, leaving bases of "SafeHeaderMixin,  # note" --
        # which names no handler, so the class was skipped and the
        # repository read as having no HTTP server in it.
        result = self._check({'a.py': (
            'class Handler(http.server.BaseHTTPRequestHandler,  # note)\n'
            '              SafeHeaderMixin):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('listed after', result['details'])

    def test_an_unclosable_base_list_is_reported_not_skipped(self):
        # Nothing the paren walk can close. A skip here is
        # indistinguishable from a repository with no handler in it,
        # which on a security check is a clean bill nobody earned.
        result = self._check({'a.py': (
            'class Handler(http.server.BaseHTTPRequestHandler,\n'
            '              SafeHeaderMixin\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('could not read the base list', result['details'])

    def test_audit_ok_marker_exempts_one_class(self):
        # A module may hold both a real server and a test fixture, so
        # the marker is read on the class rather than on the file.
        result = self._check({'a.py': (
            '# audit-ok: header-sanitization -- fixture, literal headers\n'
            'class Fixture(http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
            '\n'
            '\n'
            'class Real(http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Real', result['details'])
        self.assertNotIn('Fixture', result['details'])

    def test_a_handler_defined_inside_a_function_is_examined(self):
        # http.server gives no way to pass arguments to a handler, so
        # defining one in a closure is the common idiom. Reported as
        # not_applicable, it reads as 'no raw HTTP server here'.
        result = self._check({'a.py': (
            'def serve(state):\n'
            '    class Handler(http.server.BaseHTTPRequestHandler):\n'
            '        pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('does not inherit SafeHeaderMixin', result['details'])

    def test_a_commented_base_list_is_read_without_the_comment(self):
        # The comment's dot used to be read as attribute access, so
        # the base names came out mangled and the ordering finding
        # this check exists for was reported as a missing mixin.
        result = self._check({'a.py': (
            'class Handler(BaseHTTPRequestHandler,  # http.server bits\n'
            '              SafeHeaderMixin):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('listed after', result['details'])

    def test_a_handler_named_only_in_a_comment_is_not_a_handler(self):
        # The name is in the base list only as a comment. It used to
        # admit the class, which then had no reducible handler base
        # and reached index() with a name it had not found -- raising
        # out of the whole audit run for that repository.
        result = self._check({'a.py': (
            'class Handler(SafeHeaderMixin,  # a BaseHTTPRequestHandler\n'
            '              Base):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])

    def test_a_marker_a_blank_line_above_does_not_exempt(self):
        # security-sanitization.md says 'on or immediately above'.
        result = self._check({'a.py': (
            '# audit-ok: header-sanitization -- fixture\n'
            '\n'
            'class Handler(http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'fail')

    def test_a_marker_on_the_class_line_exempts(self):
        result = self._check({'a.py': (
            'class Handler(http.server.BaseHTTPRequestHandler):  '
            '# audit-ok: header-sanitization -- fixture\n'
            '    pass\n'
        )})
        self.assertEqual(result['status'], 'not_applicable')

    def test_a_failing_git_ls_files_is_a_finding(self):
        # Not a repository: stdout is empty, which is otherwise
        # indistinguishable from a clean bill of health.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'a.py'), 'w') as f:
                f.write('class H(http.server.BaseHTTPRequestHandler):\n'
                        '    pass\n')
            result = audit_check.check_header_sanitization(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('git ls-files failed', result['details'])

    def test_the_finding_names_a_line(self):
        result = self._check({'pkg/srv.py': (
            '\n'
            '\n'
            'class Handler(http.server.BaseHTTPRequestHandler):\n'
            '    pass\n'
        )})
        self.assertIn('pkg/srv.py:3', result['details'])


class PythonVersionTargetingTest(unittest.TestCase):
    """The python-version-targeting check.

    The interesting case is the agreement between requires-python and
    renovate.json's constraints.python. Both are the system Python of
    the oldest supported distribution, so a disagreement is one of
    them having been updated alone -- which nothing else notices,
    because renovate goes on working against the stale floor.
    """

    def _check(self, pyproject=None, renovate=None, props=None):
        with tempfile.TemporaryDirectory() as tmp:
            if pyproject is not None:
                with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                    f.write(pyproject)
            if renovate is not None:
                with open(os.path.join(tmp, 'renovate.json'), 'w') as f:
                    f.write(renovate)
            merged = {'has_pyproject_toml': pyproject is not None}
            merged.update(props or {})
            return audit_check.check_python_version_targeting(tmp, merged)

    def test_a_malformed_renovate_config_does_not_abort_the_run(self):
        # renovate.json's top level can hold any JSON value, and
        # calling .get() on it raised out of run_checks and cost the
        # repository every other check as well.
        for renovate in ('{"constraints": "3.8"}', '{"constraints": [1]}',
                         '[1, 2]', '"hi"'):
            with self.subTest(renovate=renovate):
                result = self._check(
                    '[project]\nrequires-python = ">=3.8"\n',
                    renovate=renovate)
                self.assertEqual(result['status'], 'pass',
                                 result['details'])

    def test_a_project_table_that_is_not_a_table_is_reported(self):
        result = self._check('project = "x"\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('not a table', result['details'])

    def test_no_pyproject_is_not_applicable(self):
        self.assertEqual(
            self._check()['status'], 'not_applicable')

    def test_declared_overrides_are_not_applicable(self):
        result = self._check(
            '[project]\nname = "x"\n', props={'not_python': True})
        self.assertEqual(result['status'], 'not_applicable')

    def test_missing_requires_python_fails(self):
        result = self._check('[project]\nname = "x"\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('requires-python', result['details'])

    def test_declared_requires_python_passes(self):
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n')
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_matching_renovate_constraint_passes(self):
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n',
            '{"constraints": {"python": ">=3.8"}}',
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_trailing_zero_is_the_same_floor(self):
        # ">=3.8" and ">=3.8.0" are one floor said two ways. Compared
        # as text this filed a fleet issue whose only remedy was a
        # cosmetic edit, and called one of the two stale when it was
        # not.
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n',
            '{"constraints": {"python": ">=3.8.0"}}',
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_clause_order_is_not_a_disagreement(self):
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8,<4"\n',
            '{"constraints": {"python": "<4, >=3.8"}}',
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_double_digit_minor_keeps_its_zero(self):
        # ">=3.10" must not be trimmed to ">=3.1".
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.10"\n',
            '{"constraints": {"python": ">=3.1"}}',
        )
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_an_extra_clause_is_still_a_disagreement(self):
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n',
            '{"constraints": {"python": ">=3.8,<4"}}',
        )
        self.assertEqual(result['status'], 'fail', result['details'])

    def test_disagreeing_renovate_constraint_fails(self):
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n',
            '{"constraints": {"python": ">=3.7"}}',
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('>=3.8', result['details'])
        self.assertIn('>=3.7', result['details'])

    def test_renovate_without_a_constraint_is_not_a_finding(self):
        # Only projects supporting several distributions need one.
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n',
            '{"extends": [":enablePreCommit"]}',
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_rust_project_is_not_applicable(self):
        # Mirrors pyproject-usage: a tooling pyproject.toml in a Rust
        # repository is not a package claiming an interpreter range.
        result = self._check(
            '[project]\nname = "helper-scripts"\n',
            props={'has_cargo_toml': True},
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_a_tooling_only_pyproject_is_not_applicable(self):
        result = self._check('[tool.ruff]\nline-length = 79\n')
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('tool configuration only', result['details'])

    def test_whitespace_in_the_constraint_is_not_a_disagreement(self):
        # ">= 3.8" and ">=3.8" are the same floor; filing an issue
        # whose only remedy is a whitespace edit is churn.
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">= 3.8"\n',
            '{"constraints": {"python": ">=3.8"}}',
        )
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_unparseable_renovate_json_is_not_a_version_finding(self):
        # renovate.json validity is the renovate audit's business.
        result = self._check(
            '[project]\nname = "x"\nrequires-python = ">=3.8"\n',
            'not json at all',
        )
        self.assertEqual(result['status'], 'pass', result['details'])


class OrphanSkillMarkdownTest(unittest.TestCase):
    """Markdown in a skills directory that will never load."""

    def _repo(self, tmp, files):
        for relative, content in files.items():
            path = os.path.join(tmp, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        return tmp

    def test_loose_markdown_is_an_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {'.claude/skills/debug-ci.md': '# Debug\n'})
            self.assertEqual(
                audit_check.orphan_skill_markdown(tmp),
                ['.claude/skills/debug-ci.md'],
            )

    def test_a_real_skill_is_not_an_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                '.claude/skills/debug-ci/SKILL.md': '---\nname: x\n---\n',
            })
            self.assertEqual(audit_check.orphan_skill_markdown(tmp), [])

    def test_directory_without_skill_md_is_an_orphan(self):
        # A directory of markdown with no SKILL.md loads nothing, and
        # skillsaw never sees it either, so only this check can report it.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {'.claude/skills/debug-ci/notes.md': '# n\n'})
            self.assertEqual(
                audit_check.orphan_skill_markdown(tmp),
                ['.claude/skills/debug-ci/ (no SKILL.md)'],
            )

    def test_readme_beside_the_skills_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                '.claude/skills/README.md': '# Skills\n',
                '.claude/skills/debug-ci/SKILL.md': '---\nname: x\n---\n',
            })
            self.assertEqual(audit_check.orphan_skill_markdown(tmp), [])

    def test_repo_without_skills_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_check.check_llm_context_lint(tmp, {})
            self.assertEqual(result['status'], 'not_applicable')

    def test_orphans_fail_the_check(self):
        # Guards the reason this check exists in Python: skillsaw
        # cannot see these files, so a clean skillsaw run must not be
        # enough to pass.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {'.claude/skills/debug-ci.md': '# Debug\n'})
            original = audit_check.skillsaw_errors
            audit_check.skillsaw_errors = lambda path: []
            try:
                result = audit_check.check_llm_context_lint(tmp, {})
            finally:
                audit_check.skillsaw_errors = original
        self.assertEqual(result['status'], 'fail')
        self.assertIn('debug-ci.md', result['details'])

    def test_missing_skillsaw_is_not_applicable(self):
        # A missing binary is the harness's problem. Failing would file
        # an issue against every repository for something none of them
        # can fix.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {'CLAUDE.md': '# Context\n'})
            original = audit_check.skillsaw_errors
            audit_check.skillsaw_errors = lambda path: None
            try:
                result = audit_check.check_llm_context_lint(tmp, {})
            finally:
                audit_check.skillsaw_errors = original
        self.assertEqual(result['status'], 'not_applicable')


class LlmContextLintCiTest(unittest.TestCase):
    """skillsaw runs per commit, not only in the daily audit."""

    def _repo(self, tmp, files):
        for relative, content in files.items():
            path = os.path.join(tmp, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        return tmp

    PRE_COMMIT = (
        'repos:\n'
        '  - repo: https://github.com/stbenjam/skillsaw\n'
        '    rev: v0.18.0\n'
        '    hooks:\n'
        '      - id: skillsaw\n'
    )
    WORKFLOW = 'jobs:\n  lint:\n    steps:\n      - uses: stbenjam/skillsaw@v0\n'

    # kerbside's actual shape: skillsaw installed from PyPI and
    # invoked directly, naming neither the upstream repository nor
    # pre-commit in the workflow.
    DIRECT_RUN_WORKFLOW = (
        'jobs:\n'
        '  lint:\n'
        '    steps:\n'
        '      - run: |\n'
        '          uv pip install skillsaw==0.18.0\n'
        '          skillsaw --no-custom-rules .\n'
    )

    def test_both_present_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/lint.yml': self.WORKFLOW,
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'pass')

    def test_pre_commit_alone_fails(self):
        # Pre-commit is advisory: --no-verify skips it, and a clone
        # that never ran `pre-commit install` never had it.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('CI workflow', result['details'])

    def test_ci_running_pre_commit_counts(self):
        # A workflow which runs pre-commit runs every hook the config
        # declares, skillsaw included, so the linter reaches CI without
        # the workflow naming the upstream repository.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/ci.yml': (
                    'jobs:\n'
                    '  lint:\n'
                    '    steps:\n'
                    '      - run: pre-commit run --all-files\n'
                ),
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'pass')

    def test_ci_running_pre_commit_without_the_hook_still_fails(self):
        # The indirection only counts when the hook is actually
        # declared; otherwise pre-commit runs everything except
        # skillsaw and the repository has no linting at all.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': 'repos: []\n',
                '.github/workflows/ci.yml': (
                    'jobs:\n'
                    '  lint:\n'
                    '    steps:\n'
                    '      - run: pre-commit run --all-files\n'
                ),
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')

    def test_a_commented_pre_commit_run_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/ci.yml': (
                    'jobs:\n'
                    '  lint:\n'
                    '    steps:\n'
                    '      # we should pre-commit run --all-files here\n'
                    '      - run: true\n'
                ),
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')

    def test_ci_alone_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.github/workflows/lint.yml': self.WORKFLOW,
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('.pre-commit-config.yaml', result['details'])

    def test_a_comment_does_not_count(self):
        # A workflow that only mentions skillsaw in a header comment
        # describes a thing it does not do.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/lint.yml': (
                    '# stbenjam/skillsaw runs in the other lane\n'
                    'jobs:\n  lint:\n    steps: []\n'
                ),
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')

    def test_direct_invocation_passes(self):
        # A workflow can also invoke skillsaw directly after installing
        # it from PyPI, naming neither the upstream repository nor
        # pre-commit.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/lint.yml': self.DIRECT_RUN_WORKFLOW,
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'pass')

    def test_install_without_invocation_fails(self):
        # Installing skillsaw is not running it. The anchor in
        # SKILLSAW_RUN_RE is what separates the two -- without it, this
        # case would be indistinguishable from a real invocation, since
        # the bare word "skillsaw" appears on the install line too.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/lint.yml': (
                    'jobs:\n'
                    '  lint:\n'
                    '    steps:\n'
                    '      - run: pip install skillsaw\n'
                ),
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('CI workflow', result['details'])

    def test_direct_invocation_in_a_comment_does_not_count(self):
        # A full-line comment describes what runs elsewhere; it is not
        # itself an invocation.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': self.PRE_COMMIT,
                '.github/workflows/lint.yml': (
                    'jobs:\n'
                    '  lint:\n'
                    '    steps:\n'
                    '      # skillsaw runs elsewhere\n'
                    '      - run: true\n'
                ),
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')

    def test_direct_invocation_without_pre_commit_hook_fails(self):
        # CI running skillsaw directly does not excuse the pre-commit
        # side -- the two halves are independent obligations, and the
        # failure must name the pre-commit half, not the CI half.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                'CLAUDE.md': '# Context\n',
                '.pre-commit-config.yaml': 'repos: []\n',
                '.github/workflows/lint.yml': self.DIRECT_RUN_WORKFLOW,
            })
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('.pre-commit-config.yaml', result['details'])
        self.assertNotIn('CI workflow', result['details'])

    def test_repo_without_context_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_check.check_llm_context_lint_ci(tmp, {})
        self.assertEqual(result['status'], 'not_applicable')


class SelfHostedRunnerLabelPositionTest(unittest.TestCase):
    """A GitHub-hosted label only counts where a runner can be named.

    The check scans every line rather than only 'runs-on:' lines, so
    that matrix values feeding 'runs-on: ${{ matrix.os }}' are caught.
    That breadth is what made it read image names, artifact names and
    job names as runner references.
    """

    def _check(self, workflow):
        with tempfile.TemporaryDirectory() as tmp:
            workflows = os.path.join(tmp, '.github', 'workflows')
            os.makedirs(workflows)
            with open(os.path.join(workflows, 'ci.yml'), 'w') as f:
                f.write(workflow)
            return audit_check.check_self_hosted_runners(
                tmp, {'has_workflows_dir': True}
            )

    def test_a_bare_runs_on_is_reported(self):
        result = self._check('jobs:\n  a:\n    runs-on: ubuntu-latest\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ubuntu-latest', result['details'])

    def test_a_list_element_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: [foo, windows-latest]\n')
        self.assertEqual(result['status'], 'fail')

    def test_a_matrix_item_is_reported(self):
        # The reason the check scans every line: this feeds
        # runs-on: ${{ matrix.os }} somewhere else in the file.
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            '        os:\n          - ubuntu-24.04\n'
            '          - macos-latest\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ubuntu-24.04', result['details'])

    def test_a_quoted_matrix_item_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            "        os:\n          - 'windows-11-arm'\n")
        self.assertEqual(result['status'], 'fail')

    def test_a_matrix_include_mapping_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            '        include:\n          - os: ubuntu-24.04-arm\n')
        self.assertEqual(result['status'], 'fail')

    def test_self_hosted_on_the_same_line_is_not_reported(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: [self-hosted, ubuntu-24.04]\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_marked_exception_is_not_reported(self):
        result = self._check(
            'jobs:\n  a:\n'
            '    # audit-ok: github-hosted-runner\n'
            '    runs-on: macos-latest\n')
        self.assertEqual(result['status'], 'pass')

    def test_an_image_path_is_not_a_runner(self):
        # shakenfist's functional-tests.yml names Shaken Fist image
        # labels this way. The trailing path separator before the label
        # is what distinguishes it from a value position.
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            "      - run: echo 'sf://label/ci-images/ubuntu-2404'\n")
        self.assertEqual(result['status'], 'pass')

    def test_a_job_name_containing_a_label_is_not_a_runner(self):
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            "      - run: echo 'ubuntu-2404-slim-primary'\n")
        self.assertEqual(result['status'], 'pass')

    def test_a_label_inside_a_shell_command_is_not_a_runner(self):
        # shakenfist/actions uploads an artifact named ubuntu-2004 from
        # inside an ssh command. Reporting it asked for an audit-ok
        # marker on a line which never described a runner.
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            '      - run: |\n'
            '          ssh host "${setup} ubuntu-2004 /srv/ci/ubuntu:20.04"\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_name_ending_in_a_label_is_not_a_runner(self):
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n          name: build-ubuntu-latest\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_trailing_comment_does_not_hide_a_real_runner(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: ubuntu-latest  # why not\n')
        self.assertEqual(result['status'], 'fail')

    def test_no_workflows_directory_is_not_applicable(self):
        result = audit_check.check_self_hosted_runners(
            '/nonexistent', {'has_workflows_dir': False}
        )
        self.assertEqual(result['status'], 'not_applicable')


class RetiredCommentAddresserTest(unittest.TestCase):
    """The comment addresser is retired and must not still be deployed.

    It was never used -- review items are worked through interactively
    instead -- and what it leaves behind is a workflow triggered by
    issue_comment holding contents: write on the pull request branch.
    The scripts go with it: address-comments-with-claude.sh was its only
    entry point, and render-review.py plus review-schema.json were only
    ever there for that script to call.
    """

    def _repo(self, tmp, leftovers=()):
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        for wf in ('pr-re-review.yml', 'pr-retest.yml'):
            with open(os.path.join(workflows, wf), 'w') as f:
                f.write('uses: shakenfist/actions/pr-bot-trigger@main\n'
                        'uses: shakenfist/actions/'
                        'review-pr-with-claude@main\n')
        for path in leftovers:
            full = os.path.join(tmp, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w') as f:
                f.write('x\n')
        return tmp

    def _check(self, leftovers=(), docs_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, leftovers)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_a_repository_without_the_addresser_passes(self):
        result = self._check()
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_workflow_alone_fails(self):
        result = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])

    def test_the_scripts_alone_fail(self):
        # Deleting the trigger but keeping the scripts is a half-done
        # job, and the scripts are what the next person copies.
        result = self._check(['tools/address-comments-with-claude.sh'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('address-comments-with-claude.sh', result['details'])

    def test_render_review_and_its_schema_are_reaped_too(self):
        # Nothing else in a project calls render-review.py: the reviewer
        # uses the copy inside shakenfist/actions.
        result = self._check(
            ['tools/render-review.py', 'tools/review-schema.json']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('render-review.py', result['details'])
        self.assertIn('review-schema.json', result['details'])

    def test_the_whole_chain_is_one_finding(self):
        chain = [audit_check.RETIRED_ADDRESSER_WORKFLOW] + [
            'tools/%s' % name
            for name in audit_check.RETIRED_ADDRESSER_SCRIPTS
        ]
        result = self._check(chain)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['details'].count('still deployed'), 1)
        for path in chain:
            self.assertIn(os.path.basename(path), result['details'])

    def test_scripts_outside_tools_are_found_too(self):
        # tools/ is the canonical home, but deployments put them
        # elsewhere; the check this replaced found a contrib/ copy.
        result = self._check(['contrib/render-review.py'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('contrib/render-review.py', result['details'])

    def test_the_git_directory_is_not_walked(self):
        # .git can hold checked-out state from another branch. Findings
        # from in there are not actionable.
        result = self._check(['.git/stash/render-review.py'])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_docs_only_project_is_checked_too(self):
        # cloudgood is exempt from most of this audit, but a workflow
        # holding contents: write is not a documentation concern.
        result = self._check(
            ['.github/workflows/pr-address-comments.yml'], docs_only=True
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])

    def test_the_reviewer_actions_own_copies_are_not_leftovers(self):
        # shakenfist/actions is in the matrix and is where
        # render-review.py and its schema actually live -- the copies
        # every project's reviewer runs, and the ones this retirement
        # sends projects to instead of their own. The finding says to
        # remove the whole chain in one commit, so reporting these would
        # be telling the maintainer to delete the renderer out from
        # under the reviewer in every repository at once.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            'review-pr-with-claude/review-schema.json',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_exemption_does_not_cover_the_rest_of_the_repository(self):
        # shakenfist/actions carries genuine leftovers of its own next
        # to the action. Exempting the action's directory must not
        # exempt the repository, or the one repository that hosts the
        # replacement is the one that never gets told to clean up.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            '.github/workflows/pr-address-comments.yml',
            'tools/address-comments-with-claude.sh',
        ])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])
        self.assertIn('address-comments-with-claude.sh', result['details'])
        self.assertNotIn('review-pr-with-claude', result['details'])

    def test_any_composite_action_is_exempt_not_just_the_reviewer(self):
        # The exemption keys on action.yml rather than on the reviewer's
        # directory name, so a second action which vendors a renderer of
        # its own does not have to be added here to avoid a false
        # finding. Hardcoding the one name we know about today is how a
        # check acquires a maintenance burden nobody remembers.
        result = self._check([
            'some-other-action/action.yml',
            'some-other-action/render-review.py',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_yaml_spelling_of_the_manifest_counts(self):
        # Actions accepts action.yaml as readily as action.yml. Missing
        # the spelling produces the exact false finding the exemption
        # exists to prevent, and the finding says to delete everything
        # it names.
        result = self._check([
            'vendored-action/action.yaml',
            'vendored-action/render-review.py',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_template_copy_of_the_workflow_is_named(self):
        # The workflow is matched by name anywhere, not only at
        # .github/workflows/. A template directory's copy does not run,
        # but it is the one the next project installs, and the
        # remediation is "remove everything the finding names in one
        # commit" -- so a finding which skipped it would have the
        # maintainer delete the scripts, leave the template, and pass
        # the audit from then on while still propagating the chain.
        result = self._check(
            ['templates/ci-review-automation/pr-address-comments.yml']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'templates/ci-review-automation/pr-address-comments.yml',
            result['details'])

    def test_only_the_installed_workflow_claims_contents_write(self):
        # The finding is the whole content of an auto-filed issue on
        # another repository. Only .github/workflows/ actually runs, so
        # asserting a privileged workflow for a template copy sends the
        # maintainer hunting for one that is not there.
        installed = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assertIn('contents: write', installed['details'])
        template = self._check(
            ['templates/ci-review-automation/pr-address-comments.yml']
        )
        self.assertNotIn('contents: write', template['details'])
        self.assertIn('dead weight', template['details'])

    def test_leftover_scripts_alone_do_not_claim_contents_write(self):
        # The normal state after a partial cleanup: the workflow is
        # gone, the scripts are not.
        result = self._check(['tools/render-review.py'])
        self.assertEqual(result['status'], 'fail')
        self.assertNotIn('contents: write', result['details'])

    def test_the_schema_alone_is_found(self):
        # review-schema.json is only ever exercised beside
        # render-review.py elsewhere in this suite, so a regression
        # which matched only the .py suffix would pass. It is dead on
        # its own too: nothing else in a project reads it.
        result = self._check(['tools/review-schema.json'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('review-schema.json', result['details'])

    def test_a_docs_only_project_is_checked_for_scripts_too(self):
        # The docs-only branch returns early on the addresser finding.
        # The workflow leftover pins that branch elsewhere; a script
        # leftover takes the same return and had nothing holding it.
        result = self._check(['tools/render-review.py'], docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('render-review.py', result['details'])

    def test_the_exemption_is_the_directory_not_the_name(self):
        # An action.yml exempts the directory it sits in and nothing
        # below it, so a leftover parked one level down is still found.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/old/render-review.py',
        ])
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'review-pr-with-claude/old/render-review.py', result['details'])


class PrReReviewTriggerTest(unittest.TestCase):
    """pr-re-review.yml must use pr-bot-trigger, not hand-rolled shell.

    The shared action refuses fork pull requests. Its pr-ref output is
    .head.ref -- a branch name in the head repository, with nothing to
    say which repository that is -- and callers check that name out and
    push to it in their own. A fork pull request opened from the fork's
    default branch names "main". A hand-rolled copy of the trigger
    handling does not get that guard, and did not get any of the other
    fixes made to the action either.
    """

    INLINE = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  check_and_review:\n'
        '    runs-on: [self-hosted, claude-code]\n'
        '    steps:\n'
        '      - name: Check commenter permissions\n'
        '        run: gh api repos/x/collaborators/y/permission\n'
    )
    USES_ACTION = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  trigger-re-review:\n'
        '    runs-on: [self-hosted, static]\n'
        '    steps:\n'
        '      - uses: shakenfist/actions/pr-bot-trigger@main\n'
    )

    def _repo(self, tmp, re_review_body=None):
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        with open(os.path.join(workflows, 'pr-retest.yml'), 'w') as f:
            f.write('uses: shakenfist/actions/'
                    'review-pr-with-claude@main\n')
        if re_review_body is not None:
            with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
                f.write(re_review_body)
        return tmp

    def _check(self, re_review_body=None, docs_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, re_review_body)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_using_the_shared_action_passes(self):
        result = self._check(self.USES_ACTION)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_hand_rolled_trigger_handling_fails(self):
        result = self._check(self.INLINE)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-bot-trigger@main', result['details'])
        self.assertIn('fork', result['details'])

    def test_an_absent_workflow_is_reported_once_not_twice(self):
        # Its absence is already a finding. Saying "missing" and "does
        # not use the action" about the same missing file is two
        # findings for one problem.
        result = self._check(None)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Missing pr-re-review.yml', result['details'])
        self.assertNotIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INLINE, docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_passes_when_the_action_is_used(self):
        result = self._check(self.USES_ACTION, docs_only=True)
        self.assertEqual(result['status'], 'pass', result['details'])


class GitHooksDisabledTest(unittest.TestCase):
    """The workflows that check out PR code must neuter core.hooksPath.

    Layer 4 of the security model in docs/ci-review-automation.md
    names these three files and asserts the control is set in them.
    Nothing in check_ci_review_automation inspects checkout steps, so
    without this a template edit could drop the step and leave the
    document claiming a control that is not there -- which is the
    defect this test's own pull request existed to fix. The assertion
    is on this repository's files rather than on a synthetic tree
    because the templates are the fleet's source of truth: a repo that
    copies them inherits whatever is here.
    """

    WORKFLOWS = [
        os.path.join('.github', 'workflows', 'pr-re-review.yml'),
        os.path.join(
            'templates', 'ci-review-automation', 'pr-re-review.yml'),
        os.path.join(
            'templates', 'test-drift-fix', 'test-drift-fix.yml'),
    ]

    # Matched as a pattern rather than as one exact spelling. This
    # test is the fleet's guard, and its failures are read by people
    # who did not write it: `git config --local core.hooksPath` sets
    # the same thing, and reporting it as a missing line would send
    # them to delete a correct one.
    HOOKS_PATH = re.compile(r'git config (--local )?core\.hooksPath')

    def test_hooks_path_is_set_after_the_checkout(self):
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    lines = f.read().splitlines()

                config = [
                    i for i, line in enumerate(lines)
                    if self.HOOKS_PATH.search(line)
                    and not line.lstrip().startswith('#')
                ]
                self.assertEqual(
                    len(config), 1,
                    f'{name} must set core.hooksPath exactly once')

                # Ordering matters as much as presence: "git config"
                # outside a work tree fails, and hooks set before the
                # checkout would be overwritten by it. Against the
                # last checkout rather than the first, because a
                # second one added after the config step would
                # re-clone the tree and discard .git/config.
                checkout = [
                    i for i, line in enumerate(lines)
                    if 'actions/checkout@' in line
                ]
                self.assertTrue(
                    checkout, f'{name} has no checkout step')
                self.assertGreater(
                    config[0], checkout[-1],
                    f'{name} sets core.hooksPath before its last '
                    'checkout, which would discard the setting')

    def test_the_setting_is_repository_local(self):
        # --global would outlive the job on the shared claude-code
        # pool and disable hooks for every later job on that machine.
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    body = f.read()
                self.assertNotIn(
                    'git config --global core.hooksPath', body)

    def test_the_document_still_names_these_workflows(self):
        # The test and the claim have to move together: a workflow
        # dropped from the list here but left in the document is the
        # same unbacked claim in the other direction.
        #
        # Scoped to layer 4 rather than the whole document on purpose.
        # "test-drift-fix.yml" also appears under Workflow Templates,
        # so a document-wide assertIn would stay green after the name
        # was struck from the security model -- a guard that passes
        # for a reason unrelated to what it defends.
        layer = self._security_model_layer_four()
        self.assertIn('core.hooksPath=/dev/null', layer)
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertIn(os.path.basename(name), layer)

    # AGENTS.md: a document parsed by phrase gets named constants
    # and an assertion, not a bare index() that raises ValueError
    # without naming the phrase that stopped matching. Same treatment
    # as AuditScopeIsStatedOnceTest.bulleted_block(), including the
    # count assertions -- they report the phrase rather than dumping
    # the document the way assertIn would.
    DOC = os.path.join('docs', 'ci-review-automation.md')
    LAYER_FOUR = '4. **Git hooks disabled**'
    LAYER_FIVE = '5. **'

    def _security_model_layer_four(self):
        with open(os.path.join(REPO_ROOT, self.DOC)) as f:
            doc = f.read()
        self.assertEqual(
            doc.count(self.LAYER_FOUR), 1,
            f'{self.DOC} must contain "{self.LAYER_FOUR}" exactly '
            f'once: it is where this test starts reading the layer, '
            f'and a renumbered or reworded security model has to fail '
            f'as that rather than as a missing control')
        after = doc.split(self.LAYER_FOUR, 1)[1]
        self.assertEqual(
            after.count(self.LAYER_FIVE), 1,
            f'{self.DOC} must contain "{self.LAYER_FIVE}" exactly '
            f'once after "{self.LAYER_FOUR}": it is where this test '
            f'stops reading, and without it the parse runs to the end '
            f'of the file')
        return self.LAYER_FOUR + after.split(self.LAYER_FIVE, 1)[0]


class PrAutoReviewSecretsInheritTest(unittest.TestCase):
    """The reviewer job must not pass "secrets: inherit".

    pr-auto-review.yml reads no secrets -- it and review-pr-with-claude
    authenticate with github.token from the caller's permissions block
    -- so inheriting buys nothing and hands every secret the calling
    repository holds, publishing tokens included, to a workflow in
    another repository.
    """

    REVIEWER = (
        '  automated_reviewer:\n'
        '    permissions:\n'
        '      contents: read\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'pr-auto-review.yml@main\n'
    )
    INHERITS = REVIEWER + '    secrets: inherit\n'
    # smoke-cluster.yml genuinely needs the cluster secrets. Only the
    # reviewer job is the finding.
    SMOKE_INHERITS = (
        '  smoke:\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'smoke-cluster.yml@main\n'
        '    secrets: inherit\n'
    )

    def _repo(self, tmp, reviewer_job):
        # A compliant repository apart from whatever the reviewer job
        # under test does: both required workflows present, the shared
        # trigger action used, and none of the retired addresser's
        # files deployed. Anything else here shows up as an unrelated
        # finding and masks the one being tested.
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        with open(os.path.join(workflows, 'pr-retest.yml'), 'w') as f:
            f.write('uses: shakenfist/actions/'
                    'review-pr-with-claude@main\n')
        with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
            f.write('  - uses: shakenfist/actions/pr-bot-trigger@main\n')
        with open(os.path.join(workflows, 'ci.yml'), 'w') as f:
            f.write('jobs:\n' + reviewer_job)
        return tmp

    def _check(self, reviewer_job, docs_only=False, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, reviewer_job)
            for name, content in (extra or {}).items():
                path = os.path.join(tmp, '.github', 'workflows', name)
                with open(path, 'w') as f:
                    f.write(content)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_a_reviewer_without_inherit_passes(self):
        result = self._check(self.REVIEWER)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_reviewer_which_inherits_fails(self):
        result = self._check(self.INHERITS)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('secrets: inherit', result['details'])
        self.assertIn('ci.yml', result['details'])

    def test_other_callers_may_inherit(self):
        # smoke-cluster.yml reads real secrets. Sweeping it up in this
        # finding would be telling projects to break their own CI.
        result = self._check(self.REVIEWER + self.SMOKE_INHERITS)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_commented_out_inherit_is_not_a_finding(self):
        commented = self.REVIEWER + '    # secrets: inherit\n'
        result = self._check(commented)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_trailing_comment_does_not_hide_it(self):
        # The realistic evasion. Someone who reads the template text or
        # receives the audit issue is likelier to annotate the line than
        # to delete it, and Actions treats this as plain inherit.
        annotated = self.REVIEWER + (
            '    secrets: inherit  # TODO: drop once migrated\n')
        result = self._check(annotated)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])

    def test_a_quoted_inherit_does_not_hide_it(self):
        for quoted in ("    secrets: 'inherit'\n",
                       '    secrets: "inherit"\n'):
            result = self._check(self.REVIEWER + quoted)
            self.assertEqual(result['status'], 'fail', quoted)
            self.assertIn('ci.yml', result['details'])

    def test_a_named_secret_is_not_inherit(self):
        # The explicit mapping form passes only what it names, which is
        # the false positive worth declining.
        named = self.REVIEWER + (
            '    secrets:\n      MY_TOKEN: ${{ secrets.MY_TOKEN }}\n')
        result = self._check(named)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INHERITS, docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('secrets: inherit', result['details'])

    def test_every_offending_workflow_is_named(self):
        # Most projects carry the reviewer job in functional-tests.yml
        # rather than ci.yml, so the finding has to name whichever file
        # it found rather than the one the fixtures happen to use. Two
        # at once also exercises the sorted join, which is what the
        # audit issue body shows the person doing the work.
        result = self._check(self.INHERITS, extra={
            'functional-tests.yml': 'jobs:\n' + self.INHERITS,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertIn('functional-tests.yml', result['details'])
        self.assertLess(result['details'].index('ci.yml'),
                        result['details'].index('functional-tests.yml'))

    def test_a_workflow_with_no_jobs_key_is_skipped(self):
        # workflow_job_blocks finds nothing in a file with no top-level
        # jobs: key. That must skip the file rather than throw, or one
        # malformed workflow stops the check measuring the rest of the
        # repository -- and a check which does not run reports pass.
        result = self._check(self.INHERITS, extra={
            'dependabot-notes.yml': 'on:\n  push:\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertNotIn('dependabot-notes.yml', result['details'])


class MergeGroupCancellationTest(unittest.TestCase):
    """Which merge group jobs must be able to cancel each other."""

    QUEUE_REF_KEY = """    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}-cluster
      cancel-in-progress: true
"""

    STABLE_KEY = """    concurrency:
      group: >-
        ${{ github.workflow }}-cluster-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""

    def _job(self, concurrency='', runs_on='[self-hosted, vm, debian-12, l]',
             condition=''):
        return (
            '  cluster:\n'
            f'    runs-on: {runs_on}\n'
            + (f'    if: {condition}\n' if condition else '')
            + concurrency
            + '    steps:\n      - run: deploy.sh\n'
        )

    # Whether the repository's merge queue builds one entry at a
    # time. The check asks GitHub; these tests answer for it, both to
    # stay offline and because the interesting case -- a queue that
    # stacks speculatively, where the base_ref key would cancel a live
    # entry -- does not exist in the fleet to point at.
    serial_queue = True

    def setUp(self):
        self._real_serial = audit_check.merge_queue_is_serial
        audit_check.merge_queue_is_serial = (
            lambda repo_name, org: self.serial_queue
        )

    def tearDown(self):
        audit_check.merge_queue_is_serial = self._real_serial

    def _check(self, workflows):
        with tempfile.TemporaryDirectory() as tmp:
            wdir = os.path.join(tmp, '.github', 'workflows')
            os.makedirs(wdir)
            for name, content in workflows.items():
                with open(os.path.join(wdir, name), 'w') as f:
                    f.write(content)
            return audit_check.check_merge_group_cancellation(
                tmp, {'has_workflows_dir': True}, 'testrepo', 'shakenfist'
            )

    def _merge_group_workflow(self, job):
        return 'on:\n  pull_request:\n  merge_group:\njobs:\n' + job

    def test_a_queue_ref_key_fails(self):
        # The bug this audit exists for: on merge_group github.ref is
        # gh-readonly-queue/<base>/pr-N-<SHA>, unique per rebuild, so
        # cancel-in-progress never matches.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    def test_a_merge_group_aware_key_passes(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_no_concurrency_block_at_all_fails(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job())})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no concurrency block', result['details'])

    def test_cancel_in_progress_must_be_on(self):
        # A stable key that queues instead of cancelling still leaves
        # the superseded run holding the runner.
        block = """    concurrency:
      group: ${{ github.workflow }}-merge
      cancel-in-progress: false
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('cancel-in-progress is not true', result['details'])

    def test_a_workflow_level_block_covers_a_bare_job(self):
        content = (
            'on:\n  merge_group:\n'
            'concurrency:\n'
            "  group: ${{ github.workflow }}-${{ github.event_name =="
            " 'merge_group' && 'queue' || github.ref }}\n"
            '  cancel-in-progress: true\n'
            'jobs:\n' + self._job()
        )
        result = self._check({'ci.yml': content})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_job_that_cannot_run_on_merge_group_is_out_of_scope(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      condition="github.event_name != 'merge_group'"))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_static_pool_is_out_of_scope(self):
        # Gate jobs and path filters are seconds long on an
        # always-on shared pool; there is nothing to starve.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, static]'))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_self_hosted_pool_without_the_vm_label_is_in_scope(self):
        # instar's ephemeral runners are [self-hosted, debian-12, xl].
        # The sibling path-filter audit's 'vm' test would miss them
        # while an abandoned merge group holds one for two hours.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, debian-12, xl]'))})
        self.assertEqual(result['status'], 'fail')

    def test_a_github_hosted_runner_is_out_of_scope(self):
        # No fleet runner to starve, so the workflow is examined and
        # reports nothing rather than being skipped entirely.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY, runs_on='ubuntu-latest'))})
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('0 job(s)', result['details'])

    def test_an_unresolvable_runs_on_expression_is_out_of_scope(self):
        # ryll's cross-platform build matrix is runs-on:
        # ${{ matrix.os }}; there is nothing to resolve it against.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='${{ matrix.os }}'))})
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('0 job(s)', result['details'])

    def test_a_reusable_workflow_is_audited(self):
        # It inherits the caller's event, and a callee published for
        # the fleet cannot know what that event is. This is
        # shakenfist/actions' smoke-cluster.yml.
        result = self._check({'smoke-cluster.yml': (
            'on:\n  workflow_call:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'fail')

    def test_a_reusable_workflow_is_audited_despite_an_in_repo_caller(self):
        # Inferring reachability from in-repo callers exempted
        # smoke-cluster.yml on the strength of a scheduled canary
        # calling it, while every shakenfist merge group ran four
        # nested clusters through it from another repository.
        result = self._check({
            'smoke-cluster.yml': (
                'on:\n  workflow_call:\njobs:\n'
                + self._job(self.QUEUE_REF_KEY)
            ),
            'canary.yml': (
                'on:\n  schedule:\n    - cron: "0 3 * * *"\njobs:\n'
                '  canary:\n'
                '    uses: ./.github/workflows/smoke-cluster.yml\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('smoke-cluster.yml:cluster', result['details'])

    def test_calling_a_reusable_workflow_is_out_of_scope(self):
        # The caller job has no runner of its own; the group that
        # matters is in the callee, audited where it is defined.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            '    uses: shakenfist/actions/.github/workflows/'
            'smoke-cluster.yml@main\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_marked_exception_is_allowed(self):
        result = self._check({'test-drift-fix.yml': (
            'on:\n  workflow_call:\n'
            '# audit-ok: merge-group-cancellation -- issue_comment only\n'
            'jobs:\n' + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_comment_quoting_the_bad_key_does_not_count(self):
        # Every fixed workflow explains itself with a comment naming
        # github.ref directly above the corrected key.
        block = """    # github.ref is wrong here on merge_group.
    concurrency:
      group: ${{ github.workflow }}-merge-queue
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_github_sha_key_fails(self):
        # The same defect wearing a different name: on merge_group
        # github.sha is the per-attempt merge commit, not the pull
        # request head, so it is minted afresh on every rebuild.
        block = """    concurrency:
      group: ${{ github.workflow }}-${{ github.sha }}-cluster
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    def _matrix_job(self, concurrency, key='${{ matrix.topology }}'):
        return (
            '  cluster:\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            '    strategy:\n'
            '      matrix:\n'
            '        topology: [slim-primary, slim-tier]\n'
            + concurrency
            + '    steps:\n      - run: deploy.sh\n'
        )

    def test_matrix_lanes_sharing_one_group_fails(self):
        # The expensive half of getting this wrong: the lanes cancel
        # each other inside a single run, the queue sees a cancelled
        # required check, and the pull request is ejected.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._matrix_job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('lanes cancel each other', result['details'])

    def test_a_matrix_key_in_the_group_passes(self):
        block = """    concurrency:
      group: >-
        ${{ github.workflow }}-cluster-${{ matrix.topology }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._matrix_job(block))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_matrix_of_self_hosted_runners_is_in_scope(self):
        # runs-on: ${{ matrix.runner }} is only unresolvable in the
        # sense that a regex cannot read it. The matrix says what it
        # resolves to, and a whole matrix of cloud builds should not
        # drop out of the audit because of the indirection.
        job = (
            '  cluster:\n'
            '    strategy:\n'
            '      matrix:\n'
            '        runner: [[self-hosted, vm, debian-12, l],\n'
            '                 [self-hosted, vm, debian-12, xl]]\n'
            '    runs-on: ${{ matrix.runner }}\n'
            + self.QUEUE_REF_KEY
            + '    steps:\n      - run: deploy.sh\n'
        )
        result = self._check({'ci.yml': self._merge_group_workflow(job)})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    REUSABLE_HEAD = (
        'on:\n'
        '  workflow_call:\n'
        '    inputs:\n'
        '      concurrency_key:\n'
        '        type: string\n'
        '        default: \'\'\n'
        'jobs:\n'
    )

    def test_a_callee_group_made_only_of_caller_contexts_fails(self):
        # Every invocation on one ref renders the same group, so a
        # matrix of four callers cancels itself down to one.
        block = """    concurrency:
      group: >-
        smoke-cluster-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({
            'smoke-cluster.yml': self.REUSABLE_HEAD + self._job(block),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('every invocation on a ref', result['details'])

    def test_a_callee_keyed_on_an_input_passes(self):
        block = """    concurrency:
      group: >-
        smoke-cluster-${{ inputs.concurrency_key }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({
            'smoke-cluster.yml': self.REUSABLE_HEAD + self._job(block),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def _caller(self, name, extra_with='', matrix=''):
        return (
            f'  {name}:\n'
            + matrix
            + '    uses: shakenfist/actions/.github/workflows/'
            'smoke-cluster.yml@main\n'
            '    with:\n'
            '      component: shakenfist\n'
            + extra_with
        )

    def test_two_invocations_of_one_callee_need_distinct_keys(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier')
            + self._caller('ansible_modules')
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('more than once per ref', result['details'])

    def test_distinct_concurrency_keys_pass(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier', '      concurrency_key: merge-tier\n')
            + self._caller(
                'ansible_modules',
                '      concurrency_key: ansible-modules\n')
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_same_concurrency_key_twice_fails(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier', '      concurrency_key: full\n')
            + self._caller(
                'ansible_modules', '      concurrency_key: full\n')
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('passes the same concurrency_key', result['details'])

    def test_a_matrix_caller_must_vary_its_key(self):
        # shakenfist runs four nested clusters through one callee from
        # a single matrix job. Varying topology and base image is not
        # enough: the callee keys its group on concurrency_key, and
        # what does not vary there does not separate the lanes.
        matrix = (
            '    strategy:\n'
            '      matrix:\n'
            '        topology: [slim-primary, slim-tier]\n'
        )
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      topology: ${{ matrix.topology }}\n'
                '      concurrency_key: full\n',
                matrix=matrix)
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('same for every matrix lane', result['details'])

        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      concurrency_key: ${{ matrix.topology }}\n',
                matrix=matrix)
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_callee_outside_the_fleet_is_reported(self):
        # Nothing here can see its concurrency group, and the caller
        # cannot fix it either.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    uses: someone-else/ci/.github/workflows/build.yml@v1\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('outside the audited fleet', result['details'])

    def test_a_marked_exception_only_exempts_its_own_job(self):
        # The marker used to be read against the whole file, so one
        # job's stated exception silently stopped the other fourteen
        # in an eight hundred line workflow being measured.
        exempt = (
            '  drift:\n'
            '    # audit-ok: merge-group-cancellation -- comment only\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            + self.QUEUE_REF_KEY
            + '    steps:\n      - run: drift.sh\n'
        )
        result = self._check({'ci.yml': self._merge_group_workflow(
            exempt + self._job(self.QUEUE_REF_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml:cluster', result['details'])
        self.assertNotIn('ci.yml:drift', result['details'])

    def test_a_stacking_merge_queue_makes_the_base_ref_key_unsafe(self):
        # The pattern this audit requires aliases every live entry in
        # the queue. That is only safe while the queue builds one at a
        # time, which merge-queue-config is what enforces -- so the
        # precondition is checked rather than left as a note.
        self.serial_queue = False
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('aliases live entries', result['details'])

    def test_a_repo_with_no_merge_group_is_not_applicable(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
