"""The criteria about documentation content.

What README.md is for, where its links point, whether pages under
docs/ link outward absolutely, and how diagrams are drawn.

The common thread is that all five judge prose a person wrote, so all
five have to know which parts of a page were generated and must not be
judged at all.
"""

import os
import re
import urllib.parse

from audit.check import Check
from audit.files import (
    file_mentions, iter_doc_content_files, iter_docs_markdown_files,
)
from audit.text.markdown import (
    MD_LINK_RE, MD_REFDEF_RE, blank_generated_blocks, iter_fenced_blocks,
    link_target, link_target_is_relative, strip_markdown_code,
)


# README structure limits: the top-level README is a pitch, not a
# reference manual. See docs/audits/readme-structure.md.
README_MAX_LINES = 150


README_MAX_WORDS = 1200


# Box-drawing characters, used to tell a drawn block from prose.
DIAGRAM_BOX_CHARS = set('─│┌└├┐┘┬┴┼╭╮╰╯━┃')

# The corner of a drawn box, in either character set. A file tree uses
# tees and elbows but never a top corner, which is most of why this is
# measured separately from DIAGRAM_BOX_CHARS.
DIAGRAM_CORNER_RE = re.compile(r'[┌┐╭╮]|\+-{2,}\+')

# An edge between two nodes: a solid triangular arrowhead anywhere, or
# an ASCII or box rule of at least two characters ending in an angle
# bracket. Thin single arrows (up, down, left, right) are deliberately
# absent. In this fleet they are overwhelmingly annotation pointers --
# "0x08: data_gpa (u64) <- guest phys addr" inside a register map, or
# the head and tail markers under a ring buffer -- and counting them
# turns two memory maps into diagrams.
DIAGRAM_ARROW_RE = re.compile(r'[▼▲▶◀►◄]|[-─━=]{2,}>|<[-─━=]{2,}')

# A flow connector: a line drawn from nothing but connector glyphs,
# carrying a downward arrowhead, optionally with a parenthetical label
# ("v  (L2 table offset)"). A file tree's "| # comment" is not one,
# because it has prose on it. Only the downward forms count: a caret
# is the callout character in a bit-field diagram, where a row of them
# points up at the fields above.
DIAGRAM_FLOW_RE = re.compile(
    r'^[\s|│+\-─━]*[v▼][\s|│+\-─━v▼]*(\s*\(.*\))?\s*$'
)


# A row of a memory map or address table: an offset in the first
# column. Three of them mean the block is a layout, where alignment
# carries the meaning and mermaid has nothing to offer.
DIAGRAM_HEX_ROW_RE = re.compile(r'^\s*(0x)?[0-9A-Fa-f]{4}[_ :]')


DIAGRAM_FORMAT_OK = 'audit-ok: diagram-format'


DIAGRAM_MAX_SHOWN = 10


def is_ascii_diagram(lang, lines):
    """Is this fenced block a diagram drawn in characters?

    Deliberately conservative. A false positive files an issue against
    a repository whose documentation is correct, and the only way to
    close it is to mark the block exempt; a false negative costs
    nothing, because the diagram-discipline shared block puts a human
    reviewer in front of every new one. So the rule asks for a drawn
    structure *and* an unambiguous edge, and lets the ambiguous cases
    through.
    """
    # The first word of the info string, so that an attribute after
    # the language ("```mermaid title=x") is still mermaid source
    # rather than something to run the ASCII heuristic over.
    if lang.lower().split()[:1] == ['mermaid']:
        return False
    if sum(1 for line in lines if DIAGRAM_HEX_ROW_RE.match(line)) >= 3:
        return False

    corners = sum(
        1 for line in lines if DIAGRAM_CORNER_RE.search(line)
    )
    box_lines = sum(
        1 for line in lines
        if any(c in DIAGRAM_BOX_CHARS for c in line)
    )
    arrows = sum(1 for line in lines if DIAGRAM_ARROW_RE.search(line))
    flows = sum(
        1 for line in lines
        if line.strip() and DIAGRAM_FLOW_RE.match(line)
    )

    # Boxes plus any edge at all. The corner is what separates a drawn
    # diagram from a file tree, which has neither corners nor edges.
    if corners >= 1 and (arrows >= 1 or flows >= 1):
        return True
    # Boxless flows: a sequence diagram drawn with bare verticals, or
    # a pipeline drawn with arrows and no boxes at all. Two edges
    # rather than one, because there is no corner here to corroborate
    # them.
    return box_lines >= 2 and (arrows >= 2 or flows >= 2)


def diagram_format_exempt(lines, fence_lineno):
    """Is this fence marked exempt from the diagram-format audit?

    The marker is accepted on the fence line itself or on the nearest
    non-blank line above it. Blank lines are skipped because putting
    one after an HTML comment is ordinary markdown style and what most
    markdown linters prefer, so a window of exactly one line means the
    natural way to write the exemption is the way that silently does
    not work -- and the failure presents as an issue filed against a
    repository that believes it has already answered.

    Only blank lines are skipped, so the marker cannot be inherited
    from a paragraph further up that was talking about something else.
    """
    index = fence_lineno - 1
    if index < len(lines) and DIAGRAM_FORMAT_OK in lines[index]:
        return True
    index -= 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    return index >= 0 and DIAGRAM_FORMAT_OK in lines[index]


# The wrapper an adopting repository copies from
# templates/mermaid-lint/. Named rather than pattern-matched, because
# what makes this measurable is that every project runs the same
# script.
MERMAID_LINT_SCRIPT = 'tools/mermaid-lint.sh'


# Backticks, with no space before the language. Deliberately narrower
# than markdown allows, because mmdc recognises only this form: a
# ~~~mermaid block renders nothing and exits zero, so treating one as
# a diagram to lint would call a repository covered for a diagram its
# linter never sees.
#
# tools/mermaid-lint.sh draws the same line and then acts on it more
# strongly: it selects this form to lint and refuses the two forms
# GitHub renders but mmdc does not -- a tilde fence, and a language
# separated from the backticks by a space. So the two agree about
# what a diagram is and disagree about what to do with the rest --
# N/A here, exit 1 there. The script also tracks fence state, so a
# fence nested inside a longer one is an example rather than a
# diagram; this regex is a line match and does not, which at worst
# asks for a linter that then finds nothing to lint.
# docs/audits/mermaid-lint-ci.md is the spec.
MERMAID_FENCE_RE = re.compile(r'^\s*```mermaid\b')


def repo_has_mermaid(repo_path):
    """Does any markdown file here carry a mermaid diagram?

    Walks the whole repository rather than the documentation scope:
    the linter renders every tracked markdown file, so a diagram in a
    plan or a template counts for whether the linter is needed even
    though it is out of scope for diagram-format.
    """
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [
            d for d in dirnames
            if d not in ('.git', 'node_modules', 'target', 'vendor')
            and not d.startswith('.cargo')
        ]
        for filename in filenames:
            if not filename.endswith('.md'):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, 'r', errors='replace') as f:
                for line in f:
                    if MERMAID_FENCE_RE.match(line):
                        return True
    return False


class ReadmeStructure(Check):
    id = 'readme-structure'
    spec = 'docs/audits/readme-structure.md'
    template = None
    issue_title = 'README structure'

    def run(self, repo):
        """Check that the top-level README.md reads as a pitch.

        "Is this a good pitch" is a judgment call enforced at push time by
        the readme-discipline shared block (see check_push_audit); this
        check enforces the measurable proxies: a length cap, and a link
        into docs/ when a docs/ directory exists.
        """
        if not repo.exists('README.md'):
            return self.skip('No top-level README.md')

        with open(
            os.path.join(repo.path, 'README.md'), 'r', errors='replace'
        ) as f:
            content = f.read()

        problems = []
        lines = len(content.splitlines())
        words = len(content.split())
        if lines > README_MAX_LINES or words > README_MAX_WORDS:
            problems.append(
                f'README.md is {lines} lines / {words} words (limits: '
                f'{README_MAX_LINES} lines, {README_MAX_WORDS} words); '
                f'move detail into docs/ and keep the README a pitch'
            )

        if os.path.isdir(os.path.join(repo.path, 'docs')):
            scannable = strip_markdown_code(content)
            targets = [
                m.group(1) for m in MD_LINK_RE.finditer(scannable)
            ] + [
                m.group(1) for m in MD_REFDEF_RE.finditer(scannable)
            ]
            if not any('docs/' in t for t in targets):
                problems.append(
                    'README.md has no link into docs/ despite a docs/ '
                    'directory existing; add curated links to the '
                    'detailed documentation'
                )

        if problems:
            return self.fail('; '.join(problems))
        return self.ok('README.md is pitch-sized and links into docs/')


class ReadmeAbsoluteLinks(Check):
    id = 'readme-absolute-links'
    spec = 'docs/audits/readme-absolute-links.md'
    template = None
    issue_title = 'README absolute links'

    def run(self, repo):
        """Check that every link in the top-level README.md is absolute.

        Only the top-level README.md is audited: it is the file rendered
        off the repository landing page (PyPI long description, crates.io,
        mirrors), where relative links -- resolved against the wrong base
        -- silently break. READMEs in subdirectories are only ever viewed
        on the GitHub tree, where relative links resolve correctly, so
        they are intentionally out of scope.
        """
        if not repo.exists('README.md'):
            return self.skip('No top-level README.md')

        with open(
            os.path.join(repo.path, 'README.md'), 'r', errors='replace'
        ) as f:
            content = f.read()

        scannable = strip_markdown_code(content)
        relative = []
        for match in MD_LINK_RE.finditer(scannable):
            if link_target_is_relative(match.group(1)):
                relative.append(match.group(1).strip())
        for match in MD_REFDEF_RE.finditer(scannable):
            if link_target_is_relative(match.group(1)):
                relative.append(match.group(1).strip())

        if relative:
            uniq = sorted(set(relative))
            shown = ', '.join(uniq[:10])
            more = '' if len(uniq) <= 10 else f' (+{len(uniq) - 10} more)'
            return self.fail(
                f'{len(uniq)} relative link target(s) in README.md '
                f'(use absolute URLs so the README renders off the '
                f'repo landing page): {shown}{more}')
        return self.ok('All README.md links are absolute')


class DocsExternalLinks(Check):
    id = 'docs-external-links'
    spec = 'docs/audits/docs-external-links.md'
    template = None
    issue_title = 'Links out of docs/ are absolute'

    def run(self, repo):
        """Check docs/ links resolve inside docs/, or else are absolute.

        docs/ is not only rendered on the GitHub file tree. It is
        synchronised into shakenfist/shakenfist under docs/components/
        and published on shakenfist.com, where the tree above docs/ does
        not exist. A relative link out of docs/ -- ../tools/x.sh,
        ../README.md -- resolves against the wrong base there and 404s,
        while the same link looks fine on GitHub, so nothing catches it.

        Links whose target stays inside docs/ are fine and stay relative:
        they move with the tree and resolve in both renderings. Anything
        pointing outside docs/ must be an absolute
        https://github.com/<org>/<repo>/blob/<branch>/... URL.

        A relative target that resolves inside docs/ but names no file
        that exists is reported too. It is nearly always a link out of
        docs/ written against the repository root (ryll/src/app.rs rather
        than ../../ryll/src/app.rs), which is the same defect wearing a
        different spelling, and it is dead on GitHub as well.

        Site-root-absolute targets (/operator_guide/locks/) are left
        alone. They are the mkdocs convention for addressing another page
        of the same site and resolve on the published site, which is the
        rendering this audit exists to protect.

        Generated consistency-audit blocks are skipped: see
        blank_generated_blocks().
        """
        if not os.path.isdir(os.path.join(repo.path, 'docs')):
            return self.skip('No docs/ directory')

        offenders = []
        for rel_path in iter_docs_markdown_files(repo.path, repo.props):
            with open(
                os.path.join(repo.path, rel_path), 'r', errors='replace'
            ) as f:
                scannable = strip_markdown_code(
                    blank_generated_blocks(f.read())
                )

            rel_dir = os.path.dirname(rel_path)
            raw_targets = [m.group(1) for m in MD_LINK_RE.finditer(scannable)]
            raw_targets += [m.group(1) for m in MD_REFDEF_RE.finditer(scannable)]
            for raw in raw_targets:
                if not link_target_is_relative(raw):
                    continue
                target = link_target(raw)
                if target.startswith('/'):
                    continue
                # Drop the fragment: it addresses a heading in the target
                # document, not a path component.
                path = target.split('#', 1)[0]
                if not path:
                    continue
                resolved = os.path.normpath(
                    os.path.join(rel_dir, urllib.parse.unquote(path))
                )
                if resolved == 'docs' or resolved.startswith('docs/'):
                    if os.path.exists(os.path.join(repo.path, resolved)):
                        continue
                offenders.append(f'{rel_path} -> {target}')

        if offenders:
            uniq = sorted(set(offenders))
            shown = ', '.join(uniq[:10])
            more = '' if len(uniq) <= 10 else f' (+{len(uniq) - 10} more)'
            return self.fail(
                f'{len(uniq)} relative link(s) in docs/ that do not '
                f'resolve to a file inside docs/ (use absolute '
                f'https://github.com/... URLs, which survive the docs '
                f'site import): {shown}{more}')
        return self.ok('All links out of docs/ are absolute')


class DiagramFormat(Check):
    id = 'diagram-format'
    spec = 'docs/audits/diagram-format.md'
    template = None
    issue_title = 'Diagram format'

    def run(self, repo):
        """Check documentation draws its diagrams in mermaid.

        A diagram of structure or flow -- boxes and the arrows between
        them, an ordered exchange, a state machine -- renders as a picture
        on GitHub and on the mkdocs sites when it is written as a mermaid
        fence, and as a wall of characters when it is drawn by hand. The
        fleet was split down the middle on this: three repositories used
        mermaid and the rest had not started.

        Scope is the documentation content files, so plans are out. A plan
        is a working document read by the people writing it, and sweeping
        a repository's plan history for diagrams is archaeology rather
        than maintenance.

        What is *not* a diagram is most of what this had to learn to
        ignore: file trees, memory maps, register and bit-field layouts
        with caret callouts, wire-format byte tables. See is_ascii_diagram
        for how they are told apart, and
        templates/shared-blocks/diagram-discipline.md for the policy a
        reviewer applies where a script cannot.

        A block that is genuinely better drawn by hand is exempted by an
        "audit-ok: diagram-format" comment on the fence line or the line
        above it.
        """
        files = list(iter_doc_content_files(repo.path, repo.props))
        if not files:
            return self.skip('No documentation content to audit')

        hits = []
        for rel in files:
            with open(
                os.path.join(repo.path, rel), 'r', errors='replace'
            ) as f:
                content = blank_generated_blocks(f.read())
            lines = content.splitlines()
            for lang, start, body in iter_fenced_blocks(content):
                if not is_ascii_diagram(lang, body):
                    continue
                if diagram_format_exempt(lines, start):
                    continue
                hits.append(f'{rel}:{start}')

        if hits:
            shown = ', '.join(hits[:DIAGRAM_MAX_SHOWN])
            more = (
                '' if len(hits) <= DIAGRAM_MAX_SHOWN
                else f' (+{len(hits) - DIAGRAM_MAX_SHOWN} more)'
            )
            return self.fail(
                f'{len(hits)} diagram(s) drawn in ASCII rather than '
                f'mermaid (convert them, or mark a block that is '
                f'genuinely better drawn by hand with an '
                f'"audit-ok: diagram-format" comment above the '
                f'fence): {shown}{more}')
        return self.ok(
            'No ASCII diagrams in README.md, AGENTS.md, '
            'ARCHITECTURE.md or docs/')


class MermaidLintCi(Check):
    id = 'mermaid-lint-ci'
    spec = 'docs/audits/mermaid-lint-ci.md'
    template = 'templates/mermaid-lint/'
    issue_title = 'Mermaid diagrams linted in CI'

    def run(self, repo):
        """Check repositories with mermaid diagrams lint them in CI.

        Mermaid fails at render time rather than at commit time. A syntax
        error commits cleanly, passes every linter the fleet runs, and
        then shows an error box on GitHub and nothing at all on the
        mkdocs sites, so nothing catches it until a person looks at the
        page. That is the gap this closes, and it is why the check applies
        to repositories that *have* diagrams rather than to all of them.

        Both halves are required. The script alone is a thing nobody runs,
        and a workflow step alone would mean each project inventing its
        own invocation of a container -- the whole point of shipping the
        wrapper is that the docker arguments, the entrypoint override and
        the exit-status handling are written once.
        """
        if not repo_has_mermaid(repo.path):
            return self.skip('No mermaid diagrams to lint')

        missing = []
        if not repo.exists(MERMAID_LINT_SCRIPT):
            missing.append(MERMAID_LINT_SCRIPT)

        workflows = [
            os.path.join(repo.path, '.github', 'workflows', workflow)
            for workflow in repo.workflows()
        ]
        if not any(
            file_mentions(workflow, MERMAID_LINT_SCRIPT)
            for workflow in workflows
        ):
            missing.append('a CI workflow that runs it')

        if missing:
            return self.fail(
                f'mermaid diagrams are not linted: missing '
                f'{" and ".join(missing)} (copy '
                f'templates/mermaid-lint/ from the development '
                f'repository)')
        return self.ok('mermaid diagrams are rendered by CI')
