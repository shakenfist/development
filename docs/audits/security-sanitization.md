# Audit: HTTP header and file path sanitization

## What we check

### HTTP response header sanitization

Projects using `http.server.BaseHTTPRequestHandler` directly must
override `send_header()` to strip `\r` and `\n` characters from
header values. This prevents HTTP response splitting (CWE-113).

The canonical implementation is `SafeHeaderMixin` in
`occystrap/util.py`.

Projects using Flask (kerbside, shakenfist, agent-python) are
already protected by Werkzeug's `Headers` class.

### File path sanitization

Projects that construct file paths from user-controlled data must
validate that the resulting path stays within the intended base
directory. This prevents path traversal attacks (CWE-22).

The canonical implementation is `safe_path_join()` in
`occystrap/util.py`, which uses `os.path.realpath()` and prefix
checking.

## Template

No template -- these are code-level patterns. Reference
implementations are in `occystrap/util.py`.

## Projects

| Project | HTTP headers | File paths | Issue |
|---------|-------------|------------|-------|
| agent-python | N/A (Flask) | N/A | - |
| client-python | N/A | N/A | - |
| clingwrap | N/A | N/A | - |
| cloudgood | N/A | N/A | - |
| imago | N/A (Rust) | N/A (Rust) | - |
| kerbside | N/A (Flask) | N/A (Flask) | - |
| kerbside-patches | N/A | N/A | - |
| library-utilities | N/A | N/A | - |
| occystrap | compliant | compliant | - |
| ryll | N/A (Rust) | N/A (Rust) | - |
| shakenfist | N/A (Flask) | N/A (Flask) | - |

N/A: Project does not use raw `BaseHTTPRequestHandler` or
construct file paths from user input, or uses a framework that
provides built-in protection.
