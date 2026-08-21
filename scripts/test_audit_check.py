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
        for name, block in (
            ('readme-discipline', self.readme_block),
            ('llm-doc-discipline', self.llm_doc_block),
            ('comment-proportion', self.comment_block),
            ('plan-phase-references', self.phase_block),
        ):
            with open(
                os.path.join(self._blocks.name, f'{name}.md'), 'w'
            ) as f:
                f.write(block)
        self.canonical = (
            f'{self.readme_block}\n{self.llm_doc_block}\n'
            f'{self.comment_block}\n{self.phase_block}'
        )

    def _check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
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
    .github/workflows/consistency-audit.yml is what actually runs, the
    in-scope list in audits/README.md is what a reader is told, and
    the excluded list in PROJECT-CONSISTENCY-AUDITS.md is what the
    standard claims. Nothing else ties them together, so a repository
    added to the matrix alone is audited while the documentation says
    it is not -- and one dropped from the matrix alone silently stops
    being measured while both documents say it is.
    """

    root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )

    def read(self, relative):
        with open(os.path.join(self.root, relative)) as f:
            return f.read()

    def matrix_repos(self):
        text = self.read('.github/workflows/consistency-audit.yml')
        block = text.split('        repo:\n', 1)[1]
        repos = []
        for line in block.splitlines():
            if line.startswith('          - '):
                repos.append(line[len('          - '):].strip())
            elif line.strip() and not line.lstrip().startswith('#'):
                break
        return repos

    def documented_in_scope(self):
        text = self.read('audits/README.md')
        block = text.split('## In-scope projects', 1)[1]
        block = block.split('One project is in scope', 1)[0]
        return [
            line[2:].strip() for line in block.splitlines()
            if line.startswith('- ')
        ]

    def documented_excluded(self):
        text = self.read('PROJECT-CONSISTENCY-AUDITS.md')
        block = text.split('are **excluded**', 1)[1]
        block = block.split('The `actions` repository', 1)[0]
        return [
            line[2:].strip() for line in block.splitlines()
            if line.startswith('* ')
        ]

    def partially_scoped(self):
        return {
            name for name, overrides
            in audit_check.REPO_OVERRIDES.items()
            if overrides.get('only_checks')
        }

    def test_matrix_matches_the_documented_scope(self):
        matrix = set(self.matrix_repos())
        self.assertIn('development', matrix)
        self.assertEqual(
            matrix - self.partially_scoped(),
            set(self.documented_in_scope()),
            'the audit matrix and the in-scope list in '
            'audits/README.md disagree',
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
            'PROJECT-CONSISTENCY-AUDITS.md lists these as excluded '
            'but the audit matrix runs every check against them',
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
        # table has to agree with the issue title map.
        ids = self._ids()
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertEqual(
            sorted(ids), sorted(audit_check.CHECK_NAMES.keys())
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
        self.assertEqual(len(by_id), len(audit_check.CHECK_NAMES))

        for check_id, check in by_id.items():
            if check_id == 'sfui-vendor':
                self.assertNotEqual(check['details'], reason)
                continue
            self.assertEqual(check['status'], 'not_applicable')
            self.assertEqual(check['details'], reason)

        # Nothing is dropped from the results, because a check missing
        # from the JSON renders as "unknown" in the audits/ tables.
        self.assertEqual(
            results['summary']['total'], len(audit_check.CHECK_NAMES)
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


class RenderReviewSchemaTest(unittest.TestCase):
    """A deployed render-review.py must keep review-schema.json beside it.

    render-review.py resolves SCHEMA_PATH as
    Path(__file__).parent / 'review-schema.json'. Separate the two and
    load_schema() returns None, validate_review() drops to structural
    checks, and --validate starts accepting reviews with invented
    categories and actions while still exiting zero. ryll was in that
    state when this check was written, and the template directory shipped
    the script without the schema, which is how ryll got there.
    """

    def _repo(self, tmp, script_dirs, schema_dirs):
        """Build a fixture repo that otherwise passes the audit."""
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        for wf in ('pr-re-review.yml', 'pr-address-comments.yml',
                   'pr-retest.yml'):
            with open(os.path.join(workflows, wf), 'w') as f:
                f.write('uses: shakenfist/actions/'
                        'review-pr-with-claude@main\n')
                # pr-re-review.yml must reach pr-bot-trigger, or the
                # fork-guard check fires and this fixture fails for a
                # reason that has nothing to do with the schema.
                if wf == 'pr-re-review.yml':
                    f.write('uses: shakenfist/actions/'
                            'pr-bot-trigger@main\n')
        for directory in script_dirs:
            os.makedirs(os.path.join(tmp, directory), exist_ok=True)
            with open(
                os.path.join(tmp, directory, 'render-review.py'), 'w'
            ) as f:
                f.write('# render-review.py\n')
        for directory in schema_dirs:
            os.makedirs(os.path.join(tmp, directory), exist_ok=True)
            with open(
                os.path.join(tmp, directory, 'review-schema.json'), 'w'
            ) as f:
                f.write('{}\n')
        return tmp

    def _check(self, script_dirs, schema_dirs):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, script_dirs, schema_dirs)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': False}
            )

    def test_script_beside_its_schema_passes(self):
        result = self._check(['tools'], ['tools'])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_script_without_its_schema_fails(self):
        result = self._check(['tools'], [])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('tools/render-review.py', result['details'])
        self.assertIn('review-schema.json', result['details'])

    def test_a_schema_in_a_different_directory_does_not_count(self):
        # The lookup is relative to the script, not to the repository, so
        # a schema filed somewhere tidier does not help it.
        result = self._check(['tools'], ['schemas'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('tools/render-review.py', result['details'])

    def test_every_broken_copy_is_reported_not_just_the_first(self):
        # Both copies are broken, so reporting one of them is a partial
        # answer that reads like a complete one. An earlier version of
        # this test left the second copy's schema in place, which meant
        # it passed against a check that stopped at the first finding.
        result = self._check(['tools', 'contrib'], [])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('tools/render-review.py', result['details'])
        self.assertIn('contrib/render-review.py', result['details'])

    def test_a_good_copy_alongside_a_broken_one_is_not_reported(self):
        result = self._check(['tools', 'contrib'], ['contrib'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('tools/render-review.py', result['details'])
        self.assertNotIn('contrib/render-review.py', result['details'])

    def test_a_repository_with_no_copy_at_all_is_unaffected(self):
        result = self._check([], [])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_git_directory_is_not_walked(self):
        # .git can hold anything, including checked-out worktree state
        # from another branch. Findings from in there are not actionable.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, [], [])
            os.makedirs(os.path.join(tmp, '.git', 'stash'))
            with open(
                os.path.join(tmp, '.git', 'stash', 'render-review.py'), 'w'
            ) as f:
                f.write('# stale\n')
            result = audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': False}
            )
        self.assertEqual(result['status'], 'pass', result['details'])


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
        for wf in ('pr-address-comments.yml', 'pr-retest.yml'):
            with open(os.path.join(workflows, wf), 'w') as f:
                f.write('uses: shakenfist/actions/'
                        'review-pr-with-claude@main\n')
        if re_review_body is not None:
            with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
                f.write(re_review_body)
        # Keep the render-review.py check quiet.
        os.makedirs(os.path.join(tmp, 'tools'))
        for name in ('render-review.py', 'review-schema.json'):
            with open(os.path.join(tmp, 'tools', name), 'w') as f:
                f.write('x\n')
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


if __name__ == '__main__':
    unittest.main()
