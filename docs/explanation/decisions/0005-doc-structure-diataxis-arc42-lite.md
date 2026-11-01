# 0005 — Documentation in Diataxis, architecture in five arc42 chapters

- Status: proposed
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

The project has one maintainer, a Home Assistant custom integration as its product, and a rule that documentation must stay walkable for three audiences: users, contributors and AI agents. Two frameworks compete for the explanation layer: Diataxis sorts documents by what the reader needs, arc42 sorts the architecture by chapter. Neither is free, and a document set that duplicates itself rots faster than it is read.

## Decision

Documentation follows Diataxis — tutorial, how-to, reference, explanation, Markdown only, one document in exactly one quadrant. For the architecture we adopt exactly five arc42 chapters, in arc42-lite form: 3 context, 5 building blocks, 6 runtime view, 8 crosscutting concepts, 9 decisions. ADRs live in `docs/explanation/decisions/` and are the only place where rejected options are kept.

## Consequences

**Positive**

- Every document has one home and one job, so a contradiction has an obvious place to be fixed, and the reader always knows which quadrant to open next.
- The five chapters cover what this integration actually has: a context boundary, a handful of modules, one sequence, a few conventions and the decisions. Nothing is written to fill a template, and the habit of block → decision → consequence works because `architecture.md` links building blocks to ADRs.

**Negative**

- Two frameworks mean two indexes to keep current: the Diataxis map in `docs/README.md` (#27) and the chapter index in `architecture.md` (#92).
- Skipping chapters 1, 2, 4, 7, 10, 11 and 12 is a deliberate gap. A reviewer who expects a full arc42 document will look for them; the chapter map has to say where each topic went instead.
- ADRs are explanation, so a reader who wants current behaviour must not stop there; reference and how-to stay authoritative and have to be updated with every behaviour change.

**Follow-ups**

- #92 writes `architecture.md` with the chapter index and one link per building block to its ADRs.
- #125 `data-model.md` carries context and constraints, #129 `conventions.md` carries chapter 8; `docs/README.md` (#27) maps the quadrants and `mise run docs` (#127) enforces placement.

## Alternatives considered

**A full arc42 document set, all twelve obligatory chapters.** Complete on paper, but at this size it duplicates the ADRs (chapters 4, 9, 11), the reference docs (10) and the plan (1, 2), and every behaviour change would need edits in three places. Rejected: the duplication rots faster than the document is read.

**A plain README, no explanation layer.** The cheapest option and the one most HACS repositories choose. Rejected because the auth flow, the statistics strategy and the poll interval all have non-obvious reasons, and without an ADR the next contributor re-litigates them; the plan already names "a second maintainer can pick up any task" as a success criterion.

## Links

- Issues #27 (docs map), #92 (`architecture.md`), #93 (ADRs), #125, #127, #129; `PLAN.md` → "Document structure (Diataxis, Markdown only)" and "ADRs inside Diataxis"
- ADR-0001 to ADR-0004 — the first decisions this structure has to carry
