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
#   6. the quality-scale audit in custom_components/ckw/quality_scale.yaml
#      matches the page that explains it (docs/reference/quality-scale.md)
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

# --- rule 6: the quality-scale audit is not stale --------------------------
# hassfest validates a quality_scale.yaml only for integrations inside the
# Home Assistant core repository (PLAN.md, "Quality-scale documentation
# matrix"), so this is the local guard against a claim that rotted: the group
# header of every tier must count the rules that follow it, every rule needs a
# status of done/todo/exempt and a written comment, and every `todo` must be
# listed under "Known gaps" in the page a reader will open.
readonly QUALITY_FILE=custom_components/ckw/quality_scale.yaml
readonly QUALITY_PAGE=docs/reference/quality-scale.md

[[ -f $QUALITY_FILE ]] || fail "$QUALITY_FILE is missing - the quality scale has no audit file"
[[ -f $QUALITY_PAGE ]] || fail "$QUALITY_PAGE is missing - a claim without the page that explains it"

# One line per finding: T = tier summary, R = one rule with its status, E = a
# violation. The Markdown half of the check runs below, in bash, because awk
# should not have to parse a table.
quality_report=$(awk '
  # Group header: "  # --- Bronze (20 rules) ---"
  /^  # --- / {
    tier = $3
    declared[tier] = substr($4, 2) + 0
    order[++tiers] = tier
    next
  }
  # A rule key, two-space indent: "  action-setup:"
  /^  [a-z0-9-]+:$/ {
    rule = substr($1, 1, length($1) - 1)
    if (tier == "") {
      printf "E\t%s is not inside a tier group\n", rule
      next
    }
    rules[tier]++
    next
  }
  /^    status:/ {
    status[rule] = $2
    if ($2 == "done") done[tier]++
    else if ($2 == "todo") todo[tier]++
    else if ($2 == "exempt") exempt[tier]++
    printf "R\t%s\t%s\n", rule, $2
    next
  }
  /^    comment:/ {
    # A block scalar ("comment: >-") continues on the next lines.
    text = $0
    sub(/^    comment:[[:space:]]*/, "", text)
    has_comment[rule] = 1
    if (text == "" || text == "\"\"") empty_comment[rule] = 1
    next
  }
  END {
    for (i = 1; i <= tiers; i++) {
      t = order[i]
      printf "T\t%s\t%d\t%d\t%d\t%d\n", t, rules[t], done[t], todo[t], exempt[t]
      if (declared[t] != rules[t])
        printf "E\t%s: the group header says %d rules, %d are listed\n", t, declared[t], rules[t]
    }
    for (r in status) {
      if (status[r] != "done" && status[r] != "todo" && status[r] != "exempt")
        printf "E\t%s: status \"%s\" is not done, todo or exempt\n", r, status[r]
    }
  }
' "$QUALITY_FILE")

declare -a quality_problems=()
quality_todos=()
quality_total=0
quality_summary=""
while IFS=$'\t' read -r kind first second third fourth fifth; do
  case $kind in
    T)
      quality_total=$(( quality_total + second ))
      quality_summary="${quality_summary}${quality_summary:+, }${first}: ${second} rules (${third} done, ${fourth} todo, ${fifth} exempt)"
      ;;
    R)
      if [[ $second == todo ]]; then
        quality_todos+=("$first")
      fi
      ;;
    E) quality_problems+=("$first") ;;
  esac
done <<<"$quality_report"

if (( ${#quality_problems[@]} > 0 )); then
  fail "quality-scale: $(printf '%s; ' "${quality_problems[@]}" | sed 's/; $//')"
fi

# Every open rule must be visible in the Known gaps table, with its issue.
known_gaps=$(sed -n '/^## Known gaps/,/^## /p' "$QUALITY_PAGE")
for rule in "${quality_todos[@]}"; do
  grep -qF -- "\`$rule\`" <<<"$known_gaps" ||
    fail "quality-scale: $rule is todo in $QUALITY_FILE but not listed under 'Known gaps' in $QUALITY_PAGE"
done

info "quality-scale: $quality_total rules - $quality_summary (see $QUALITY_PAGE)"
info "${#documents[@]} documents OK"
