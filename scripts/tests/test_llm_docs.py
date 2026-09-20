#!/usr/bin/env python3

"""Tests for audit/checks/llm_docs.py.

Run with: python3 scripts/tests/test_llm_docs.py
"""

# audit-ok: plan-reference-file
#
# The PLAN- paths below are fixtures, not pointers. The llm-doc-structure
# check treats a reference to docs/plans/ as not counting as a pointer
# into docs/, and testing that means naming a plan that does not exist.
# The marker sits on the file rather than on the lines because a fixture
# path is what every plan reference in here will ever be.

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import llm_docs  # noqa: E402
from tests.base import CheckTestCase, FixtureRepo  # noqa: E402

LLM_DOC_STRUCTURE_OK = llm_docs.LLM_DOC_STRUCTURE_OK

orphan_skill_markdown = llm_docs.orphan_skill_markdown


class LlmDocStructureTest(CheckTestCase):
    check_class = llm_docs.LlmDocStructure

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
        """docs maps docs/-relative filenames to content.

        Each call builds its own fixture rather than adding to the one
        setUp made: the case comparing the two line caps runs the check
        twice, and the over-long AGENTS.md written by the first call
        would still be on disk for the second, which expects a pass.
        """
        self.fixture = FixtureRepo(self.tempdir())
        self.fixture.write_all({
            name: content
            for name, content in (('AGENTS.md', agents),
                                  ('ARCHITECTURE.md', architecture))
            if content is not None
        })
        if docs is not None:
            os.mkdir(os.path.join(self.fixture.path, 'docs'))
            self.fixture.write_all({
                os.path.join('docs', name): content
                for name, content in docs.items()
            })
        return self.check()

    def test_not_applicable_without_either_file(self):
        self.assert_skip(self._check())

    def test_summary_sized_files_pass(self):
        result = self._check(
            self.AGENTS, self.ARCHITECTURE, docs={'usage.md': 'Frob.\n'}
        )
        self.assert_pass(result)

    def test_one_file_alone_is_still_checked(self):
        result = self._check(agents=self.AGENTS + ('filler\n' * 400))
        self.assert_fail(result, containing='AGENTS.md is')

    def test_agents_line_cap_is_tighter_than_architecture(self):
        # 400 lines: over the AGENTS.md cap, under the
        # ARCHITECTURE.md one. AGENTS.md is loaded into every
        # session, so it pays for its length on every task.
        body = '\n'.join(f'line {n}' for n in range(400)) + '\n'
        self.assert_fail(self._check(agents=self.AGENTS + body))
        self.assert_pass(
            self._check(architecture=self.ARCHITECTURE + body))

    def test_word_cap_fails_on_few_lines(self):
        result = self._check(agents=self.AGENTS + ('word ' * 3000))
        self.assert_fail(result, containing='words')

    def test_missing_docs_reference_fails_when_docs_exist(self):
        result = self._check(
            agents='# AGENTS.md\n\nNo pointers here.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assert_fail(result, containing='references no page under docs/')

    def test_backticked_docs_path_counts_as_a_reference(self):
        # These files are read on GitHub and by agents, not rendered
        # off-site, so a backticked path points as well as a link.
        result = self._check(
            agents='# AGENTS.md\n\nSee `docs/usage.md`.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assert_pass(result)

    def test_plan_reference_alone_does_not_count(self):
        # A plan is a design record, not the documentation these
        # files should be delegating to.
        result = self._check(
            agents='# AGENTS.md\n\nSee `docs/plans/PLAN-frob.md`.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assert_fail(result)

    def test_docs_reference_not_required_without_docs_dir(self):
        result = self._check(agents='# AGENTS.md\n\nNo pointers here.\n')
        self.assert_pass(result)

    def test_docs_holding_only_plans_is_not_a_docs_dir(self):
        # client-python's docs/ contains nothing but plans/, so there
        # is no documentation page to delegate to.
        self.fixture.write('docs/plans/PLAN-frob.md', '# Plan\n')
        self.fixture.write('AGENTS.md', '# AGENTS.md\n\nNo pointers here.\n')
        self.assert_pass(self.check())

    def test_shared_heading_between_files_fails(self):
        result = self._check(
            self.AGENTS + '\n## Code Organisation\n\nCrates.\n',
            self.ARCHITECTURE + '\n## code organisation\n\nCrates.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assert_fail(result, containing='share the headings')
        self.assertIn('code organisation', result['details'])

    def test_shared_heading_can_be_suppressed(self):
        marker = LLM_DOC_STRUCTURE_OK
        result = self._check(
            self.AGENTS + f'\n## Testing {marker}\n\nHow to run.\n',
            self.ARCHITECTURE + '\n## Testing\n\nWhere they live.\n',
            docs={'usage.md': 'Frob.\n'},
        )
        self.assert_pass(result)

    def test_heading_restating_a_docs_page_fails(self):
        result = self._check(
            architecture=self.ARCHITECTURE + '\n## Configuration\n\n.vv.\n',
            docs={'configuration.md': 'Every flag.\n'},
        )
        self.assert_fail(result, containing='docs/configuration.md')

    def test_docs_page_heading_match_ignores_hyphens_and_case(self):
        result = self._check(
            agents=self.AGENTS + '\n## Control Socket\n\nVerbs.\n',
            docs={'control-socket.md': 'The wire protocol.\n'},
        )
        self.assert_fail(result)

    def test_docs_index_heading_is_allowed(self):
        # "## Index" pointing at docs/index.md is the behaviour the
        # audit wants, not a duplication finding.
        result = self._check(
            agents=self.AGENTS + '\n## Index\n\nStart here.\n',
            docs={'index.md': 'Contents.\n'},
        )
        self.assert_pass(result)

    def test_headings_in_code_blocks_are_ignored(self):
        fenced = '\n```markdown\n## Configuration\n```\n'
        result = self._check(
            agents=self.AGENTS + fenced,
            architecture=self.ARCHITECTURE + fenced,
            docs={'configuration.md': 'Every flag.\n'},
        )
        self.assert_pass(result)


class OrphanSkillMarkdownTest(CheckTestCase):
    """Markdown in a skills directory that will never load."""

    check_class = llm_docs.LlmContextLint

    # Kept for the four cases which call orphan_skill_markdown()
    # directly: those test the helper against a directory, not the
    # check against the fixture repository, so they build their own.
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
                orphan_skill_markdown(tmp),
                ['.claude/skills/debug-ci.md'],
            )

    def test_a_real_skill_is_not_an_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                '.claude/skills/debug-ci/SKILL.md': '---\nname: x\n---\n',
            })
            self.assertEqual(orphan_skill_markdown(tmp), [])

    def test_directory_without_skill_md_is_an_orphan(self):
        # A directory of markdown with no SKILL.md loads nothing, and
        # skillsaw never sees it either, so only this check can report it.
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {'.claude/skills/debug-ci/notes.md': '# n\n'})
            self.assertEqual(
                orphan_skill_markdown(tmp),
                ['.claude/skills/debug-ci/ (no SKILL.md)'],
            )

    def test_readme_beside_the_skills_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, {
                '.claude/skills/README.md': '# Skills\n',
                '.claude/skills/debug-ci/SKILL.md': '---\nname: x\n---\n',
            })
            self.assertEqual(orphan_skill_markdown(tmp), [])

    def test_repo_without_skills_is_not_applicable(self):
        self.assert_skip(self.check())

    def test_orphans_fail_the_check(self):
        # Guards the reason this check exists in Python: skillsaw
        # cannot see these files, so a clean skillsaw run must not be
        # enough to pass.
        self.fixture.write('.claude/skills/debug-ci.md', '# Debug\n')
        original = llm_docs.skillsaw_errors
        llm_docs.skillsaw_errors = lambda path: []
        try:
            result = self.check()
        finally:
            llm_docs.skillsaw_errors = original
        self.assert_fail(result, containing='debug-ci.md')

    def test_missing_skillsaw_is_not_applicable(self):
        # A missing binary is the harness's problem. Failing would file
        # an issue against every repository for something none of them
        # can fix.
        self.fixture.write('CLAUDE.md', '# Context\n')
        original = llm_docs.skillsaw_errors
        llm_docs.skillsaw_errors = lambda path: None
        try:
            result = self.check()
        finally:
            llm_docs.skillsaw_errors = original
        self.assert_skip(result)


class LlmContextLintCiTest(CheckTestCase):
    """skillsaw runs per commit, not only in the daily audit."""

    check_class = llm_docs.LlmContextLintCi

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

    def _check(self, files):
        """The check over a repository made of exactly these files.

        Each call builds its own fixture rather than adding to the one
        setUp made: the case showing the invocation form is not pinned
        runs the check once per spelling, and a workflow left behind by
        the previous spelling would answer for the next.
        """
        self.fixture = FixtureRepo(self.tempdir())
        self.fixture.write_all(files)
        return self.check()

    def _with_workflow(self, workflow):
        """The verdict for a repository whose only variable is CI."""
        return self._check({
            'CLAUDE.md': '# Context\n',
            '.pre-commit-config.yaml': self.PRE_COMMIT,
            '.github/workflows/lint.yml': workflow,
        })

    def test_both_present_passes(self):
        self.assert_pass(self._with_workflow(self.WORKFLOW))

    def test_pre_commit_alone_fails(self):
        # Pre-commit is advisory: --no-verify skips it, and a clone
        # that never ran `pre-commit install` never had it.
        result = self._check({
            'CLAUDE.md': '# Context\n',
            '.pre-commit-config.yaml': self.PRE_COMMIT,
        })
        self.assert_fail(result, containing='CI workflow')

    def test_ci_running_pre_commit_counts(self):
        # A workflow which runs pre-commit runs every hook the config
        # declares, skillsaw included, so the linter reaches CI without
        # the workflow naming the upstream repository.
        result = self._check({
            'CLAUDE.md': '# Context\n',
            '.pre-commit-config.yaml': self.PRE_COMMIT,
            '.github/workflows/ci.yml': (
                'jobs:\n'
                '  lint:\n'
                '    steps:\n'
                '      - run: pre-commit run --all-files\n'
            ),
        })
        self.assert_pass(result)

    def test_ci_running_pre_commit_without_the_hook_still_fails(self):
        # The indirection only counts when the hook is actually
        # declared; otherwise pre-commit runs everything except
        # skillsaw and the repository has no linting at all.
        result = self._check({
            'CLAUDE.md': '# Context\n',
            '.pre-commit-config.yaml': 'repos: []\n',
            '.github/workflows/ci.yml': (
                'jobs:\n'
                '  lint:\n'
                '    steps:\n'
                '      - run: pre-commit run --all-files\n'
            ),
        })
        self.assert_fail(result)

    def test_a_commented_pre_commit_run_does_not_count(self):
        result = self._check({
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
        self.assert_fail(result)

    def test_ci_alone_fails(self):
        result = self._check({
            'CLAUDE.md': '# Context\n',
            '.github/workflows/lint.yml': self.WORKFLOW,
        })
        self.assert_fail(result, containing='.pre-commit-config.yaml')

    def test_a_comment_does_not_count(self):
        # A workflow that only mentions skillsaw in a header comment
        # describes a thing it does not do.
        result = self._with_workflow(
            '# stbenjam/skillsaw runs in the other lane\n'
            'jobs:\n  lint:\n    steps: []\n'
        )
        self.assert_fail(result)

    def test_direct_invocation_passes(self):
        # A workflow can also invoke skillsaw directly after installing
        # it from PyPI, naming neither the upstream repository nor
        # pre-commit.
        self.assert_pass(self._with_workflow(self.DIRECT_RUN_WORKFLOW))

    def test_install_without_invocation_fails(self):
        # Installing skillsaw is not running it. The anchor in
        # SKILLSAW_RUN_RE is what separates the two -- without it, this
        # case would be indistinguishable from a real invocation, since
        # the bare word "skillsaw" appears on the install line too.
        result = self._with_workflow(
            'jobs:\n'
            '  lint:\n'
            '    steps:\n'
            '      - run: pip install skillsaw\n'
        )
        self.assert_fail(result, containing='CI workflow')

    def test_a_wrapped_install_without_invocation_fails(self):
        # The shape both of this repository's own workflows use: the
        # line wrap this project enforces puts the pinned package on a
        # line of its own, where a bare word boundary sits happily
        # between 'w' and '=' and reads it as a command. Requiring
        # whitespace or end of line after the token is what separates
        # a wrapped install from an invocation.
        result = self._with_workflow(
            'jobs:\n'
            '  lint:\n'
            '    steps:\n'
            '      - run: |\n'
            '          /tmp/venv/bin/pip install --disable-pip-version-check \\\n'
            '              skillsaw==0.18.0\n'
        )
        self.assert_fail(result, containing='CI workflow')

    def test_a_version_probe_is_not_a_lint_run(self):
        # This repository's consistency-audit.yml asserts the pinned
        # release exactly this way. It proves the install worked; it
        # never looks at the tree, so it cannot be the CI half.
        result = self._with_workflow(
            'jobs:\n'
            '  lint:\n'
            '    steps:\n'
            '      - run: |\n'
            "          echo \"reports: $(skillsaw --version)\"\n"
            "          skillsaw --version | grep -qx 'skillsaw 0.18.0'\n"
        )
        self.assert_fail(result, containing='CI workflow')

    def test_a_yaml_key_is_not_an_invocation(self):
        # instar and ryll both name a 'skillsaw:' job whose steps run
        # the pre-commit hook. The job key is a label, not a command,
        # and counting it would credit the CI half to any repository
        # that merely names a job after the linter.
        result = self._with_workflow(
            'jobs:\n'
            '  skillsaw:\n'
            '    name: skillsaw\n'
            '    steps:\n'
            '      - run: true\n'
        )
        self.assert_fail(result, containing='CI workflow')

    def test_the_invocation_form_is_not_pinned(self):
        # A single-command step is normally written inline rather than
        # in a block scalar, and a PyPI install pairs naturally with a
        # runner or an explicit venv path. Matching only one YAML
        # formatting choice would file a consistency issue against a
        # repository that does run the linter.
        for step in (
            '      - run: skillsaw --no-custom-rules .\n',
            '      - run: skillsaw .\n',
            '      - run: uvx skillsaw .\n',
            '      - run: uv run skillsaw .\n',
            '      - run: python -m skillsaw .\n',
            '      - run: /tmp/venv/bin/skillsaw .\n',
            '      - run: |\n          cd src && skillsaw .\n',
        ):
            with self.subTest(step=step.strip()):
                result = self._with_workflow(
                    'jobs:\n  lint:\n    steps:\n' + step)
                self.assert_pass(result)

    def test_direct_invocation_in_a_comment_does_not_count(self):
        # A full-line comment describes what runs elsewhere; it is not
        # itself an invocation. The fixture is a commented-out step
        # rather than prose about skillsaw, because prose could not
        # match the pattern in the first place -- comment stripping
        # has to be the thing that makes this fail, or the test passes
        # for a reason unrelated to what it claims to check.
        result = self._with_workflow(
            'jobs:\n'
            '  lint:\n'
            '    steps:\n'
            '      # - run: skillsaw --no-custom-rules .\n'
            '      - run: true\n'
        )
        self.assert_fail(result)

    def test_direct_invocation_without_pre_commit_hook_fails(self):
        # CI running skillsaw directly does not excuse the pre-commit
        # side -- the two halves are independent obligations, and the
        # failure must name the pre-commit half, not the CI half.
        result = self._check({
            'CLAUDE.md': '# Context\n',
            '.pre-commit-config.yaml': 'repos: []\n',
            '.github/workflows/lint.yml': self.DIRECT_RUN_WORKFLOW,
        })
        self.assert_fail(result, containing='.pre-commit-config.yaml')
        self.assertNotIn('CI workflow', result['details'])

    def test_repo_without_context_is_not_applicable(self):
        self.assert_skip(self.check())


if __name__ == '__main__':
    unittest.main()
