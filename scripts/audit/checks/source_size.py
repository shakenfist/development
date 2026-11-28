"""The criteria about source size.

How large a unit of source may grow before somebody has to say why, at
two scales: a file-level ratchet, which pins every file over the
threshold to its current length so that growing one is a baseline edit
a reviewer cannot scroll past, and a function-level lint for the
languages whose toolchain can measure one.

Not in `packaging.py` beside `rust-unwrap-lint`, nor in
`ci_workflows.py` beside the other pre-commit criteria, although each
has a claim on one of these. Both modules, and both of their test
suites, sit exactly at their `tools/source-size-baseline` entries, so
adding a criterion to either would mean raising an entry by hand --
which is the question the ratchet exists to ask, and not one a
criterion implementing the ratchet gets to answer with "to land the
criterion".

So a reader looking for the Rust lint beside `rust-unwrap-lint`, or
for the pre-commit criterion beside `pre-commit-config`, finds it
here: this module is the family those two have in common, and the
modules they are not in are the ones that have run out of room.
"""

import ast
import os

from audit.check import Check, ToolFailure
from audit.files import (
    PRE_COMMIT_RUN_ALL_RE, file_matches, file_mentions, require_tracked_paths,
)
from audit.repo import DEVELOPMENT_ROOT, READ_MAX_BYTES, read_within


#: The template an adopter copies, and the path it copies it to. The
#: copy is compared with the template byte for byte, so both spellings
#: are the criterion's rather than a convenience.
RATCHET_TEMPLATE = 'templates/source-size-ratchet/source-size-ratchet.py'
RATCHET_COPY = 'tools/source-size-ratchet.py'

PRE_COMMIT_CONFIG = '.pre-commit-config.yaml'


def source_extensions():
    """SOURCE_EXTENSIONS as the shipped ratchet spells it.

    The list of what the tool counts belongs to the tool, and a copy
    here would be a second list to keep in step -- a repository whose
    only source is in a language the template learned about later
    would be reported as having none.

    Read out of the template's text rather than imported, because the
    template's filename is hyphenated and so is not a module name. The
    assignment is found with `ast` and its value read as a literal:
    nothing in the file is executed. Importing it by path, exec'ing it
    or shelling out to it would each run the script this criterion
    exists to compare rather than read, inside the audit, for one
    tuple.

    A template the audit cannot read, or whose assignment is not where
    this expects it, is the audit's own checkout being wrong rather
    than a finding about anybody, so it raises.
    """
    text = read_within(
        DEVELOPMENT_ROOT, os.path.join(DEVELOPMENT_ROOT, RATCHET_TEMPLATE))
    if text is None:
        raise ToolFailure(
            f'Could not read {RATCHET_TEMPLATE} in the audit\'s own checkout')
    try:
        tree = ast.parse(text, filename=RATCHET_TEMPLATE)
    except SyntaxError as e:
        raise ToolFailure(
            f'Could not parse {RATCHET_TEMPLATE} in the audit\'s own '
            f'checkout: {e}') from e
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name)
                   and target.id == 'SOURCE_EXTENSIONS'
                   for target in node.targets):
            continue
        try:
            return tuple(ast.literal_eval(node.value))
        except (ValueError, TypeError) as e:
            raise ToolFailure(
                f'SOURCE_EXTENSIONS in {RATCHET_TEMPLATE} is not a literal '
                f'this check can read: {e}') from e
    raise ToolFailure(
        f'{RATCHET_TEMPLATE} in the audit\'s own checkout declares no '
        f'SOURCE_EXTENSIONS')


class SourceSizeRatchet(Check):
    id = 'source-size-ratchet'
    spec = 'docs/audits/source-size-ratchet.md'
    template = 'templates/source-size-ratchet/'
    issue_title = 'Source size ratchet'

    def applies(self, repo):
        """Skip a repository with no tracked source to measure.

        The index rather than a tree walk, for the reason the tool
        itself uses it: what is on disk includes build output, crate
        caches and virtualenvs, and the index is the one list of what
        the repository actually carries that needs no exclusions
        maintained.

        The generated shapes the tool never counts -- protoc output,
        minified bundles -- and the repository's own exclude file are
        deliberately not applied here. A repository whose every source
        file is generated would still be right to adopt the ratchet,
        and the alternative is this criterion keeping a second copy of
        the tool's exclusion logic in order to declare itself
        inapplicable.
        """
        extensions = source_extensions()
        tracked = require_tracked_paths(
            repo.path, *(f'*{extension}' for extension in extensions))
        if not tracked:
            return (f'No tracked source files for the ratchet to measure '
                    f'(nothing matching {", ".join(extensions)})')
        return None

    def run(self, repo):
        """Check the ratchet is adopted, and that CI reaches it.

        Three things, each reported separately because each has its own
        fix: the script is this repository's template byte for byte,
        `.pre-commit-config.yaml` invokes it, and at least one workflow
        reaches it.

        The bytes rather than the text, as the review tracking
        templates are compared: the script's threshold is a constant
        rather than a flag precisely so that one review of the template
        is a review of every copy, and a copy differing by a byte is a
        different blob that has to be reviewed again wherever it is
        carried.

        The hook is matched by its entry rather than its id, so that a
        repository which renamed the id still counts and a comment
        naming the tool does not.

        CI reach is the half a config read cannot answer. A hook in
        `.pre-commit-config.yaml` gates a commit by somebody who has
        hooks installed; it gates a merge only if something in CI runs
        it. Two shapes count, which are the two the fleet has: a
        workflow running every hook over the tree, as `pre-commit run
        --all-files` does, or a workflow invoking the script itself,
        which is how a repository that runs each hook as its own job
        reaches it. An invocation naming a single other hook is
        neither: it runs that hook and no others, so counting it would
        pass a repository whose ratchet job had been deleted on the
        strength of a job that lints something else.

        What this does not do is re-run the ratchet. Whether the
        baseline is honoured is the hook's job on every pull request,
        and an audit re-judging it would only disagree with CI. Neither
        `tools/source-size-baseline` nor `tools/source-size-exclude` is
        required to exist: a missing baseline is an empty one, so a
        repository with nothing over the threshold is compliant without
        the file, and an exclude file is for a repository that has a
        generated tree to exclude.
        """
        problems = []

        canonical = read_within(
            DEVELOPMENT_ROOT,
            os.path.join(DEVELOPMENT_ROOT, RATCHET_TEMPLATE), binary=True)
        if canonical is None:
            raise ToolFailure(
                f'Could not read {RATCHET_TEMPLATE} in the audit\'s own '
                f'checkout')
        copy = repo.read_blob(RATCHET_COPY)
        # lexists rather than repo.exists(), which hides a link out of
        # the checkout: here the name being there is the point.
        if copy is None and os.path.lexists(repo.join(RATCHET_COPY)):
            problems.append(
                f'{RATCHET_COPY} is not a regular file the audit will read '
                f'(a dangling link, a link out of the checkout, a directory, '
                f'or over {READ_MAX_BYTES // (1024 * 1024)} MiB)')
        elif copy is None:
            problems.append(f'{RATCHET_COPY} is missing')
        elif copy != canonical:
            problems.append(f'{RATCHET_COPY} differs from {RATCHET_TEMPLATE}')

        in_pre_commit = file_mentions(repo, PRE_COMMIT_CONFIG, RATCHET_COPY)
        if not in_pre_commit:
            problems.append(
                f'{PRE_COMMIT_CONFIG} does not invoke {RATCHET_COPY}')

        workflows = [
            f'.github/workflows/{workflow}' for workflow in repo.workflows()
        ]
        # A workflow running every pre-commit hook runs the ratchet
        # hook with them, so the tool reaches CI without the workflow
        # naming it -- but only if the hook is declared, which is what
        # it would run. PRE_COMMIT_RUN_ALL_RE rather than the looser
        # PRE_COMMIT_RUN_RE for the reason it carries: a job running
        # one named hook does not run this one.
        via_pre_commit = in_pre_commit and any(
            file_matches(repo, workflow, PRE_COMMIT_RUN_ALL_RE)
            for workflow in workflows
        )
        runs_directly = any(
            file_mentions(repo, workflow, RATCHET_COPY)
            for workflow in workflows
        )
        if not via_pre_commit and not runs_directly:
            problems.append(
                f'no workflow runs every pre-commit hook (as `pre-commit run '
                f'--all-files` does) or invokes {RATCHET_COPY}, so nothing '
                f'gates a merge or a branch pushed with --no-verify')

        if problems:
            return self.fail(
                f'{"; ".join(problems)}. Adopt the ratchet from '
                f'templates/source-size-ratchet/ in shakenfist/development: '
                f'copy the script byte for byte, merge its hook stanza into '
                f'{PRE_COMMIT_CONFIG}, and give CI a lane that reaches it')

        return self.ok(
            f'{RATCHET_COPY} matches {RATCHET_TEMPLATE}, runs from '
            f'{PRE_COMMIT_CONFIG}, and is reached by CI')
