#!/usr/bin/env bash
#
# docs-check - enforce the documentation layout of PLAN.md, section
# "Document structure (Diataxis, Markdown only)" (documentation rules 6-8) and
# issue #127. Run from the repository root, normally through `mise run docs`:
#
#   markdownlint-cli2 "**/*.md" "#node_modules" && bash scripts/docs-check.sh
#
# Checks, in this order:
#   1. every docs/**/*.md sits in exactly one Diataxis quadrant
#   2. every document is linked from docs/README.md (the map, #27)
#   3. every page opens with a heading and a "This page answers: ..." line
#   4. every page has a "Related" block
#   5. every relative Markdown link resolves to an existing file
#
# Any violation prints one `docs-check: <reason>` line and exits non-zero; a
# clean run prints a one-line summary and exits 0.
#
# Bootstrap tolerance, both spelled out in the output instead of passing
# silently: docs/README.md may be missing while the docs map (#27) is written,
# and a page may already link a document that PLAN.md schedules but nobody has
# written yet (#119-#129). A link to a target that PLAN.md does not name is a
# typo and still fails.

set -euo pipefail

readonly QUADRANTS=(tutorial how-to reference explanation)

# All failures leave through here, so the message format is uniform and the
# reason is always the first thing on the line.
fail() {
  printf 'docs-check: %s\n' "$*" >&2
  exit 1
}

info() {
  printf 'docs-check: %s\n' "$*"
}

# Documents are discovered with find, not git, so a file that is not yet
# committed is checked too.
[[ -d docs ]] || fail "no docs/ directory - run this from the repository root"

mapfile -t documents < <(find docs -type f -name '*.md' | LC_ALL=C sort)
(( ${#documents[@]} > 0 )) || fail "no Markdown documents found under docs/"

is_quadrant() {
  local candidate
  for candidate in "${QUADRANTS[@]}"; do
    [[ $candidate == "$1" ]] && return 0
  done
  return 1
}

# ADRs follow the MADR-mini skeleton in docs/explanation/decisions/0000-template.md
# (`# NNNN - Title`, then Status/Date/Deciders, Context, Decision, Consequences,
# Links), not the Diataxis page template. They are exempt from rules 2-4: the map
# links docs/explanation/decisions/README.md, and that index links every ADR, so
# the series stays walkable without listing each record in the map. Their
# placement (rule 1) and their links (rule 5) are still checked.
is_adr() {
  [[ $1 =~ ^docs/explanation/decisions/[0-9]{4}-[^/]*\.md$ ]]
}

# Resolve `a/../b`, `./c` and repeated slashes without requiring the path to
# exist (realpath -m is not portable to the macOS dev machines).
normalize_path() {
  local part
  local -a parts=() stack=()
  IFS=/ read -ra parts <<<"$1"
  for part in "${parts[@]}"; do
    case $part in
      '' | .) continue ;;
      ..)
        # Climbing above the repository root is not a relative link in it.
        (( ${#stack[@]} > 0 )) || return 1
        unset 'stack[-1]'
        continue
        ;;
    esac
    stack+=("$part")
  done
  local IFS=/
  printf '%s' "${stack[*]}"
}

# Documents PLAN.md already names, so "not written yet" is told apart from
# "wrong path". The plan is the source of truth for what docs will exist.
planned_documents=()
if [[ -f PLAN.md ]]; then
  mapfile -t planned_documents < <(
    grep -oE 'docs/[A-Za-z0-9._/-]+\.md' PLAN.md | LC_ALL=C sort -u
  )
fi

is_planned() {
  local candidate
  for candidate in "${planned_documents[@]}"; do
    [[ $candidate == "$1" ]] && return 0
    # PLAN.md names a few documents by bare file name in one table and by full
    # path in another (#102 writes docs/quality-scale.md, the quadrant table
    # says reference/quality-scale.md); accept the document, not the spelling.
    [[ ${candidate##*/} == "${1##*/}" ]] && return 0
  done
  return 1
}

# --- rule 1: exactly one Diataxis quadrant ---------------------------------
for path in "${documents[@]}"; do
  relative=${path#docs/}

  # The map itself is the one document allowed at the root of docs/.
  [[ $relative == README.md ]] && continue

  quadrant=${relative%%/*}
  is_quadrant "$quadrant" ||
    fail "$path is not in a Diataxis quadrant (${QUADRANTS[*]})"

  # One quadrant means one folder: a nested document is only allowed where the
  # plan names a subfolder (the ADR series inside explanation).
  directories=$(awk -F/ '{ print NF - 1 }' <<<"$relative")
  if (( directories > 1 )); then
    [[ $relative == explanation/decisions/* ]] ||
      fail "$path is nested deeper than its quadrant folder (only docs/explanation/decisions/ may nest)"
  fi
done

# --- rule 2: every document is reachable from the map ----------------------
# docs/README.md (#27) is written by the docs-map task and may legitimately be
# absent while the documentation set is being bootstrapped: skip this rule then,
# but say so instead of passing silently.
if [[ -f docs/README.md ]]; then
  for path in "${documents[@]}"; do
    relative=${path#docs/}
    [[ $relative == README.md ]] && continue
    # ADRs are reached through the index that the map links (see is_adr).
    is_adr "$path" && continue
    grep -qF -- "$relative" docs/README.md ||
      fail "$path is not linked from docs/README.md"
  done
else
  info "docs/README.md missing (allowed during bootstrap)"
fi

# --- rules 3 and 4: page opening and Related block -------------------------
for path in "${documents[@]}"; do
  relative=${path#docs/}
  [[ $relative == README.md ]] && continue
  is_adr "$path" && continue

  first_line=$(head -n 1 "$path")

  if [[ $first_line == 'This page answers:'* ]]; then
    : # a page may open with the sentence itself
  elif [[ $first_line == '#'* ]]; then
    head -n 5 "$path" | grep -qE '^This page answers:' ||
      fail "$path has no 'This page answers:' line in the first 5 lines"
  else
    fail "$path must open with a '#' heading followed by 'This page answers: ...'"
  fi

  grep -qE '^(#{1,6}[[:space:]]+Related([[:space:]]|$)|Related([[:space:]]|$))' "$path" ||
    fail "$path has no 'Related' block linking its neighbours in the other quadrants"
done

# --- rule 5: relative Markdown links resolve -------------------------------
# Targets that PLAN.md schedules but nobody has written yet are collected and
# reported once, so a forward reference during bootstrap stays visible instead
# of failing the gate (#119-#129 are the tasks that owe those pages).
declare -A pending=()
for path in "${documents[@]}"; do
  directory=$(dirname "$path")

  # Only the inside of ]( ... ) is inspected; images, reference-style links and
  # bare URLs are not our concern here.
  while IFS= read -r raw; do
    [[ -n $raw ]] || continue

    # Drop an optional link title: ](path.md "Title").
    target=${raw%%[[:space:]]*}
    # External links and pure in-page anchors are not relative file links.
    case $target in
      '' | \#* | http://* | https://* | mailto:* | ftp://* | /*) continue ;;
    esac
    # A fragment or query only decorates the file it points at.
    target=${target%%#*}
    target=${target%%\?*}
    [[ -n $target ]] || continue

    if ! resolved=$(normalize_path "$directory/$target"); then
      fail "$path links to '$raw', which points outside the repository"
    fi

    if [[ -e $resolved ]]; then
      continue
    fi

    # A page may point forward at a document the plan still owes; a target the
    # plan does not name is a broken link.
    if is_planned "$resolved"; then
      pending["$resolved"]=$raw
      continue
    fi

    fail "$path links to '$raw', which does not exist (expected $resolved)"
  done < <(grep -oE '\]\([^)]*\)' "$path" | sed -e 's/^](//' -e 's/)$//' || true)
done

if (( ${#pending[@]} > 0 )); then
  mapfile -t outstanding < <(printf '%s\n' "${!pending[@]}" | LC_ALL=C sort)
  info "${#outstanding[@]} linked documents are planned but not written yet: ${outstanding[*]}"
fi

info "${#documents[@]} documents OK"
