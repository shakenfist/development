"""The criteria about how a project is built and packaged.

Releases, dependency pinning, pyproject.toml, the generated version
file, the Python and Rust version and lint settings, and the two
source-level checks that go with them: how a console script sets up
logging, and whether an HTTP handler sanitises what it logs.
"""

import fnmatch
import json
import os
import re
import subprocess
import tomllib

from audit.check import Check
from audit.files import (
    check_file_contains, check_file_exists, toml_section_has_key,
)
from audit.text.workflows import (
    step_action, step_with_inputs, workflow_job_blocks, workflow_step_blocks,
)
from audit.text.python_source import (
    HTTP_HANDLER_BASES, console_entry_point_files, handler_base_names,
    imported_top_level_modules, mask_comments_and_strings, mask_strings,
    parse_class_statements, python_source_files, python_specifier_clauses,
    sets_own_logger_propagate,
)


def uses_remote_pre_commit_hooks(repo_path):
    """True when .pre-commit-config.yaml pins at least one remote hook.

    Remote hooks are the only kind that carry a version to bump: a
    `repo: local` entry runs a script from the tree itself. Every remote
    entry pins a `rev:`, so the presence of one is the signal that there
    is something for renovate to manage.
    """
    return check_file_contains(
        repo_path, '.pre-commit-config.yaml', r'(?m)^\s*rev:'
    )


def renovate_manages_pre_commit(repo_path):
    """True when renovate.json enables the pre-commit manager.

    Renovate ships the pre-commit manager disabled, so it has to be
    turned on deliberately. There are three supported ways to do that
    and all of them count.
    """
    filepath = os.path.join(repo_path, 'renovate.json')
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, 'r', errors='replace') as f:
            config = json.load(f)
    except ValueError:
        return False
    if not isinstance(config, dict):
        return False

    manager = config.get('pre-commit')
    if isinstance(manager, dict) and manager.get('enabled') is True:
        return True

    enabled = config.get('enabledManagers')
    if isinstance(enabled, list) and 'pre-commit' in enabled:
        return True

    extends = config.get('extends')
    if isinstance(extends, list):
        for preset in extends:
            if isinstance(preset, str) and preset.endswith(
                ':enablePreCommit'
            ):
                return True
    return False


# A project is in scope for indirect dependency pinning when it already
# exactly pins its own direct dependencies. That is the project declaring
# it controls its runtime environment, which is exactly the condition
# under which pinning transitive dependencies is safe. Libraries we
# publish deliberately constrain loosely (">=") so that downstream
# consumers -- distribution packagers especially -- are free to resolve
# against whatever they already ship, and pinning transitive versions on
# their behalf takes that freedom away. The split is unambiguous in
# practice: our applications pin ~97% of their direct dependencies, our
# libraries pin none of theirs.
PIN_INTENT_THRESHOLD = 0.5


def pins_direct_dependencies(repo_path):
    """Report whether a project exactly pins its own direct dependencies.

    Returns (in_scope, exact, total). Only the [project] dependencies
    array is considered: optional-dependencies groups are extras, and a
    pinned test extra says nothing about how the project wants its
    runtime resolved.
    """
    pyproject = os.path.join(repo_path, 'pyproject.toml')
    try:
        with open(pyproject, 'rb') as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False, 0, 0

    dependencies = parsed.get('project', {}).get('dependencies', [])
    if not dependencies:
        return False, 0, 0

    exact = 0
    for dependency in dependencies:
        match = DEP_SPEC_RE.match(dependency)
        # A bare name with no specifier at all ("python-debian") has no
        # spec group to test, and is as unpinned as a dependency gets.
        spec = match.group('spec') if match else None
        if spec and EXACT_PIN_RE.match(spec.strip()):
            exact += 1
    ratio = exact / len(dependencies)
    return ratio >= PIN_INTENT_THRESHOLD, exact, len(dependencies)


# A pinned dependency entry in a pyproject.toml array, e.g.
# '    "typing-extensions==4.16.0",' or
# '    "gunicorn[gevent]==25.3.0",  # mit'. Captures the distribution
# name, optional [extras], and the leading version specifier.
DEP_PIN_RE = re.compile(
    r'''^\s*["']
        (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)
        (?P<extras>\[[^\]]*\])?
        \s*(?P<spec>(?:[<>=!~]=|===)\s*[^"',;]+)
    ''',
    re.VERBOSE,
)


# A PEP 508 requirement string as it appears once TOML parsing has
# already stripped the quoting, e.g. 'click>=8.0.0' or
# 'gunicorn[gevent]==26.0.0'. Unlike DEP_PIN_RE this matches the value
# rather than the source line, so it does not need to tolerate quotes,
# indentation or trailing comments.
DEP_SPEC_RE = re.compile(
    r'''^\s*
        (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)
        (?P<extras>\[[^\]]*\])?
        \s*(?P<spec>(?:[<>=!~]=|===)\s*[^,;]+)?
    ''',
    re.VERBOSE,
)


# An exact pin (== or ===), capturing the version. A floor/ceiling
# constraint (>=, <=, ~=, !=) is not an exact pin: a direct dependency
# declared as ">=X" that also carries an exact "==Y" pin (appended by
# the indirect-dependency workflow) is intentional and resolves fine.
EXACT_PIN_RE = re.compile(r'^={2,3}\s*(\S+)')


# The start of a TOML array (e.g. 'dependencies = [') or a table
# header (e.g. '[project.optional-dependencies]'). Either boundary
# starts a fresh dependency grouping, so a name pinned once in the main
# dependencies array and once in an optional-dependencies group is not
# treated as a conflict.
DEP_ARRAY_OPEN_RE = re.compile(r'=\s*\[')


TOML_SECTION_RE = re.compile(r'^\s*\[[A-Za-z_]')


def canonical_dependency_name(name):
    """Return the PEP 503 canonical form of a distribution name.

    Lowercase, with any run of '-', '_' and '.' collapsed to a single
    '-'. This is how pip, uv and Renovate compare names, so
    'typing_extensions' and 'typing-extensions' are one package.
    """
    return re.sub(r'[-_.]+', '-', name).lower()


#: Distributions whose import name cannot be derived from the name they
#: are distributed under. candidate_module_names() derives everything
#: else the fleet declares; this table is for the cases where the two
#: names share no letters worth matching on. Keys are canonical, and
#: values are lowercase because imported_top_level_modules() lowercases
#: what it finds. Entries for distributions no repository declares yet
#: are cheap insurance: without one the first repository to add Pillow
#: gets an issue asking it to justify a dependency nobody doubted.
IMPORT_NAME_ALIASES = {
    'attrs': {'attr'},
    'beautifulsoup4': {'bs4'},
    'googleapis-common-protos': {'google'},
    'grpcio': {'grpc'},
    'grpcio-health-checking': {'grpc_health'},
    'grpcio-status': {'grpc_status'},
    'grpcio-tools': {'grpc_tools'},
    'mysqlclient': {'mysqldb'},
    'pillow': {'pil'},
    'protobuf': {'google'},
    'py-cpuinfo': {'cpuinfo'},
    'pycryptodome': {'crypto'},
    'pycryptodomex': {'cryptodome'},
    'setuptools': {'pkg_resources'},
    'websocket-client': {'websocket'},
}


def candidate_module_names(name):
    """The module names a distribution might plausibly install.

    Derivation rather than knowledge: the module a wheel unpacks is in
    its metadata, and the audit runs against checkouts it does not
    install. So this generates the spellings the fleet actually uses --
    the name itself, the name with separators turned into underscores,
    and the name with a "python" or "py" affix taken off ("PyYAML" ->
    "yaml", "python-magic" -> "magic") -- and IMPORT_NAME_ALIASES
    carries the rest.

    Erring towards more candidates is deliberate. A spurious candidate
    can only make a dependency look used, and this criterion files an
    issue when one looks unused: a false pass costs a finding we would
    have got later anyway, while a false failure sends somebody to
    justify a dependency that was never in question.
    """
    candidates = {name.lower(), canonical_dependency_name(name)}
    for candidate in list(candidates):
        candidates.add(candidate.replace('-', '_').replace('.', '_'))
    for candidate in list(candidates):
        for prefix in ('python_', 'python-'):
            if candidate.startswith(prefix):
                candidates.add(candidate[len(prefix):])
        for suffix in ('_python', '-python'):
            if candidate.endswith(suffix):
                candidates.add(candidate[:-len(suffix)])
        if candidate.startswith('py') and len(candidate) > 4:
            candidates.add(candidate[2:].lstrip('_-'))
    candidates |= IMPORT_NAME_ALIASES.get(
        canonical_dependency_name(name), set())
    return {c for c in candidates if c}


#: A dependency line's assertion that the distribution is used without
#: being imported, e.g.
#: `# not-imported: uv -- invoked as a subprocess by the image fetcher`.
#: The reason is required: an unexplained exception is
#: indistinguishable from silencing a finding.
NOT_IMPORTED_RE = re.compile(
    r'#\s*not-imported:\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)'
    r'\s*--\s*(?P<reason>\S.*?)\s*$'
)


#: A quoted distribution name at the start of a dependency array entry.
DEP_ENTRY_NAME_RE = re.compile(
    r"""^\s*["'](?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"""
)


def read_dependency_array(repo_path):
    """Read [project] dependencies, keeping what TOML parsing discards.

    Returns (direct, generated, markers):

    * `direct` maps a canonical distribution name to the spelling the
      manifest uses and the 1-based line it is declared on -- the
      dependencies a human wrote. The spelling is kept because a
      finding that says "oslo-concurrency" sends the reader to grep
      pyproject.toml for a string that is not in it.
    * `generated` maps the same way for the names inside the
      START_OF_INDIRECT_DEPS block. tools/pin-indirect-dependencies.sh
      owns that block and regenerates it from what the direct
      dependencies resolve to, so asking whether the project *should*
      import them is the wrong question about a line no human wrote.
      Asking whether it *does* is a different question, and a useful
      one -- see UndeclaredDirectDependency.
    * `markers` maps a canonical name to the reason given for a
      `# not-imported:` annotation.

    tomllib is the authority on which names are declared, because it
    reads every spelling of a TOML array. The line scan is what finds
    the block boundaries and the annotations, both of which are
    comments and so are gone by the time tomllib has finished.
    """
    pyproject = os.path.join(repo_path, 'pyproject.toml')
    try:
        with open(pyproject, 'rb') as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}, {}, {}

    declared = {
        canonical_dependency_name(
            DEP_SPEC_RE.match(dependency).group('name'))
        for dependency in parsed.get('project', {}).get('dependencies', [])
        if DEP_SPEC_RE.match(dependency)
    }
    if not declared:
        return {}, {}, {}

    with open(pyproject, 'r', errors='replace') as f:
        lines = f.read().splitlines()

    entries, generated, markers = {}, {}, {}
    section, in_array, in_generated = None, False, False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if TOML_SECTION_RE.match(stripped):
            section, in_array = stripped.strip('[]'), False
            continue
        if not in_array:
            in_array = (section == 'project'
                        and re.match(r'^dependencies\s*=\s*\[', stripped))
            continue
        if stripped.startswith(']'):
            in_array = False
            continue

        marker = NOT_IMPORTED_RE.search(line)
        if marker:
            markers[canonical_dependency_name(marker.group('name'))] = (
                marker.group('reason'))
        if 'START_OF_INDIRECT_DEPS' in stripped:
            in_generated = True
            continue
        if 'END_OF_INDIRECT_DEPS' in stripped:
            in_generated = False
            continue

        entry = DEP_ENTRY_NAME_RE.match(line)
        if not entry:
            continue
        spelling = entry.group('name')
        name = canonical_dependency_name(spelling)
        if in_generated:
            generated.setdefault(name, (spelling, number))
        else:
            entries.setdefault(name, (spelling, number))

    direct = {
        name: entries.get(name, (name, None))
        for name in declared if name not in generated
    }
    return direct, generated, markers


#: The action which pulls build artifacts back into a later job, and
#: the action which attaches them to the GitHub release.
DOWNLOAD_ARTIFACT_ACTION = 'actions/download-artifact'
GH_RELEASE_ACTION = 'softprops/action-gh-release'


def release_asset_issues(repo_path):
    """Findings for a release.yml which can publish an empty release.

    Two defects, and it takes both to make the failure silent.

    `actions/download-artifact` invoked with no inputs downloads every
    artifact of the run and decides the destination itself, which is
    not where a later `files: dist/*` looks. The sibling publish job
    in the same template gets this right -- `name: dist` with
    `path: dist/` names the destination -- so the two jobs in one file
    disagreed about where the distribution lives.

    `softprops/action-gh-release` then treats a glob matching nothing
    as a warning rather than an error (`fail_on_unmatched_files`
    defaults to false), so the job goes green having attached no
    files.

    The bug presents as intermittent because the release jobs run on
    the persistent `[self-hosted, static]` pool. When the release job
    lands on the same runner as the build it finds the build's
    leftover `dist/` in the reused workspace and the glob matches --
    which also means the assets came from unverified files on disk
    rather than from the download, whose SHA256 nothing then checked.
    library-utilities v0.8.7 built on shakenfist-static-2, released
    from shakenfist-static-1, and shipped an empty release; kerbside
    v0.5.0 did both on shakenfist-static-1 and shipped two files,
    from identical workflow files.

    Only jobs which actually attach files are judged, so a project
    that publishes no release assets is not asked to configure how it
    would.
    """
    filepath = os.path.join(repo_path, '.github', 'workflows', 'release.yml')
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', errors='replace') as f:
        content = f.read()

    issues = []
    for name, body in workflow_job_blocks(content):
        steps = workflow_step_blocks(body)
        attaching = [
            step for step in steps
            if step_action(step) == GH_RELEASE_ACTION
            and step_with_inputs(step).get('files')
        ]
        if not attaching:
            continue

        for step in steps:
            if step_action(step) != DOWNLOAD_ARTIFACT_ACTION:
                continue
            inputs = step_with_inputs(step)
            if inputs.get('name') or inputs.get('merge-multiple') == 'true':
                continue
            issues.append(
                f'the {name} job downloads artifacts without "name:" or '
                '"merge-multiple: true", so the files do not land where '
                'its "files:" glob looks and the release is published '
                'empty; add "name: dist" and "path: dist/" as the '
                'publish job does')

        for step in attaching:
            inputs = step_with_inputs(step)
            if inputs.get('fail_on_unmatched_files') == 'true':
                continue
            issues.append(
                f'the {name} job attaches release assets without '
                '"fail_on_unmatched_files: true", so a glob which '
                'matches nothing is a warning and an empty release '
                'still reports success')

    return issues


class ReleaseProcess(Check):
    id = 'release-process'
    spec = 'docs/audits/release-process.md'
    template = 'templates/release-automation/'
    issue_title = 'Release process'

    def run(self, repo):
        """Check release process compliance."""
        if not repo.props['has_pyproject_toml']:
            return self.skip('No pyproject.toml (not a Python package)')

        issues = []
        if repo.exists('release.sh'):
            issues.append('release.sh still exists (should be removed)')
        if repo.exists('requirements.txt'):
            issues.append('requirements.txt still exists (use pyproject.toml)')
        if not check_file_exists(
            repo.path, '.github/workflows/release.yml'
        ):
            issues.append('Missing .github/workflows/release.yml')
        if not repo.exists('RELEASE-SETUP.md'):
            issues.append('Missing RELEASE-SETUP.md')
        issues.extend(release_asset_issues(repo.path))

        if issues:
            return self.fail('; '.join(issues))
        return self.ok('Release process is compliant')


class Renovate(Check):
    id = 'renovate'
    spec = 'docs/audits/renovate.md'
    template = 'templates/renovate/'
    issue_title = 'Renovate'

    def run(self, repo):
        """Check for renovate workflow and config."""
        missing = []
        if not check_file_exists(
            repo.path, '.github/workflows/renovate.yml'
        ):
            missing.append('.github/workflows/renovate.yml')
        if not repo.exists('renovate.json'):
            missing.append('renovate.json')

        if missing:
            return self.fail(f'Missing: {", ".join(missing)}', missing=missing)

        if uses_remote_pre_commit_hooks(repo.path) and not (
            renovate_manages_pre_commit(repo.path)
        ):
            return self.fail(
                'renovate.json does not enable the pre-commit manager, '
                'so the hook revisions in .pre-commit-config.yaml are '
                'unmanaged and drift silently')

        return self.ok('Renovate workflow and config exist')


class PinIndirectDependencies(Check):
    id = 'pin-indirect-dependencies'
    spec = 'docs/audits/pin-indirect-dependencies.md'
    template = 'templates/pin-indirect-dependencies/'
    issue_title = 'Pin indirect dependencies'

    def run(self, repo):
        """Check for indirect dependency pinning."""
        if not repo.props['has_pyproject_toml']:
            return self.skip('No pyproject.toml (not a Python package)')

        in_scope, exact, total = pins_direct_dependencies(repo.path)
        if not in_scope:
            return self.skip(
                f'Direct dependencies are not exactly pinned '
                f'({exact} of {total}), so this is a library rather than '
                f'an application we control the environment of. Pinning '
                f'transitive versions here would constrain downstream '
                f'consumers and distribution packagers')

        issues = []
        if not check_file_exists(
            repo.path,
            '.github/workflows/pin-indirect-dependencies.yml',
        ):
            issues.append(
                'Missing .github/workflows/'
                'pin-indirect-dependencies.yml'
            )
        for marker in ['START_OF_INDIRECT_DEPS', 'END_OF_INDIRECT_DEPS']:
            if not check_file_contains(
                repo.path, 'pyproject.toml', marker
            ):
                issues.append(
                    f'Missing # {marker} marker in pyproject.toml'
                )
        if not check_file_exists(
            repo.path, 'tools/pin-indirect-dependencies.sh'
        ):
            issues.append(
                'Missing tools/pin-indirect-dependencies.sh '
                '(reconciler script from the template)'
            )

        if issues:
            return self.fail('; '.join(issues))
        return self.ok('Indirect dependency pinning is configured')


class DependencyNameNormalization(Check):
    id = 'dependency-name-normalization'
    spec = 'docs/audits/dependency-name-normalization.md'
    template = None
    issue_title = 'Dependency name normalization'

    def run(self, repo):
        """Check pyproject.toml has no duplicate pins under PEP 503 names.

        A distribution pinned twice under different spellings (e.g.
        'typing-extensions' and 'typing_extensions') is silently fine while
        both copies carry the same version, but the moment one spelling is
        bumped -- for instance by a Renovate PR -- the two exact pins
        diverge and the resolver rejects the project as unsatisfiable.
        Renovate also treats the two spellings as separate packages and
        opens duplicate PRs. We flag, within a single dependency array,
        any canonical name carrying two or more exact (==) pins with no
        extras, or two or more conflicting exact versions.
        """
        if not repo.props['has_pyproject_toml']:
            return self.skip('No pyproject.toml (not a Python package)')

        pyproject = os.path.join(repo.path, 'pyproject.toml')
        with open(pyproject, 'r', errors='replace') as f:
            lines = f.read().splitlines()

        conflicts = []

        def evaluate(group):
            for canonical, entries in sorted(group.items()):
                if len(entries) < 2:
                    continue
                exact_versions = {e['version'] for e in entries if e['version']}
                exact_plain = [
                    e for e in entries if e['version'] and not e['has_extras']
                ]
                # Two conflicting exact versions are an outright
                # unsatisfiable pin. Two plain exact pins (differing only
                # by spelling) are the latent form that breaks the instant
                # one is bumped -- the shape that produced the duplicate
                # Renovate PRs. A base+extras pair at one version (e.g.
                # gunicorn and gunicorn[gevent]) is intentional.
                if len(exact_versions) > 1 or len(exact_plain) > 1:
                    spellings = sorted({e['raw'] for e in entries})
                    conflicts.append(f'{canonical} ({", ".join(spellings)})')

        group = {}
        for line in lines:
            if TOML_SECTION_RE.match(line) or DEP_ARRAY_OPEN_RE.search(line):
                evaluate(group)
                group = {}
            match = DEP_PIN_RE.match(line)
            if not match:
                continue
            exact = EXACT_PIN_RE.match(match.group('spec').strip())
            group.setdefault(
                canonical_dependency_name(match.group('name')), []
            ).append({
                'raw': match.group('name') + (match.group('extras') or ''),
                'version': exact.group(1) if exact else None,
                'has_extras': bool(match.group('extras')),
            })
        evaluate(group)

        if conflicts:
            return self.fail(
                f'{len(conflicts)} distribution(s) pinned under multiple '
                f'spellings that PEP 503 treats as one package: '
                f'{"; ".join(conflicts)}. Consolidate to a single '
                f'canonical pin -- divergent spellings become '
                f'unsatisfiable when one is bumped and cause duplicate '
                f'Renovate PRs')
        return self.ok('No duplicate dependency pins under PEP 503 normalization')


#: Families of distributions whose members are released in lockstep, so
#: that Renovate raises a pull request per member on the same day for
#: what upstream published as one coordinated release. Grouping is what
#: turns that back into one review.
#:
#: Each entry is (family name, canonical-name pattern, description).
#: The pattern is matched against PEP 503 canonical names, so
#: "oslo.concurrency" is tested as "oslo-concurrency".
#:
#: Deliberately seeded with one family. The fleet already groups
#: pydantic, zope and the grpc stack by hand in the repositories that
#: carry them, and promoting those to audited requirements is a
#: separate decision from this one -- adding a family here files an
#: issue against every repository that pins two of its members.
LOCKSTEP_FAMILIES = (
    ('oslo', re.compile(r'^oslo-'), 'the OpenStack oslo libraries'),
)


def renovate_config(repo_path):
    """Parse renovate.json, or None when it is missing or malformed."""
    filepath = os.path.join(repo_path, 'renovate.json')
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', errors='replace') as f:
            config = json.load(f)
    except ValueError:
        return None
    return config if isinstance(config, dict) else None


def _match_pattern(entry, spellings):
    """Does one Renovate package matcher select this distribution?

    Renovate's matchPackageNames accepts three forms -- an exact name,
    a glob, and a "/regex/" -- and any of them may be negated with a
    leading "!". All three are read because the fleet's existing groups
    use two of them: shakenfist matches "/grpcio/" and the literal
    "zope.event" in the same file.

    `spellings` is every way the manifest writes the distribution's
    name, because Renovate matches the spelling while this module
    compares canonically. A rule saying "oslo.*" is right for Renovate
    and matches nothing at all against "oslo-concurrency", so testing
    the canonical form alone would fail a repository that had done
    exactly what this criterion asks of it.
    """
    if entry.startswith('/'):
        body, _, flags = entry[1:].rpartition('/')
        if not body:
            return False
        try:
            pattern = re.compile(body, re.IGNORECASE if 'i' in flags else 0)
        except re.error:
            return False
        return any(pattern.search(s) for s in spellings)
    if '*' in entry or '?' in entry:
        return any(fnmatch.fnmatchcase(s.lower(), entry.lower())
                   for s in spellings)
    wanted = canonical_dependency_name(entry)
    return any(canonical_dependency_name(s) == wanted for s in spellings)


#: The packageRules fields Renovate rewrites into the two package
#: matchers it still has, and the entry each one becomes. Renovate
#: migrates a configuration before it validates or applies it -- see
#: lib/config/migrations/custom/package-rules-migration.ts -- so
#: excludePackageNames is not a spelling this module gets to choose
#: whether to read: by the time Renovate matches anything it is a "!"
#: entry in matchPackageNames. Migrating here too leaves one
#: implementation of the matching semantics rather than one per
#: spelling, and stops a rule written the old way being scored as
#: covering a family it excludes.
#:
#: The prefix forms become a "*" glob rather than Renovate's
#: "{/,}**". The brace alternation is there to stop a prefix
#: swallowing a path separator, and a distribution name has none.
RULE_MATCHER_MIGRATIONS = {
    'packageNames': ('matchPackageNames', '{}'),
    'packagePatterns': ('matchPackageNames', '/{}/'),
    'matchPackagePatterns': ('matchPackageNames', '/{}/'),
    'matchPackagePrefixes': ('matchPackageNames', '{}*'),
    'excludePackageNames': ('matchPackageNames', '!{}'),
    'excludePackagePatterns': ('matchPackageNames', '!/{}/'),
    'excludePackagePrefixes': ('matchPackageNames', '!{}*'),
    'matchDepPatterns': ('matchDepNames', '/{}/'),
    'matchDepPrefixes': ('matchDepNames', '{}*'),
    'excludeDepNames': ('matchDepNames', '!{}'),
    'excludeDepPatterns': ('matchDepNames', '!/{}/'),
    'excludeDepPrefixes': ('matchDepNames', '!{}*'),
}

#: Renovate's migration leaves a bare "*" as the glob rather than
#: wrapping it into the "/*/" regex the other entries become, and only
#: in the package spelling.
STAR_PATTERN_FIELDS = ('packagePatterns', 'matchPackagePatterns')


def migrated_matchers(rule):
    """A packageRules entry's package matchers, as Renovate sees them.

    Returns the matchPackageNames and matchDepNames entry lists with
    every deprecated spelling in RULE_MATCHER_MIGRATIONS folded in.
    Non-string entries are dropped rather than raising: renovate.json
    is somebody's hand-written file and this criterion should report
    what it can read, not lose the repository's run.
    """
    entries = {'matchPackageNames': [], 'matchDepNames': []}
    for field in entries:
        entries[field].extend(
            e for e in rule.get(field) or [] if isinstance(e, str))
    for field, (target, template) in RULE_MATCHER_MIGRATIONS.items():
        for entry in rule.get(field) or []:
            if not isinstance(entry, str):
                continue
            if entry == '*' and field in STAR_PATTERN_FIELDS:
                entries[target].append('*')
            else:
                entries[target].append(template.format(entry))
    return entries


def rule_covers(rule, members):
    """The canonical names a single packageRules entry selects.

    `members` maps a canonical distribution name to every spelling the
    manifest uses for it. Only the package-name matchers are read: a
    rule narrowed by matchUpdateTypes is not a group for this purpose
    and never reaches here (see grouping_rules()).

    Exclusions are gathered and subtracted once at the end rather than
    applied where they sit in the list. Renovate drops a package
    matched by any "!" entry wherever that entry appears, so
    ["!oslo.config", "/^oslo/"] and ["/^oslo/", "!oslo.config"] are one
    configuration to it. Reading them in order made the first cover the
    whole family and pass -- a false pass on exactly the configuration
    this criterion exists to catch.

    A list holding only exclusions constrains nothing positively, and
    Renovate reads it as every package bar the ones it names. The two
    matchers are separate conditions a package must satisfy together,
    so what a rule setting both covers is the intersection.

    A rule with no package matcher at all covers nothing. Either it is
    narrowed only by a matcher this module does not model --
    matchManagers, matchDatasources, matchFileNames -- or it carries no
    selector whatsoever, which Renovate rejects as a configuration
    error rather than applying to every package. Neither leaves a
    family this criterion can call grouped, and both fail visibly.
    """
    entries = migrated_matchers(rule)
    covered = None
    for field in ('matchPackageNames', 'matchDepNames'):
        if not entries[field]:
            continue
        included, excluded, positive = set(), set(), False
        for entry in entries[field]:
            negated = entry.startswith('!')
            body = entry[1:] if negated else entry
            matched = {name for name, spellings in members.items()
                       if _match_pattern(body, spellings)}
            if negated:
                excluded |= matched
            else:
                positive = True
                included |= matched
        selected = (included if positive else set(members)) - excluded
        covered = selected if covered is None else covered & selected
    return covered if covered is not None else set()


def grouping_rules(config):
    """The packageRules entries that put updates into a named group.

    A rule narrowed by matchUpdateTypes is skipped. Grouping only the
    minor and patch stream leaves the major releases arriving one pull
    request per package, which for a lockstep family is the whole
    problem: oslo's coordinated releases bump the major version of
    every member together.
    """
    rules = config.get('packageRules')
    if not isinstance(rules, list):
        return []
    return [
        rule for rule in rules
        if isinstance(rule, dict)
        and isinstance(rule.get('groupName'), str)
        and rule['groupName']
        and not rule.get('matchUpdateTypes')
    ]


def declared_distributions(repo_path):
    """Every distribution named in pyproject.toml, canonically.

    Returns a mapping of canonical name to the spellings the manifest
    uses for it: Renovate matches the spelling rather than the
    canonical form.

    Runtime dependencies and every optional-dependencies group, because
    Renovate's pep621 manager reads both and raises pull requests for
    both: a lockstep family sitting in a test extra churns exactly as
    much as one in the runtime set.
    """
    pyproject = os.path.join(repo_path, 'pyproject.toml')
    try:
        with open(pyproject, 'rb') as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}

    project = parsed.get('project', {})
    requirements = list(project.get('dependencies') or [])
    for group in (project.get('optional-dependencies') or {}).values():
        if isinstance(group, list):
            requirements.extend(group)

    names = {}
    for requirement in requirements:
        if not isinstance(requirement, str):
            continue
        match = DEP_SPEC_RE.match(requirement)
        if match:
            spelling = match.group('name')
            names.setdefault(
                canonical_dependency_name(spelling), set()).add(spelling)
    return names


class UnusedDeclaredDependency(Check):
    id = 'unused-declared-dependency'
    spec = 'docs/audits/unused-declared-dependency.md'
    template = None
    issue_title = 'Unused declared dependency'

    def applies(self, repo):
        if repo.props['not_python']:
            return 'Python is incidental here, so there is nothing to import'
        if not repo.props['has_pyproject_toml']:
            return 'No pyproject.toml (not a Python package)'
        return None

    def run(self, repo):
        """Flag runtime dependencies the project never imports.

        A dependency nobody imports is not free. It carries its own
        transitive closure into every consumer, and every package in
        that closure is another stream of Renovate pull requests
        against a project that would behave identically without it.
        library-utilities declares oslo.concurrency and imports it
        nowhere; that one line puts twelve packages into shakenfist's
        install, which is fifteen percent of its dependency closure and
        fourteen percent of its dependency bumps.

        Only [project] dependencies are read. An optional-dependencies
        group is test and build tooling that is meant to be run rather
        than imported, and reading it would flag every one of tox,
        stestr, coverage and flake8.
        """
        direct, _generated, markers = read_dependency_array(repo.path)
        if not direct:
            return self.skip(
                '[project] dependencies is empty or unreadable, so there '
                'is nothing declared to use')

        sources = python_source_files(repo.path)
        if not sources:
            return self.skip(
                'No Python source outside build and environment '
                'directories, so nothing here imports anything')

        imported = imported_top_level_modules(sources)

        unused, annotated = [], 0
        for name in sorted(direct):
            if candidate_module_names(name) & imported:
                continue
            if name in markers:
                annotated += 1
                continue
            spelling, line = direct[name]
            unused.append(
                f'{spelling} (pyproject.toml:{line})' if line else spelling)

        if unused:
            return self.fail(
                f'Declared but never imported: {", ".join(unused)}. '
                f'Remove each, or record why it is installed with a '
                f'"# not-imported: <name> -- <reason>" comment in the '
                f'dependencies array',
                unused=unused)

        noun = 'dependency is' if len(direct) == 1 else 'dependencies are'
        detail = f'All {len(direct)} declared {noun} imported'
        if annotated:
            detail = (
                f'{len(direct) - annotated} of {len(direct)} declared '
                f'dependencies are imported, and {annotated} are '
                f'annotated as used without being imported')
        return self.ok(detail)


class UndeclaredDirectDependency(Check):
    id = 'undeclared-direct-dependency'
    spec = 'docs/audits/undeclared-direct-dependency.md'
    template = None
    issue_title = 'Undeclared direct dependency'

    def applies(self, repo):
        if repo.props['not_python']:
            return 'Python is incidental here, so there is nothing to import'
        if not repo.props['has_pyproject_toml']:
            return 'No pyproject.toml (not a Python package)'
        return None

    def run(self, repo):
        """Flag imports satisfied only by the generated indirect block.

        A package the project imports but never declares resolves
        anyway, for as long as something else happens to require it.
        That is not a dependency, it is a coincidence, and it ends the
        day the intermediate library drops the requirement: shakenfist
        imported oslo_concurrency for years on an edge that existed
        only because shakenfist-utilities declared a dependency it
        never used.

        The generated block is where the coincidence is visible. Every
        name in it is there because something resolved to it, and
        tools/pin-indirect-dependencies.sh will remove it the moment
        that stops being true -- so an import of one of those names is
        a break with a date on it rather than a style problem.
        """
        direct, generated, _markers = read_dependency_array(repo.path)
        if not generated:
            return self.skip(
                'No generated indirect dependency block, so there are no '
                'transitive pins an import could be relying on')

        sources = python_source_files(repo.path)
        if not sources:
            return self.skip(
                'No Python source outside build and environment '
                'directories, so nothing here imports anything')

        imported = imported_top_level_modules(sources)

        # A module a directly declared distribution could equally have
        # provided is not evidence about the transitive one. The
        # namespace packages are why: protobuf and
        # googleapis-common-protos both install into "google", so
        # "import google.protobuf" would otherwise report whichever of
        # them happened to land in the generated block.
        declared_modules = set()
        for name in direct:
            declared_modules |= candidate_module_names(name)

        undeclared = []
        for name in sorted(generated):
            modules = candidate_module_names(name) - declared_modules
            if not modules & imported:
                continue
            spelling, line = generated[name]
            undeclared.append(f'{spelling} (pyproject.toml:{line})')

        if undeclared:
            return self.fail(
                f'Imported but declared only as a transitive pin: '
                f'{", ".join(undeclared)}. Declare each above the '
                f'# START_OF_INDIRECT_DEPS marker; the reconciler drops '
                f'the generated copy on its next run',
                undeclared=undeclared)
        return self.ok(
            f'Nothing in the {len(generated)} generated pins is imported '
            f'directly')


class RenovateLockstepGroups(Check):
    id = 'renovate-lockstep-groups'
    spec = 'docs/audits/renovate-lockstep-groups.md'
    template = None
    issue_title = 'Renovate lockstep groups'

    def applies(self, repo):
        if not repo.props['has_pyproject_toml']:
            return 'No pyproject.toml (not a Python package)'
        if renovate_config(repo.path) is None:
            return (
                'No readable renovate.json, so there is no grouping '
                'configuration to hold to this -- the renovate criterion '
                'is the one that covers its absence')
        return None

    def run(self, repo):
        """Require lockstep dependency families to bump as one group.

        Renovate treats every distribution as independent, which is
        right until upstream stops doing so. The oslo libraries cut
        coordinated releases: oslo.concurrency, oslo.config, oslo.i18n
        and oslo.utils go out together, so an ungrouped project gets
        four pull requests on the same evening for one upstream event,
        four CI runs, and four chances to land a partial upgrade.
        """
        config = renovate_config(repo.path)
        declared = declared_distributions(repo.path)
        rules = grouping_rules(config)

        applicable, problems = [], []
        for family, pattern, description in LOCKSTEP_FAMILIES:
            members = {name: spellings
                       for name, spellings in declared.items()
                       if pattern.search(name)}
            # One member cannot arrive out of step with itself, and
            # grouping it would be a rule that changes nothing.
            if len(members) < 2:
                continue
            applicable.append(family)
            if any(rule_covers(rule, members) >= set(members)
                   for rule in rules):
                continue
            spelled = sorted(
                s for spellings in members.values() for s in spellings)
            problems.append(
                f'{family} ({", ".join(spelled)}) -- {description}')

        if not applicable:
            return self.skip(
                'No lockstep dependency family has more than one member '
                'declared here')
        if problems:
            return self.fail(
                f'Not grouped for Renovate: {"; ".join(problems)}. '
                f'Add a packageRules entry with a groupName covering '
                f'every member, unrestricted by matchUpdateTypes',
                ungrouped=sorted(problems))
        return self.ok(
            f'Every lockstep family declared here is grouped: '
            f'{", ".join(sorted(applicable))}')


class PyprojectUsage(Check):
    id = 'pyproject-usage'
    spec = 'docs/audits/pyproject-usage.md'
    template = None
    issue_title = 'pyproject.toml usage'

    def run(self, repo):
        """Check Python projects use pyproject.toml for packaging.

        Any project with Python code must have a pyproject.toml, and
        must not carry legacy packaging files (setup.py, setup.cfg)
        alongside it.
        """
        if repo.props['is_docs_only']:
            return self.skip('Docs-only repo')
        if repo.props['has_cargo_toml']:
            return self.skip('Rust project (any Python is helper scripts)')
        if repo.props['not_python']:
            return self.skip('Not a Python project (per overrides)')

        if repo.props['has_pyproject_toml']:
            legacy = [
                f for f in ['setup.py', 'setup.cfg']
                if repo.exists(f)
            ]
            if legacy:
                return self.fail(
                    f'Legacy packaging files exist alongside '
                    f'pyproject.toml: {", ".join(legacy)}')
            return self.ok(
                'pyproject.toml exists with no legacy '
                'packaging files')

        # No pyproject.toml: only a problem if there is Python code.
        try:
            result = subprocess.run(
                ['git', '-C', repo.path, 'ls-files', '--', '*.py'],
                capture_output=True, text=True, timeout=30,
            )
            python_files = [
                line for line in result.stdout.splitlines()
                if line.strip()
            ]
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Could not run git ls-files: {e}')

        if python_files:
            return self.fail(
                f'{len(python_files)} Python file(s) but no '
                f'pyproject.toml')
        return self.skip('No Python code')


class VersionFileGitignore(Check):
    id = 'version-file-gitignore'
    spec = 'docs/audits/version-file-gitignore.md'
    template = None
    issue_title = 'Generated version file'

    def run(self, repo):
        """Check generated version files are gitignored and not tracked.

        setuptools_scm writes a version file (usually _version.py) at
        build time. That file must never be committed: it should be
        covered by .gitignore and must not be tracked by git.
        """
        if not repo.props['has_pyproject_toml']:
            return self.skip('No pyproject.toml (not a Python package)')

        pyproject = os.path.join(repo.path, 'pyproject.toml')
        with open(pyproject, 'r', errors='replace') as f:
            content = f.read()

        match = re.search(
            r'^\s*(?:write_to|version_file|version-file)\s*=\s*'
            r'["\']([^"\']+)["\']',
            content, re.MULTILINE,
        )

        issues = []

        # A tracked generated version file is always wrong, whether or
        # not we can work out the configured path.
        try:
            result = subprocess.run(
                [
                    'git', '-C', repo.path,
                    'ls-files', '--', '*_version.py',
                ],
                capture_output=True, text=True, timeout=30,
            )
            tracked = [
                line for line in result.stdout.splitlines()
                if line.strip()
            ]
            if tracked:
                issues.append(
                    f'Generated version file tracked in git '
                    f'(use git rm --cached): {", ".join(sorted(tracked))}'
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            issues.append(f'Could not run git ls-files: {e}')

        if not match:
            if issues:
                return self.fail('; '.join(issues))
            return self.skip(
                'No generated version file configured in '
                'pyproject.toml')

        version_file = match.group(1)
        try:
            result = subprocess.run(
                [
                    'git', '-C', repo.path,
                    # --no-index so a tracked copy doesn't mask the
                    # .gitignore coverage answer.
                    'check-ignore', '-q', '--no-index', version_file,
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 1:
                issues.append(
                    f'{version_file} is not covered by .gitignore'
                )
            elif result.returncode != 0:
                issues.append(
                    f'Could not check .gitignore coverage: '
                    f'{result.stderr.strip()}'
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            issues.append(f'Could not run git check-ignore: {e}')

        if issues:
            return self.fail('; '.join(issues))
        return self.ok(
            f'{version_file} is gitignored and no generated '
            f'version file is tracked')


class ConsoleLogging(Check):
    id = 'console-logging'
    spec = 'docs/audits/console-logging.md'
    template = None
    issue_title = 'Console script logging setup'

    def run(self, repo):
        """Check console entry points also configure the root logger.

        shakenfist_utilities.logs.setup_console() raises the root
        logger's level but attaches its handler to one named logger. Every
        other module's records therefore propagate to a root logger with
        no handler on it and are dropped, so an entry point calling it
        must also call logging.basicConfig() to put a handler there -- and
        must then stop its own logger propagating into that handler, or
        every line it logs is emitted twice.

        Only files named by [project.scripts] are examined, and only those
        that call setup_console(). A repository that declares no console
        scripts, or whose entry points do not use the helper, is not
        applicable: this is a rule about how the helper is used, not a
        requirement to use it.
        """
        entry_points, unresolved = console_entry_point_files(repo.path)
        if not entry_points:
            # "None declared" and "declared, none found" are different
            # facts, and reporting the second as the first is a false
            # statement about a repository nobody has looked at yet.
            if unresolved:
                return self.skip(
                    f'{len(unresolved)} entry point declaration(s) '
                    f'resolved to no file in this checkout: '
                    + ', '.join(unresolved))
            return self.skip('No console or GUI entry points declared')

        problems = []
        using = []
        exempt = []
        for relative in entry_points:
            try:
                with open(
                    os.path.join(repo.path, relative), 'r', errors='replace'
                ) as f:
                    content = f.read()
            except OSError:
                continue

            # Matched against the code rather than the file: a
            # commented-out logging.basicConfig() satisfied the
            # requirement it is the absence of, and a docstring saying a
            # module deliberately does not call setup_console() made it
            # a caller.
            code = mask_comments_and_strings(content)
            if not re.search(r'(?<!def )setup_console\s*\(', code):
                continue

            # Read per file rather than per line: the finding is about
            # the file's logging setup as a whole, so there is no single
            # line for a marker to sit on. Read from the comment view,
            # because a marker is a comment: a docstring mentioning the
            # marker exempted the file that mentioned it.
            if 'audit-ok: console-logging' in mask_strings(content):
                exempt.append(relative)
                continue
            using.append(relative)

            missing = []
            if not re.search(r'logging\.basicConfig\s*\(', code):
                missing.append(
                    'logging.basicConfig() (INFO from every other module '
                    'reaches a root logger with no handler and is dropped)'
                )
            if not sets_own_logger_propagate(code):
                missing.append(
                    'propagate = False on its own logger (its own lines '
                    'are emitted twice once root has a handler)'
                )
            if missing:
                problems.append(f'{relative}: missing {"; ".join(missing)}')

        # Named in every outcome, not only when nothing resolved at all.
        # A repository with a mixed layout had its unresolved entry
        # points dropped and collected a pass on the ones that did
        # resolve -- the same clean bill for a file nobody opened as the
        # total case, in the partial rather than the whole.
        unnamed = (
            f'{len(unresolved)} declaration(s) resolved to no file in '
            f'this checkout: ' + ', '.join(unresolved)
        ) if unresolved else ''

        if not using:
            if exempt:
                return self.skip(
                    f'{len(exempt)} console entry point(s) calling '
                    f'setup_console() exempt by audit-ok marker'
                    + (f'; {unnamed}' if unnamed else ''))
            return self.skip(
                f'{len(entry_points)} console entry point(s), none '
                f'calling shakenfist_utilities.logs.setup_console()'
                + (f'; {unnamed}' if unnamed else ''))

        if problems:
            return self.fail(
                f'{len(problems)} of {len(using)} console entry '
                f'point(s) calling setup_console() do not configure '
                f'the root logger -- ' + '; '.join(sorted(problems))
                + (f'; {unnamed}' if unnamed else ''))

        # A pass is a statement about every entry point the repository
        # declares, so one that resolved to no file withholds it. The
        # ones that did resolve are compliant and the details say so, but
        # the criterion has not been assessed -- which is not_applicable,
        # the same answer the total case gives, rather than a fail this
        # checkout has not demonstrated.
        if unresolved:
            return self.skip(
                f'{len(using)} console entry point(s) calling '
                f'setup_console() configure the root logger, but '
                + unnamed)

        return self.ok(
            f'{len(using)} console entry point(s) calling '
            f'setup_console() configure the root logger')


class HeaderSanitization(Check):
    id = 'header-sanitization'
    spec = 'docs/audits/security-sanitization.md'
    template = None
    issue_title = 'HTTP header sanitization'

    def run(self, repo):
        """Check http.server handler subclasses strip header newlines.

        A header value carrying a CR or LF splits the response
        (CWE-113), which CodeQL reports as py/http-response-splitting.
        The fleet remedy is occystrap's SafeHeaderMixin, which strips
        both before delegating to the base class.

        The mixin has to be listed *first* in the bases or the MRO
        reaches the base send_header() and the override never runs,
        which is why position is checked rather than mere presence.

        Flask projects are unaffected -- Werkzeug's Headers raises on a
        line break -- and are not applicable here because they have no
        http.server handler subclass to find.
        """
        try:
            result = subprocess.run(
                ['git', '-C', repo.path, 'ls-files', '--', '*.py'],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Could not run git ls-files: {e}')

        # A non-zero exit leaves stdout empty, which is indistinguishable
        # from a repository holding no Python at all. On a security check
        # a silent clean bill is the worse default, so say so instead.
        if result.returncode != 0:
            return self.fail(
                f'git ls-files failed with exit {result.returncode}: '
                f'{result.stderr.strip()}')

        handlers = []
        unreadable = []
        problems = []
        for relative in result.stdout.splitlines():
            relative = relative.strip()
            if not relative:
                continue
            path = os.path.join(repo.path, relative)
            try:
                with open(path, 'r', errors='replace') as f:
                    content = f.read()
            except OSError:
                continue
            if not any(
                base in mask_comments_and_strings(content)
                for base in HTTP_HANDLER_BASES
            ):
                continue
            aliases = handler_base_names(content)

            # Indented definitions count. http.server gives no way to
            # pass arguments to a handler, so defining one inside a
            # function to close over state is the common idiom -- and
            # anchoring at column zero reported those repositories as
            # having no raw HTTP server at all.
            for name, bases, start, stop in parse_class_statements(content):
                # A base list may span lines, and a dotted base is an
                # attribute of the module it was imported from.
                names = [] if bases is None else [
                    b.strip().split('.')[-1] for b in bases.split(',')
                ]
                names = [n for n in names if n]
                handler = next((n for n in names if n in aliases), None)
                # Compared as whole names rather than as substrings, so
                # a class inheriting MyBaseHTTPRequestHandlerWrapper is
                # out of scope instead of being failed for not carrying
                # a mixin it has no reason to.
                if bases is not None and handler is None:
                    continue

                line = content.count('\n', 0, start) + 1
                where = f'{relative}:{line} ({name})'

                # The marker is read on the class statement rather than
                # per file: a module may hold both a real server and a
                # test fixture, and exempting the file would exempt both.
                # To the end of the line so a trailing comment counts as
                # being *on* the statement, and exactly one line above,
                # which is what security-sanitization.md advertises.
                # Sliced from the comment view rather than the file,
                # so a string constant holding the marker no longer
                # exempts the class below it. Masking preserves offsets,
                # so start and stop still address the same characters.
                commented = mask_strings(content)
                end = commented.find('\n', stop)
                statement = commented[
                    start:end if end != -1 else len(commented)]
                preceding = commented[:start].splitlines()[-1:]
                if 'audit-ok: header-sanitization' in statement or any(
                    'audit-ok: header-sanitization' in p for p in preceding
                ):
                    continue

                # A base list this cannot read is not a class this can
                # clear. Reported rather than skipped, because the skip
                # is indistinguishable from a repository with no handler
                # in it and reads as a clean bill on a security check.
                if bases is None:
                    unreadable.append(where)
                    continue

                handlers.append(where)
                if 'SafeHeaderMixin' not in names:
                    problems.append(
                        f'{where}: does not inherit SafeHeaderMixin, so '
                        f'send_header() passes CR and LF straight through'
                    )
                elif names.index('SafeHeaderMixin') > names.index(handler):
                    problems.append(
                        f'{where}: SafeHeaderMixin is listed after '
                        f'{handler}, so the MRO reaches the base '
                        f'send_header() and the override never runs'
                    )

        problems.extend(
            f'{where}: could not read the base list, so whether it is an '
            f'HTTP request handler is unknown'
            for where in unreadable
        )

        if not handlers and not unreadable:
            return self.skip('No http.server request handler subclasses')

        if problems:
            return self.fail(
                f'{len(problems)} of {len(handlers) + len(unreadable)} '
                f'HTTP request handler class(es) do not sanitize '
                f'header values: ' + '; '.join(sorted(problems)))

        return self.ok(
            f'{len(handlers)} HTTP request handler subclass(es) '
            f'inherit SafeHeaderMixin first')


class PythonVersionTargeting(Check):
    id = 'python-version-targeting'
    spec = 'docs/audits/python-version.md'
    template = None
    issue_title = 'Python version targeting'

    def run(self, repo):
        """Check the declared Python floor exists and is stated once.

        A package that does not declare requires-python claims to support
        every interpreter, which is never true and gives pip nothing to
        refuse an install with.

        Where renovate.json also carries constraints.python, the two must
        agree. Both are derived from the same fact -- the system Python of
        the oldest supported distribution -- so a disagreement means one
        of them was updated and the other forgotten, and renovate then
        proposes bumps against a floor the package does not claim.
        """
        if repo.props.get('not_python') or repo.props.get('is_docs_only'):
            return self.skip('Not a Python project (per overrides)')
        if repo.props.get('has_cargo_toml'):
            return self.skip('Rust project (any Python is helper scripts)')
        if not repo.props['has_pyproject_toml']:
            return self.skip('No pyproject.toml (not a Python package)')

        try:
            with open(os.path.join(repo.path, 'pyproject.toml'), 'rb') as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as e:
            return self.fail(f'Could not parse pyproject.toml: {e}')

        # A pyproject.toml holding only [tool.*] sections configures
        # linters; it does not package anything, and has nothing to
        # declare an interpreter floor for.
        if not isinstance(data, dict) or 'project' not in data:
            return self.skip(
                'pyproject.toml carries tool configuration only, not '
                'packaging metadata')

        # Valid TOML, invalid PEP 621. Reported rather than raised: the
        # AttributeError this used to throw propagated out of run_checks
        # and cost the repository every other check as well.
        if not isinstance(data['project'], dict):
            return self.fail(
                f'pyproject.toml declares [project] as a '
                f'{type(data["project"]).__name__}, not a table, so it '
                f'carries no packaging metadata to read')

        requires = data['project'].get('requires-python')
        if not requires:
            return self.fail(
                'pyproject.toml declares no requires-python, so the '
                'package claims to support every interpreter and pip '
                'has nothing to refuse an install with')

        renovate = os.path.join(repo.path, 'renovate.json')
        if os.path.exists(renovate):
            try:
                with open(renovate, 'r', errors='replace') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, OSError):
                config = {}
            # Guarded rather than assumed: renovate.json's top level can
            # be any JSON value, and calling .get() on the array one
            # repository had there raised out of run_checks and cost
            # that repository every other check as well.
            if not isinstance(config, dict):
                config = {}
            constraints = config.get('constraints')
            constraint = (
                constraints.get('python')
                if isinstance(constraints, dict) else None
            )
            # Compared clause by clause rather than as text, so the
            # finding is a real disagreement about which interpreters are
            # supported and not a difference in how one was spelled.
            if constraint and (
                python_specifier_clauses(constraint)
                != python_specifier_clauses(requires)
            ):
                return self.fail(
                    f'requires-python is "{requires}" but '
                    f'renovate.json constraints.python is '
                    f'"{constraint}". Both describe the '
                    f'interpreters this package supports, so they '
                    f'disagree, and renovate is resolving dependency '
                    f'versions against a range the package does not '
                    f'claim')

        return self.ok(f'requires-python is "{requires}"')


class RustUnwrapLint(Check):
    id = 'rust-unwrap-lint'
    spec = 'docs/audits/rust-unwrap-lint.md'
    template = None
    issue_title = 'Rust unwrap lint'

    def run(self, repo):
        """Check Rust projects enable clippy's unwrap_used lint.

        The root Cargo.toml must set unwrap_used to warn or deny (under
        [workspace.lints.clippy], or [lints.clippy] for single-crate
        repos), a clippy.toml must exempt test code with
        allow-unwrap-in-tests, and every first-party crate manifest must
        either inherit the workspace lints or define the lint itself.
        Fuzz harness crates are exempt.
        """
        if not repo.props['has_cargo_toml']:
            return self.skip('No Cargo.toml (not a Rust project)')

        if repo.exists('Cargo.toml'):
            root_manifest = 'Cargo.toml'
        else:
            root_manifest = 'src/Cargo.toml'
        root_dir = os.path.dirname(root_manifest)

        # Accepts unwrap_used = "warn", "deny", or the table form
        # { level = "warn", priority = -1 }.
        lint_pattern = r'unwrap_used\s*=\s*.*"(warn|deny)"'

        issues = []

        with open(
            os.path.join(repo.path, root_manifest), 'r', errors='replace'
        ) as f:
            root_content = f.read()
        if not (
            toml_section_has_key(
                root_content, 'workspace.lints.clippy', lint_pattern
            )
            or toml_section_has_key(
                root_content, 'lints.clippy', lint_pattern
            )
        ):
            issues.append(
                f'clippy unwrap_used lint not set to warn or deny '
                f'in {root_manifest}'
            )

        clippy_toml = os.path.join(root_dir, 'clippy.toml')
        if not check_file_contains(
            repo.path, clippy_toml,
            r'(?m)^\s*allow-unwrap-in-tests\s*=\s*true',
        ):
            issues.append(
                f'{clippy_toml} missing allow-unwrap-in-tests = true'
            )

        # Every other first-party crate manifest must inherit the
        # workspace lints or define the lint itself.
        try:
            result = subprocess.run(
                ['git', '-C', repo.path, 'ls-files', '--', '*Cargo.toml'],
                capture_output=True, text=True, timeout=30,
            )
            manifests = [
                line for line in result.stdout.splitlines()
                if line.strip()
            ]
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return self.fail(f'Could not run git ls-files: {e}')

        for manifest in manifests:
            if manifest == root_manifest:
                continue
            if 'fuzz' in manifest.split('/'):
                continue
            with open(
                os.path.join(repo.path, manifest), 'r', errors='replace'
            ) as f:
                content = f.read()
            if '[package]' not in content:
                continue
            inherits = (
                toml_section_has_key(
                    content, 'lints', r'workspace\s*=\s*true'
                )
                or re.search(
                    r'^\s*lints\.workspace\s*=\s*true', content,
                    re.MULTILINE,
                )
            )
            defines = toml_section_has_key(
                content, 'lints.clippy', lint_pattern
            )
            if not (inherits or defines):
                issues.append(
                    f'{manifest} neither inherits workspace lints '
                    f'([lints] workspace = true) nor defines '
                    f'unwrap_used itself'
                )

        if issues:
            return self.fail('; '.join(issues))
        return self.ok(
            'clippy unwrap_used lint is enabled with '
            'allow-unwrap-in-tests')


class Flake8Wrap(Check):
    id = 'flake8wrap'
    spec = 'docs/audits/workflow-standards.md'
    template = None
    issue_title = 'Workflow standards (flake8wrap)'
    column = 'flake8wrap'

    def run(self, repo):
        """Check flake8wrap.sh for correct SC2086 handling."""
        if not repo.props['has_flake8wrap']:
            return self.skip('No tools/flake8wrap.sh')

        filepath = os.path.join(repo.path, 'tools', 'flake8wrap.sh')
        with open(filepath, 'r', errors='replace') as f:
            content = f.read()

        issues = []
        if 'SC2086' not in content:
            issues.append('Missing shellcheck disable=SC2086 directive')

        # Check for quoted ${filtered_files} on diff/flake8 lines
        # (the variable must NOT be quoted)
        for line in content.splitlines():
            if ('filtered_files' in line
                    and ('diff' in line or 'FLAKE' in line or 'flake' in line)):
                if '"${filtered_files}"' in line:
                    issues.append(
                        'filtered_files is incorrectly quoted '
                        'on diff/flake8 line'
                    )
                    break

        if issues:
            return self.fail('; '.join(issues))
        return self.ok('flake8wrap.sh has correct SC2086 handling')
