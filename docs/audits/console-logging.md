# Audit: Console script logging setup

## What we check

Every file named by `[project.scripts]` in `pyproject.toml` that calls
`shakenfist_utilities.logs.setup_console()` must also:

* call `logging.basicConfig()`; and
* stop its own logger propagating into the root handler.

```python
LOG = logs.setup_console(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).propagate = False
```

`setup_console()` raises the root logger's level to INFO but attaches
its handler to the *named* logger only. Records from every other
module therefore propagate up to a root logger with no handler on it
and are dropped -- so without `basicConfig()` the entry point sees its
own INFO messages and nothing else. Once root does have a handler, the
entry point's own records reach both it and the handler
`setup_console()` installed, which is what `propagate = False`
prevents.

Only declared entry points are examined. occystrap calls
`logs.setup_console(__name__)` at the top of all 24 of its modules,
and only `occystrap/main.py` is an entry point; a call anywhere else
is a module getting a logger, not a console script setting up logging.
A repository that declares no console scripts, or whose entry points
do not use the helper, is not applicable -- this is a rule about how
the helper is used, not a requirement to use it.

A file that genuinely should not configure logging -- because
something else in the process already has -- carries an
`audit-ok: console-logging` comment, ideally with a reason. The marker
is read per file rather than per line: the finding is about the file's
logging setup as a whole, so there is no single line for it to sit on.

### Verbose handling

Part of the standard, and reviewed rather than measured. When a
`--verbose` or `--debug` flag is handled, the root handler level has
to move too, or raising `LOG`'s level alone changes nothing:

```python
if verbose:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        handler.setLevel(logging.DEBUG)
    LOG.setLevel(logging.DEBUG)
```

This is not checked because there is no reliable signal for "this
entry point has a verbosity flag" that does not also match parsers
that pass the value straight through. occystrap's `main.py` is the
worked example of the shape.

## Template

No template -- this is a code-level pattern.

## Projects

<!-- consistency-audit:begin -->
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
