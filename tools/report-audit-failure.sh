#!/bin/bash -e

# File an issue saying the scheduled consistency audit failed, or
# comment on the one already open, so that an outage which runs for
# several weeks is one issue rather than one per run. Run by the
# report-failure job in consistency-audit.yml, which supplies GH_TOKEN
# and RUN_URL (the runner supplies GITHUB_REPOSITORY); it files issues,
# so it is not for running by hand.

: "${GH_TOKEN:?GH_TOKEN must hold a token that can write issues}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must name the repository to file on, as owner/name}"
: "${RUN_URL:?RUN_URL must link the failed run}"

existing=$(gh issue list --repo "${GITHUB_REPOSITORY}" \
    --label audit-failure --state open --limit 1 --json number \
    --jq '.[0].number // empty')

# printf rather than a multi-line quoted string: the latter carries the
# indentation of wherever it is written into every continuation line,
# and four leading spaces make GitHub-flavoured Markdown render the
# whole body as a code block.
body=$(printf '%s\n' \
    "The scheduled consistency audit failed." \
    "" \
    "Run: ${RUN_URL}" \
    "" \
    "While this is failing no compliance page is being" \
    "regenerated and no consistency issues are being filed or" \
    "closed, across every audited repository. The tables still" \
    "show the previous run's verdicts, so the audit looks" \
    "healthy from the outside -- check the run before trusting" \
    "anything in docs/audits/." \
    "" \
    "The audit runs weekly and nothing retries it, so a failure" \
    "lasts until the next Sunday unless someone acts. Once the" \
    "cause is fixed, start a run by hand with" \
    "\`gh workflow run consistency-audit.yml --repo ${GITHUB_REPOSITORY}\`" \
    "and close this issue once that run passes.")

if [ -n "${existing}" ]; then
    echo "Commenting on existing audit failure issue #${existing}."
    gh issue comment "${existing}" \
        --repo "${GITHUB_REPOSITORY}" --body "${body}"
else
    echo "Filing a new audit failure issue."
    gh issue create --repo "${GITHUB_REPOSITORY}" \
        --title "Scheduled consistency audit is failing" \
        --label audit-failure \
        --body "${body}"
fi
