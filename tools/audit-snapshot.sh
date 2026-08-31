#!/bin/bash
#
# Capture or compare consistency audit snapshots.
#
# The audit publishes its details strings: they render into
# docs/audits/compliance.md and into the bodies of issues filed
# fleet-wide. So the test that a change to scripts/audit-check.py is
# safe is not that the unit tests pass -- it is that the checker says
# exactly what it said before about the same repositories. Capture a
# baseline before the change, capture another after, and diff them.
#
# Snapshots are deliberately not committed. Generated JSON under
# scripts/ or docs/ would land in review scope (review-scope.toml
# includes *.json) and would sit permanently stale in the review queue.
# A repeatable command and a scratch directory give the same guarantee.
#
# Usage:
#   tools/audit-snapshot.sh <clones-dir> <out-dir>
#   tools/audit-snapshot.sh --diff <old-dir> <new-dir>
#
# <clones-dir> holds one checkout per repository, named for it, as
# ~/src/shakenfist does. Git worktrees (a '-wt-' in the name) are
# skipped: they are another branch of a repository already in the set,
# so auditing them would compare a repository against itself.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="${HERE}/../scripts/audit-check.py"
HELPER="${HERE}/../scripts/audit_snapshot.py"

usage() {
    echo "usage: $0 <clones-dir> <out-dir>" >&2
    echo "       $0 --diff <old-dir> <new-dir>" >&2
    exit 2
}

if [ "$#" -ne 3 ] && [ "$#" -ne 2 ]; then
    usage
fi

if [ "${1}" = "--diff" ]; then
    [ "$#" -eq 3 ] || usage
    exec python3 "${HELPER}" diff "${2}" "${3}"
fi

[ "$#" -eq 2 ] || usage

CLONES="${1}"
OUT="${2}"

[ -d "${CLONES}" ] || { echo "not a directory: ${CLONES}" >&2; exit 1; }
mkdir -p "${OUT}"

captured=0
for clone in "${CLONES}"/*/; do
    repo="$(basename "${clone}")"

    case "${repo}" in
        *-wt-*)
            echo "skipping worktree ${repo}"
            continue
            ;;
    esac

    if [ ! -d "${clone}/.git" ]; then
        echo "skipping ${repo}: not a git checkout"
        continue
    fi

    echo "auditing ${repo}"
    result="${OUT}/audit-result-${repo}.json"
    python3 "${CHECKER}" \
        --repo-path "${clone%/}" \
        --repo-name "${repo}" > "${result}"
    python3 "${HELPER}" normalise "${result}"
    captured=$((captured + 1))
done

echo "captured ${captured} repositories into ${OUT}"
