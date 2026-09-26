#!/bin/bash

# Run the code review tracking helper by hand. Subcommands:
#
#   stamp   record blob SHAs for newly reviewed files, regen REVIEWS.md
#   prune   drop review marks for files changed since review, regen
#   import  mark files byte-identical to ones fully reviewed in
#           shakenfist/development as reviewed, pointing at the signed
#           review there, regen (a no-op here; see below)
#   regen   regenerate REVIEWS.md from current state
#   next    pick a random unreviewed in-scope file and open it
#   status  report effective review coverage against HEAD (read-only)
#   scope-orphans
#           list tracked files that are in neither include nor
#           exclude, so nobody has decided about them (read-only)
#
# Typical session: "prune" after a pull, "stamp" before committing
# review marks. On main itself the prune-reviews workflow runs prune
# then import automatically after every push and once a day (via
# tools/ci-prune-reviews.sh); import against this repository is a
# no-op, since it is the source reviews are imported from.
#
# Adopting repositories carry a wrapper that goes looking for a clone
# of this repository, because the implementation lives here. This one
# does not have to look: scripts/review-tracking.py is in the tree
# beside it. See docs/code-review-tracking.md.

set -e

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"
exec ./scripts/review-tracking.py "$@"
