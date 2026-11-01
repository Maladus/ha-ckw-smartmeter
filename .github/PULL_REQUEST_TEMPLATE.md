# Pull request

One branch per issue (`gh issue develop <N> --checkout`), and the issue number is the task identifier.
`CONTRIBUTING.md` explains the gates below; `AGENTS.md` is the same contract for an AI agent.

## What this changes

<!-- One paragraph: the symptom or the missing capability, and what the change does about it. -->

## Checklist

- [ ] `mise run check` is green — G-1 lint, the docs check, G-2 tests and G-3 hooks, plus G-6
      `mise run dev-smoke` when the dev container was running
- [ ] `mise run types` is green (`mypy --strict`), and hassfest ran in this pull request when
      `manifest.json`, `strings.json`, `icons.json` or `quality_scale.yaml` changed
- [ ] Every behaviour change carries a test, and coverage stays at or above the floor in `pyproject.toml`
- [ ] The affected Diátaxis page under `docs/` is updated — tutorial, how-to, reference or explanation —
      and `CHANGELOG.md` has its entry
- [ ] An ADR is added under `docs/explanation/decisions/` and its index row added, when the change touches
      auth, tokens, statistics, entity or unique ids, user-visible behaviour, or adds a dependency
- [ ] This body contains `Closes #N`
- [ ] The `PLAN.md` row is marked ✅

## Notes for the reviewer

<!-- Anything a human has to verify on a real instance: token storage, the reauth flow, statistics, backfill. -->
