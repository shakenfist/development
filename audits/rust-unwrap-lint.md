# Audit: Rust unwrap lint

## What we check

Rust projects must enable clippy's `unwrap_used` lint so that
`.unwrap()` calls in production code are flagged, while test code is
exempted. `unwrap()` converts a recoverable error into a panic, and a
panic on data from outside the process (network input, configuration,
files, other systems) is an outage waiting to happen -- this is the
failure mode behind the November 2025 Cloudflare outage, where an
`unwrap()` on a feature file that another system had generated too
large panicked their core proxy fleet-wide.

Concretely, we require:

1. The root `Cargo.toml` sets `unwrap_used` to `warn` or `deny` in
   `[workspace.lints.clippy]` (or `[lints.clippy]` for single-crate
   repositories):

   ```toml
   [workspace.lints.clippy]
   unwrap_used = "warn"
   ```

2. A `clippy.toml` at the repository root exempts test code:

   ```toml
   allow-unwrap-in-tests = true
   ```

3. Every first-party crate manifest either inherits the workspace
   lints or defines the lint itself:

   ```toml
   [lints]
   workspace = true
   ```

   Fuzz harness crates (any `Cargo.toml` under a `fuzz` directory)
   are exempt -- their whole purpose is to crash noisily on bad
   input.

`warn` in `Cargo.toml` combined with `-D warnings` in the CI clippy
run is the preferred arrangement: local iterative builds are not
blocked mid-refactor, but nothing lands with a new production
`unwrap()`. Setting `deny` directly is also accepted.

Note what this audit deliberately does **not** require:

* We do not lint `expect_used`. Replacing a provably-infallible
  `unwrap()` with `expect("why this cannot fail")` is the sanctioned
  fix -- it documents the invariant and produces a self-explanatory
  panic message.
* Idiomatic poisoning panics such as `mutex.lock().unwrap()` may be
  kept via a scoped `#[allow(clippy::unwrap_used)]` (ideally with a
  comment), converted to `.expect("lock poisoned")`, or wrapped in a
  small helper. Escalating a panic rather than running on
  possibly-corrupt shared state is usually correct.
* Zero panics. Slice indexing, `assert!` and arithmetic overflow can
  still panic; this audit targets the most common way untrusted input
  becomes a panic, not the entire panic surface.

## Template

No template -- this is a code-level pattern. See the configuration
snippets above.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-15T08:15:40.094463+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | N/A | - |
| client-python | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| divergulent | N/A | - |
| instar | non-compliant | shakenfist/instar#424 |
| kerbside | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| ryll | compliant | - |
| shakenfist | N/A | - |

Details for non-compliant projects:

- **instar** (Status): clippy unwrap_used lint not set to warn or deny in src/Cargo.toml; src/clippy.toml missing allow-unwrap-in-tests = true; crates/guest-protocol/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/helloworld/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/helloworld/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/helloworld2/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/helloworld2/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/info/core/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/info/operations/copy/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/info/operations/info/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/info/shared/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/info/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/pluggable/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/pluggable/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/pluggable2/core/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/pluggable2/operations/copy/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/pluggable2/shared/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/pluggable2/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block2/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block2/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block3/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block3/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block4/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block4/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block5/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block5/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block6/guest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; prototypes/virtio-block6/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/core/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/amend/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/bench/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/bitmap/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/check/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/commit/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/create/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/dd/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/luks/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/measure/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/qcow2-write-exec/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/qcow2-write/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/qcow2/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/raw/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/rebase/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/resize/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/snapshot/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/vhd/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/vhdx/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/crates/vmdk/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/amend/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/bench/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/bitmap/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/check/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/commit/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/compare/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/convert/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/copy/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/create/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/info/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/map/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/measure/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/rebase/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/resize/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/operations/snapshot/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/shared/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; src/vmm/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself
<!-- consistency-audit:end -->
