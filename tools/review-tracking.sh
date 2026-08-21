#!/bin/bash

# Run the code review tracking helper by hand. Subcommands:
#
#   stamp   record blob SHAs for newly reviewed files, regen REVIEWS.md
#   prune   drop review marks for files changed since review, regen
#   regen   regenerate REVIEWS.md from current state
#   next    pick a random unreviewed in-scope file and open it
#   status  report effective review coverage against HEAD (read-only)
#
# Typical session: "prune" after a pull, "stamp" before committing
# review marks. On main itself the prune-reviews workflow runs prune
# automatically after every push (via tools/ci-prune-reviews.sh).
#
# Adopting repositories carry a wrapper that goes looking for a clone
# of this repository, because the implementation lives here. This one
# does not have to look: scripts/review-tracking.py is in the tree
# beside it. See docs/code-review-tracking.md.

set -e

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"
exec ./scripts/review-tracking.py "$@"
