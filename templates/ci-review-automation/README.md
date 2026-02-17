# CI Review Automation Templates

These templates set up Claude Code-powered PR review automation and
bot-triggered workflows for Shaken Fist projects. All files can be
copied directly with no modifications.

For automatic test fixing (suited to projects with large test
suites), see the separate
[`templates/test-drift-fix/`](../test-drift-fix/) templates.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `pr-re-review.yml` | `.github/workflows/pr-re-review.yml` | Manual re-review trigger |
| `pr-retest.yml` | `.github/workflows/pr-retest.yml` | Manual functional test re-run |
| `pr-address-comments.yml` | `.github/workflows/pr-address-comments.yml` | Address review comments |

## Additional CI changes

Beyond adding these workflow files, you also need to modify the
project's main CI workflow (e.g. `functional-tests.yml`) to add:

1. **Top-level `permissions` block** -- restrict default token scope
2. **`check-bot-commit` job** -- prevent infinite loops from bot commits
3. **`automated_reviewer` job** -- run Claude Code review after tests pass

See the `automated-reviewer-job-snippet.md` section below for the
exact YAML to add.

### Automated reviewer job snippet

Add this to your main CI workflow after the test jobs:

```yaml
permissions:
  contents: read
  pull-requests: write

jobs:
  check-bot-commit:
    name: "Check bot commit"
    if: github.event_name == 'pull_request'
    runs-on: [self-hosted, static]
    outputs:
      is_bot: ${{ steps.check.outputs.is_bot }}
    steps:
      - name: Checkout code
        uses: actions/checkout@v6
        with:
          fetch-depth: 1

      - name: Check if last commit was from bot
        id: check
        run: |
          LAST_AUTHOR_EMAIL=$(git log -1 --format='%ae')
          echo "Last commit author: $LAST_AUTHOR_EMAIL"
          if [ "$LAST_AUTHOR_EMAIL" = "bot@shakenfist.com" ]; then
            echo "is_bot=true" >> $GITHUB_OUTPUT
          else
            echo "is_bot=false" >> $GITHUB_OUTPUT
          fi

  automated_reviewer:
    name: "Automated reviewer"
    permissions:
      contents: read
      pull-requests: write
    runs-on: [self-hosted, claude-code]
    needs: [your-test-job, check-bot-commit]
    if: |
      github.event_name == 'pull_request' &&
      needs.check-bot-commit.outputs.is_bot != 'true'
    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}-reviewer
      cancel-in-progress: true

    steps:
      - name: Checkout code
        uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - name: Run automated reviewer
        uses: shakenfist/actions/review-pr-with-claude@main
        with:
          pr-number: ${{ github.event.pull_request.number }}
```

Replace `your-test-job` with the name of your test job(s).

## Prerequisites

These workflows require:

- Self-hosted runners with `claude-code` and `static` labels
- Claude Code CLI installed and authenticated on `claude-code` runners
- `gh` CLI available on all runners
- The shared actions from
  [shakenfist/actions](https://github.com/shakenfist/actions):
  - `pr-bot-trigger` -- handles bot command parsing and authorisation
  - `review-pr-with-claude` -- runs automated code reviews

## Bot commands

Once deployed, repository collaborators with write access can
comment on PRs with:

| Command | Description |
|---------|-------------|
| `@shakenfist-bot please retest` | Re-run functional tests |
| `@shakenfist-bot please re-review` | Request a fresh automated review |
| `@shakenfist-bot please address comments` | Address review comments |

For the `@shakenfist-bot please attempt to fix` command, see the
separate [`templates/test-drift-fix/`](../test-drift-fix/) templates.

## Projects using these templates

| Project | Status |
|---------|--------|
| [imago](https://github.com/shakenfist/imago) | Live (original) |
| [occystrap](https://github.com/shakenfist/occystrap) | Live |
| [agent-python](https://github.com/shakenfist/agent-python) | Added |
