#!/bin/bash -e

# Commit regenerated audit compliance tables back to main. Run by the
# update-docs job in the consistency-audit workflow after
# audit-update-docs.py has rewritten the tables in audits/*.md.

if git diff --quiet -- audits/; then
    echo "No changes to the audit compliance tables."
    exit 0
fi

git config user.name 'shakenfist-bot'
git config user.email 'bot@shakenfist.com'

git add audits/
git commit -m 'Regenerate audit compliance tables.

Automated commit by the consistency-audit workflow.'

# Another push may have landed while the audit ran; rebase our doc
# commit on top rather than failing the workflow.
git pull --rebase origin main
git push origin main
