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

# Pinned deliberately. Renovate's stock managers do not read a docker
# reference out of a shell script, so this moves when somebody moves
# it; check for a newer tag when a mermaid feature is missing.
IMAGE="ghcr.io/mermaid-js/mermaid-cli/mermaid-cli:11.4.2"

repo_root=$(git rev-parse --show-toplevel)
cd "${repo_root}"

# git ls-files rather than find, so vendored and ignored trees are
# excluded for free. A Rust project's .cargo-cache alone holds
# thousands of markdown files nobody here wrote, several of which
# contain diagrams that are not ours to fix.
if [ "$#" -gt 0 ]; then
    candidates=("$@")
else
    mapfile -t candidates < <(git ls-files '*.md')
fi

files=()
for candidate in "${candidates[@]}"; do
    [ -f "${candidate}" ] || continue
    if grep -q '^[[:space:]]*```mermaid' "${candidate}"; then
        files+=("${candidate}")
    fi
done

if [ "${#files[@]}" -eq 0 ]; then
    echo "No markdown files contain mermaid diagrams; nothing to lint."
    exit 0
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
# green.
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
    '
