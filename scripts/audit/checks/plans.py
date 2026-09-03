"""The criteria about planning documents.

Where plans live, how they are indexed, what a plan template must
carry, and the rule that documentation outside docs/plans/ describes
the software rather than the history of how it was built.

push-audit is here too. It is about a runbook rather than a plan, but
what it checks is the same shared blocks the plan template is checked
against, and splitting them would put one validator in two places.
"""

import os
import re
import subprocess

from audit.check import Check
from audit.files import check_file_contains, iter_doc_content_files
from audit.text.markdown import (
    blank_generated_blocks, iter_lines_outside_fences,
    iter_markdown_table_rows, markdown_heading,
)
from audit.text.shared_blocks import validate_shared_blocks


# Plan phase references: documentation outside plans directories
# describes current behaviour, not the phase of the plan that built
# it. See docs/audits/plan-phase-references.md.
PHASE_REFERENCE_RE = re.compile(r'\bphases?\s+\d+\b', re.IGNORECASE)


PHASE_REFERENCE_OK = '<!-- audit-ok: phase-reference -->'


# Plan source references: a plan pointer written into source or
# configuration must resolve in this repository, or else be an
# absolute URL. See docs/audits/plan-source-references.md.
PLAN_SOURCE_REF_RE = re.compile(r'[\w./-]*PLAN-[\w.-]*\.md')


PLAN_SOURCE_URL_RE = re.compile(r'[a-z][a-z0-9+.-]*://\S*', re.IGNORECASE)


PLAN_SOURCE_REF_OK = 'audit-ok: plan-reference'


# The file-scope form of the marker above, for a file that is made of
# plan paths rather than merely containing one -- a suite exercising
# this check has to build both references that resolve and references
# that deliberately do not, and neither kind is a pointer a reader
# follows. It exempts the whole file, so it is the blunter instrument
# of the two: a file carrying it stops being audited for plan
# references entirely, including for prose that really has rotted.
# Prefer the line marker, and say in the file why the exemption is
# right.
PLAN_SOURCE_FILE_OK = 'audit-ok: plan-reference-file'


PLAN_SOURCE_MAX_BYTES = 2 * 1024 * 1024


# PLAN-TEMPLATE.md is not a plan. It is the template plans are written
# from, it sits at the repository root rather than in docs/plans/, and
# the plan-template audit is what holds it there. Naming it in a script
# or a config is therefore not a pointer into docs/plans/ that can rot
# out from under a reader, so it is not this audit's business.
PLAN_SOURCE_TEMPLATE_NAME = 'PLAN-TEMPLATE.md'


def plan_file_names(repo_path):
    """Basenames of every markdown file under docs/plans/, any depth.

    Archived plans live in docs/plans/completed/, so the index is
    built recursively: a bare `PLAN-<name>.md` in a comment names no
    directory and should resolve wherever the file actually sits.
    """
    names = set()
    for _dirpath, _dirnames, filenames in os.walk(
        os.path.join(repo_path, 'docs', 'plans')
    ):
        for filename in filenames:
            if filename.endswith('.md'):
                names.add(filename)
    return names


def plan_reference_resolves(repo_path, token, names):
    """Whether a plan reference names a file this repository has.

    A path-qualified reference (docs/plans/PLAN-<name>.md) is resolved
    as written, from the repository root and then from docs/ -- the
    latter because mkdocs navigation addresses pages relative to the
    documentation root. A bare filename is matched against every
    plan file in the repository.
    """
    if os.path.exists(os.path.join(repo_path, token)):
        return True
    if os.path.exists(os.path.join(repo_path, 'docs', token)):
        return True
    return '/' not in token and token in names


# The blocks every PUSH-AUDIT.md must carry. Named here rather than
# inline so docs/audits/push-audit.md can be tested against the list:
# a block required by the check but absent from its spec page files a
# fleet issue naming something the page never mentions.
PUSH_AUDIT_BLOCKS = [
    'readme-discipline', 'llm-doc-discipline',
    'diagram-discipline', 'comment-proportion',
    'plan-phase-references', 'path-traversal-review',
    'python-version-discipline', 'functional-test-coverage',
]


# The controlled vocabulary a plan status cell may use, canonically
# documented in templates/shared-blocks/plan-status-vocabulary.md. A
# test asserts the two agree, so the wording repositories are handed
# and the wording we enforce cannot drift apart.
PLAN_STATUSES = (
    'Proposed',
    'Not started',
    'In progress',
    'Blocked',
    'Complete',
    'Abandoned',
    'Superseded',
)


# The leading columns every docs/plans/index.md table must carry, in
# this order. Chronological order is the reading order for a plan
# index, and a fixed column order is what lets tooling find the status
# without guessing.
PLAN_INDEX_LEAD_COLUMNS = ('date', 'plan')


PLAN_INDEX_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# A phase plan is named after its master plan, so it is not itself an
# entry the index has to carry.
PLAN_PHASE_FILE_RE = re.compile(r'-phase-\d')


# Markdown decoration ignored when reading a table cell.
PLAN_CELL_DECORATION_RE = re.compile(r'[`*_~]')


PLAN_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')


# A link target that leaves this repository: an absolute URL, or
# anything else carrying a scheme. An index may point at a plan in
# another repository -- a superseded-by, or a mirror -- and such a
# target says nothing about the local tree. Resolved against
# docs/plans/ it is either judged as a same-named local plan, or
# reported as a plan whose file is missing, which reads as a broken
# link when the link is fine. Matching a scheme rather than "://"
# catches mailto: and friends, and cannot misread a relative link: a
# relative reference may not carry a colon in its first path segment
# (RFC 3986 section 4.2), for exactly this reason.
PLAN_LINK_SCHEME_RE = re.compile(r'^(?:[a-z][a-z0-9+.-]*:|//)', re.IGNORECASE)


# How many offending items to name before summarising.
PLAN_INDEX_MAX_SHOWN = 5


def plan_cell_text(cell):
    """A cell's text, with any markdown link collapsed to its label."""
    return PLAN_CELL_DECORATION_RE.sub(
        '', PLAN_LINK_RE.sub(r'\1', cell)
    ).strip()


def plan_table_columns(cells):
    """A table header row's cells, folded to comparable column names.

    Handed to iter_markdown_table_rows so that every table this module
    reads names its columns the same way: a header written `**Plan**`
    or `` `Status` `` is the column the check is looking for, and the
    decoration is presentation.
    """
    return [plan_cell_text(cell).lower() for cell in cells]


def plan_index_summarise(label, items):
    """One problem string naming at most PLAN_INDEX_MAX_SHOWN items."""
    shown = ', '.join(items[:PLAN_INDEX_MAX_SHOWN])
    more = (
        '' if len(items) <= PLAN_INDEX_MAX_SHOWN
        else f' (+{len(items) - PLAN_INDEX_MAX_SHOWN} more)'
    )
    return f'{label}: {shown}{more}'


# The statuses that carve a plan out of this criterion. A plan
# deliberately dropped or replaced is no more going to write the diff
# an audit would read than a finished one is, so all three terminal
# terms of the status vocabulary carve out and the four live ones
# bind. The plan-push-audit-phase block still words the carve-out as
# `Complete` alone: bumping it to v3 restales every embedded copy
# across the fleet, which is a sweep, and the sweep is phase 4 of
# docs/plans/PLAN-push-audit-phase.md. The check implements the
# vocabulary's terminal set meanwhile, and the block catches up there.
PLAN_TERMINAL_STATUSES = ('Complete', 'Abandoned', 'Superseded')


# The runbook a master plan's final phase runs. Named here rather
# than written into each message so the check and the sentence it
# files an issue with cannot disagree about the filename.
PLAN_AUDIT_RUNBOOK = 'PUSH-AUDIT.md'


# What a push audit phase calls itself. The rule is about a phase, and
# a phase names itself in its title -- "8. Push audit" in an Execution
# table, "### Phase 5: Push audit" as a section -- while the sentence
# that names PUSH-AUDIT.md sits in the prose under it. Requiring the
# literal filename inside the phase entry would therefore fail almost
# every compliant plan in the fleet, so the title is what is matched
# here and the plan is separately required to name the runbook file
# somewhere.
PLAN_AUDIT_PHASE_RE = re.compile(r'push[-\s]?audit', re.IGNORECASE)


# A push audit phase written as a trailing section of its own rather
# than as a numbered phase, which is how a plan whose phases are
# headings ends when the audit was appended after the numbering was
# settled. Anchored and terminated so that "Push audit findings",
# which is a record of a completed audit rather than a phase, does not
# count as one.
PLAN_AUDIT_SECTION_RE = re.compile(r'^push[-\s]audit\s*$', re.IGNORECASE)


# A heading that all but names the trailing audit section: "Push audit
# phase", "Push audit (final)", "Push audit findings". None of these is
# read as a phase, and deliberately so -- but a message saying the plan
# has no push audit phase denies a heading its author can see on the
# page. Collected so the message can name the heading it did not read
# rather than pretending it is not there.
PLAN_AUDIT_SECTION_NEAR_RE = re.compile(r'^push[-\s]audit\b', re.IGNORECASE)


# Where a plan's phases live: a heading naming execution, an
# implementation, phases, or workstreams. The numbered subsections
# under such a heading are the plan's phases. The list is empirical
# rather than principled -- it is the set of headings the fleet has
# actually been observed to write, and `workstreams` joined it when
# divergulent's PLAN-release-1.0.md turned out to be carrying eight
# phases under one. A plan that files its phases under some other
# heading is reported as unjudged rather than judged wrongly, which
# is what makes the next shape visible instead of silent.
PLAN_PHASE_SECTION_RE = re.compile(
    r'execution|implementation|phase|workstream', re.IGNORECASE)


# A heading that is a phase: "### Phase 5: Push audit", or a bare
# "### 5. Push audit" inside one of the sections above. The bare form
# is only read inside a phase section because plans also number
# ordinary subsections -- ryll's follow-up plans are lists of numbered
# findings -- and reading those as phases would report a plan for not
# ending its findings list with an audit.
#
# The title is optional, because "### Phase 1", "### Phase 2" is a
# shape the fleet writes and a plan using it had no phases this check
# could read at all. Only the explicit form is allowed to omit it --
# see where this is matched -- since a heading that is a bare number
# is exactly the numbered subsection the paragraph above excludes.
PLAN_PHASE_HEADING_RE = re.compile(
    r'^(?:phase\s*)?(\d+)\s*(?:[.:)]\s+(\S.*))?$', re.IGNORECASE)


PLAN_PHASE_EXPLICIT_HEADING_RE = re.compile(r'^phase\s*\d', re.IGNORECASE)


# A phase table's first cell. Looser than the heading form because the
# cell is a column rather than a sentence: it is written "8", "8.",
# "8. Push audit" and "Phase 8" across the fleet, and the row's other
# cells carry the description either way.
PLAN_PHASE_CELL_RE = re.compile(r'^(?:phase\s*)?(\d+)\b', re.IGNORECASE)


# The punctuation between a phase cell's number and its name, stripped
# so that "8." and "8) " are recognised as the bare-number shape while
# "8. Push audit" is recognised as a named one.
PLAN_PHASE_CELL_TAIL_RE = re.compile(r'^[\s.:)\u2013\u2014-]+')


# The heading a phase table's first column carries.
PLAN_PHASE_TABLE_COLUMN = 'phase'


# The number a phase entry opens with, in either shape: a table cell
# ("8", "8.", "8. Push audit", "Phase 8") or a section heading
# ("Phase 8: Push audit"). Stripped off so that what remains is the
# phase's name and nothing else.
PLAN_PHASE_NUMBER_RE = re.compile(
    r'^(?:phase\s*)?(\d+)\s*[.:)–—-]*\s*', re.IGNORECASE)


PLAN_PHASE_WORD_RE = re.compile(r'[^0-9a-z]+')


def plan_phase_name(text):
    """The words a phase entry carries after its number.

    Empty for a bare-number entry -- "8", "8.", "Phase 8" -- which
    names nothing, and a list of lower-case words otherwise. Reduced
    to words so that a table row and the section heading describing
    the same phase can be compared without the punctuation between
    them mattering: "1. Foundations" and "1. Foundations -- this
    repository" agree on the word that identifies the phase.
    """
    stripped = text.strip()
    match = PLAN_PHASE_NUMBER_RE.match(stripped)
    rest = stripped[match.end():] if match else stripped
    return [word for word in PLAN_PHASE_WORD_RE.split(rest.lower()) if word]


def plan_section_summary(lines, offset):
    """The first paragraph under a heading, joined into one line.

    What a phase written as a bare "### Phase 5" heading says it is.
    The heading names nothing, so its prose has to answer for it, and
    the prose is read the way a bare table cell's row is read: whole,
    because the plan put the phase's name wherever the document read
    best. Bounded to the first paragraph and stopped by the next
    heading, so a cross-reference three paragraphs down cannot name
    the phase. `lines` is expected to have had fenced code blanked
    already, so a runbook snippet under the heading contributes
    nothing.
    """
    body = []
    for line in lines[offset + 1:]:
        stripped = line.strip()
        if markdown_heading(stripped):
            break
        if not stripped:
            if body:
                break
            continue
        body.append(stripped)
    return ' '.join(body)


def plan_phase_heading_extends(title, label):
    """Whether a numbered heading is a table row's own section.

    A plan carrying both shapes writes one entry per phase in each,
    and the heading is where the phase's prose actually sits. But
    plans number things that are not phases too -- a findings list
    under a trailing audit section is numbered from one, exactly as
    the phases are -- and treating such a heading as a phase's section
    drags the phase's anchor below the audit, which reports a plan
    that recorded its audit's findings for putting the audit in the
    wrong place. So a heading is accepted only where its name begins
    with the row's: "2. Deploy" for the row "2. Deploy", and
    "1. Foundations -- this repository" for "1. Foundations", but not
    "1. A defect we noticed" for "1. Build". A row that names nothing
    has nothing to compare, and is decided by the caller.
    """
    name = plan_phase_name(label)
    return bool(name) and plan_phase_name(title)[:len(name)] == name


# A plan the check cannot judge because it has no phases at all: a
# standalone follow-up list, or a plan still in prose. There is no
# last phase to require anything of, and inventing one would file
# issues against work that was never phased.
PLAN_AUDIT_UNPHASED = 'unphased'


PLAN_AUDIT_OK = 'ok'


PLAN_AUDIT_PROBLEM = 'problem'


def plan_index_entries(path):
    """Every master plan the index links, with the status beside it.

    The index formats differ across the fleet by design -- one
    repository carries a phase count column, another an inline list of
    phase names, this one no phase column at all -- so nothing here
    reads a phase from the index. What is read is the pair the index
    does agree on: which plan files it links, and what each one's
    status cell says.

    A link in the Plan column takes that row's status. A link anywhere
    else -- prose above the table, a bullet list in a repository whose
    index is not a table yet, another column of the row -- is recorded
    with no status, and a later row that names the same plan properly
    fills it in. Statuses are never read from another plan's row,
    because a Description cell that mentions a second plan would
    otherwise hand it the first plan's status.

    The link target is carried alongside the filename rather than
    flattened to it. Archived plans are linked from a subdirectory --
    `completed/PLAN-<name>.md` -- and a caller handed the basename
    alone would look for a file that is not there and silently drop
    the plan.

    A target carrying a URL scheme is not recorded at all: it names a
    plan in another repository, and nothing under this repository's
    docs/plans/ can answer for it either way.

    A link inside a fenced code block is not recorded either. An index
    that shows what a row looks like -- which is how a repository
    documents its own conventions -- is showing sample text, not
    registering the plan it names, and reading it as a registration
    would hand this criterion a plan file that need not exist.

    Returns a list of (filename, link target, status or None) in the
    order the index introduces them.
    """
    with open(path, 'r', errors='replace') as f:
        lines = f.read().splitlines()

    found = {}

    def record(target, status):
        cleaned = target.split('#')[0].strip()
        if PLAN_LINK_SCHEME_RE.match(cleaned):
            return
        name = os.path.basename(cleaned)
        if not name.startswith('PLAN-') or not name.endswith('.md'):
            return
        if PLAN_PHASE_FILE_RE.search(name):
            return
        if name == PLAN_SOURCE_TEMPLATE_NAME:
            return
        if name not in found or (found[name][1] is None and status):
            found[name] = (cleaned, status)

    for _offset, line, is_header, header, cells in iter_markdown_table_rows(
            lines, columns=plan_table_columns):
        if cells is None:
            # Prose between tables ends the run of rows, so a row is
            # never read against the header of an earlier table. The
            # iterator has already blanked fenced code, so an example
            # index shown in a runbook snippet registers nothing.
            for _, target in PLAN_LINK_RE.findall(line):
                record(target, None)
            continue
        if is_header:
            continue

        status = None
        plan_column = None
        if header:
            if 'status' in header:
                column = header.index('status')
                if column < len(cells):
                    status = plan_cell_text(cells[column]) or None
            # The Plan column, which plan-index requires to be the
            # second one. Found by name where the header names it, so
            # that a repository carrying an extra leading column is
            # read correctly rather than silently losing its statuses.
            plan_column = (
                header.index('plan') if 'plan' in header
                else (1 if len(header) > 1 else None)
            )

        for index, cell in enumerate(cells):
            for _, target in PLAN_LINK_RE.findall(cell):
                record(
                    target,
                    status if index == plan_column else None,
                )

    return [
        (name, target, status) for name, (target, status) in found.items()
    ]


def plan_file_paths(plans_dir):
    """Basename to path for every markdown file under plans_dir.

    Built once by the caller and handed to plan_index_target_path,
    the way plan_file_names() is handed to plan_reference_resolves():
    the by-name fallback fires for every link an index writes from the
    repository root rather than from beside itself, and walking the
    tree once per link is a walk per plan for no answer that changes.

    First one wins, in os.walk order, which is the file the fallback
    used to return when it walked the tree itself.
    """
    paths = {}
    for dirpath, _dirnames, filenames in os.walk(plans_dir):
        for filename in filenames:
            if filename.endswith('.md'):
                paths.setdefault(filename, os.path.join(dirpath, filename))
    return paths


def plan_index_target_path(plans_dir, target, name, paths):
    """The plan file an index link names, or None if there is none.

    Resolved as the link is written first, so that a plan linked from
    docs/plans/completed/ is found where it actually sits. A target
    that does not resolve that way is looked up by basename in
    `paths`, the plan_file_paths() map of everything under
    docs/plans/, which is how plan references resolve elsewhere in
    this module and covers an index whose links are written from the
    repository root rather than from beside it.
    """
    candidate = os.path.normpath(os.path.join(plans_dir, target))
    root = os.path.normpath(plans_dir)
    if (candidate == root or candidate.startswith(root + os.sep)) \
            and os.path.isfile(candidate):
        return candidate
    return paths.get(name)


def plan_phases(content):
    """The plan's numbered phases, and where any audit section sits.

    Phases are read from whichever of the two shapes the plan uses,
    and from both where it uses both: the rows of a table whose first
    column is Phase, and headings that number themselves. They are
    keyed by phase number rather than by position, because a plan that
    carries both shapes also carries other tables -- the reconstructed
    landing commits of a backfilled plan are a Phase table sitting
    inside the audit phase's own section -- and the phase numbers
    agree where document order does not.

    Returns a dict of number to (line index, text, label, status).

    The line index is where the phase's own content sits: its section
    heading where the plan writes one for it, and otherwise the table
    row that introduces it. That distinction is what a trailing audit
    section is judged against, so it matters that a phase described
    below the table is anchored below the table.

    The text is what the phase is matched against. A named phase cell
    ("8. Push audit") answers for itself, because the row's other
    columns -- a Notes cell mentioning an audit the phase is not --
    must not answer for it. A bare-number cell ("8", "Phase 8") names
    nothing, so there the whole row is read, which is where a plan
    with a numbers-only Phase column describes its phases. A heading
    that names nothing either -- "### Phase 8" -- is answered for by
    the first paragraph of its own section, for the same reason.

    The label is the short name a message quotes back, and the status
    is the phase's own Status cell where its table has one. The status
    is what separates a plan whose audit has not run yet from one
    carrying phases after a finished audit, which want opposite fixes.
    Returns, alongside the phases, the line indexes of any trailing
    push audit section headings, and the titles of any headings that
    all but name one -- "Push audit phase", "Push audit findings" --
    so that a message can name what it did not read rather than say
    the plan has no such heading at all.
    """
    # Fenced code is blanked once, up front. This function reads both
    # the document's headings and its tables, and the two readings
    # have to agree about which lines are sample text: a `# comment` in
    # a shell snippet popped the heading stack and took the phase table
    # below it out of the plan's Execution section, which reported the
    # plan as having no phases at all. iter_markdown_table_rows blanks
    # again below, which is a no-op on an already-blanked list.
    lines = [
        line for _, line in iter_lines_outside_fences(content.splitlines())
    ]
    phases = {}
    sections = []
    near = []
    anchors = {}
    bare = set()
    stack = []
    in_phase_table = False
    status_column = None

    for offset, line, is_header, columns, cells in iter_markdown_table_rows(
            lines, columns=plan_table_columns):
        stripped = line.strip()

        heading = markdown_heading(stripped) if cells is None else None
        if heading:
            level, raw = heading
            title = plan_cell_text(raw)
            while stack and stack[-1][0] >= level:
                stack.pop()
            in_phase_table = False

            numbered = PLAN_PHASE_HEADING_RE.match(title)
            inside = any(
                PLAN_PHASE_SECTION_RE.search(text) for _, text in stack
            )
            explicit = PLAN_PHASE_EXPLICIT_HEADING_RE.match(title)
            if numbered and numbered.group(2) is None and not explicit:
                # A heading that is a number and nothing else -- "### 5"
                # -- is not read as a phase. Plans number ordinary
                # subsections too, and a findings list under a
                # completed audit is numbered from one exactly as the
                # phases are. Only the explicit "### Phase 5" form is
                # unambiguous enough to be read without a title.
                numbered = None
            if numbered:
                # Anchored on every numbered heading, not only the
                # ones read as phases here. The anchor answers "where
                # does phase N's own prose sit", and a plan that
                # carries both shapes writes its phase sections
                # wherever the document reads best -- under the
                # audit's own heading in the fixture that motivated
                # this -- so a rule that only anchored headings this
                # loop reads as phases would put the anchor back on
                # the table row and lose the comparison. Which of
                # these headings actually belongs to a phase is
                # decided below, once the table rows are known. First
                # occurrence wins, so a later numbered heading reusing
                # the number cannot drag a phase's anchor down the
                # document.
                anchors.setdefault(
                    int(numbered.group(1)),
                    (offset, title, bool(inside or explicit)))
            if numbered and (inside or explicit):
                # A phase written as a section carries no status
                # cell, so there is none to read.
                name = numbered.group(2)
                text = title
                if name is None:
                    # "### Phase 5" names nothing beyond its number,
                    # so the phase's own prose answers for it -- the
                    # same reading a bare "| 5 |" cell gets, where the
                    # rest of the row answers instead of the cell.
                    # Without it a plan whose headings are bare is
                    # judged on titles that say only what the phase
                    # number already said.
                    body = plan_section_summary(lines, offset)
                    if body:
                        text = f'{title} {body}'
                phases.setdefault(
                    int(numbered.group(1)),
                    (offset, text, name or title, None))
            elif PLAN_AUDIT_SECTION_RE.match(title):
                sections.append(offset)
            elif PLAN_AUDIT_SECTION_NEAR_RE.match(title):
                near.append(title)

            stack.append((level, title))
            continue

        if cells is None:
            in_phase_table = False
            continue

        if is_header:
            in_phase_table = bool(
                columns
                and columns[0] == PLAN_PHASE_TABLE_COLUMN
                and any(PLAN_PHASE_SECTION_RE.search(text)
                        for _, text in stack)
            )
            status_column = (
                columns.index('status') if 'status' in columns else None
            )
            continue

        if in_phase_table and cells:
            label = plan_cell_text(cells[0])
            numbered = PLAN_PHASE_CELL_RE.match(label)
            if numbered:
                status = None
                if status_column is not None and status_column < len(cells):
                    status = plan_cell_text(cells[status_column]) or None
                named = PLAN_PHASE_CELL_TAIL_RE.sub(
                    '', label[numbered.end():]).strip()
                number = int(numbered.group(1))
                if number not in phases:
                    phases[number] = (
                        offset,
                        label if named else plan_cell_text(stripped),
                        label,
                        status)
                    if not named:
                        bare.add(number)

    # A phase's section heading, where it has one, is later in the
    # document than the table row that lists it, and it is the section
    # that says what the phase does. Anchoring on it is what stops a
    # push audit section written above the last phase's own section
    # from counting as the plan's final phase.
    #
    # A heading counts as a phase's own only where this loop already
    # read it as a phase in its own right, or where its name begins
    # with the row's. Anchoring on every numbered heading instead
    # hands a plan that ran its audit and wrote the findings up as a
    # numbered list -- which is what a plan looks like once the phase
    # this criterion asks for has done its job -- an anchor below its
    # own audit section, and reports it for putting the audit in the
    # wrong place.
    for number, (offset, text, label, status) in list(phases.items()):
        anchor = anchors.get(number)
        if anchor is None:
            continue
        anchor_offset, title, heading_is_phase = anchor
        if not (heading_is_phase
                or plan_phase_heading_extends(title, label)):
            continue
        if number in bare:
            # The row named nothing, so this heading is where the
            # phase says what it is, and dropping the title would
            # leave the plan looking as though the phase were
            # nameless: a Phase column of "Phase 1", "Phase 2" whose
            # names live in "### Phase 2: Push audit" sections was
            # reported as having no audit phase at all. The row is
            # kept alongside the title rather than replaced, because
            # a bare row is read whole precisely so that a later
            # column can answer for the phase too.
            text = f'{text} {title}'
            label = title
        phases[number] = (max(offset, anchor_offset), text, label, status)

    return phases, sections, near


def plan_status_is_terminal(status):
    """Whether a status cell means the work is not going to happen."""
    return (status or '').lower() in {
        term.lower() for term in PLAN_TERMINAL_STATUSES
    }


def plan_audit_phase_state(content):
    """Whether a plan's last phase is the push audit phase.

    Returns (state, detail). The state is PLAN_AUDIT_UNPHASED for a
    plan with no phases to judge, PLAN_AUDIT_OK for a compliant one,
    and PLAN_AUDIT_PROBLEM otherwise, in which case the detail says
    what is wrong in the terms the plan's author would fix it in.

    An audit phase that is not last has two quite different causes,
    and naming the wrong one sends the author to the wrong edit. A
    plan whose audit has not run yet simply has its phases in the
    wrong order, and the fix is to move the audit after them. A plan
    carrying phases after an audit that has already run cannot be
    fixed that way at all: moving a finished phase to the end would
    claim it audited work that landed after it. That one wants a
    second audit phase covering the later work, so the phase's own
    status is what decides which sentence the author is handed.

    Where there is no status to read -- a phase written as a numbered
    section carries no Status cell, which is how this repository and
    several others write plans -- neither sentence can be asserted, so
    the message states what is seen and names both fixes. Guessing the
    reorder there is the guess that costs something: it is exactly the
    false record of what was audited the other branch exists to avoid.
    """
    phases, sections, near = plan_phases(content)
    if not phases:
        return PLAN_AUDIT_UNPHASED, None

    last = max(phases)
    line, text, label, _ = phases[last]
    named = PLAN_AUDIT_RUNBOOK in content

    # The trailing-section shape: a push audit written as a section of
    # its own rather than as a numbered phase, which is how a plan
    # whose phases were numbered before the convention arrived ends.
    # It counts only when it sits after the last phase's own content
    # -- its section heading where the plan writes one, its table row
    # otherwise -- because comparing against the table row alone says
    # only that the audit heading is somewhere below the plan's index
    # of phases, which every heading in the document is. That reading
    # passed a plan whose final phase was described below the audit
    # section, which is exactly the outrun audit this criterion
    # exists to catch.
    if PLAN_AUDIT_PHASE_RE.search(text) or any(s > line for s in sections):
        if named:
            return PLAN_AUDIT_OK, None
        return PLAN_AUDIT_PROBLEM, (
            f'ends with a push audit phase that never names '
            f'{PLAN_AUDIT_RUNBOOK}'
        )

    summary = label
    if not plan_phase_name(label):
        # A bare-number label quotes nothing back: `phase 8 is "8"`
        # tells a reader who has only the issue body what the phase
        # number already told them. The row the phase was read from
        # names something.
        summary = text
    if len(summary) > 60:
        summary = summary[:57] + '...'

    # An audit phase somewhere earlier, rather than the words
    # appearing anywhere in the plan: a Future work note mentioning a
    # push audit is not a phase, and telling an author their phase is
    # in the wrong place when they never wrote one sends them looking
    # for something that is not there.
    audits = [
        number for number, (_, other, _, _) in phases.items()
        if PLAN_AUDIT_PHASE_RE.search(other)
    ]
    if audits:
        audit = max(audits)
        status = phases[audit][3]
        if plan_status_is_terminal(status):
            # Stated as what the plan says, not as what happened to
            # it. The check sees a terminal audit phase with phases
            # after it; whether those phases were appended after the
            # audit ran, or were always there, or are simply stale is
            # not visible from here, and this sentence is filed
            # verbatim as a GitHub issue on another repository, where
            # a confident wrong diagnosis costs a round trip. So the
            # facts are asserted and the remedy is conditional on the
            # one the author can check.
            return PLAN_AUDIT_PROBLEM, (
                f'phase {audit} is the push audit phase and is '
                f'{status}, but phases up to {last} ("{summary}") come '
                f'after it; if that work landed after the audit ran, '
                f'append a new push audit phase rather than moving the '
                f'finished one'
            )
        if status is None:
            # No status to read at all, which is the ordinary case for
            # a plan whose phases are numbered sections: a heading
            # carries no Status cell. Asserting "move the audit phase
            # after it" here is a guess, and it is the guess that is
            # wrong precisely when the audit has already run --
            # moving a finished phase to the end is the false record
            # of what was audited that this criterion exists to
            # prevent. So the same shape as the terminal branch: the
            # facts, and a remedy the author picks between.
            return PLAN_AUDIT_PROBLEM, (
                f'phase {audit} is the push audit phase, but phases up '
                f'to {last} ("{summary}") come after it and the plan '
                f'records no status for the audit phase; move the audit '
                f'phase after them if it has not run, and append a new '
                f'one if it has'
            )
        # A status the plan states and that is not terminal: the audit
        # has not run, so the phases are simply in the wrong order and
        # the remedy can be asserted.
        return PLAN_AUDIT_PROBLEM, (
            f'push audit phase is not last, so phase {last} '
            f'("{summary}") is unaudited; move the audit phase after it'
        )
    if sections:
        # The trailing-section shape, outrun. A section carries no
        # Status cell at all -- that is what makes it a section rather
        # than a phase -- so this is the unknown-status case above
        # with no cell to have read, and it gets the same shape for
        # the same reason: telling an author to move a section whose
        # audit has already run asks for the false record of what was
        # audited that this criterion exists to prevent.
        return PLAN_AUDIT_PROBLEM, (
            f'the plan has a push audit section, but phases up to '
            f'{last} ("{summary}") come after it and the plan records '
            f'no status for it; move the audit section after them if it '
            f'has not run, and append a new push audit phase if it has'
        )
    if near:
        # A heading the author can see on the page, that this check
        # deliberately does not read as a phase. Saying the plan has
        # no push audit phase sends them looking for something they
        # believe they already wrote, so the heading is named and the
        # two shapes that are read are spelled out instead.
        return PLAN_AUDIT_PROBLEM, (
            f'no push audit phase; the plan has a "{near[-1]}" heading, '
            f'but that is not read as one -- a push audit phase is a '
            f'numbered phase, or a section headed exactly "Push audit"'
        )
    return PLAN_AUDIT_PROBLEM, (
        f'no push audit phase; phase {last} is "{summary}"'
    )


# Shared blocks every PLAN-TEMPLATE.md must carry. The model roster
# is deliberately separate from the rest of the step guidance: it
# churns whenever a model ships or retires, and keeping it apart
# means the issue filed against a lagging repository names the
# roster rather than the surrounding prose.
PLAN_TEMPLATE_BLOCKS = [
    'plan-file-conventions',
    'plan-status-vocabulary',
    'subagent-execution-model',
    'plan-planning-effort',
    'subagent-step-guidance',
    'subagent-model-roster',
    'plan-review-checklist',
    'plan-closeout-sections',
    'plan-push-audit-phase',
]


class PlanPhaseReferences(Check):
    id = 'plan-phase-references'
    spec = 'docs/audits/plan-phase-references.md'
    template = None
    issue_title = 'Plan phase references'

    def run(self, repo):
        """Check documentation does not cite implementation plan phases.

        Docs describe the current state of the software; "implemented in
        phase 5" describes the history of how it was built, usually
        without even naming the plan. The word "phase" is reserved for
        plan documents (procedural docs use "step" or "stage"), so any
        "phase <number>" outside a plans/ directory is flagged. Fenced
        code, inline code spans, generated consistency-audit blocks,
        and lines carrying the audit-ok: phase-reference marker are
        skipped.
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

            fence = None
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.lstrip()
                marker = None
                if stripped.startswith('```'):
                    marker = '```'
                elif stripped.startswith('~~~'):
                    marker = '~~~'

                if fence is not None:
                    if marker == fence:
                        fence = None
                    continue
                if marker is not None:
                    fence = marker
                    continue
                if PHASE_REFERENCE_OK in line:
                    continue
                scannable = re.sub(r'`+[^`\n]*`+', '', line)
                if PHASE_REFERENCE_RE.search(scannable):
                    hits.append(f'{rel}:{lineno}')

        if hits:
            shown = ', '.join(hits[:10])
            more = '' if len(hits) <= 10 else f' (+{len(hits) - 10} more)'
            return self.fail(
                f'{len(hits)} plan phase reference(s) in '
                f'documentation (describe the current behaviour, or '
                f'link the master plan in docs/plans/ instead of '
                f'citing a phase number): {shown}{more}')
        return self.ok('No plan phase references in README.md or docs/')


class PlanSourceReferences(Check):
    id = 'plan-source-references'
    spec = 'docs/audits/plan-source-references.md'
    template = None
    issue_title = 'Plan references in source'

    def run(self, repo):
        """Check plan references in source and configuration resolve.

        Comments and configuration point at docs/plans/PLAN-*.md to say
        where a decision is recorded. Nothing renders those pointers, so
        when a plan is renamed or archived into docs/plans/completed/
        they rot silently. Every reference must resolve in this
        repository or be an absolute URL; markdown files are out of
        scope, being covered by docs-external-links.

        A test suite is deliberately not out of scope. Test files carry
        rotted pointers like anything else -- instar's
        tests/test_adversarial.py cites a plan that no longer exists in
        its module docstring -- so a suite that genuinely is all fixture
        paths marks itself with PLAN_SOURCE_FILE_OK rather than being
        skipped by its name.
        """
        try:
            result = subprocess.run(
                ['git', '-C', repo.path, 'ls-files'],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Could not run git ls-files: {e}')

        names = plan_file_names(repo.path)
        hits = []
        total = 0
        for rel in result.stdout.splitlines():
            rel = rel.strip()
            if not rel or rel.endswith('.md'):
                continue
            path = os.path.join(repo.path, rel)
            if not os.path.isfile(path):
                continue
            if os.path.getsize(path) > PLAN_SOURCE_MAX_BYTES:
                continue
            with open(path, 'r', errors='replace') as f:
                content = f.read()
            if 'PLAN-' not in content:
                continue
            if PLAN_SOURCE_FILE_OK in content:
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                if PLAN_SOURCE_REF_OK in line:
                    continue
                scannable = PLAN_SOURCE_URL_RE.sub('', line)
                for match in PLAN_SOURCE_REF_RE.finditer(scannable):
                    token = match.group(0)
                    if os.path.basename(token) == PLAN_SOURCE_TEMPLATE_NAME:
                        continue
                    total += 1
                    if not plan_reference_resolves(repo.path, token, names):
                        hits.append(f'{rel}:{lineno} -> {token}')

        if not total:
            return self.skip('No plan references outside markdown files')

        if hits:
            shown = ', '.join(hits[:10])
            more = '' if len(hits) <= 10 else f' (+{len(hits) - 10} more)'
            return self.fail(
                f'{len(hits)} of {total} plan reference(s) in source '
                f'or configuration do not resolve (update the path, '
                f'or use an absolute https://github.com/... URL for a '
                f'plan in another repository): {shown}{more}')
        return self.ok(f'All {total} plan reference(s) outside markdown resolve')


class PlanIndex(Check):
    id = 'plan-index'
    spec = 'docs/audits/plan-index.md'
    template = 'templates/shared-blocks/'
    issue_title = 'Plan index'

    def run(self, repo):
        """Check docs/plans/index.md layout, ordering and statuses.

        The index is the one place that answers "what has this repository
        planned, and what still wants attention". That only works if it is
        mechanically readable, so every table in it leads with a Date
        column and a Plan column, rows run in date order, every master
        plan is listed, and status cells are drawn from the shared
        vocabulary rather than written as prose.

        Repositories with no docs/plans/ directory are N/A. Whether every
        project should plan this way is a separate decision.
        """
        plans_dir = os.path.join(repo.path, 'docs', 'plans')
        if not os.path.isdir(plans_dir):
            return self.skip('No docs/plans/ directory')

        masters = sorted(
            name for name in os.listdir(plans_dir)
            if name.endswith('.md')
            and name != 'index.md'
            and not PLAN_PHASE_FILE_RE.search(name)
            and os.path.isfile(os.path.join(plans_dir, name))
        )
        index_path = os.path.join(plans_dir, 'index.md')
        if not os.path.exists(index_path):
            if not masters:
                return self.skip('No plans in docs/plans/')
            return self.fail(
                f'docs/plans/index.md is missing, so none of the '
                f'{len(masters)} plan(s) in docs/plans/ are registered')

        with open(index_path, 'r', errors='replace') as f:
            content = f.read()

        linked = set()
        statuses = {s.lower() for s in PLAN_STATUSES}

        # Findings are gathered per table and only reported for tables that
        # turn out to list plans. An index may hold a table that is not a
        # plan listing at all, and holding a legend to the plan layout
        # would be a finding nobody could act on.
        tables = []
        current = None
        # A header is the row the separator underlines. Recognising it by
        # its position rather than by "has no links" means a data row that
        # happens to carry no link cannot be mistaken for the start of a
        # new table. That reading, and the fence handling that keeps an
        # example index out of it, are iter_markdown_table_rows' job: this
        # criterion, plan_index_entries and plan_phases all read the same
        # markdown, and three copies of the idiom is how a fix reaches two
        # of them.
        for offset, line, is_header, columns, cells in \
                iter_markdown_table_rows(content.splitlines(),
                                         columns=plan_table_columns):
            lineno = offset + 1
            for _, target in PLAN_LINK_RE.findall(line):
                name = target.split('#')[0].strip()
                if name.endswith('.md'):
                    linked.add(os.path.basename(name))

            if cells is None:
                # Prose between tables ends the run of rows, so two
                # adjacent tables never compare dates across the boundary.
                current = None
                continue

            stripped = line.strip()
            if is_header:
                current = {
                    'columns': columns,
                    'lead_ok': (tuple(columns[:len(PLAN_INDEX_LEAD_COLUMNS)])
                                == PLAN_INDEX_LEAD_COLUMNS),
                    'header': f'line {lineno} starts "{" | ".join(cells[:2])}"',
                    'has_plans': False,
                    'previous_date': None,
                    'bad_dates': [],
                    'unsorted': [],
                    'bad_statuses': [],
                }
                tables.append(current)
                continue

            if current is None or len(current['columns']) < 2 or len(cells) < 2:
                continue
            if PLAN_LINK_RE.search(stripped):
                current['has_plans'] = True
            if not current['lead_ok']:
                # The column order is already reported; reading dates and
                # statuses out of the wrong columns would only add noise.
                continue

            plan = plan_cell_text(cells[1]) or f'line {lineno}'

            date = plan_cell_text(cells[0])
            if not PLAN_INDEX_DATE_RE.match(date):
                current['bad_dates'].append(f'{plan} ("{date}")')
            else:
                previous = current['previous_date']
                if previous is not None and date < previous:
                    current['unsorted'].append(f'{plan} ({date} after {previous})')
                current['previous_date'] = date

            if 'status' in current['columns']:
                index = current['columns'].index('status')
                if index < len(cells):
                    status = plan_cell_text(cells[index])
                    if status.lower() not in statuses:
                        excerpt = (
                            status if len(status) <= 40 else status[:37] + '...'
                        )
                        current['bad_statuses'].append(f'{plan} ("{excerpt}")')

        plan_tables = [t for t in tables if t['has_plans']]

        problems = []
        if not plan_tables:
            problems.append(
                'index has no plan table (it must list plans in a table '
                'led by Date and Plan columns, not as prose or a bullet '
                'list)'
            )

        bad_columns = [t['header'] for t in plan_tables if not t['lead_ok']]
        if bad_columns:
            problems.append(plan_index_summarise(
                f'{len(bad_columns)} table(s) not led by Date then Plan '
                f'columns', bad_columns))

        bad_dates = [item for t in plan_tables for item in t['bad_dates']]
        if bad_dates:
            problems.append(plan_index_summarise(
                f'{len(bad_dates)} row(s) without a YYYY-MM-DD date',
                bad_dates))

        unsorted = [item for t in plan_tables for item in t['unsorted']]
        if unsorted:
            problems.append(plan_index_summarise(
                f'{len(unsorted)} row(s) out of date order', unsorted))

        bad_statuses = [item for t in plan_tables for item in t['bad_statuses']]
        if bad_statuses:
            problems.append(plan_index_summarise(
                f'{len(bad_statuses)} status cell(s) outside the shared '
                f'vocabulary ({", ".join(PLAN_STATUSES)})', bad_statuses))

        unregistered = [name for name in masters if name not in linked]
        if unregistered:
            problems.append(plan_index_summarise(
                f'{len(unregistered)} master plan(s) not listed in the '
                f'index', unregistered))

        if problems:
            return self.fail('; '.join(problems))
        return self.ok(
            f'docs/plans/index.md lists {len(masters)} plan(s) in date '
            f'order with statuses from the shared vocabulary')


class PlanAuditPhase(Check):
    id = 'plan-audit-phase'
    spec = 'docs/audits/plan-audit-phase.md'
    template = None
    issue_title = 'Push audit phase in master plans'

    def run(self, repo):
        """Check master plans end with a push audit phase.

        Every master plan's last phase runs the repository's
        PUSH-AUDIT.md over the accumulated diff of the whole plan.
        Last is the part of the rule that matters and the part that
        rots: an audit scheduled in the middle of a plan is outrun by
        the phases that follow it, and the plan reaches Complete with
        several phases nothing ever audited. The convention was swept
        into the fleet's plans by hand, and until this check existed
        nothing stopped the next plan from omitting it.

        A plan whose status is terminal -- Complete, Abandoned or
        Superseded -- and that does not carry the phase passes. That
        is the shared block's carve-out, not an oversight: a plan
        whose work has landed, or that was dropped or replaced, is not
        reopened to acquire a phase that would audit a diff nobody is
        going to write. It also decides the difference between a check
        that names the handful of plans still able to act on a finding
        and one that files an issue against every plan the fleet has
        ever closed. The converse is equally deliberate: such a plan
        that does carry the phase is not inspected either, because
        whether the audit was actually run is a judgement about a
        plan's own record, not something the presence of a heading can
        settle.

        A plan with no phases the check can read is not judged, but
        it is named. Follow-up plans, issue lists and single-commit
        plans are written without an Execution table, and there is no
        last phase for the rule to bind -- but a plan that keeps its
        phases under a heading this check does not recognise looks
        exactly the same from here, and passing it silently is how
        that stays undiscovered. Naming them in the result is what
        lets a person check the handful by hand.

        A plan whose index link names no file under docs/plans/ is
        named the same way, for the same reason. Whether the link is
        broken is docs-external-links' finding rather than this one's,
        but a plan this check walked past has to be visible as one.

        Repositories with no docs/plans/index.md are N/A -- whether
        every project should plan this way is a separate decision,
        made by plan-index rather than here.
        """
        index_path = os.path.join(repo.path, 'docs', 'plans', 'index.md')
        if not os.path.exists(index_path):
            return self.skip('No docs/plans/index.md')

        plans_dir = os.path.dirname(index_path)
        # Built once rather than per link: the by-name fallback fires
        # for every index whose links are written from the repository
        # root, and that is a walk of docs/plans/ per plan.
        paths = plan_file_paths(plans_dir)
        judged = 0
        terminal = 0
        unphased = []
        unresolved = []
        problems = []
        for name, target, status in plan_index_entries(index_path):
            path = plan_index_target_path(plans_dir, target, name, paths)
            if path is None:
                # Whose fault a broken link is stays docs-external-
                # links' business, and this check does not fail for
                # one. But a plan it silently walked past is
                # indistinguishable from a plan it passed, so the name
                # is carried into the verdict rather than dropped.
                unresolved.append(name)
                continue
            if plan_status_is_terminal(status):
                terminal += 1
                continue

            with open(path, 'r', errors='replace') as f:
                content = f.read()
            state, detail = plan_audit_phase_state(content)
            if state == PLAN_AUDIT_UNPHASED:
                unphased.append(name)
                continue

            judged += 1
            if state == PLAN_AUDIT_PROBLEM:
                problems.append(f'{name} ({detail})')

        if not judged and not terminal and not unphased:
            if unresolved:
                return self.skip(plan_index_summarise(
                    'docs/plans/index.md links no master plan file that '
                    'resolves', unresolved))
            return self.skip('docs/plans/index.md links no master plans')

        # The unjudged plans are named on both paths. A plan whose
        # phases this check cannot find is not the same thing as a
        # plan that is fine, and a verdict that only counts them
        # leaves nobody able to tell which was which.
        unjudged = []
        if unphased:
            unjudged.append(plan_index_summarise(
                f'{len(unphased)} plan(s) with no phases this check can '
                f'read, not judged', unphased))
        if unresolved:
            unjudged.append(plan_index_summarise(
                f'{len(unresolved)} plan(s) the index links but no file '
                f'under docs/plans/ matches, not judged', unresolved))

        if problems:
            # The remedy is per plan rather than in the preamble: an
            # audit that has not run yet is moved, one that ran and was
            # overtaken is joined by a second, and one sentence cannot
            # ask for both.
            found = plan_index_summarise(
                f'{len(problems)} of {judged} incomplete master plan(s) '
                f'do not end with a phase running {PLAN_AUDIT_RUNBOOK}, '
                f'which the plan-push-audit-phase shared block requires; '
                f'each is named with the fix it needs', problems)
            return self.fail('; '.join([found] + unjudged))

        # The count leads only when there is something behind it. A
        # repository where every linked plan was carved out or could
        # not be read has had nothing measured in it, and opening its
        # verdict with "0 ... end with a PUSH-AUDIT.md phase" reads as
        # a statement of compliance rather than of silence.
        parts = []
        if judged:
            parts.append(
                f'{judged} incomplete master plan(s) end with a '
                f'{PLAN_AUDIT_RUNBOOK} phase')
        if terminal:
            parts.append(f'{terminal} terminal-status plan(s) not judged')
        return self.ok('; '.join(parts + unjudged))


class PushAudit(Check):
    id = 'push-audit'
    spec = 'docs/audits/push-audit.md'
    template = 'templates/shared-blocks/'
    issue_title = 'Pre-push audit file'

    def __init__(self, blocks_dir=None):
        """The canonical blocks to compare against.

        A parameter rather than a constant so the tests can point the
        check at a fixture directory. It was a keyword argument on the
        old function; it is constructor state now, because a Check is
        instantiated once and asked to run many times.
        """
        self.blocks_dir = blocks_dir

    def run(self, repo):
        """Check the pre-push audit file name and its shared blocks.

        The pre-push audit runbook must be named PUSH-AUDIT.md (the
        historical PUSH-TEMPLATE.md name is flagged as legacy) and must
        embed the current PUSH_AUDIT_BLOCKS shared blocks -- the
        documentation and code-quality standards, plus the three criteria
        delegated to the reviewer because no grep can judge them.
        Repositories with no pre-push audit file at all are N/A --
        whether every project should have one is a separate decision.

        The runbook must also be reachable. Checking only its contents
        is how it went untriggered: across the fleet the file was
        current and correct in eight repositories while three AGENTS.md
        files mentioned it at all, and exactly one of those said when to
        run it. Mention is what this check measures. AGENTS.md is loaded
        into every session, which makes it the one place a reference is
        certain to be read.
        """
        has_new = repo.exists('PUSH-AUDIT.md')
        has_legacy = repo.exists('PUSH-TEMPLATE.md')
        if not has_new and not has_legacy:
            return self.skip('No pre-push audit file')

        problems = []
        if has_legacy:
            problems.append(
                'legacy filename PUSH-TEMPLATE.md (rename to '
                'PUSH-AUDIT.md and update references)'
            )

        filename = 'PUSH-AUDIT.md' if has_new else 'PUSH-TEMPLATE.md'
        with open(
            os.path.join(repo.path, filename), 'r', errors='replace'
        ) as f:
            content = f.read()
        problems += validate_shared_blocks(
            content,
            required=PUSH_AUDIT_BLOCKS,
            blocks_dir=self.blocks_dir,
        )

        # The reference check. Match the filename the repository
        # actually uses, so a repository on the legacy name is told
        # about the rename once rather than being told twice that
        # nothing points at a file it does not have.
        if not repo.exists('AGENTS.md'):
            problems.append(
                f'no AGENTS.md to reference {filename} from (see the '
                'llm-tooling audit)'
            )
        elif not check_file_contains(
            repo.path, 'AGENTS.md', re.escape(filename)
        ):
            problems.append(
                f'AGENTS.md does not reference {filename} (an audit '
                'nothing points at does not get run)'
            )

        if problems:
            return self.fail('; '.join(problems))
        return self.ok(
            'PUSH-AUDIT.md carries current shared blocks and is '
            'referenced from AGENTS.md')


class PlanTemplate(Check):
    id = 'plan-template'
    spec = 'docs/audits/plan-template.md'
    template = 'templates/shared-blocks/'
    issue_title = 'Plan template'

    def __init__(self, blocks_dir=None):
        """The canonical blocks to compare against.

        A parameter rather than a constant so the tests can point the
        check at a fixture directory. It was a keyword argument on the
        old function; it is constructor state now, because a Check is
        instantiated once and asked to run many times.
        """
        self.blocks_dir = blocks_dir

    def run(self, repo):
        """Check PLAN-TEMPLATE.md carries the current shared blocks.

        The generic half of a plan template -- phase file naming, the
        sub-agent execution model, the effort ladder, the model roster,
        the review checklist and the close-out sections -- is shared
        fleet-wide; only the project-specific half (what to read before
        planning, the success criteria) is written per repository.

        Repositories with no PLAN-TEMPLATE.md at all are N/A: whether
        every project should have one is a separate decision.
        """
        if not repo.exists('PLAN-TEMPLATE.md'):
            return self.skip('No PLAN-TEMPLATE.md')

        with open(
            os.path.join(repo.path, 'PLAN-TEMPLATE.md'), 'r',
            errors='replace',
        ) as f:
            content = f.read()
        problems = validate_shared_blocks(
            content,
            required=PLAN_TEMPLATE_BLOCKS,
            blocks_dir=self.blocks_dir,
        )

        if problems:
            return self.fail('; '.join(problems))
        return self.ok('PLAN-TEMPLATE.md carries current shared blocks')
