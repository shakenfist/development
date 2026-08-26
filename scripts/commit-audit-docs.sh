#!/bin/bash -e

# Commit the regenerated audit compliance page back to main. Run by
# the update-docs job in the consistency-audit workflow after
# audit-update-docs.py has rewritten docs/audits/compliance.md.
#
# The path is deliberately the one generated file rather than the
# whole of docs/audits/. Everything else in that directory is
# hand-written prose under human review, and a wider `git add` here
# would commit anything else present in the checkout to main under the
# bot's name with this commit message.

PAGE=docs/audits/compliance.md

if git diff --quiet -- "${PAGE}"; then
    echo "No changes to the audit compliance page."
    exit 0
fi

git config user.name 'shakenfist-bot'
git config user.email 'bot@shakenfist.com'

git add "${PAGE}"
git commit -m 'Regenerate the audit compliance page.

Automated commit by the consistency-audit workflow.'

# Another push may have landed while the audit ran; rebase our doc
# commit on top rather than failing the workflow.
git pull --rebase origin main
git push origin main
