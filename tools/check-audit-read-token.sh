#!/bin/bash -e

# Check that the consistency audit's read token is set and can see the
# repository settings the audit reads. Run by each leg of the audit job
# in consistency-audit.yml, which supplies GH_TOKEN from
# AUDIT_READ_TOKEN (the runner supplies GITHUB_REPOSITORY). It only
# reads, so it is safe to run by hand with a token to test.
#
# An unset secret expands to an empty string, and gh given an empty
# GH_TOKEN falls back to whatever login the runner's user holds, so
# test for it rather than let that decide what is measured.
if [ -z "${GH_TOKEN}" ]; then
    echo '::error::AUDIT_READ_TOKEN is not set'
    exit 1
fi
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must name a repository, as owner/name}"

# GitHub leaves security_and_analysis out of the REST answer for a
# caller without admin access. Without it github-security would stop
# checking secret scanning without saying so. Under -e a refused call
# would end the script with gh's own error and not this one, and gh's
# own reason (an expired token, an organisation's lifetime limit) is
# what the reader needs, so it is printed above this line.
if ! security=$(gh api "repos/${GITHUB_REPOSITORY}" \
        --jq '.security_and_analysis != null'); then
    echo '::error::AUDIT_READ_TOKEN was refused by the API; the reason is on the line above'
    exit 1
fi

# The REST answer leaves out delete_branch_on_merge, and every other
# merge setting, for a fine-grained token with read-only
# Administration, whatever that token can see. GraphQL answers it, so
# delete-branch-on-merge reads it there, and so does this.
# The query is the one DeleteBranchOnMerge sends, on one line.
query='query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { deleteBranchOnMerge } }'
if ! delete_branch=$(gh api graphql -f query="${query}" \
        -f owner="${GITHUB_REPOSITORY%%/*}" -f name="${GITHUB_REPOSITORY#*/}" \
        --jq '.data.repository.deleteBranchOnMerge != null'); then
    echo '::error::AUDIT_READ_TOKEN was refused by the GraphQL API; the reason is on the line above'
    exit 1
fi

if [ "${security}" != true ] || [ "${delete_branch}" != true ]; then
    echo "::error::AUDIT_READ_TOKEN cannot see repository settings" \
        "(security_and_analysis: ${security}, deleteBranchOnMerge: ${delete_branch})." \
        'It needs read-only Administration on repositories owned by the organisation.'
    exit 1
fi
