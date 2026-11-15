<!-- shared-block: source-file-size v2 -->
Source file size (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/source-file-size.md`):

- Where a repository tracks whole-file human review, a file's cost
  is its length times how often it is touched: every change
  discards the review of the whole file, and the next session
  re-reads all of it. That, rather than taste, is why length is
  worth raising in review at all.
- Treat a source file over roughly 800 lines as a candidate to
  split, and one over roughly 1,500 as wanting a stated reason to
  stay whole. These hold whether or not a repository tracks review
  per file: tracking is what makes the cost repeat and become
  measurable, not what makes a long file expensive to read.
- There is no cap, and no file is ever forced to split: a file
  already over 1,500 lines may stay exactly that long. What it may
  not do is grow, and no file may cross 1,500, without a reviewed
  edit to the repository's baseline of oversize files -- the
  ratchet in shakenfist/development's
  `templates/source-size-ratchet/` checks that on every commit.
  Raising an entry is allowed: it is the stated reason, asked for
  where a reviewer cannot scroll past it. Where a repository has
  not adopted the ratchet, the push auditor applies the same rule
  by hand.
- The push auditor reads the files the diff touches, not only the
  diff. A branch that grows a file already over 800 lines is a
  finding to raise even when its own addition is small, because
  per-diff review is how slow growth gets past: every step looks
  reasonable on its own. Under 1,500 the finding opens a question
  rather than recording a defect. A raised baseline entry is
  reviewed as the decision it is, and its reason belongs in the
  pull request description.
- Generated files, vendored trees and protocol or data tables are
  exempt: they are not read the way source is, and a tool that
  counts them -- the ratchet included -- is measuring the wrong
  thing, so they belong in its exclusion list, not its baseline.
- Split along a seam that already exists -- one module's public
  entry point, one check, one subcommand, one endpoint -- so that
  a later change touches one of the pieces rather than all of
  them. A file split at a line number rather than at a seam is
  worse than the long file it replaced.
- Length is never reduced by deleting the comments and docstrings
  that explain why the code is the way it is. Those are what make
  a long file reviewable, and trading them for a line count makes
  the review worse while making the number better. A diff that
  shrinks a baselined file by deleting rationale comments or
  docstrings is a finding, not a win: the ratchet counts lines and
  cannot tell the difference, so the reviewer has to. Cut
  duplicated scaffolding first; see `comment-proportion` for what
  earns its length.
<!-- shared-block-end -->
