#!/usr/bin/env python3

"""Tests for audit/checks/docs_content.py.

Run with: python3 scripts/tests/test_docs_content.py
"""

# audit-ok: plan-reference-file
#
# The PLAN- paths below are fixtures, not pointers. These criteria are
# tested by naming plans that do not resolve, so the marker belongs to
# the file rather than to any one line of it.

import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import docs_content  # noqa: E402
from audit.checks import llm_docs as llm_docs_module  # noqa: E402
from tests.base import CheckTestCase, run_check  # noqa: E402


def check_readme_structure(path, props=None):
    return run_check(docs_content.ReadmeStructure(), path, props)


def check_docs_external_links(path, props=None):
    return run_check(docs_content.DocsExternalLinks(), path, props)


def check_diagram_format(path, props=None):
    return run_check(docs_content.DiagramFormat(), path, props)


def check_mermaid_lint_ci(path, props=None):
    return run_check(docs_content.MermaidLintCi(), path, props)


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
            return check_readme_structure(tmp, {})

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
            return check_docs_external_links(tmp, props or {})

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
            return check_diagram_format(tmp, props or {})

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
            return check_mermaid_lint_ci(tmp, {})

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
        narrow form the script selects on.

        The two then part company about what to do with such a block:
        the audit declines to count it, the script refuses it. See
        MermaidLintScriptTest.test_a_tilde_fence_is_refused, which
        pins the other half of that deliberate disagreement.
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
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
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

    def test_the_skill_pins_the_same_image(self):
        """The image is named in two places, so it can be bumped in one.

        The skill hands an agent a copy-pasteable docker run. A tag is
        mutable, so both references carry the digest; a bump that
        moves only one of them leaves the other pulling something
        nobody chose, which is the whole reason for pinning.
        """
        script = self._repo('tools', 'mermaid-lint.sh').decode('utf-8')
        skill = self._repo('.claude', 'skills', 'diagram-conversion',
                           'SKILL.md').decode('utf-8')

        # The script composes the reference from a tag and a digest,
        # so that neither line has to be 129 characters wide.
        script_tags = re.findall(r'mermaid-cli:([0-9][^"@\s]*)', script)
        script_digests = re.findall(r'@(sha256:[0-9a-f]{64})', script)
        skill_refs = re.findall(
            r'mermaid-cli:([0-9][^@\s]*)@(sha256:[0-9a-f]{64})', skill)

        self.assertEqual(len(script_tags), 1, script_tags)
        self.assertEqual(len(script_digests), 1, script_digests)
        self.assertEqual(len(skill_refs), 1, skill_refs)
        self.assertEqual([(script_tags[0], script_digests[0])], skill_refs)


class MermaidLintScriptTest(unittest.TestCase):
    """The script's own behaviour, run rather than read.

    Everything here stops short of the container: a repository with
    nothing lintable, and one the script refuses outright, are both
    decided before docker is invoked. That is what makes the central
    behaviour of this lane testable in a suite that has no docker --
    and it needs testing, because a linter whose failure mode is
    exiting zero cannot be checked by reading it.
    """

    # A stub on PATH rather than the real container. What the script
    # decides -- which files it selects, and how it combines a refusal
    # with the renderer's verdict -- is decided before docker is
    # reached, so stubbing it makes the positive path assertable in a
    # suite that has no docker. Without it every case here asserts a
    # refusal or an empty result, and a scanner that selected nothing
    # at all would pass the lot.
    STUB = ('#!/bin/sh\n'
            'printf "%s\\n" "$@" >> "${MERMAID_LINT_TEST_ARGV}"\n'
            'exit ${MERMAID_LINT_TEST_DOCKER_RC}\n')

    BACKTICK = '# P\n\n```mermaid\nflowchart TB\n  a --> b\n```\n'
    TILDE = '# P\n\n~~~mermaid\nflowchart TB\n  a --> b\n~~~\n'

    def _run(self, files, args=(), docker_rc=0):
        """Lint a throwaway repository built from {path: content}."""
        env = dict(os.environ)
        # A stray global excludesFile or template would change what
        # git ls-files reports, and the script trusts that list.
        env['GIT_CONFIG_GLOBAL'] = os.devnull
        env['GIT_CONFIG_SYSTEM'] = os.devnull

        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            'tools', 'mermaid-lint.sh')

        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(['git', 'init', '-q', repo],
                           check=True, env=env)
            for name, content in files.items():
                target = os.path.join(repo, name)
                os.makedirs(os.path.dirname(target) or repo, exist_ok=True)
                # Binary, so that a fixture can carry CRLF endings
                # without the platform rewriting them.
                with open(target, 'wb') as f:
                    f.write(content.encode('utf-8'))
            # Tracked, not merely present: the script walks the index.
            subprocess.run(['git', 'add', '-A'],
                           cwd=repo, check=True, env=env)

            with tempfile.TemporaryDirectory() as stub_dir:
                stub = os.path.join(stub_dir, 'docker')
                with open(stub, 'w') as f:
                    f.write(self.STUB)
                os.chmod(stub, 0o755)
                env['PATH'] = stub_dir + os.pathsep + env['PATH']
                env['MERMAID_LINT_TEST_ARGV'] = os.path.join(
                    stub_dir, 'argv')
                env['MERMAID_LINT_TEST_DOCKER_RC'] = str(docker_rc)
                result = subprocess.run(
                    [script, *args], cwd=repo, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    universal_newlines=True)
                result.docker_ran = os.path.exists(env[
                    'MERMAID_LINT_TEST_ARGV'])
                return result

    def test_a_tilde_fence_is_refused(self):
        """Refused, not skipped -- and the message says what to do.

        GitHub renders a ~~~mermaid block and mmdc reads nothing in
        one, so skipping it ships an unlinted diagram while the run
        reports success. The audit's matching half is
        test_a_tilde_fence_does_not_make_it_applicable above.
        """
        result = self._run({'docs/x.md': self.TILDE})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('docs/x.md', result.stderr)
        self.assertIn('use a backtick fence', result.stderr)
        self.assertNotIn('nothing to lint', result.stdout)

    def test_a_named_file_is_refused_the_same_way(self):
        """The per-file form is the one a person reaches for by hand."""
        result = self._run(
            {'docs/x.md': '# P\n\n~~~mermaid\nflowchart TB\n~~~\n'},
            args=('docs/x.md',))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('use a backtick fence', result.stderr)

    def test_a_fence_inside_a_longer_fence_is_an_example(self):
        """Writing about the rule must not fail the repository.

        A page explaining that tilde fences are refused contains a
        tilde fence, wrapped in a longer one. A line match would
        refuse that page; the scan tracks fence state instead.
        templates/mermaid-lint/README.md is a real instance.
        """
        result = self._run({
            'docs/x.md': (
                '# P\n\n````markdown\n~~~mermaid\n'
                'flowchart TB\n  a --> b\n~~~\n````\n'
            ),
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nothing to lint', result.stdout)

    def test_a_repository_with_no_diagrams_passes(self):
        result = self._run({'docs/x.md': '# P\n\nProse only.\n'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nothing to lint', result.stdout)

    def test_a_missing_named_file_is_an_error(self):
        """A typo must not be an empty run that exits zero."""
        result = self._run({'docs/x.md': '# P\n'}, args=('docs/nope.md',))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('no such file', result.stderr)
        self.assertFalse(result.docker_ran)

    def test_a_backtick_fence_is_selected_and_rendered(self):
        """The positive path, which the refusal cases cannot pin.

        A scanner that classified nothing at all would satisfy every
        other case here while linting nothing, ever, and exiting 0 --
        which is this lane's defining failure. So assert that the file
        reaches the renderer.
        """
        result = self._run({'docs/x.md': self.BACKTICK})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Linting 1 file(s)', result.stdout)
        self.assertTrue(result.docker_ran)

    def test_a_crlf_file_is_still_selected(self):
        """mmdc reads CRLF, so the scanner must too.

        A carriage return left on the line would land in the info
        string, so no fence would open and none would close, and the
        whole file would go unlinted behind a green run.
        """
        result = self._run(
            {'docs/x.md': self.BACKTICK.replace('\n', '\r\n')})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Linting 1 file(s)', result.stdout)

    def test_a_crlf_tilde_fence_is_still_refused(self):
        result = self._run(
            {'docs/x.md': self.TILDE.replace('\n', '\r\n')})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('use a backtick fence', result.stderr)

    def test_a_space_before_the_language_is_refused(self):
        """GitHub renders it and mmdc reads nothing in it.

        That is the same failure as a tilde fence, so it gets the same
        answer. Selecting the file instead would be worse than the old
        grep, which at least did not print "ok" for a diagram nothing
        rendered.
        """
        result = self._run({'docs/x.md': self.BACKTICK.replace(
            '```mermaid', '``` mermaid')})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('remove the space', result.stderr)
        self.assertFalse(result.docker_ran)

    def test_a_refusal_and_a_lint_are_reported_together(self):
        """One virtual machine, both halves of the answer."""
        result = self._run({
            'docs/good.md': self.BACKTICK,
            'docs/bad.md': self.TILDE,
        })
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('use a backtick fence', result.stderr)
        self.assertIn('Linting 1 file(s)', result.stdout)
        self.assertTrue(result.docker_ran)

    def test_a_failing_render_fails_the_run(self):
        """The renderer's verdict is the point of the script.

        Asserted with 125 rather than 1 so that it also pins the
        status reaching the caller unchanged: a failed image pull
        stays distinguishable from a diagram that does not parse, and
        a pipeline added around the docker call -- the mistake the
        script's own comment warns about -- would flatten it.
        """
        result = self._run({'docs/x.md': self.BACKTICK}, docker_rc=125)
        self.assertEqual(result.returncode, 125, result.stderr)

    def test_a_refusal_survives_a_clean_render(self):
        """rc is combined, not overwritten by the renderer's success."""
        result = self._run({
            'docs/good.md': self.BACKTICK,
            'docs/bad.md': self.TILDE,
        }, docker_rc=0)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_a_closed_fence_lets_the_next_one_open(self):
        """Fence state must clear, or nothing after the first opens.

        A scan that never recognised a closing fence would skip every
        diagram below the first fence in the file -- and would still
        satisfy the nesting case above, which asserts only that
        something is skipped.
        """
        result = self._run({'docs/x.md': (
            '# P\n\n```text\nnot a diagram\n```\n\n' + self.BACKTICK
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Linting 1 file(s)', result.stdout)

    def test_a_shorter_fence_does_not_close_a_longer_one(self):
        """CommonMark closes on a run at least as long, not any run.

        Without the length rule the lone ``` below would close the
        outer fence, putting the tilde example at the top level and
        refusing a page that is only quoting one.
        """
        result = self._run({'docs/x.md': (
            '# P\n\n````markdown\n```\n~~~mermaid\nflowchart TB\n'
            '~~~\n````\n'
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nothing to lint', result.stdout)

    def test_a_different_character_does_not_close_a_fence(self):
        """A tilde fence is not closed by backticks, or the reverse.

        Without the character rule the backtick run below would close
        the tilde fence, and the quoted diagram would be selected and
        sent to the renderer as though it were real.
        """
        result = self._run({'docs/x.md': (
            '# P\n\n~~~~markdown\n````\n```mermaid\nflowchart TB\n'
            '```\n~~~~\n'
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nothing to lint', result.stdout)
        self.assertFalse(result.docker_ran)

    def test_a_fence_carrying_an_info_string_does_not_close(self):
        """Only a bare fence closes, or a quoted example leaks out.

        The ````python line below is content, not a close. Treating it
        as one would put the tilde example at the top level and refuse
        a page that is merely showing two fenced blocks.
        """
        result = self._run({'docs/x.md': (
            '# P\n\n````markdown\n````python\n~~~mermaid\n'
            'flowchart TB\n~~~\n````\n'
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nothing to lint', result.stdout)

    def test_inline_code_at_the_start_of_a_line_is_not_a_fence(self):
        """Under three characters is not a fence, and must not open one.

        A line beginning with a single backtick is ordinary prose. If
        it opened a fence, every diagram below it in the file would be
        swallowed as fence content and go unlinted -- silently, and
        behind a green run.
        """
        result = self._run({'docs/x.md': (
            '# P\n\n`config.yaml` is read at startup.\n\n'
            + self.BACKTICK
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Linting 1 file(s)', result.stdout)

    def test_a_closed_tilde_fence_lets_a_diagram_below_it_open(self):
        result = self._run({'docs/x.md': (
            '# P\n\n~~~text\nnot a diagram\n~~~\n\n' + self.TILDE
        )})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('use a backtick fence', result.stderr)

    def test_a_fence_indented_inside_a_list_item_is_still_linted(self):
        """Four spaces is a list item far more often than a quote.

        The scan does not model indented code blocks, deliberately: a
        fence indented under a list item is an ordinary diagram, and
        skipping it would fail open on real content to spare a rarer
        false positive. templates/mermaid-lint/README.md documents the
        consequence -- quote a fence by nesting, not by indenting.
        """
        result = self._run({'docs/x.md': (
            '# P\n\n- item\n\n    ```mermaid\n    flowchart TB\n'
            '      a --> b\n    ```\n'
        )})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Linting 1 file(s)', result.stdout)


class ReadmeAbsoluteLinksTest(CheckTestCase):
    """README.md is rendered off the landing page, where relative breaks."""

    check_class = docs_content.ReadmeAbsoluteLinks

    def test_without_a_readme_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No top-level README.md')

    def test_absolute_links_pass(self):
        self.fixture.write('README.md', (
            '# Thing\n\n'
            'See [the docs](https://github.com/shakenfist/x/blob/main/'
            'docs/usage.md).\n'))
        self.assert_pass(self.check())

    def test_a_relative_link_fails_and_names_it(self):
        self.fixture.write('README.md', '# Thing\n\nSee [docs](docs/usage.md).\n')
        self.assert_fail(self.check(), containing='docs/usage.md')

    def test_a_reference_definition_is_checked_too(self):
        self.fixture.write('README.md',
                           '# Thing\n\nSee [docs][d].\n\n[d]: docs/usage.md\n')
        self.assert_fail(self.check(), containing='docs/usage.md')

    def test_a_link_inside_a_fence_is_a_sample_not_a_link(self):
        self.fixture.write('README.md',
                           '# Thing\n\n```\n[docs](docs/usage.md)\n```\n')
        self.assert_pass(self.check())

    def test_an_anchor_is_not_a_relative_path(self):
        self.fixture.write('README.md', '# Thing\n\n[top](#thing)\n')
        self.assert_pass(self.check())


class LlmToolingTest(CheckTestCase):
    check_class = llm_docs_module.LlmTooling

    def test_both_files_present_passes(self):
        self.fixture.write('AGENTS.md', '# Agents\n')
        self.fixture.write('ARCHITECTURE.md', '# Architecture\n')
        self.assert_pass(self.check())

    def test_a_missing_file_is_named(self):
        self.fixture.write('AGENTS.md', '# Agents\n')
        result = self.assert_fail(self.check(), containing='ARCHITECTURE.md')
        self.assertEqual(result['missing'], ['ARCHITECTURE.md'])

    def test_both_missing_are_named(self):
        result = self.assert_fail(self.check())
        self.assertEqual(result['missing'], ['AGENTS.md', 'ARCHITECTURE.md'])


if __name__ == '__main__':
    unittest.main()
