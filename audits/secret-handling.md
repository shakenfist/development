# Audit: Credential handling and leak detection

## What we check

Two related things: that credentials do not get written into
places with weaker access control than the credential itself, and
that a scanner is watching for the times they do anyway.

The automated part of this audit checks only the scanner. The
code-level patterns below are review criteria -- a grep for them
is either trivially evaded or drowns in false positives, so a
passing check means "a scanner is running", not "this project has
no credentials in its logs".

### A secret scanner runs in CI

Every project with CI must run a repository secret scanner on
pull requests and on pushes to the default branch. `gitleaks` is
the reference implementation; `trufflehog` and `detect-secrets`
are accepted equivalents.

The reference invocation is in ryll's
`.github/workflows/supply-chain.yml`:

```yaml
  gitleaks:
    name: gitleaks
    runs-on: [self-hosted, vm, debian-13, s]
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - name: Install gitleaks
        run: sudo apt-get update && sudo apt-get install -y gitleaks

      - name: Run gitleaks
        run: gitleaks detect --source . --redact --verbose --no-banner
```

Two things in there are not obvious and cost time to rediscover:

* `gitleaks-action@v2` refuses to run on organization repositories
  without a paid licence, so the upstream binary is invoked
  directly instead.
* `gitleaks` is only packaged from Debian 13 (trixie) onward --
  bookworm has no package -- so the job needs a `debian-13`
  runner.

`fetch-depth: 0` matters: a secret committed and then reverted is
still in the history, and still needs rotating.

This is distinct from the GitHub-hosted secret scanning covered by
[github-security.md](github-security.md). That one detects known
third-party credential formats and needs GitHub Advanced Security
for custom patterns; this one runs locally, costs nothing, and can
be taught a project's own credential format.

### Credentials do not go into logs or events

Anything a project writes to a log line, an audit event, an
exception message, or a metrics label is readable by a wider
audience than the credential is, and usually leaves the machine
entirely -- Shaken Fist events, for instance, go to syslog *and*
to Loki, so a credential in an event is a credential in log
aggregation.

Concretely, none of these belong in a log or event payload:

* Bearer tokens and session cookies, including ones the process
  just minted and ones it received on the request it is serving.
* Passwords and API key secrets, in any form the recipient could
  replay. A stored hash counts: it is offline-attackable.
* Revocation handles such as a token nonce. Publishing one tells
  a reader which captured tokens are still live.
* Raw HTTP request and response bodies on routes that carry
  credentials. This is the one most often missed, because the
  logging is usually generic request tracing added for debugging
  long before the credential-bearing route existed.

Log the *identifier* instead -- the key name, the token's `jti`,
the account -- which is what makes an audit trail useful without
making it a credential store.

Redact by route rather than by field name where a framework logs
bodies generically. Field-name redaction has to know which route
it is on anyway (a field called `key` is a metadata key name on
most endpoints and a secret on a few) and starts leaking silently
the day somebody adds a route it has not heard of.

### Secret-carrying types refuse to stringify

Where a language offers a wrapper type that renders as asterisks
instead of its contents, secret fields should use it. This turns
"remember not to log this" into a property of the type:

* Python: `pydantic.SecretStr`. `str()` and `repr()` yield
  `'**********'`; the real value comes back only from an explicit
  `.get_secret_value()`.
* Rust: the `secrecy` crate's `Secret<T>`, or a manual `Debug`
  implementation that prints a placeholder. Deriving `Debug` on a
  struct with a secret field is the Rust version of this bug.

The unwrap calls then cluster at the few places that genuinely
need the plaintext -- a hash comparison, a signature, an outbound
header -- and each one is a place a reviewer can stop and ask
whether the value belongs there.

### Credentials the project mints are recognisable

Where a project generates a credential rather than accepting one
chosen by a user, the generated form should carry a short
identifying prefix and a checksum, following the pattern GitHub
(`ghp_`), GitLab (`glpat-`), Stripe (`sk_live_`) and Slack
(`xoxb-`) use. The prefix makes the credential greppable in logs
and repositories; the checksum lets a scanner reject lookalikes
without an API call, which is what makes scanning at volume
tolerable rather than alert spam.

This costs nothing cryptographically. A bearer token is a random
identifier, not ciphertext, so a fixed prefix is a label sitting
beside a random value rather than a revealed piece of one -- the
entropy of the random part is unchanged.

It applies only to credentials the project generates. A secret
the user chose cannot carry the project's prefix, and requiring
one would be a breaking API change for no benefit.

## Template

No template -- the scanner job is a workflow snippet (see the
reference invocation above, and ryll's
`.github/workflows/supply-chain.yml` for it in context), and the
rest are code-level patterns.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-30T08:29:44.045321+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#113 |
| client-python | non-compliant | shakenfist/client-python#354 |
| clingwrap | non-compliant | shakenfist/clingwrap#111 |
| cloudgood | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#57 |
| instar | non-compliant | shakenfist/instar#464 |
| kerbside | non-compliant | shakenfist/kerbside#185 |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#1504 |
| library-utilities | non-compliant | shakenfist/library-utilities#41 |
| occystrap | non-compliant | shakenfist/occystrap#101 |
| ryll | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3546 |

Details for non-compliant projects:

- **agent-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **client-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **clingwrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **divergulent** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **instar** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **kerbside** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **kerbside-patches** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **library-utilities** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **occystrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **shakenfist** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
<!-- consistency-audit:end -->
