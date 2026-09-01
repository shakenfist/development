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
from audit.text.markdown import blank_generated_blocks
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


PLAN_TABLE_SEPARATOR_RE = re.compile(r'^[\s|:-]+$')


# How many offending items to name before summarising.
PLAN_INDEX_MAX_SHOWN = 5


def plan_index_cells(line):
    """The cells of a markdown table row, outer empties trimmed."""
    return [c.strip() for c in line.strip().strip('|').split('|')]


def plan_cell_text(cell):
    """A cell's text, with any markdown link collapsed to its label."""
    return PLAN_CELL_DECORATION_RE.sub(
        '', PLAN_LINK_RE.sub(r'\1', cell)
    ).strip()


def plan_index_summarise(label, items):
    """One problem string naming at most PLAN_INDEX_MAX_SHOWN items."""
    shown = ', '.join(items[:PLAN_INDEX_MAX_SHOWN])
    more = (
        '' if len(items) <= PLAN_INDEX_MAX_SHOWN
        else f' (+{len(items) - PLAN_INDEX_MAX_SHOWN} more)'
    )
    return f'{label}: {shown}{more}'


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
        lines = content.splitlines()
        for offset, line in enumerate(lines):
            lineno = offset + 1
            for _, target in PLAN_LINK_RE.findall(line):
                name = target.split('#')[0].strip()
                if name.endswith('.md'):
                    linked.add(os.path.basename(name))

            stripped = line.strip()
            if not stripped.startswith('|'):
                # Prose between tables ends the run of rows, so two
                # adjacent tables never compare dates across the boundary.
                current = None
                continue
            if PLAN_TABLE_SEPARATOR_RE.match(stripped):
                continue

            cells = plan_index_cells(stripped)

            # A header is the row the separator underlines. Recognising it
            # by its position rather than by "has no links" means a data
            # row that happens to carry no link cannot be mistaken for the
            # start of a new table.
            following = (
                lines[offset + 1].strip() if offset + 1 < len(lines) else ''
            )
            if (following.startswith('|')
                    and PLAN_TABLE_SEPARATOR_RE.match(following)):
                columns = [plan_cell_text(c).lower() for c in cells]
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
