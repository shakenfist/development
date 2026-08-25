<!-- shared-block: path-traversal-review v1 -->
Path construction from outside data (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/path-traversal-review.md`):

- Treat as a candidate any filesystem path built from a value the
  process did not choose: a request parameter, an image name, tag or
  digest, a layer path, an archive member name, a filename out of a
  configuration file or a database row.
- The question is not whether the value looks dangerous but whether
  the resulting path is *proved* to stay inside its intended base
  directory. Resolve the joined path with `os.path.realpath()` and
  verify it still starts with the base; a check on the untrusted
  component alone is defeated by symlinks and by encodings the
  check did not anticipate.
- Prefer a helper that cannot be forgotten at a call site --
  `safe_path_join()` in occystrap, or the framework's own
  (`send_from_directory` in Flask) -- over an inline guard repeated
  at each join.
- Archive extraction is the case most often missed: a member name
  inside a tarball or zip is attacker-controlled in exactly the same
  way as a request parameter.
- Where a bare join is correct because every component is
  process-chosen, say so in a comment rather than leaving the
  reader to re-derive it.
<!-- shared-block-end -->
