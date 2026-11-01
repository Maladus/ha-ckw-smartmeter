# Release checklist

This page answers: what has to be true before this integration gets a tag, and in which order do the
merge, the tag, the release notes and the clean-instance verification happen?

A release is a separate, deliberate step after the gates, not the merge itself. Nothing below is
optional for a version that HACS users will install, and the two steps a machine cannot do — the
clean-instance verification and the decision to publish — are named as such.

## Ready to release means

| # | Condition | How it is checked |
| --- | --- | --- |
| 1 | The quality-scale audit is complete and green: every Bronze, Silver and Gold rule `done` or `exempt` with a written reason, no `todo` left | `mise run docs` — `scripts/docs-check.sh` compares [quality_scale.yaml](../../custom_components/ckw/quality_scale.yaml) with [quality-scale.md](quality-scale.md) and fails on a `todo` rule that is not listed under "Known gaps" |
| 2 | The local gates are green on the release commit, and so are the three required pull-request checks | `mise run check` (G-1 lint, docs, G-2 tests, G-3 hooks, and G-6 `dev-smoke` when the dev container runs), plus `mise run types`; *Gates (mise run)*, *Hassfest* (G-4) and *HACS validation* (G-5) on the pull request |
| 3 | Test coverage is at or above the floor of 95 % | `mise run test` prints the per-module table and the total; the floor is `fail_under` in [pyproject.toml](../../pyproject.toml) and a miss fails the gate |
| 4 | No drift between the plan and the issue tracker | `mise run issues` — zero drift, and the issue this release closes is the one the pull request names |
| 5 | `CHANGELOG.md` describes the release: the `## [Unreleased]` entries are folded into the new `## [X.Y.Z] - YYYY-MM-DD` section, everything merged since the last tag is in it, and nothing that was dropped is still claimed | read the section against `git log --oneline <last-tag>..HEAD`, then `mise run docs` |
| 6 | `"version"` in [manifest.json](../../custom_components/ckw/manifest.json) is `X.Y.Z` and equals the tag without its `v`. [hacs.json](../../hacs.json) carries no version of its own, so only its `name`, `country` and `homeassistant` minimum are kept in step with the README; a `filename` is added only if a release asset is ever used | the version bump is its own commit, `chore(release): X.Y.Z` |
| 7 | The release commit is merged into `main` through a pull request with the three green checks | [CONTRIBUTING.md](../../CONTRIBUTING.md#open-the-pull-request) |
| 8 | The tag `vX.Y.Z` points at the merged release commit, is annotated, and its message is `X.Y.Z` | `git tag -a vX.Y.Z -m "X.Y.Z"`, then `git push origin vX.Y.Z` |
| 9 | The GitHub release exists for that tag, and its notes are the changelog section, not a retyped summary | `gh release create vX.Y.Z --notes-file …` |
| 10 | [The fresh-install check](fresh-install-check.md) was re-run against the tag from a clean Home Assistant instance: install, login, entry, entities, statistics, diagnostics, log | a human does this; the recorded run in that page is the template |

Steps 1–4 are the same gates every pull request runs, so a release fails there long before the tag.
Steps 5–7 are the release commit. Steps 8–10 happen after the merge, in that order: no tag before the
merge, no release before the tag, no release notes that the changelog does not contain.

The tag and the GitHub release are created by the maintainer, never by a pull request and never by an
agent: a tag is not reviewable, and a released version cannot be superseded without confusing every
user who installed it.

## The release commit

One commit on a branch, merged like every other change:

```bash
mise run check                 # G-1, docs, G-2, G-3, G-6
mise run types                 # mypy --strict
mise run issues                # zero drift
git switch -c ckw-X-Y-Z        # one branch per issue, #N is the release issue
# bump "version" in custom_components/ckw/manifest.json, fold CHANGELOG.md's
# [Unreleased] into ## [X.Y.Z] - YYYY-MM-DD, leave [Unreleased] empty
git commit -m "chore(release): X.Y.Z"
git push -u origin ckw-X-Y-Z
gh pr create --base main --head ckw-X-Y-Z \
  --title "chore(release): X.Y.Z" \
  --body "… — Closes #N"
```

The changelog rules are in [CHANGELOG.md](../../CHANGELOG.md#maintaining-this-changelog): one line per
change under the group that fits, in the same pull request as the change that caused it. A release
commit only moves those lines, it does not invent them — an entry written at release time is a review
finding, not a release note.

## After the merge

```bash
git switch main && git pull
git tag -a vX.Y.Z -m "X.Y.Z"      # annotated, message is the bare version
git push origin vX.Y.Z

# The notes are the changelog section, copied verbatim.
awk '/^## \[X\.Y\.Z\]/{flag=1;next}/^## /{flag=0}flag' CHANGELOG.md > /tmp/ckw-X.Y.Z-notes.md
gh release create vX.Y.Z --title "X.Y.Z" --notes-file /tmp/ckw-X.Y.Z-notes.md
```

Then the clean-instance verification, against the tag rather than against `main`:

```bash
git clone --depth 1 --branch vX.Y.Z \
  https://github.com/Maladus/ha-ckw-smartmeter /tmp/ha-ckw-clone
```

followed by the steps of [the fresh-install check](fresh-install-check.md). Install the release the way
a stranger does — through HACS with the custom repository, or by copying `custom_components/ckw` — and
compare what you see with the recorded run: one entry, one device per electricity meter, four enabled
sensors, statistics rows for the backfilled days, a diagnostics file without a token or a complete
Zählpunkt, no `Traceback` in the log.

What the verification is *not*: a live-power check. The newest value is 1 to 3 days old by design, and a
run that ends before a second poll cannot show a publication line either. Those are the documented
behaviour, not a failed release.

If the verification fails, fix it on `main` and cut the next patch version. Do not move or re-tag a
published version, and do not edit the release notes of a release users already have.

## Closing the task

The release issue is closed by the merged pull request through `Closes #N`. Its `PLAN.md` row is marked
✅ in the same pull request only when the tag exists; otherwise the ✅ follows in a small docs pull
request once the release is out, because `mise run issues` fails on a ✅ row whose issue is still open.

The submission to the HACS default list (`hacs/default`) is a separate pull request. It follows a few
weeks of real-world use, never release day: the default list is a curated index that expects a
repository with a release history, and a bug found in week one is cheaper to fix than a withdrawn
submission.

## Related

- [CHANGELOG.md](../../CHANGELOG.md#releasing) — the same procedure in five lines, next to the version
  history it describes.
- [Fresh-install verification](fresh-install-check.md) — the recorded run this checklist's last step
  repeats against the tag.
- [Quality scale](quality-scale.md) — the audit behind condition 1, and the commands that re-check it.
- [How-to: install](../../docs/how-to/install.md) — the two install routes the verification exercises.
- [CONTRIBUTING.md](../../CONTRIBUTING.md#releasing) — the contributor-side view, including the
  pull-request flow the release commit goes through.
- [PLAN.md](../../PLAN.md) — the release tasks, the gates and the issue each one closes.
