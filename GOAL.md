# Goal

This page answers: why does this integration exist, and what is it deliberately not?

CKW customers get their smart-meter consumption inside Home Assistant. Daily kWh lands in the Energy
dashboard, read from the official CKW OpenEnergy API. No portal scraping, no stored CKW password.

The CHF amounts CKW returns are parsed but deliberately not exposed: they are base prices without VAT
and surcharges, so a cost sensor would be a number nobody can reconcile with a bill. There are no cost
entities in this integration.

## In scope

- Official CKW OpenEnergy API with the public client `oe-wigger`: daily aggregates, HT/NT split
- One device per delivery point (Zählpunkt), long-term statistics, roughly two years of backfilled history
- Installable from HACS (now Home Assistant Marketplace)
- Quality scale Gold at v1.0.0, German and English UI

## Out of scope

- Real-time power. The Netzbetreiber may legally read the meter once per day, so the data stops at yesterday
- Invoices, product switching, relocation, portal charts
- Other Swiss grid operators. That needs a provider registry, not this project
- Dynamic tariff prices. The companion integration `swiss-dynamic-tariffs` covers those
- Platinum, for the reason in the last section

## Success criteria

- A CKW customer installs from HACS and sees the Energy dashboard populated within 15 minutes
- The integration stores no password, sends no SMS in normal operation, and refreshes tokens silently across restarts
- CI is green on ruff, pytest, hassfest and the HACS action
- All 54 quality-scale rules are `done` or `exempt` with a written reason
- A second maintainer can pick up any task from `PLAN.md` without asking questions

## Gold is the target, Platinum is not a work item

v1.0.0 claims Gold: every Bronze, Silver and Gold rule done, or exempt with a reason. Platinum is a
separate decision taken after Gold is verified, and it grades the published Python library rather than
the integration. Until that decision is taken, no task, no branch and no issue targets Platinum.

## Related

- `README.md` is the entry point for users.
- `ROADMAP.md` turns this goal into user-visible milestones.
- `PLAN.md` holds the task-level detail, the gates and the risk register.
