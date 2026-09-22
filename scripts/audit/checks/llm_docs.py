"""The criteria about agent-facing context.

`AGENTS.md` and `ARCHITECTURE.md` exist, carry the name every agent
reads rather than one vendor's, say what only they can say rather than
restating `docs/`, and the skills beside them are linted.

Five criteria, one subject: what an agent reads when it opens the
repository, and whether anything keeps it honest.
"""

import json
import os
import re
import subprocess

from audit.check import Check
from audit.files import check_file_exists, file_mentions, tracked_paths
from audit.text.markdown import iter_markdown_headings, normalise_heading


# AGENTS.md / ARCHITECTURE.md structure limits: both files are a
# summary and an index into docs/, not reference manuals. AGENTS.md
# is loaded into every session, so it gets the tighter cap.
# See docs/audits/llm-doc-structure.md.
LLM_DOC_LIMITS = {
    'AGENTS.md': (300, 2500),
    'ARCHITECTURE.md': (500, 4000),
}


LLM_DOC_STRUCTURE_OK = '<!-- audit-ok: llm-doc-structure -->'


# A reference to a documentation page under docs/, in any form: a
# markdown link target, an inline-code path, or bare prose. Unlike
# README.md -- which is rendered off the repository landing page and
# so needs real absolute links (see readme-absolute-links) -- these
# two files are read on GitHub and by agents, where a backticked
# `docs/design-tokens.md` points just as well as a link does.
# docs/plans/ is excluded: a plan is a design record, not the
# documentation these files should be delegating to.
DOCS_PAGE_REFERENCE_RE = re.compile(r'\bdocs/(?!plans/)[\w./-]+\.md\b')


# Directories whose markdown is loaded as agent context. A skill is a
# directory holding SKILL.md; a bare markdown file alongside them is
# inert, which is the failure this audit exists to catch.
SKILL_ROOTS = ('.claude/skills', '.codex/skills')


# Markdown that legitimately sits beside skill directories rather than
# being a skill itself.
ALLOWED_LOOSE_SKILL_FILES = ('readme.md', 'index.md')


# Files whose presence means a repository has agent context worth
# linting. A repository with none of these has nothing for skillsaw to
# look at, and reporting it either way would be noise.
#
# CLAUDE.md and GEMINI.md are here although llm-doc-naming asks for
# them to be renamed, and that is not a contradiction: a repository
# part-way through the rename still has context an agent loads, and
# switching the linter off until it finishes would leave the files
# most in need of linting unlinted.
AGENT_CONTEXT_MARKERS = (
    '.claude', '.codex', 'AGENTS.md', 'CLAUDE.md', 'GEMINI.md',
)


# Agent instruction files named for one vendor rather than for the
# job. `AGENTS.md` is the name every agent now reads -- Claude Code,
# Codex and Gemini CLI all load it -- so a file named for a single
# tool is no longer the way to reach that tool. It is either a copy of
# AGENTS.md that will drift, or a second set of instructions loaded
# alongside it, and both are read as authoritative by whichever agent
# recognises the name.
#
# Matched on the basename at any depth, case-insensitively. That is
# what catches `.claude/CLAUDE.md` and `.gemini/GEMINI.md` without
# naming those directories, and a nested `subdir/CLAUDE.md`, which is
# loaded when an agent works in that subdirectory and is the copy
# nobody remembers to update.
#
# CLAUDE.local.md is in the set although it is meant to be a personal
# override: the paths come from `git ls-files`, so an untracked one is
# invisible here and a tracked one is a personal override shipped to
# everybody.
#
# The set is a constant so that it can grow. GitHub Copilot's
# `.github/copilot-instructions.md` is the obvious next member and is
# deliberately not in it yet: nothing in the fleet has one, and a rule
# is easier to defend when every repository it names is a repository
# we have looked at.
VENDOR_AGENT_DOCS = frozenset({
    'claude.md', 'claude.local.md', 'gemini.md',
})


# How a repository is expected to invoke skillsaw. The pre-commit hook
# and the action both live in the upstream repository, so one string
# identifies either.
SKILLSAW_SOURCE = 'stbenjam/skillsaw'


# A workflow can also invoke skillsaw directly after installing it from
# PyPI, naming neither the upstream repository nor pre-commit. Two
# distinctions are load-bearing, and both are about not mistaking a
# mention of the package for a run of the linter.
#
# Naming it is not running it. The token has to be followed by
# whitespace or the end of the line, which is why a bare word boundary
# is not enough: a wrapped install puts '    skillsaw==0.18.0' on a
# line of its own, as both of this repository's workflows do, and a
# word boundary sits happily between 'w' and '='. The same rule
# rejects a YAML key such as 'skillsaw: true'.
#
# Probing it is not running it either. 'skillsaw --version', which
# this repository's consistency-audit.yml uses to assert the pinned
# release, proves the install worked and nothing more. Excluding the
# two no-op flags is not the same as pinning an argument list: every
# other invocation counts, whatever its arguments.
#
# What the pattern deliberately does not pin is where the command
# sits. It may start a line inside a 'run: |' block, follow an inline
# 'run:', come after a shell operator, or be reached through a runner
# ('uvx', 'uv run', 'python -m') or an explicit path into a venv.
# Pinning one YAML formatting choice would fail repositories whose
# only sin is writing a single-command step on one line.
SKILLSAW_RUN_RE = re.compile(
    r'(?:^|[|&;(]|\brun:|\buvx|\buv\s+run|\s-m)\s*'
    r'(?:[\w./-]*/)?skillsaw(?!\S)(?!\s+(?:--version|--help|-V|-h)(?![\w=-]))'
)


# A CI job which runs pre-commit over the tree runs every hook the
# pre-commit config declares, skillsaw included. Requiring the linter
# to be named in a workflow as well would report those repositories as
# non-compliant for a wiring that does run it -- and would fail the
# reference invocation in this repository's own consistency-audit.yml,
# which installs skillsaw from PyPI and so never names the upstream
# repository either.
PRE_COMMIT_RUN_RE = re.compile(r'pre-commit\s+run\b')


def file_matches(filepath, pattern):
    """Does a file match a regex, outside of its comments?

    The comment handling matches file_mentions: a header comment
    describing what something else does must not count as doing it.
    """
    if not os.path.exists(filepath):
        return False
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            if line.lstrip().startswith('#'):
                continue
            if pattern.search(line):
                return True
    return False


def has_agent_context(repo_path):
    """Does this repository carry agent context files at all?"""
    return any(
        check_file_exists(repo_path, marker)
        for marker in AGENT_CONTEXT_MARKERS
    )


def vendor_agent_docs(paths):
    """The paths whose basename names one vendor, sorted.

    Takes the tracked list rather than reading it, so that the caller
    can ask the same list whether AGENTS.md is tracked. Asking the
    index for one and the filesystem for the other is how a gitignored
    working-copy AGENTS.md comes to be named as the destination of a
    merge it is not part of.

    The list comes from the index rather than a tree walk for two
    reasons. An untracked CLAUDE.md is somebody's scratch file in
    their own clone and not a property of the repository -- the audit
    runs against a fresh clone and would never see one, so a walk
    would report a finding that only exists when the check is run by
    hand. And a walk would have to decide what to do about the build
    output and vendored trees that WALK_SKIP exists for, where a
    CLAUDE.md belongs to a dependency rather than to us; the index
    answers that question already. A submodule falls out the same
    way and for the same reason: it is one gitlink entry, so its
    CLAUDE.md is never in the list, and it is audited in its own
    repository if it is in scope.
    """
    return sorted(
        path for path in paths
        if path.rsplit('/', 1)[-1].lower() in VENDOR_AGENT_DOCS
    )


def orphan_skill_markdown(repo_path):
    """Markdown in a skills directory that will never load as a skill.

    A skill is ``<skills dir>/<name>/SKILL.md``. Markdown directly in
    the skills directory, or in a subdirectory with no SKILL.md, is
    read by nobody: the agent does not load it, and skillsaw does not
    lint it either, because it is never discovered as a skill. That
    combination is why this is checked here in Python rather than left
    to skillsaw -- a linter cannot report a file it cannot see, and
    the resulting clean run reads as a pass.
    """
    orphans = []
    for relative in SKILL_ROOTS:
        skills_dir = os.path.join(repo_path, relative)
        if not os.path.isdir(skills_dir):
            continue

        for entry in sorted(os.listdir(skills_dir)):
            path = os.path.join(skills_dir, entry)

            if os.path.isfile(path) and entry.lower().endswith('.md'):
                if entry.lower() in ALLOWED_LOOSE_SKILL_FILES:
                    continue
                orphans.append(f'{relative}/{entry}')
                continue

            if not os.path.isdir(path):
                continue
            if os.path.exists(os.path.join(path, 'SKILL.md')):
                continue
            stray = sorted(
                name for name in os.listdir(path)
                if name.lower().endswith('.md')
            )
            if stray:
                orphans.append(f'{relative}/{entry}/ (no SKILL.md)')

    return orphans


def skillsaw_errors(repo_path):
    """Error-severity skillsaw violations, or None if it cannot run.

    Only error severity is collected. skillsaw's warning and info
    tiers carry style opinions -- unlinked path references alone run
    to dozens per repository -- and an audit that reports them would
    spend more of our time than it saves. The error tier is the
    structural subset: invalid frontmatter, malformed manifests,
    embedded secrets, smuggled unicode.
    """
    try:
        result = subprocess.run(
            [
                'skillsaw', 'lint',
                '--no-progress', '--no-custom-rules',
                '--format', 'json',
                repo_path,
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    return [
        violation for violation in report.get('violations', [])
        if violation.get('severity') == 'error'
    ]


class LlmTooling(Check):
    id = 'llm-tooling'
    spec = 'docs/audits/llm-tooling.md'
    template = None
    issue_title = 'LLM tooling'

    def run(self, repo):
        """Check for AGENTS.md and ARCHITECTURE.md."""
        missing = []
        if not repo.exists('AGENTS.md'):
            missing.append('AGENTS.md')
        if not repo.exists('ARCHITECTURE.md'):
            missing.append('ARCHITECTURE.md')

        if missing:
            return self.fail(f'Missing: {", ".join(missing)}', missing=missing)
        return self.ok('AGENTS.md and ARCHITECTURE.md both exist')


class LlmDocStructure(Check):
    id = 'llm-doc-structure'
    spec = 'docs/audits/llm-doc-structure.md'
    template = None
    issue_title = 'AGENTS.md / ARCHITECTURE.md structure'

    def run(self, repo):
        """Check AGENTS.md and ARCHITECTURE.md are a summary and an index.

        The llm-tooling check covers whether these files exist; this one
        covers their shape. "Is this a good summary" is a judgment call
        enforced at push time by the llm-doc-discipline shared block (see
        check_push_audit); this check enforces the measurable proxies: a
        length cap per file, a pointer to a docs/ page when docs/ holds
        any, and two duplication signals -- a heading shared between the
        two files, and a heading naming a page that docs/ already has.
        """
        present = {
            name: os.path.join(repo.path, name)
            for name in LLM_DOC_LIMITS
            if repo.exists(name)
        }
        if not present:
            return self.skip('No AGENTS.md or ARCHITECTURE.md')

        contents = {}
        for name, path in present.items():
            with open(path, 'r', errors='replace') as f:
                contents[name] = f.read()

        # docs/ pages the two files could be delegating to, keyed by
        # normalised filename stem. A docs/ directory holding nothing but
        # plans/ has no documentation to point at, so an empty mapping
        # switches both docs/-aware proxies off rather than demanding a
        # pointer to something that does not exist.
        docs_pages = {}
        docs_dir = os.path.join(repo.path, 'docs')
        if os.path.isdir(docs_dir):
            for entry in sorted(os.listdir(docs_dir)):
                if entry.endswith('.md') and entry != 'index.md':
                    docs_pages[normalise_heading(entry[:-3])] = f'docs/{entry}'
        has_docs = bool(docs_pages) or os.path.exists(
            os.path.join(docs_dir, 'index.md')
        )
        problems = []

        for name in sorted(contents):
            content = contents[name]
            max_lines, max_words = LLM_DOC_LIMITS[name]
            lines = len(content.splitlines())
            words = len(content.split())
            if lines > max_lines or words > max_words:
                problems.append(
                    f'{name} is {lines} lines / {words} words (limits: '
                    f'{max_lines} lines, {max_words} words); move detail '
                    f'into docs/ and leave a summary and a link'
                )

            if has_docs and not DOCS_PAGE_REFERENCE_RE.search(content):
                problems.append(
                    f'{name} references no page under docs/ despite a '
                    f'docs/ directory existing; it should point at the '
                    f'detailed documentation rather than restate it'
                )

        # Duplication signal one: the same subject documented in both
        # files. Only ## headings, because ### headings are subdivisions
        # whose names collide innocently ("Overview", "Example").
        headings = {}
        for name, content in contents.items():
            headings[name] = {
                normalise_heading(text): line
                for _, text, line in iter_markdown_headings(
                    content, levels=(2,)
                )
            }
        if len(headings) == 2:
            agents, architecture = (
                headings['AGENTS.md'], headings['ARCHITECTURE.md']
            )
            shared = sorted(
                key for key in agents.keys() & architecture.keys()
                if LLM_DOC_STRUCTURE_OK not in agents[key]
                and LLM_DOC_STRUCTURE_OK not in architecture[key]
            )
            if shared:
                problems.append(
                    'AGENTS.md and ARCHITECTURE.md share the headings '
                    + ', '.join(f'"{key}"' for key in shared)
                    + '; give each fact one home and link to it from the '
                    'other file'
                )

        # Duplication signal two: a heading naming a docs/ page. index.md
        # is excluded from docs_pages because a "## Index" style heading
        # pointing at it is exactly the behaviour we want.
        if docs_pages:
            for name in sorted(contents):
                hits = sorted({
                    f'"{text}" ({docs_pages[normalise_heading(text)]})'
                    for _, text, line in iter_markdown_headings(
                        contents[name]
                    )
                    if normalise_heading(text) in docs_pages
                    and LLM_DOC_STRUCTURE_OK not in line
                })
                if hits:
                    problems.append(
                        f'{name} has headings restating a docs/ page: '
                        + ', '.join(hits)
                        + '; summarise and link instead'
                    )

        if problems:
            return self.fail('; '.join(problems))
        return self.ok(
            'AGENTS.md and ARCHITECTURE.md are summary-sized and do '
            'not restate docs/')


class LlmContextLint(Check):
    id = 'llm-context-lint'
    spec = 'docs/audits/llm-context-lint.md'
    template = None
    issue_title = 'LLM context linting'

    def run(self, repo):
        """Check the repository's agent context passes skillsaw cleanly.

        Two findings are combined because they answer the same question
        from opposite ends: skillsaw validates the context it can see, and
        orphan_skill_markdown() finds the context it cannot.

        A missing skillsaw reports not_applicable rather than fail. The
        binary is the audit harness's responsibility, not the audited
        repository's, and failing would file an issue against every
        project in the fleet for a problem none of them can fix. The
        consistency-audit workflow installs a pinned skillsaw, so this
        state should not arise; when it does, every row flipping to N/A at
        once is the signal that it has.
        """
        if not has_agent_context(repo.path):
            return self.skip('No agent context files to lint')

        problems = []

        orphans = orphan_skill_markdown(repo.path)
        if orphans:
            problems.append(
                f'Markdown that will never load as a skill: '
                f'{", ".join(orphans)}'
            )

        errors = skillsaw_errors(repo.path)
        if errors is None:
            return self.skip('skillsaw is not available in the audit environment')

        if errors:
            described = ', '.join(
                sorted({
                    f'{violation.get("rule_id", "unknown")} '
                    f'({violation.get("file_path", "?")})'
                    for violation in errors
                })
            )
            problems.append(f'skillsaw errors: {described}')

        if problems:
            return self.fail('; '.join(problems))

        return self.ok('Agent context lints clean at error severity')


class LlmContextLintCi(Check):
    id = 'llm-context-lint-ci'
    spec = 'docs/audits/llm-context-lint-ci.md'
    template = None
    issue_title = 'LLM context linting in pre-commit and CI'

    def run(self, repo):
        """Check skillsaw runs in pre-commit and in CI.

        The daily audit is a backstop, not the feedback loop. A malformed
        skill or a smuggled instruction should be caught by the commit
        that introduces it, so the audit checks that each repository runs
        the linter itself rather than waiting to be told once a day.

        As with the secret scanner check, how skillsaw is invoked is
        deliberately not pinned. Naming the upstream repository in a
        pre-commit config and in a workflow, running it via a pre-commit
        step in CI, or invoking the `skillsaw` command directly (installed
        from PyPI rather than named as the upstream repository) all count
        -- requiring a particular rev, argument list, or install source
        would make the check brittle against reasonable variation. What
        the direct-invocation route does have to separate is running the
        linter from installing or probing it: see SKILLSAW_RUN_RE, where
        both distinctions are drawn and neither costs the check any
        tolerance for how the command is written.
        """
        if not has_agent_context(repo.path):
            return self.skip('No agent context files to lint')

        missing = []

        pre_commit_config = os.path.join(repo.path, '.pre-commit-config.yaml')
        in_pre_commit = file_mentions(pre_commit_config, SKILLSAW_SOURCE)
        if not in_pre_commit:
            missing.append('.pre-commit-config.yaml')

        workflows = [
            os.path.join(repo.path, '.github', 'workflows', workflow)
            for workflow in repo.workflows()
        ]
        named_in_ci = any(
            file_mentions(workflow, SKILLSAW_SOURCE) for workflow in workflows
        )
        # A workflow which runs pre-commit runs the skillsaw hook with it,
        # so the linter reaches CI without the workflow naming it.
        via_pre_commit = in_pre_commit and any(
            file_matches(workflow, PRE_COMMIT_RUN_RE) for workflow in workflows
        )
        # A workflow can also invoke skillsaw directly, having installed
        # it from PyPI rather than naming the upstream repository.
        run_directly = any(
            file_matches(workflow, SKILLSAW_RUN_RE) for workflow in workflows
        )
        if not named_in_ci and not via_pre_commit and not run_directly:
            missing.append('a CI workflow')

        if missing:
            return self.fail(f'skillsaw does not run from {" or ".join(missing)}')

        return self.ok('skillsaw runs in pre-commit and in CI')


class LlmDocNaming(Check):
    id = 'llm-doc-naming'
    spec = 'docs/audits/llm-doc-naming.md'
    template = None
    issue_title = 'Agent instruction file naming'

    def run(self, repo):
        """Check agent instructions live in AGENTS.md, not a vendor's file.

        `AGENTS.md` is the name every agent we use now reads, so a
        CLAUDE.md or a GEMINI.md is no longer how a project reaches a
        particular tool. What it is instead decides the fix, and the
        detail says which of the three it is:

         * a symlink to AGENTS.md is a second name for one file rather
           than a second document. It was a reasonable bridge while
           tooling caught up, and it is `git rm` and nothing else now.
         * with no AGENTS.md tracked, the vendor file *is* the
           project's agent context under a name only one tool reads.
           The fix is a rename, and llm-tooling is failing for the
           missing AGENTS.md at the same time -- this check names the
           file that rename starts from.
         * with an AGENTS.md tracked, both are loaded, and the vendor
           file is a second set of instructions with equal authority.
           The fix is to merge what is still true into AGENTS.md and
           delete the original, which is a read rather than a
           `git mv`: the fleet's copies run to hundreds of lines and
           predate the AGENTS.md beside them, so they hold both stale
           duplication and detail that was never carried across.

        A repository with none reports a pass rather than not_applicable,
        including one with no agent context at all. "Nothing here is
        named for a vendor" is a true statement about a repository with
        no AGENTS.md either, and llm-tooling is the criterion that has an
        opinion about that.

        A directory git cannot list reports not_applicable. Unlike
        PyprojectUsage, which fails on a broken ls-files, this check
        cannot say anything either way without the index -- and the
        missing piece is the audit harness's git, not the audited
        repository, so failing would file an issue nobody on that
        repository can fix. That is the reading LlmContextLint takes
        of a missing skillsaw, a few classes up, and the same signal
        applies: every row flipping to N/A at once is how a broken
        runner announces itself.
        """
        tracked = tracked_paths(repo.path)
        if tracked is None:
            return self.skip(
                'Not the root of a git checkout, or git could not '
                'list its index')

        found = vendor_agent_docs(tracked)
        if not found:
            return self.ok(
                'No agent instruction files named for a single tool')

        count = len(found)
        many = count > 1

        # AGENTS.md from the same list the findings came from: see
        # vendor_agent_docs.
        agents_tracked = 'AGENTS.md' in tracked

        # A finding resolving to the same file as AGENTS.md is an
        # alias, not a document. Matched on resolution alone rather
        # than on which side carries the link, because a project that
        # started on CLAUDE.md most often adopts the shared name by
        # symlinking AGENTS.md *at* it -- and two regular files cannot
        # share a realpath, so nothing else can match this way. A
        # symlink pointing somewhere else, or dangling, resolves
        # elsewhere and correctly wants the ordinary advice.
        agents_path = repo.join('AGENTS.md')
        agents_real = os.path.realpath(agents_path)
        aliases = [
            path for path in found
            if os.path.realpath(repo.join(path)) == agents_real
        ]

        # Which side carries the link decides the fix. Told to merge
        # into AGENTS.md and delete the original, a maintainer whose
        # AGENTS.md *is* the link would write the merge through it
        # into the file they then delete, and be left with a dangling
        # AGENTS.md and no content.
        promote = [
            path for path in aliases if not os.path.islink(repo.join(path))
        ]

        if agents_tracked and len(aliases) == count and promote:
            # AGENTS.md resolves to one of the findings, so the
            # document is already written -- it is only under the
            # wrong name. There is at most one: the others, if any,
            # are links to it.
            real = promote[0]
            fix = (
                f'AGENTS.md is a symlink to {real}, so the document is '
                f'already here under the wrong name: git rm AGENTS.md and '
                f'git mv {real} AGENTS.md'
            )
        elif agents_tracked and len(aliases) == count:
            these_are, them = (
                ('these are', 'them') if many else ('this is', 'it'))
            fix = (
                f'{these_are} a second name for AGENTS.md rather than a '
                f'second document: git rm {them}'
            )
        elif agents_tracked:
            them, each_is = (
                ('them', 'each is') if many else ('it', 'it is'))
            fix = (
                f'AGENTS.md is tracked beside {them}, so {each_is} a second '
                f'set of instructions loaded with equal authority: merge '
                f'what is still true into AGENTS.md and delete the original'
            )
        else:
            # Nothing guarantees one of them is at the repository root,
            # so the plural wording does not name a file that may not
            # be in the findings list.
            these_are, rename = (
                ('these are',
                 'one of them to AGENTS.md at the repository root and fold '
                 'the rest into it')
                if many else ('this is', 'it to AGENTS.md'))
            fix = (
                f'no AGENTS.md is tracked, so {these_are} the project\'s '
                f'agent context under a name only one tool reads: rename '
                f'{rename}'
            )

        noun = 'files are' if many else 'file is'
        return self.fail(
            f'{count} agent instruction {noun} named for a single tool '
            f'rather than AGENTS.md ({", ".join(found)}); {fix}',
            findings=found)
