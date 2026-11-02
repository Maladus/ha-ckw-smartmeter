# Write a commit

This page answers: which commit types this repository uses, how an agent commit gets its
`Co-Authored-By` trailer, how a human removes that trailer, and how to find commits by model.

## Commit types

Conventional Commits, one type per commit, imperative subject, no trailing period. The type says what
the change *is*, not which files it touches.

| Type | Use for | Example subject |
| --- | --- | --- |
| `feat` | user-visible behaviour or a new capability | `feat: add the quarter-hour consumption sensor per delivery point` |
| `fix` | a defect in shipped behaviour | `fix: keep the refresh token across a Home Assistant restart` |
| `docs` | documentation only, including ADRs and this page | `docs: add architecture and data model explanations` |
| `test` | tests and fixtures, no production change | `test: add local mock CKW API and fake Keycloak` |
| `chore` | tooling, dependencies, gates, housekeeping | `chore: add markdown linting, docs layout check and co-author trailers` |
| `ci` | workflows under `.github/workflows/` | `ci: install the test requirements before running pytest` |
| `refactor` | same behaviour, different structure | `refactor: split the API client from the coordinator` |

Breaking changes carry `!` after the type (`feat!:`) and a `BREAKING CHANGE:` trailer. Scope is optional
and only used where it helps the log (`fix(auth): ...`).

## Co-author trailer

Agent commits credit the model that wrote them, so a reviewer can tell a hand-written change from a
generated one without opening the diff. `.pre-commit-config.yaml` runs `scripts/hooks/co_author.py` in
the `commit-msg` stage; the hook appends one trailer when both environment variables are set:

```text
Co-Authored-By: pi (<PI_MODEL>) <noreply@pi.local>
```

The model name is taken verbatim from the agent session: `PI_MODEL` is the model id including any tag,
so `PI_MODEL=deepseek-v4.1-flash:1017` produces:

```text
Co-Authored-By: pi (deepseek-v4.1-flash:1017) <noreply@pi.local>
```

The provider is deliberately not recorded. `PI_PROVIDER` only decides whether the session is an agent
session at all; it is never printed, because the same model can be served by a local runtime or a cloud
provider and the provider is a deployment detail of the model, not a second author. A fully qualified
model string (`PI_MODEL=ollama-cloud/deepseek-v4.1-flash:1017`) has its leading `<provider>/` stripped,
so the trailer can never name a provider.

Rules the hook follows:

- both `PI_PROVIDER` and `PI_MODEL` must be set, otherwise the commit message is left alone, which is
  what happens for a human commit from a plain shell; `PI_MODEL` is what appears in the trailer
- a message that already has any `Co-Authored-By:` line is never touched, so re-running an amend is a
  no-op and a human co-author is not overwritten
- the trailer is appended after the last trailer block (or as a new block after the body), never in the
  middle of the message
- the hook is installed by `mise run setup`; to install it by hand:
  `pre-commit install --hook-type commit-msg`

## Remove the trailer from your own commit

Edit the message, do not bypass the hooks:

```bash
git commit --amend
```

Delete the `Co-Authored-By: pi (...)` line, save and close the editor. `git commit --amend` re-runs the
`commit-msg` hook, which is fine: the message no longer needs a trailer and the hook only adds one when
none is present.

`git commit --no-verify` is not the answer. It skips every hook, including the gitleaks secret scan and
the commit-msg stage, so a leaked token would go through unnoticed, and the trailer you wanted to remove
was already written into the message.

## Find commits by model

```bash
git log --grep='Co-Authored-By: pi'                          # every agent commit
git log --grep='Co-Authored-By: pi (deepseek-v4.1-flash'    # one model family
git log --grep='Co-Authored-By: pi (deepseek-v4.1-flash:1017)' --format='%h %s'   # one exact model
```

`--grep` matches the trailer text, so a model is found by the id it was committed under. Searching by
provider finds nothing, by design: the trailer never contains one.

`--grep` is case-sensitive by default; add `-i` if you are unsure how the trailer was spelled.

## Secret scanning in three layers

A commit is scanned three times: the local `gitleaks` pre-commit hook on the staged diff and the
pre-push hook on the full history (gate G-3), the [`Secrets` workflow](../../.github/workflows/secrets.yml)
on every push to `main` and every pull request, and GitHub secret scanning with push protection, which
is enabled on this public repository and rejects a pushed token before it reaches a branch (#118). A
finding is a rotation first, then the commit that removes it — [SECURITY.md](../../SECURITY.md).

## Related

- [Architecture](../explanation/architecture.md) — the modules these commits change.
- [ADR-0005 — documentation structure](../explanation/decisions/0005-doc-structure-diataxis-arc42-lite.md)
  — why this page is a how-to and where the reasoning lives.
- [PLAN.md](../../PLAN.md) — cross-cutting gates G-1 to G-7 that every commit has to pass.
- [SECURITY.md](../../SECURITY.md) — what counts as a leaked credential and what to do about one.
