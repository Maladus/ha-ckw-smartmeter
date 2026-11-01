# Roadmap

This page answers: what will this integration do for me, and when?

Each milestone ships when its quality-scale rules are met, not on a date. Task-level detail and current
status live in [`PLAN.md`](PLAN.md) and the GitHub issue tracker.

## v0.1.0: first usable release (Bronze)

- Log in with your CKW Single Sign-On account and pick your Zählpunkt
- Daily kWh and CHF appear as sensors for each delivery point
- The Energy dashboard fills with your consumption and cost history, backfilled about two years
- Install and update through HACS

## v0.3.0: solid day to day (Silver)

- When CKW stops accepting your login, Home Assistant asks you to sign in again instead of failing quietly
- You can reload or remove the integration without restarting Home Assistant
- Download a diagnostics file for a bug report; your Zählpunkt and tokens are masked
- Every error shows up in German or English, never as a raw Python exception
- Test coverage stays above 95%

## v1.0.0: quality scale Gold

- A repair notice appears when a meter is exchanged or CKW rejects the client, and it says what to do
- A newly added Zählpunkt shows up on its own; a removed one is marked stale and you decide when to delete it
- Every entity has a proper icon and every error a translated message
- The full documentation set is there: tutorial, how-tos, reference and explanations
- All 54 quality-scale rules are done or exempt with a reason

## Related

- `GOAL.md` explains the scope behind these milestones.
- `PLAN.md` lists the tasks, dependencies and gates.
