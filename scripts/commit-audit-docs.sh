#!/bin/bash -e

# Commit the regenerated audit compliance page back to main. Run by
# the push-docs job in the consistency-audit workflow, after
# update-docs has rewritten docs/audits/compliance.md with
# audit-update-docs.py and uploaded it as an artifact.
#
#     scripts/commit-audit-docs.sh PAGE
#
# PAGE is the regenerated page, as downloaded from that artifact. Run
# this from the top of a clone of this repository checked out at the
# commit the audit ran on, which is the commit update-docs generated
# the page from. The page's prose outside its markers is carried over
# from that commit, so the commit made here changes only the tables,
# and the rebase below brings it up to date with anything that has
# landed since. PUSH_TOKEN must hold a token that can push to main.
# The script pushes, so it is not for running by hand.
#
# The path is deliberately the one generated file rather than the
# whole of docs/audits/. Everything else in that directory is
# hand-written prose under human review, and a wider `git add` here
# would commit anything else present in the checkout to main under the
# bot's name with this commit message.

TARGET='docs/audits/compliance.md'
BRANCH='main'

# PUSH_TOKEN is the review-marks App's installation token, the identity
# main's ruleset admits as its bypass actor, and nothing else will do.
# There is no fallback to GITHUB_TOKEN, unlike prune-reviews.yml: GitHub
# does not accept the built-in Actions app as a bypass actor, so once
# the ruleset requires pull requests a push with GITHUB_TOKEN is
# refused. A fallback would land the page until then and fail with a
# message about rules from then on, rather than failing now with the
# real reason. prune-reviews.yml falls back on purpose, because it is a
# template copied into repositories with no such ruleset; this script
# runs only here.
#
# The token is then taken out of the environment, so that nothing this
# script runs inherits it. Only push_page hands it on, to the one git
# push. unset changes only what children inherit: this script's own
# /proc/<pid>/environ is fixed at exec and still holds PUSH_TOKEN.
take_push_token() {
    if [ -z "${PUSH_TOKEN:-}" ]; then
        echo 'PUSH_TOKEN is empty, so the compliance page cannot be pushed. It must hold' >&2
        echo "a review-marks App installation token that can push to ${BRANCH}; GITHUB_TOKEN" >&2
        echo 'is not a substitute. Check that the review-marks environment holds' >&2
        echo 'REVIEW_MARKS_APP_KEY and that the token step in push-docs succeeded.' >&2
        exit 1
    fi
    push_token="${PUSH_TOKEN}"
    unset PUSH_TOKEN
}

# Push HEAD to main, the one git command given the token. The clone
# holds no credentials, so the token reaches git here as an
# http.extraheader for github.com, through GIT_CONFIG_COUNT and friends
# on this command alone: never in argv, where any process on the runner
# could read it, and never in .git/config, where it would outlive the
# push. printf is a builtin, so the token is not in base64's argv
# either. This is push_review_state from tools/ci-prune-reviews.sh,
# which says what exposure through /proc remains and what bounds it.
push_page() {
    local header

    header="AUTHORIZATION: basic $(printf 'x-access-token:%s' "${push_token}" | base64 -w 0)"
    GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0='http.https://github.com/.extraheader' GIT_CONFIG_VALUE_0="${header}" \
        git push origin "HEAD:${BRANCH}"
}

run() {
    local page

    if [ "$#" -ne 1 ]; then
        echo "Usage: $0 PAGE" >&2
        exit 1
    fi
    page="$1"

    # Before anything else runs, so that a missing token fails every
    # run and not only the first one with something to push.
    take_push_token

    # A page that is missing or empty means the artifact did not arrive
    # as expected, and committing it would blank the published page.
    if [ ! -s "${page}" ]; then
        echo "The regenerated page ${page} is missing or empty." >&2
        exit 1
    fi
    cp -- "${page}" "${TARGET}"

    if git diff --quiet -- "${TARGET}"; then
        echo "No changes to the audit compliance page."
        exit 0
    fi

    git config user.name 'shakenfist-bot'
    git config user.email 'bot@shakenfist.com'

    git add "${TARGET}"
    git commit -m 'Regenerate the audit compliance page.

Automated commit by the consistency-audit workflow.'

    # Another push may have landed while the audit ran; rebase our doc
    # commit on top rather than failing the workflow.
    git pull --rebase origin "${BRANCH}"
    push_page
}

# All of the work is in functions, called from the last line, so that
# bash has read the whole file before any of it runs: the rebase above
# rewrites the clone this script runs from, this file included.
run "$@"
