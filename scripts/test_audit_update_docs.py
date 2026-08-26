#!/usr/bin/env python3

"""Tests for audit-update-docs.py.

Run with: python3 scripts/test_audit_update_docs.py
"""

import importlib.util
import io
import os
import re
import sys
import unittest


SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'audit-update-docs.py'
)

# audit-update-docs.py is not importable by name (the hyphens are not
# valid in a module identifier), so load it from its path. It imports
# audit_common, which lives beside it.
sys.path.insert(0, os.path.dirname(SCRIPT))
_spec = importlib.util.spec_from_file_location('audit_update_docs', SCRIPT)
audit_update_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_update_docs)

# The heading render_page gives the list of criteria with no
# automated check. It is not a spec anchor, so the section-count
# assertions have to allow for it.
NO_CHECK_HEADING = 'Criteria with no automated check'


class ColumnNamesTest(unittest.TestCase):
    """COLUMN_NAMES has to cover every multi-check spec.

    This is the invariant that broke the 2026-08-12 audit run: the
    review-marks-pre-commit check joined the workflow-standards spec
    without a column heading, and rendering raised KeyError after
    every docs/audits/*.md had been rewritten but before any was
    committed, so no project's table published that day.
    """

    def _multi_check_specs(self):
        return {
            spec: ids
            for spec, ids in audit_update_docs.checks_by_spec().items()
            if len(ids) > 1
        }

    def test_multi_check_specs_have_a_heading_for_every_check(self):
        missing = []
        for spec, check_ids in sorted(self._multi_check_specs().items()):
            for check_id in check_ids:
                if check_id not in audit_update_docs.COLUMN_NAMES:
                    missing.append(f'{check_id} (in {spec})')
        self.assertEqual(
            missing, [],
            'checks sharing a spec file need a COLUMN_NAMES heading in '
            'audit-update-docs.py',
        )

    def test_the_workflow_standards_spec_is_still_multi_check(self):
        # The test above passes trivially if nothing shares a spec, so
        # assert the case it is meant to cover still exists.
        specs = self._multi_check_specs()
        self.assertIn('docs/audits/workflow-standards.md', specs)
        self.assertIn(
            'review-marks-pre-commit', specs['docs/audits/workflow-standards.md']
        )

    def test_no_heading_for_an_unknown_check(self):
        # A heading left behind after a check is renamed or removed is
        # dead weight that reads as coverage.
        known = set(audit_update_docs.AUDIT_METADATA)
        self.assertEqual(
            sorted(set(audit_update_docs.COLUMN_NAMES) - known), []
        )

    def test_single_check_specs_do_not_need_a_heading(self):
        # Specs with one check render a plain 'Status' column, so they
        # are deliberately absent from COLUMN_NAMES.
        singles = [
            ids[0] for ids in audit_update_docs.checks_by_spec().values()
            if len(ids) == 1
        ]
        self.assertTrue(singles)
        for check_id in singles:
            self.assertNotIn(check_id, audit_update_docs.COLUMN_NAMES)


class AuditMetadataPathsTest(unittest.TestCase):
    """Every spec and template path in AUDIT_METADATA must resolve.

    These paths are not dereferenced by anything that would fail: the
    audit run reads a spec file to regenerate its compliance table, but
    a path that does not exist just means no table, and
    audit-manage-issues.py pastes the path into the issue it files. So
    a typo surfaces as a dead link in somebody else's repository, days
    later. All 38 entries were rewritten by hand when the tree moved
    under docs/, which is exactly when one of them is wrong.
    """

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_every_spec_path_exists(self):
        metadata = audit_update_docs.AUDIT_METADATA
        self.assertTrue(metadata)
        for check_id, meta in sorted(metadata.items()):
            spec = meta['spec']
            self.assertTrue(
                os.path.isfile(os.path.join(self.root, spec)),
                'the %s check names spec %s, which does not exist'
                % (check_id, spec),
            )

    def test_every_template_path_exists(self):
        for check_id, meta in sorted(audit_update_docs.AUDIT_METADATA.items()):
            template = meta.get('template')
            if template is None:
                continue
            # None means the criterion has no template. An empty string
            # is a typo, and would otherwise resolve to the repository
            # root and pass -- the skip has to be exactly None.
            self.assertTrue(
                template,
                'the %s check has an empty template path; use None if '
                'it has no template' % check_id,
            )
            # Templates are recorded as directories, with the trailing
            # slash that the issue text prints.
            self.assertTrue(
                os.path.isdir(os.path.join(self.root, template)),
                'the %s check names template %s, which is not a '
                'directory here' % (check_id, template),
            )


class ColumnNameFallbackTest(unittest.TestCase):
    def test_known_check_uses_its_heading(self):
        self.assertEqual(
            audit_update_docs.column_name('workflow-permissions'),
            'Permissions',
        )

    def test_unknown_check_falls_back_and_warns(self):
        # Rendering must not take the whole fleet's tables down for a
        # missing label, but it must say so.
        stderr = io.StringIO()
        original = sys.stderr
        sys.stderr = stderr
        try:
            name = audit_update_docs.column_name('not-a-real-check')
        finally:
            sys.stderr = original
        self.assertEqual(name, 'not-a-real-check')
        self.assertIn('no COLUMN_NAMES heading', stderr.getvalue())


class DocumentedTestReferencesTest(unittest.TestCase):
    """Prose naming a test file must name one that exists, correctly.

    The guarantee is about attribution rather than existence: a
    paragraph that names a test file is checked, and any test function
    named in that paragraph must be defined in one of the files the
    paragraph names. A paragraph naming a function and no file is not
    checked, because there is nothing to check it against.

    That pointer is the whole value of the sentence carrying it:
    someone who opens the named file, does not find the named test,
    and concludes it was never written is worse off than if the
    sentence had said nothing. It has already happened once -- the
    COLUMN_NAMES invariant was attributed to test_audit_check.py,
    which does not hold it -- and a moved or renamed test is silent
    about every prose reference to it.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Prose that explains how this repository works. Everything in
    # docs/ except docs/plans/, which is a record of what was decided
    # at the time and is allowed to name things that have since moved.
    #
    # docs/audits/ is deliberately out, apart from the index. It is
    # docs/audits/compliance.md that makes it necessary: the generated
    # findings there quote file paths harvested from the audited
    # repositories, and already name a test_amend.py that belongs to
    # another project entirely, so scanning it would demand that other
    # people's test files exist under our scripts/. The criterion
    # specs beside it no longer carry generated content and could be
    # scanned now; that is a widening of this test rather than part of
    # the split that made it possible, so it has not been done.
    @property
    def doc_files(self):
        names = ['AGENTS.md', 'ARCHITECTURE.md', 'docs/audits/README.md']
        names.extend(
            sorted(
                os.path.join('docs', f)
                for f in os.listdir(os.path.join(self.REPO, 'docs'))
                if f.endswith('.md')
            )
        )
        return names

    TEST_FILE_RE = re.compile(r'\b(scripts/)?(test_[a-z0-9_]+\.py)\b')
    TEST_FUNC_RE = re.compile(r'\b(test_[a-z0-9_]+)\b(?!\.py)')

    def _docs(self):
        for name in self.doc_files:
            path = os.path.join(self.REPO, name)
            with open(path) as f:
                yield name, f.read()

    def _sources(self):
        """Every test file under scripts/, by basename."""
        sources = {}
        scripts = os.path.join(self.REPO, 'scripts')
        for filename in os.listdir(scripts):
            if filename.startswith('test_') and filename.endswith('.py'):
                with open(os.path.join(scripts, filename)) as f:
                    sources[filename] = f.read()
        self.assertTrue(sources)
        return sources

    def test_the_documentation_set_is_not_empty(self):
        # doc_files is computed, so an empty docs/ or a renamed file
        # would make every test in this class pass by vacuum.
        names = self.doc_files
        self.assertIn('docs/consistency-audits.md', names)
        for name in names:
            self.assertTrue(
                os.path.exists(os.path.join(self.REPO, name)),
                f'{name} is in the documentation set but missing',
            )

    def test_named_test_files_exist(self):
        missing = []
        for name, body in self._docs():
            for _, filename in self.TEST_FILE_RE.findall(body):
                if not os.path.exists(
                    os.path.join(self.REPO, 'scripts', filename)
                ):
                    missing.append(f'{name} names {filename}')
        self.assertEqual(missing, [])

    def test_named_tests_exist_in_the_file_they_are_attributed_to(self):
        # The failure this is really for: the test exists, but not
        # where the prose says it does, so following the pointer finds
        # nothing.
        #
        # Only functions that are defined *somewhere* under scripts/
        # are candidates. A test_-prefixed token that is defined
        # nowhere is prose, not a broken pointer -- `test_coverage` is
        # an audit criterion this very page discusses, and demanding it
        # be a function would set a trap on a phrase the next editor of
        # that page is likely to write.
        sources = self._sources()
        defined = {
            func
            for body in sources.values()
            for func in re.findall(r'def (test_[a-z0-9_]+)\(', body)
        }
        self.assertIn('test_named_test_files_exist', defined)

        wrong = []
        for name, body in self._docs():
            for paragraph in re.split(r'\n\s*\n', body):
                named_files = {
                    filename
                    for _, filename in self.TEST_FILE_RE.findall(paragraph)
                }
                if not named_files:
                    continue
                for func in sorted(set(self.TEST_FUNC_RE.findall(paragraph))):
                    if func not in defined:
                        continue
                    if not any(
                        re.search(rf'def {func}\(', sources.get(f, ''))
                        for f in named_files
                    ):
                        files = ', '.join(sorted(named_files))
                        wrong.append(f'{name} attributes {func} to {files}')
        self.assertEqual(wrong, [])


class AuditIndexIsCompleteTest(unittest.TestCase):
    """Every criterion spec must be reachable from the index.

    docs/audits/README.md is the index, and a criterion is described
    as spanning four files with the index among them. A spec missing
    from the table is a criterion nobody browsing the directory finds
    -- which is the whole reason the specifications were moved under
    docs/ and published. dependency-name-normalization.md was absent
    from the index from the day it was written and survived a
    wholesale rewrite of the page, because nothing compared the two.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    INDEX = 'docs/audits/README.md'

    def test_every_spec_file_is_named_in_the_index(self):
        audits = os.path.join(self.REPO, 'docs', 'audits')
        specs = sorted(
            f for f in os.listdir(audits)
            if f.endswith('.md') and f != 'README.md'
        )
        self.assertTrue(specs)
        with open(os.path.join(self.REPO, self.INDEX)) as f:
            index = f.read()
        # Matched as a link target rather than as a bare filename, so
        # that a spec merely mentioned in the prose does not count as
        # indexed.
        missing = [s for s in specs if '(%s)' % s not in index]
        self.assertEqual(
            [], missing,
            'these criterion specs are not linked from %s: %s'
            % (self.INDEX, ', '.join(missing)),
        )


class UnmeasuredCriteriaTest(unittest.TestCase):
    """The criteria with no check are named in prose, so pin the list.

    docs/consistency-audits.md names the current set, and this holds
    the prose to what the runner actually measures rather than letting
    it rot the first time one of them is automated.

    The identifying property used to be marker-block absence: a
    criterion with no check had no generated table in its spec file.
    Moving the tables onto one page took that away, so the property is
    now membership in AUDIT_METADATA, which is what the runner has
    always keyed on and what the compliance page's own no-check
    section is generated from. Three statements of the set are now
    tied together: the metadata, the page, and the prose here.

    Same shape as AuditScopeIsStatedOnceTest in test_audit_check.py,
    which ties the audit matrix to the two places that describe it.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DOC = 'docs/consistency-audits.md'

    def _unmeasured(self):
        return set(audit_update_docs.unmeasured_specs())

    def _documented(self):
        with open(os.path.join(self.REPO, self.DOC)) as f:
            body = f.read()
        # Whitespace is collapsed before matching: the anchor phrase
        # is prose and wraps across lines at whatever column the
        # paragraph happens to reflow to.
        paragraphs = [
            ' '.join(p.split())
            for p in re.split(r'\n\s*\n', body)
        ]
        paragraphs = [
            p for p in paragraphs if 'at the time of writing' in p
        ]
        self.assertEqual(
            len(paragraphs), 1,
            f'{self.DOC} should name the unmeasured criteria in exactly '
            f'one paragraph, anchored by "at the time of writing"',
        )
        return {
            token for token in re.findall(r'`([a-z0-9-]+)`', paragraphs[0])
            if os.path.exists(
                os.path.join(self.REPO, 'docs', 'audits', f'{token}.md')
            )
        }

    def test_the_documented_list_is_the_real_one(self):
        self.assertEqual(self._documented(), self._unmeasured())

    def test_there_is_something_to_measure(self):
        # The comparison above passes trivially if both sides are
        # empty, which is also what a renamed marker string would do.
        # The right response to this failing is usually deletion
        # rather than repair, and that is not guessable from
        # "False is not true".
        self.assertTrue(
            self._unmeasured(),
            f'every criterion spec in docs/audits/ has a check in '
            f'AUDIT_METADATA. Either every criterion is measured now '
            f'-- in which case delete this class and the "at the time '
            f'of writing" paragraph in {self.DOC} -- or '
            f'unmeasured_specs() is no longer finding the spec files.',
        )


class GeneratedNoteMatchesTheGeneratorTest(unittest.TestCase):
    """The note above each compliance table is generated, not written.

    It sits inside the marker block, which AGENTS.md says is never
    hand-maintained, so the copy in each spec page is only ever as
    current as the last run. Nothing asserted the two agreed: the
    next change to the generator's wording would have rewritten
    thirty files in one daily-workflow commit with no warning, which
    is the cross-file drift this suite closes everywhere else.
    """

    SENTINEL = 'TIMESTAMP-SENTINEL'

    def _render(self, timestamp):
        """Render the whole generated block with one synthetic repo."""
        return audit_update_docs.render_page(
            [{
                'repo': 'repo', 'org': 'shakenfist',
                'timestamp': timestamp,
                'checks': [
                    {'id': c, 'status': 'pass', 'details': ''}
                    for c in sorted(audit_update_docs.AUDIT_METADATA)
                ],
            }],
            True,
        )

    def _note_pattern(self):
        """The generator's note, as a regex over the timestamp."""
        note = self._render(self.SENTINEL).splitlines()[1]
        self.assertIn(self.SENTINEL, note)
        return re.compile('^' + '.*'.join(
            re.escape(part) for part in note.split(self.SENTINEL)) + '$')

    def _page(self):
        root = os.path.dirname(os.path.dirname(SCRIPT))
        with open(
            os.path.join(root, audit_update_docs.COMPLIANCE_PAGE)
        ) as f:
            return f.read().splitlines()

    def test_the_page_opens_with_the_current_note(self):
        lines = self._page()
        begin = lines.index(audit_update_docs.BEGIN_MARKER)
        self.assertRegex(lines[begin + 1], self._note_pattern())

    def test_the_page_has_a_section_for_every_spec(self):
        """The note test above passes on a page rendering nothing.

        It used to be guarded by requiring more than twenty spec files
        checked. Consolidating the blocks onto one page took that count
        away, so the guard is the section list instead: a generator
        that silently stopped emitting tables would still write a
        well-formed note.
        """
        expected = {
            audit_update_docs.spec_anchor(spec)
            for spec in audit_update_docs.checks_by_spec()
        }
        self.assertGreater(len(expected), 20)
        rendered = {
            line[len('## '):].strip()
            for line in self._render('when').splitlines()
            if line.startswith('## ')
        }
        self.assertEqual(expected, rendered - {NO_CHECK_HEADING})


class SpecsLinkTheirComplianceSectionTest(unittest.TestCase):
    """Each spec links its section, and carries no generated block.

    The link is the whole of the connection between a criterion and
    its compliance table now that the two are in different files, and
    it is hand-written -- so nothing but a test keeps it pointing at a
    section that exists. The marker assertion is the other half: a
    spec that regrows a generated block becomes unreviewable again,
    which is the regression the split exists to prevent, and an agent
    working an audit issue is exactly who would reintroduce one.
    """

    root = os.path.dirname(os.path.dirname(SCRIPT))
    PAGE = audit_update_docs.COMPLIANCE_PAGE

    def _specs(self):
        audits = os.path.join(self.root, audit_update_docs.AUDITS_DIR)
        specs = sorted(
            f for f in os.listdir(audits)
            if f.endswith('.md')
            and f not in audit_update_docs.NOT_A_SPEC
        )
        self.assertGreater(len(specs), 20)
        return specs

    def _body(self, name):
        path = os.path.join(
            self.root, audit_update_docs.AUDITS_DIR, name)
        with open(path) as f:
            return f.read()

    def _page_anchors(self):
        with open(os.path.join(self.root, self.PAGE)) as f:
            return {
                line[len('## '):].strip()
                for line in f.read().splitlines()
                if line.startswith('## ')
            }

    def test_no_spec_carries_a_generated_block(self):
        offenders = [
            name for name in self._specs()
            if audit_update_docs.BEGIN_MARKER in self._body(name)
        ]
        self.assertEqual(
            [], offenders,
            'these specs carry a generated compliance block, which '
            'makes them unreviewable; the tables belong on %s'
            % self.PAGE,
        )

    def test_measured_specs_link_a_section_that_exists(self):
        anchors = self._page_anchors()
        measured = {
            audit_update_docs.spec_anchor(spec)
            for spec in audit_update_docs.checks_by_spec()
        }
        page = os.path.basename(self.PAGE)
        broken = []
        for name in self._specs():
            anchor = name[:-len('.md')]
            if anchor not in measured:
                continue
            link = '(%s#%s)' % (page, anchor)
            if link not in self._body(name):
                broken.append('%s does not link %s' % (name, link))
            elif anchor not in anchors:
                broken.append('%s links a missing section' % name)
        self.assertEqual([], broken)

    def test_unmeasured_specs_link_no_section(self):
        # A compliance link on a criterion nobody measures would point
        # at a table that is never going to appear.
        page = os.path.basename(self.PAGE)
        unmeasured = audit_update_docs.unmeasured_specs()
        self.assertTrue(unmeasured)
        for anchor in unmeasured:
            body = self._body('%s.md' % anchor)
            self.assertNotIn('(%s#%s)' % (page, anchor), body)
            # It should still say where it stands, and the page names
            # it, so the reference to the page itself is expected.
            self.assertIn(page, body)

    def test_the_page_names_every_unmeasured_spec(self):
        with open(os.path.join(self.root, self.PAGE)) as f:
            body = f.read()
        heading = body.find('## %s' % NO_CHECK_HEADING)
        self.assertNotEqual(-1, heading)
        listed = body[heading:]
        for anchor in audit_update_docs.unmeasured_specs():
            self.assertIn('(%s.md)' % anchor, listed)


if __name__ == '__main__':
    unittest.main()
