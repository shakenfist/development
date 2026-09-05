#!/usr/bin/env python3

"""Tests for audit/checks/packaging.py.

Run with: python3 scripts/tests/test_packaging.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import packaging  # noqa: E402
from tests.base import (  # noqa: E402
    REPO_ROOT, CheckTestCase, run_check,
)


def check_dependency_name_normalization(path, props=None):
    return run_check(packaging.DependencyNameNormalization(), path, props)


def check_pin_indirect_deps(path, props=None):
    return run_check(packaging.PinIndirectDependencies(), path, props)


def check_renovate(path, props=None):
    return run_check(packaging.Renovate(), path, props)


def check_console_logging(path, props=None):
    return run_check(packaging.ConsoleLogging(), path, props)


def check_header_sanitization(path, props=None):
    return run_check(packaging.HeaderSanitization(), path, props)


def check_python_version_targeting(path, props=None):
    return run_check(packaging.PythonVersionTargeting(), path, props)


class DependencyNameNormalizationTest(unittest.TestCase):
    def _check(self, pyproject_body):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write(pyproject_body)
            return check_dependency_name_normalization(
                tmp, {'has_pyproject_toml': True}
            )

    def test_not_applicable_without_pyproject(self):
        result = check_dependency_name_normalization(
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
            return check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )

    def test_not_applicable_without_pyproject(self):
        result = check_pin_indirect_deps(
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
            result = check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )
        self.assertEqual(result['status'], 'pass')

    def test_unparseable_pyproject_is_out_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write('this is not = valid toml [\n')
            result = check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )
        self.assertEqual(result['status'], 'not_applicable')


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
        return check_renovate(tmp, {})

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
            result = check_renovate(tmp, {})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('renovate.json', result['missing'])


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
            return check_console_logging(tmp, {})

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
            return check_header_sanitization(tmp, {})

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
            result = check_header_sanitization(tmp, {})
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
            return check_python_version_targeting(tmp, merged)

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


class ReleaseProcessTest(CheckTestCase):
    check_class = packaging.ReleaseProcess

    def compliant(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.workflow('release.yml', 'on: push\n')
        self.fixture.write('RELEASE-SETUP.md', '# Setup\n')

    def test_without_pyproject_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No pyproject.toml')

    def test_a_compliant_project_passes(self):
        self.compliant()
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_leftover_release_script_fails(self):
        self.compliant()
        self.fixture.write('release.sh', '#!/bin/bash\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='release.sh still exists')

    def test_a_leftover_requirements_file_fails(self):
        self.compliant()
        self.fixture.write('requirements.txt', 'six\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='requirements.txt still exists')

    def test_a_missing_release_workflow_fails(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.write('RELEASE-SETUP.md', '# Setup\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='release.yml')

    def test_a_missing_setup_document_fails(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.workflow('release.yml', 'on: push\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='RELEASE-SETUP.md')

    def test_every_problem_is_reported_not_just_the_first(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.write('release.sh', '#!/bin/bash\n')
        result = self.assert_fail(self.check(has_pyproject_toml=True))
        self.assertIn('release.sh', result['details'])
        self.assertIn('RELEASE-SETUP.md', result['details'])

    # The asset-attaching half of the criterion. A release.yml can
    # satisfy every existence check above and still publish releases
    # with nothing attached to them, which is how this went unnoticed
    # across five repositories: the job reports success either way.

    RELEASE_JOB = (
        'name: Release\n'
        'jobs:\n'
        '  github-release:\n'
        '    runs-on: [self-hosted, static]\n'
        '    steps:\n'
        '      - name: Download artifacts\n'
        '        uses: actions/download-artifact@v8\n'
        '%s'
        '      - name: Create GitHub Release\n'
        '        uses: softprops/action-gh-release@v3\n'
        '        with:\n'
        '%s'
    )

    NAMED_DOWNLOAD = ('        with:\n'
                      '          name: dist\n'
                      '          path: dist/\n')
    GUARDED_FILES = ('          files: dist/*\n'
                     '          fail_on_unmatched_files: true\n')

    def release_workflow(self, download='', release='          files: dist/*\n'):
        self.compliant()
        self.fixture.workflow('release.yml', self.RELEASE_JOB % (download, release))

    def test_a_correctly_wired_release_passes(self):
        self.release_workflow(self.NAMED_DOWNLOAD, self.GUARDED_FILES)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_bare_download_fails(self):
        self.release_workflow(release=self.GUARDED_FILES)
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='downloads artifacts without "name:"')

    def test_an_unguarded_glob_fails(self):
        self.release_workflow(self.NAMED_DOWNLOAD)
        self.assert_fail(
            self.check(has_pyproject_toml=True),
            containing='without "fail_on_unmatched_files: true"')

    def test_fail_on_unmatched_files_must_be_true_not_merely_present(self):
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            '          files: dist/*\n'
            '          fail_on_unmatched_files: false\n')
        self.assert_fail(
            self.check(has_pyproject_toml=True),
            containing='without "fail_on_unmatched_files: true"')

    def test_merge_multiple_also_names_the_destination(self):
        """Downloading every artifact is fine when they are merged flat.

        A project which builds more than one distribution cannot name a
        single artifact, so the criterion is that the destination is
        determined -- not that it is spelled one particular way.
        """
        self.release_workflow(
            '        with:\n'
            '          merge-multiple: true\n'
            '          path: dist/\n',
            self.GUARDED_FILES)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_release_attaching_no_files_is_not_asked_about_globs(self):
        """No "files:" means no assets by choice, not a broken glob."""
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            '          generate_release_notes: true\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_commented_out_defect_does_not_count(self):
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            self.GUARDED_FILES
            + '        # uses: actions/download-artifact@v8 bare\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_an_inline_comment_does_not_hide_the_value(self):
        self.release_workflow(
            self.NAMED_DOWNLOAD,
            '          files: dist/*\n'
            '          fail_on_unmatched_files: true  # else empty\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_the_shipped_template_satisfies_the_criterion(self):
        """The template we tell projects to copy must itself pass.

        This is the regression that mattered: the template carried the
        bare download for as long as the criterion did not look at it.
        """
        self.compliant()
        with open(os.path.join(
                REPO_ROOT, 'templates', 'release-automation',
                'release.yml')) as f:
            self.fixture.workflow('release.yml', f.read())
        self.assert_pass(self.check(has_pyproject_toml=True))

    # The dispatch-guard half of the criterion. release.yml offers
    # workflow_dispatch as a build-and-check smoke test, which is only
    # true while the publishing jobs decline to run on the branch ref a
    # dispatch arrives on. Seven of nine repositories never received
    # the guards the template grew.

    GUARD = "    if: startsWith(github.ref, 'refs/tags/v')\n"

    def dispatch_workflow(self, sign_guard='', release_guard='',
                          dispatch=True, build_guard=''):
        """A release.yml with a build job and two publishing jobs."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  push:\n'
            "    tags: ['v*']\n"
            + ('  workflow_dispatch:\n' if dispatch else '')
            + 'jobs:\n'
            '  build:\n'
            + build_guard +
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n'
            '          name: dist\n'
            '  sign-tag:\n'
            + sign_guard +
            '    environment: release\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - name: Create signed tag\n'
            '        run: git push origin "${TAG_NAME}" --force\n'
            '  github-release:\n'
            + release_guard +
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: softprops/action-gh-release@v3\n'
            '        with:\n'
            '          files: dist/*\n'
            '          fail_on_unmatched_files: true\n')

    def test_unguarded_publishing_jobs_fail_when_dispatchable(self):
        self.dispatch_workflow()
        result = self.assert_fail(self.check(has_pyproject_toml=True),
                                  containing='not confined to tags')
        self.assertIn('sign-tag', result['details'])
        self.assertIn('github-release', result['details'])

    def test_guarded_publishing_jobs_pass(self):
        self.dispatch_workflow(sign_guard=self.GUARD,
                               release_guard=self.GUARD)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_one_unguarded_job_is_named_on_its_own(self):
        """The finding says which job, not merely that one exists."""
        self.dispatch_workflow(sign_guard=self.GUARD)
        result = self.assert_fail(self.check(has_pyproject_toml=True),
                                  containing='github-release')
        self.assertNotIn('sign-tag', result['details'])

    def test_without_a_dispatch_trigger_the_guards_are_not_required(self):
        """No manual trigger means no branch ref to defend against."""
        self.dispatch_workflow(dispatch=False)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_build_job_is_never_required_to_be_guarded(self):
        """The false positive that would break the smoke test.

        Building on a dispatch is the whole point of offering one, so a
        criterion which demanded the guard everywhere would remove the
        feature rather than make it safe. The build job here is
        unguarded in every other case above too; this states it.
        """
        self.dispatch_workflow(sign_guard=self.GUARD,
                               release_guard=self.GUARD)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_uploading_an_artifact_is_not_publishing(self):
        """A job whose only output is visible to later jobs is a build."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  workflow_dispatch:\n'
            'jobs:\n'
            '  build:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n'
            '          name: dist\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_ref_type_is_an_accepted_spelling_of_the_guard(self):
        """The criterion is the property, not one way of writing it."""
        guard = "    if: github.ref_type == 'tag'\n"
        self.dispatch_workflow(sign_guard=guard, release_guard=guard)
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_step_level_if_does_not_guard_the_job(self):
        """An `if:` on a step leaves the job itself free to run."""
        self.compliant()
        self.fixture.workflow(
            'release.yml',
            'name: Release\n'
            'on:\n'
            '  workflow_dispatch:\n'
            'jobs:\n'
            '  sign-tag:\n'
            '    environment: release\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n'
            '      - name: Create signed tag\n'
            "        if: startsWith(github.ref, 'refs/tags/v')\n"
            '        run: git tag -s\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='sign-tag')

    def test_the_shipped_template_is_guarded(self):
        """The template grew the guards; this pins them there."""
        self.compliant()
        with open(os.path.join(
                REPO_ROOT, 'templates', 'release-automation',
                'release.yml')) as f:
            self.fixture.workflow('release.yml', f.read())
        self.assert_pass(self.check(has_pyproject_toml=True))


class Flake8WrapTest(CheckTestCase):
    check_class = packaging.Flake8Wrap

    COMPLIANT = (
        '#!/bin/bash\n'
        '# shellcheck disable=SC2086\n'
        'flake8 ${filtered_files}\n'
    )

    def write(self, content):
        self.fixture.write('tools/flake8wrap.sh', content)

    def test_without_the_script_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No tools/flake8wrap.sh')

    def test_a_correct_script_passes(self):
        self.write(self.COMPLIANT)
        self.assert_pass(self.check(has_flake8wrap=True))

    def test_a_missing_disable_directive_fails(self):
        self.write('#!/bin/bash\nflake8 ${filtered_files}\n')
        self.assert_fail(self.check(has_flake8wrap=True),
                         containing='SC2086')

    def test_a_quoted_file_list_fails(self):
        """Quoting it passes one argument containing spaces."""
        self.write(
            '#!/bin/bash\n'
            '# shellcheck disable=SC2086\n'
            'flake8 "${filtered_files}"\n'
        )
        self.assert_fail(self.check(has_flake8wrap=True),
                         containing='incorrectly quoted')


class RustUnwrapLintTest(CheckTestCase):
    check_class = packaging.RustUnwrapLint

    WORKSPACE = (
        '[workspace]\n'
        'members = ["crates/thing"]\n'
        '\n'
        '[workspace.lints.clippy]\n'
        'unwrap_used = "warn"\n'
    )

    def test_without_cargo_toml_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No Cargo.toml')

    def test_a_configured_workspace_passes(self):
        self.fixture.init_git()
        self.fixture.write('Cargo.toml', self.WORKSPACE)
        self.fixture.write('clippy.toml', 'allow-unwrap-in-tests = true\n')
        self.fixture.write(
            'crates/thing/Cargo.toml',
            '[package]\nname = "thing"\n\n[lints]\nworkspace = true\n')
        self.fixture.commit()
        self.assert_pass(self.check(has_cargo_toml=True))

    def test_an_unset_lint_fails(self):
        self.fixture.init_git()
        self.fixture.write('Cargo.toml', '[workspace]\nmembers = []\n')
        self.fixture.write('clippy.toml', 'allow-unwrap-in-tests = true\n')
        self.fixture.commit()
        self.assert_fail(self.check(has_cargo_toml=True),
                         containing='unwrap_used')

    def test_a_missing_test_exemption_fails(self):
        """Without it the lint fires on every test assertion."""
        self.fixture.init_git()
        self.fixture.write('Cargo.toml', self.WORKSPACE)
        self.fixture.commit()
        self.assert_fail(self.check(has_cargo_toml=True),
                         containing='clippy.toml')

    def test_a_crate_that_neither_inherits_nor_defines_fails(self):
        self.fixture.init_git()
        self.fixture.write('Cargo.toml', self.WORKSPACE)
        self.fixture.write('clippy.toml', 'allow-unwrap-in-tests = true\n')
        self.fixture.write('crates/thing/Cargo.toml',
                           '[package]\nname = "thing"\n')
        self.fixture.commit()
        self.assert_fail(self.check(has_cargo_toml=True),
                         containing='crates/thing/Cargo.toml')


class PyprojectUsageTest(CheckTestCase):
    check_class = packaging.PyprojectUsage

    def test_a_docs_only_repository_does_not_apply(self):
        self.assert_skip(self.check(is_docs_only=True), containing='Docs-only')

    def test_a_rust_project_does_not_apply(self):
        self.assert_skip(self.check(has_cargo_toml=True), containing='Rust')

    def test_an_override_can_exclude_a_project(self):
        self.assert_skip(self.check(not_python=True), containing='overrides')

    def test_pyproject_alone_passes(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_legacy_packaging_alongside_pyproject_fails(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.write('setup.py', 'from setuptools import setup\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='setup.py')

    def test_setup_cfg_counts_as_legacy_too(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.write('setup.cfg', '[metadata]\n')
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='setup.cfg')

    def test_a_repository_with_no_python_does_not_apply(self):
        self.fixture.init_git()
        self.fixture.write('README.md', '# Thing\n')
        self.fixture.commit()
        self.assert_skip(self.check(), containing='No Python code')

    def test_python_without_pyproject_fails(self):
        self.fixture.init_git()
        self.fixture.write('thing.py', 'x = 1\n')
        self.fixture.commit()
        self.assert_fail(self.check())


class VersionFileGitignoreTest(CheckTestCase):
    check_class = packaging.VersionFileGitignore

    PYPROJECT = (
        '[project]\nname = "x"\n\n'
        '[tool.setuptools_scm]\n'
        'write_to = "thing/_version.py"\n'
    )

    def test_without_pyproject_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No pyproject.toml')

    def test_a_gitignored_untracked_version_file_passes(self):
        self.fixture.init_git()
        self.fixture.write('pyproject.toml', self.PYPROJECT)
        self.fixture.write('.gitignore', 'thing/_version.py\n')
        self.fixture.commit()
        self.assert_pass(self.check(has_pyproject_toml=True))

    def test_a_tracked_version_file_fails(self):
        """The failure this criterion exists for."""
        self.fixture.init_git()
        self.fixture.write('pyproject.toml', self.PYPROJECT)
        self.fixture.write('.gitignore', 'thing/_version.py\n')
        self.fixture.write('thing/_version.py', "__version__ = '1.0'\n")
        self.fixture.git('add', '-f', 'thing/_version.py')
        self.fixture.commit()
        self.assert_fail(self.check(has_pyproject_toml=True),
                         containing='tracked in git')

    def test_a_version_file_that_is_not_ignored_fails(self):
        self.fixture.init_git()
        self.fixture.write('pyproject.toml', self.PYPROJECT)
        self.fixture.write('.gitignore', 'build/\n')
        self.fixture.commit()
        self.assert_fail(self.check(has_pyproject_toml=True))

    def test_a_project_not_generating_a_version_file_does_not_apply(self):
        self.fixture.init_git()
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.fixture.commit()
        result = self.check(has_pyproject_toml=True)
        self.assertIn(result['status'], ('not_applicable', 'pass'),
                      result['details'])


class UnusedDeclaredDependencyTest(CheckTestCase):
    check_class = packaging.UnusedDeclaredDependency

    def pyproject(self, dependencies, extra=''):
        body = '[project]\nname = "x"\ndependencies = [\n'
        body += ''.join('    %s\n' % line for line in dependencies)
        body += ']\n' + extra
        self.fixture.write('pyproject.toml', body)

    def source(self, content, path='thing/main.py'):
        self.fixture.write(path, content)

    def test_incidental_python_does_not_apply(self):
        self.assert_skip(self.check(not_python=True),
                         containing='nothing to import')

    def test_without_pyproject_it_does_not_apply(self):
        self.assert_skip(self.check(has_pyproject_toml=False),
                         containing='No pyproject.toml')

    def test_no_declared_dependencies_does_not_apply(self):
        self.fixture.write('pyproject.toml', '[project]\nname = "x"\n')
        self.source('import os\n')
        self.assert_skip(self.check(), containing='nothing declared')

    def test_a_project_with_no_python_source_does_not_apply(self):
        self.pyproject(['"click==8.4.2",'])
        self.assert_skip(self.check(), containing='No Python source')

    def test_an_imported_dependency_passes(self):
        self.pyproject(['"click==8.4.2",'])
        self.source('import click\n')
        self.assert_pass(self.check())

    def test_an_unimported_dependency_fails_and_names_its_line(self):
        self.pyproject(['"click==8.4.2",', '"schedule==1.2.2",'])
        self.source('import click\n')
        result = self.assert_fail(self.check(), containing='schedule')
        self.assertIn('pyproject.toml:5', result['details'])
        self.assertNotIn('click', result['details'])

    def test_a_from_import_counts_as_a_use(self):
        self.pyproject(['"click==8.4.2",'])
        self.source('from click import option\n')
        self.assert_pass(self.check())

    def test_a_submodule_import_counts_as_a_use(self):
        self.pyproject(['"oslo.concurrency==7.6.1",'])
        self.source('from oslo_concurrency import processutils\n')
        self.assert_pass(self.check())

    def test_one_of_several_names_on_an_import_line_counts(self):
        self.pyproject(['"click==8.4.2",', '"semver==3.0.4",'])
        self.source('import os, click, semver\n')
        self.assert_pass(self.check())

    def test_a_relative_import_is_not_a_dependency(self):
        self.pyproject(['"click==8.4.2",'])
        self.source('from . import helpers\n')
        self.assert_fail(self.check(), containing='click')

    def test_a_commented_out_import_is_not_a_use(self):
        """The exact shape this criterion exists to find."""
        self.pyproject(['"click==8.4.2",'])
        self.source('import os\n# import click\n')
        self.assert_fail(self.check(), containing='click')

    def test_an_import_inside_a_docstring_is_not_a_use(self):
        self.pyproject(['"click==8.4.2",'])
        self.source('"""Usage:\n\nimport click\n"""\nimport os\n')
        self.assert_fail(self.check(), containing='click')

    def test_a_copy_under_build_is_not_a_use(self):
        """A stale build directory is what makes a deleted import look alive."""
        self.pyproject(['"click==8.4.2",'])
        self.source('import os\n')
        self.source('import click\n', path='build/lib/thing/main.py')
        self.assert_fail(self.check(), containing='click')

    def test_a_copy_inside_a_virtualenv_is_not_a_use(self):
        self.pyproject(['"click==8.4.2",'])
        self.source('import os\n')
        self.source('import click\n', path='.tox/py311/lib/click/__init__.py')
        self.assert_fail(self.check(), containing='click')

    def test_a_py_prefix_is_derived_away(self):
        self.pyproject(['"PyYAML==6.0.3",'])
        self.source('import yaml\n')
        self.assert_pass(self.check())

    def test_a_python_prefix_is_derived_away(self):
        self.pyproject(['"python-magic==0.4.27",'])
        self.source('import magic\n')
        self.assert_pass(self.check())

    def test_an_aliased_import_name_is_known(self):
        self.pyproject(['"protobuf==7.36.0",', '"grpcio==1.83.1",'])
        self.source('import grpc\nfrom google.protobuf import descriptor\n')
        self.assert_pass(self.check())

    def test_an_alias_matches_a_capitalised_module(self):
        """Alias values are lowercase; imports are lowercased to match."""
        self.pyproject(['"Pillow==12.1.0",'])
        self.source('from PIL import Image\n')
        self.assert_pass(self.check())

    def test_an_alias_adds_to_the_derived_names_rather_than_replacing(self):
        """setuptools derives its own name and also answers to one alias."""
        self.pyproject(['"setuptools==80.9.0",'])
        self.source('from pkg_resources import get_distribution\n')
        self.assert_pass(self.check())

    def test_the_generated_indirect_block_is_not_read(self):
        self.pyproject([
            '"click==8.4.2",',
            '# START_OF_INDIRECT_DEPS',
            '"wrapt==2.3.0",',
            '# END_OF_INDIRECT_DEPS',
        ])
        self.source('import click\n')
        self.assert_pass(self.check())

    def test_optional_dependencies_are_not_read(self):
        """Test tooling is run, not imported, and would all be flagged."""
        self.pyproject(
            ['"click==8.4.2",'],
            extra=('\n[project.optional-dependencies]\n'
                   'test = [\n    "tox==4.60.1",\n]\n'))
        self.source('import click\n')
        self.assert_pass(self.check())

    def test_a_not_imported_marker_with_a_reason_exempts_it(self):
        self.pyproject([
            '# not-imported: uv -- invoked as a subprocess by the fetcher',
            '"uv>=0.8.0",',
            '"click==8.4.2",',
        ])
        self.source('import click\n')
        result = self.assert_pass(self.check())
        self.assertIn('annotated', result['details'])

    def test_a_trailing_not_imported_marker_exempts_it(self):
        self.pyproject(['"uv>=0.8.0",  # not-imported: uv -- run as a tool'])
        self.source('import os\n')
        self.assert_pass(self.check())

    def test_a_marker_with_no_reason_does_not_exempt_it(self):
        """An unexplained exception is a silenced finding."""
        self.pyproject(['# not-imported: uv', '"uv>=0.8.0",'])
        self.source('import os\n')
        self.assert_fail(self.check(), containing='uv')

    def test_a_marker_naming_another_dependency_does_not_exempt_it(self):
        self.pyproject([
            '# not-imported: uv -- run as a tool',
            '"schedule==1.2.2",',
        ])
        self.source('import os\n')
        self.assert_fail(self.check(), containing='schedule')

    def test_the_finding_names_the_declared_spelling(self):
        """Canonical names send the reader to grep for a missing string."""
        self.pyproject(['"oslo.concurrency==7.6.1",'])
        self.source('import os\n')
        self.assert_fail(self.check(), containing='oslo.concurrency')

    def test_a_nested_package_does_not_keep_a_dependency_alive(self):
        """A sub-package declaring its own deps does not vouch for ours."""
        self.pyproject(['"click==8.4.2",', '"wrapt==2.3.0",'])
        self.source('import click\n')
        self.fixture.write('plugin/pyproject.toml',
                           '[project]\nname = "p"\ndependencies = ["wrapt"]\n')
        self.source('import wrapt\n', path='plugin/p/main.py')
        self.assert_fail(self.check(), containing='wrapt')

    def test_a_marker_matches_across_spellings(self):
        self.pyproject([
            '# not-imported: oslo_concurrency -- kept for a version floor',
            '"oslo.concurrency==7.6.1",',
        ])
        self.source('import os\n')
        self.assert_pass(self.check())

    def test_an_import_in_a_tool_script_counts(self):
        """tools/ is source too, as it is for the sibling criterion."""
        self.pyproject(['"click==8.4.2",'])
        self.source('import click\n', path='tools/build-collection.py')
        self.assert_pass(self.check())

    def test_a_single_line_array_still_gets_a_verdict(self):
        """The names come from the TOML parse, not the line scan."""
        self.fixture.write(
            'pyproject.toml',
            '[project]\nname = "x"\n'
            'dependencies = ["click==8.4.2", "schedule==1.2.2"]\n')
        self.source('import click\n')
        result = self.assert_fail(self.check(), containing='schedule')
        self.assertNotIn('pyproject.toml:', result['details'])

    def test_a_malformed_pyproject_does_not_apply(self):
        """A mid-edit manifest must not lose the whole repository's run."""
        self.fixture.write(
            'pyproject.toml', '[project]\nname = "x"\ndependencies = [\n')
        self.source('import click\n')
        self.assert_skip(self.check(), containing='unreadable')


class UndeclaredDirectDependencyTest(CheckTestCase):
    check_class = packaging.UndeclaredDirectDependency

    def pyproject(self, direct=(), generated=()):
        body = '[project]\nname = "x"\ndependencies = [\n'
        body += ''.join('    %s\n' % line for line in direct)
        if generated:
            body += '    # START_OF_INDIRECT_DEPS\n'
            body += ''.join('    %s\n' % line for line in generated)
            body += '    # END_OF_INDIRECT_DEPS\n'
        body += ']\n'
        self.fixture.write('pyproject.toml', body)

    def source(self, content, path='thing/main.py'):
        self.fixture.write(path, content)

    def test_incidental_python_does_not_apply(self):
        self.assert_skip(self.check(not_python=True),
                         containing='nothing to import')

    def test_without_pyproject_it_does_not_apply(self):
        self.assert_skip(self.check(has_pyproject_toml=False),
                         containing='No pyproject.toml')

    def test_without_a_generated_block_it_does_not_apply(self):
        self.pyproject(direct=['"click==8.4.2",'])
        self.source('import click\n')
        self.assert_skip(self.check(), containing='No generated indirect')

    def test_a_project_with_no_python_source_does_not_apply(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.assert_skip(self.check(), containing='No Python source')

    def test_an_unimported_transitive_pin_passes(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\n')
        self.assert_pass(self.check())

    def test_importing_a_transitive_pin_fails_and_names_its_line(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\nimport wrapt\n')
        result = self.assert_fail(self.check(), containing='wrapt')
        self.assertIn('pyproject.toml:6', result['details'])

    def test_the_finding_names_the_declared_spelling(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"PyJWT==2.13.0",'])
        self.source('import click\nfrom jwt.exceptions import DecodeError\n')
        self.assert_fail(self.check(), containing='PyJWT')

    def test_a_module_a_direct_dependency_could_provide_is_not_flagged(self):
        """protobuf and googleapis-common-protos share the google namespace."""
        self.pyproject(direct=['"protobuf==7.36.0",'],
                       generated=['"googleapis-common-protos==1.75.2",'])
        self.source('from google.protobuf import descriptor\n')
        self.assert_pass(self.check())

    def test_a_commented_out_import_does_not_fail(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\n# import wrapt\n')
        self.assert_pass(self.check())

    def test_a_copy_under_build_does_not_fail(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\n')
        self.source('import wrapt\n', path='build/lib/thing/main.py')
        self.assert_pass(self.check())

    def test_an_import_in_a_tool_script_counts(self):
        """tools/ is source too: it breaks the same way when the pin goes."""
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\n')
        self.source('import wrapt\n', path='tools/build-collection.py')
        self.assert_fail(self.check(), containing='wrapt')

    def test_a_nested_package_is_not_read(self):
        """A subdirectory with its own manifest is a separate package."""
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\n')
        self.fixture.write('plugin/pyproject.toml',
                           '[project]\nname = "p"\ndependencies = ["wrapt"]\n')
        self.source('import wrapt\n', path='plugin/p/main.py')
        self.assert_pass(self.check())

    def test_the_root_package_is_still_read(self):
        """Only subdirectories are tested, so the root is never pruned."""
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",'])
        self.source('import click\nimport wrapt\n')
        self.assert_fail(self.check(), containing='wrapt')

    def test_every_offender_is_reported_not_just_the_first(self):
        self.pyproject(direct=['"click==8.4.2",'],
                       generated=['"wrapt==2.3.0",', '"six==1.17.0",'])
        self.source('import click\nimport wrapt\nimport six\n')
        result = self.assert_fail(self.check())
        self.assertIn('wrapt', result['details'])
        self.assertIn('six', result['details'])

    def test_a_malformed_pyproject_does_not_apply(self):
        """A mid-edit manifest must not lose the whole repository's run."""
        self.fixture.write(
            'pyproject.toml', '[project]\nname = "x"\ndependencies = [\n')
        self.source('import click\n')
        self.assert_skip(self.check(), containing='No generated indirect')


class RenovateLockstepGroupsTest(CheckTestCase):
    check_class = packaging.RenovateLockstepGroups

    OSLO = ['"oslo.concurrency==7.6.1",', '"oslo.config==10.7.0",']

    def pyproject(self, dependencies, extra=''):
        body = '[project]\nname = "x"\ndependencies = [\n'
        body += ''.join('    %s\n' % line for line in dependencies)
        body += ']\n' + extra
        self.fixture.write('pyproject.toml', body)

    def renovate(self, *rules):
        self.fixture.write(
            'renovate.json',
            json.dumps({'packageRules': list(rules)}, indent=2) + '\n')

    def test_without_pyproject_it_does_not_apply(self):
        self.renovate()
        self.assert_skip(self.check(has_pyproject_toml=False),
                         containing='No pyproject.toml')

    def test_without_renovate_json_it_does_not_apply(self):
        self.pyproject(self.OSLO)
        self.assert_skip(self.check(), containing='No readable renovate.json')

    def test_malformed_renovate_json_does_not_apply(self):
        self.pyproject(self.OSLO)
        self.fixture.write('renovate.json', '{not json\n')
        self.assert_skip(self.check(), containing='No readable renovate.json')

    def test_a_single_family_member_does_not_apply(self):
        self.pyproject(['"oslo.concurrency==7.6.1",'])
        self.renovate()
        self.assert_skip(self.check(), containing='more than one member')

    def test_no_family_members_does_not_apply(self):
        self.pyproject(['"click==8.4.2",'])
        self.renovate()
        self.assert_skip(self.check(), containing='more than one member')

    def test_two_ungrouped_members_fail_and_are_named(self):
        self.pyproject(self.OSLO)
        self.renovate()
        result = self.assert_fail(self.check(), containing='oslo')
        self.assertIn('oslo.concurrency', result['details'])
        self.assertIn('oslo.config', result['details'])

    def test_a_regex_group_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/']})
        self.assert_pass(self.check())

    def test_a_glob_group_in_the_declared_spelling_passes(self):
        """Renovate matches the manifest spelling, so "oslo.*" is correct."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['oslo.*']})
        self.assert_pass(self.check())

    def test_an_exact_name_group_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['oslo.concurrency',
                                             'oslo.config']})
        self.assert_pass(self.check())

    def test_the_deprecated_pattern_field_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackagePatterns': ['^oslo']})
        self.assert_pass(self.check())

    def test_a_group_missing_one_member_fails(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['oslo.concurrency']})
        self.assert_fail(self.check(), containing='oslo')

    def test_an_excluded_member_fails(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/', '!oslo.config']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_group_restricted_by_update_type_fails(self):
        """Grouping minor and patch leaves the majors arriving one by one."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchUpdateTypes': ['minor', 'patch'],
                       'matchPackageNames': ['/^oslo/']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_rule_without_a_group_name_is_not_a_group(self):
        self.pyproject(self.OSLO)
        self.renovate({'matchPackageNames': ['/^oslo/'],
                       'automerge': False})
        self.assert_fail(self.check(), containing='oslo')

    def test_members_in_an_optional_group_are_counted(self):
        self.pyproject(
            ['"click==8.4.2",'],
            extra=('\n[project.optional-dependencies]\n'
                   'test = [\n    "oslo.concurrency==7.6.1",\n'
                   '    "oslo.config==10.7.0",\n]\n'))
        self.renovate()
        self.assert_fail(self.check(), containing='oslo')

    def test_oslotest_is_not_an_oslo_library(self):
        """The family is "oslo.*", not everything starting with oslo."""
        self.pyproject(['"oslo.concurrency==7.6.1",', '"oslotest==5.0.0",'])
        self.renovate()
        self.assert_skip(self.check(), containing='more than one member')

    def test_an_exclusion_listed_first_still_excludes(self):
        """Renovate ignores order, so scoring by order is a false pass."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['!oslo.config', '/^oslo/']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_dep_name_group_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchDepNames': ['oslo.concurrency', 'oslo.config']})
        self.assert_pass(self.check())

    def test_the_deprecated_dep_pattern_field_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchDepPatterns': ['^oslo']})
        self.assert_pass(self.check())

    def test_a_case_insensitive_regex_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^OSLO/i']})
        self.assert_pass(self.check())

    def test_a_case_sensitive_regex_that_does_not_match_fails(self):
        """The flag is read, rather than every regex being folded."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^OSLO/']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_group_with_no_selector_fails(self):
        """Renovate rejects the rule, so the family is grouped nowhere."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'everything'})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_deprecated_exclusion_of_a_member_fails(self):
        """Migrated, this reads "!oslo.config" -- it splits the family."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'excludePackageNames': ['oslo.config']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_deprecated_exclusion_pattern_of_the_family_fails(self):
        """A rule excluding the whole family covers none of it."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'excludePackagePatterns': ['^oslo']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_deprecated_exclusion_of_a_non_member_passes(self):
        """Excluding something else leaves the family covered."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'excludePackageNames': ['click']})
        self.assert_pass(self.check())

    def test_a_list_of_only_exclusions_covers_everything_else(self):
        """Renovate constrains nothing when no entry is positive."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['!click']})
        self.assert_pass(self.check())

    def test_a_deprecated_prefix_matcher_passes(self):
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackagePrefixes': ['oslo']})
        self.assert_pass(self.check())

    def test_a_package_matcher_survives_a_matcher_we_do_not_read(self):
        """Only a rule with no package matcher at all covers nothing."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/'],
                       'matchManagers': ['pep621']})
        self.assert_pass(self.check())

    def test_both_matchers_must_accept_a_member(self):
        """The two are separate conditions, so the rule covers one."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/'],
                       'matchDepNames': ['oslo.config']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_group_narrowed_by_a_matcher_we_do_not_read_fails(self):
        """Whether it reaches these packages is not knowable here."""
        self.pyproject(self.OSLO)
        self.renovate({'groupName': 'npm things',
                       'matchManagers': ['npm']})
        self.assert_fail(self.check(), containing='oslo')

    def test_a_malformed_pyproject_does_not_crash_the_run(self):
        """A mid-edit manifest must not lose the whole repository's run."""
        self.fixture.write(
            'pyproject.toml', '[project]\nname = "x"\ndependencies = [\n')
        self.renovate({'groupName': 'oslo',
                       'matchPackageNames': ['/^oslo/']})
        self.assert_skip(self.check(), containing='more than one member')


if __name__ == '__main__':
    unittest.main()
