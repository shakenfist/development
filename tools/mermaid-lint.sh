#!/bin/bash
#
# Render every mermaid diagram in the repository's markdown and fail on
# any that does not parse.
#
# Copied from shakenfist/development at templates/mermaid-lint/. Keep
# it in step with that copy rather than editing it in place.
#
# Mermaid fails at render time, not at commit time, so a diagram with a
# syntax error is a silently broken documentation page: GitHub shows an
# error box and mkdocs shows nothing. Nothing else in CI reads a
# diagram, which is why this exists as its own lane.
#
# Rendering is what mermaid-cli does, and rendering needs a browser, so
# this runs in the upstream container rather than installing a
# chromium and a node toolchain onto a runner. There is no lighter
# path worth taking: mermaid's own parse() under plain node throws
# "DOMPurify.addHook is not a function" for flowchart and
# stateDiagram-v2 -- the two most common types here -- so a DOM-free
# checker reports false failures on exactly the diagrams that matter.
#
# Usage:
#   tools/mermaid-lint.sh            # every tracked markdown file
#   tools/mermaid-lint.sh a.md b.md  # just these

set -euo pipefail

# Pinned deliberately, by digest as well as by tag: a tag is mutable
# and this runs a third-party container on a runner with a docker
# daemon. The tag is for a human reading this; the digest is what
# actually pins. Renovate's stock managers do not read a docker
# reference out of a shell script, so this moves when somebody moves
# it; check for a newer tag when a mermaid feature is missing, and
# take the new digest from the pull rather than trusting the tag
# alone.
IMAGE_TAG="ghcr.io/mermaid-js/mermaid-cli/mermaid-cli:11.4.2"
IMAGE="${IMAGE_TAG}@sha256:99c983b3ab4e14033f2880bc1b9de17e5090b4515dabd63fe9cf8c0ae6130956"

repo_root=$(git rev-parse --show-toplevel)

# Named files are resolved against the caller's directory and turned
# into repository-relative paths, because everything below -- the cd,
# the /src mount, the paths printed in the output -- is relative to
# the repository root. A name that does not resolve is an error
# rather than an empty run: the documented per-file usage is exactly
# the invocation a typo reaches, and a linter that exits zero on a
# path it never read is the failure this script exists to prevent.
candidates=()
if [ "$#" -gt 0 ]; then
    for arg in "$@"; do
        if [ ! -f "${arg}" ]; then
            echo "mermaid-lint: no such file: ${arg}" >&2
            exit 1
        fi
        rel=$(realpath --relative-to="${repo_root}" "${arg}")
        if [ "${rel#../}" != "${rel}" ]; then
            echo "mermaid-lint: outside the repository: ${arg}" >&2
            exit 1
        fi
        candidates+=("${rel}")
    done
    cd "${repo_root}"
else
    # git ls-files rather than find, so vendored and ignored trees are
    # excluded for free. A Rust project's .cargo-cache alone holds
    # thousands of markdown files nobody here wrote, several of which
    # contain diagrams that are not ours to fix.
    cd "${repo_root}"
    mapfile -t candidates < <(git ls-files '*.md')
fi

# Backticks only, and no space before the language: that is what mmdc
# recognises. A tilde-fenced block renders nothing and exits zero,
# which is why check_mermaid_lint_ci in the development repository
# matches the same narrow form -- the audit must not call a repository
# covered for a diagram this script cannot see.
#
# GitHub renders a tilde fence as a diagram all the same, so one would
# otherwise ship unlinted through the exact gap this script exists to
# close. Rather than fail open, refuse the file and say which fence to
# use: the narrow mmdc-compatible form stays the only one that can be
# committed unnoticed.
#
# Which means the scan has to track fence state rather than match bare
# lines. A fence shown inside a longer fence is an example being
# written about, not a diagram to render, and the page most likely to
# contain one is the page explaining this very rule -- so a line
# match would fail the repository for documenting its own linter. The
# rules are CommonMark's: a fence opens on three or more backticks or
# tildes and closes on the same character, at least as long, with no
# info string, and only fences that open at the top level are
# classified. mmdc reads nested examples too, so a file selected for
# some other diagram is still rendered whole; what this decides is
# which files are worth starting a container for, and which are
# refused outright.
existing=()
for candidate in "${candidates[@]}"; do
    # Only reachable on the git ls-files path, where an entry can be
    # staged for deletion; a named file was checked above.
    if [ -f "${candidate}" ]; then
        existing+=("${candidate}")
    fi
done

# Into a variable rather than through a process substitution, so that
# an awk that dies takes the script with it under set -e. A scan that
# failed silently would report no diagrams and exit zero, which is the
# shape of failure this whole lane exists to prevent.
scan=""
if [ "${#existing[@]}" -ne 0 ]; then
    scan=$(awk '
        FNR == 1 { fence = ""; flen = 0; backtick = 0; tilde = 0 }
        {
            line = $0
            sub(/^[ \t]*/, "", line)
            ch = substr(line, 1, 1)
            run = 0
            if (ch == "`" || ch == "~")
                while (substr(line, run + 1, 1) == ch)
                    run++
            if (run < 3)
                next

            info = substr(line, run + 1)
            sub(/^[ \t]+/, "", info)
            sub(/[ \t].*$/, "", info)

            if (fence != "") {
                if (ch == fence && run >= flen && info == "")
                    fence = ""
                next
            }

            fence = ch
            flen = run
            if (info != "mermaid")
                next
            if (ch == "`" && !backtick) {
                backtick = 1
                print "backtick", FILENAME
            }
            if (ch == "~" && !tilde) {
                tilde = 1
                print "tilde", FILENAME
            }
        }
    ' "${existing[@]}")
fi

files=()
unlintable=()
while read -r kind scanned_file; do
    case "${kind}" in
        backtick) files+=("${scanned_file}") ;;
        tilde) unlintable+=("${scanned_file}") ;;
    esac
done <<< "${scan}"

rc=0

if [ "${#unlintable[@]}" -ne 0 ]; then
    for unlintable_file in "${unlintable[@]}"; do
        echo "mermaid-lint: ${unlintable_file}: mermaid in a tilde fence" \
            "is not linted; use a backtick fence" >&2
    done
    # Fall through rather than exiting here. A repository with both a
    # tilde fence and a diagram that does not parse should learn about
    # both from one run: the virtual machine this lane needs is the
    # expensive part, and the path filter exists to avoid spinning a
    # second one to deliver the second half of the same answer.
    rc=1
fi

if [ "${#files[@]}" -eq 0 ]; then
    if [ "${rc}" -eq 0 ]; then
        echo "No markdown files contain mermaid diagrams; nothing to lint."
    fi
    exit "${rc}"
fi

workdir=$(mktemp -d)
trap 'rm -rf "${workdir}"' EXIT
printf '%s\n' "${files[@]}" > "${workdir}/files.txt"

echo "Linting ${#files[@]} file(s) containing mermaid diagrams."

# One container for the whole run: startup dominates, so a container
# per file roughly doubles the cost. The image's entrypoint supplies
# -p /puppeteer-config.json, which the sandbox needs, so overriding
# the entrypoint means passing it back by hand.
#
# The exit status of docker run is the inner shell's, and it is the
# whole point of this script. Do not pipe this into tail or grep: the
# pipeline would report the filter's status and turn every failure
# green. It is combined with rc rather than being the script's status
# directly, so that a refused tilde fence above still fails a run in
# which every backtick diagram parses.
docker run --rm -u "$(id -u):$(id -g)" \
    -v "${repo_root}":/src:ro \
    -v "${workdir}":/work \
    --entrypoint /bin/sh "${IMAGE}" -c '
        mmdc=/home/mermaidcli/node_modules/.bin/mmdc
        rc=0
        while IFS= read -r f; do
            if "${mmdc}" -p /puppeteer-config.json \
                    -i "/src/${f}" -o /work/rendered.md \
                    >/work/log 2>&1; then
                echo "ok    ${f}"
            else
                rc=1
                echo "FAIL  ${f}"
                # Trim the puppeteer stack trace through mermaid.js,
                # which says nothing about the diagram. What is left
                # is the parse error and its caret, which is what a
                # person needs.
                sed "/mermaidcli\/node_modules/,\$d" /work/log \
                    | sed "s/^/        /"
            fi
        done < /work/files.txt
        exit "${rc}"
    ' || rc=1

exit "${rc}"
