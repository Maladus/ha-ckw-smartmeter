# Pull request

One branch per issue (`gh issue develop <N> --checkout`), and the issue number is the task identifier.
The change reaches `main` through this pull request and three required checks — *Gates (mise run)*,
*Hassfest*, *HACS validation*. Never push to `main` and never merge locally; `CONTRIBUTING.md` explains
the gates below and the flow, `AGENTS.md` is the same contract for an AI agent.

## What changed

<!-- One paragraph: the files and behaviours the diff touches. -->

## Why

<!-- The symptom or the missing capability, the issue it belongs to, and what this change does about it. -->

## How it was verified

<!-- Paste the last lines of `mise run check` (and `mise run types`, `mise run dev-smoke` when they ran).
     If a check could not run, say which and why. A green CI run is not a substitute for saying what you ran. -->

```text
check: green in 21s
```

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
- [ ] This body contains `Closes #N`, which closes the issue when this pull request merges
- [ ] The task's `PLAN.md` row is marked ✅ in this pull request

## Notes for the reviewer

<!-- Anything a human has to verify on a real instance: token storage, the reauth flow, statistics, backfill. -->
