<!-- shared-block: functional-test-coverage v1 -->
Functional test coverage (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/functional-test-coverage.md`):

- The standard is "do we run the code to do the real thing, and
  does it work as intended". Every subcommand exposed on the command
  line, and every endpoint exposed by an API, should have a test
  that exercises it for real rather than against a mock of itself.
- For a change that adds or alters user-visible behaviour, the
  question to answer is which functional test would have failed
  before it and passes after. If there is none, that is the finding,
  and it is a finding about this change rather than a note for
  later.
- Unit tests are held to no coverage percentage, but a branch that
  is reachable from outside the process and has no test is worth
  naming. Error paths and argument validation are where this bites:
  they are the code most often written once and never run again.
- Mocking the system under test proves nothing. Mock the boundary --
  the network, the clock, the hypervisor -- and let the code being
  tested actually run.
- Where a gap is real but out of scope for the change in hand, say
  so plainly and record it, rather than silently widening the
  change or silently leaving it unsaid.
<!-- shared-block-end -->
