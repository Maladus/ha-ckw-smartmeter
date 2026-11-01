# 0004 — Poll every two hours over a rolling seven-day window

- Status: proposed, amended 2026-11-01: observed lag is 1-3 days, not one day
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

CKW aggregates meter readings per day; the OpenEnergy API serves them up to yesterday, so today's value does not exist yet and yesterday's may still change. The API is rate limited and the API terms allow one read per meter per day. Home Assistant expects a coordinator with a fixed interval, and a failed poll must not turn into a retry loop that hammers the identity provider, because each login on the OTP fallback costs an SMS.

## Decision

The coordinator polls every 2 hours, each time refetching a rolling 7-day window with `days` aggregation. Everything older than the window is already imported (ADR-0002) and is not fetched again. Data is only available up to yesterday; the sensors show the last closed day.

## Consequences

**Positive**

- A missed poll, a CKW outage or a HA restart costs nothing: the 7-day window repairs the gap on the next run, and days that CKW corrects are re-imported.
- Six to twelve reads per meter per day stay well inside the quota, while a 6 h refresh token keeps the session alive on its own (ADR-0001); one interval for one coordinator keeps the runtime view to one sequence.

**Negative**

- Today's consumption is invisible; users looking for "what am I using right now" find yesterday's number. This has to be stated in the docs (#125), not discovered.
- Up to 2 h plus CKW's own lag can pass between a correction at CKW and the sensor value; a manual refresh is the only shortcut.
- The rolling window hides a gap older than 7 days: after a longer outage the coordinator cannot self-heal, and only the backfill path (#123) recovers it.

**Follow-ups**

- `ConfigEntryAuthFailed` must fail the update and start reauth — never retry with a fresh login. On the SMS-OTP fallback (#53) a retry loop means an SMS per attempt and a lockout risk.
- #125 documents the one-day lag, the interval and the repair window; #64/#123 own the chunked backfill and its manual re-run.

### Amendment (2026-11-01)

Observed against a live meter: the newest interval is **1 to 3 days old**, not one. The spread varies by
meter and by day, and the contract's "up to yesterday" is a ceiling the API does not always reach. The
decision above does not change: a 2 h poll refetches the rolling 7-day window, so it picks up a day
within two hours of publication and covers the whole observed spread without a second code path. The
positive and negative consequences above stand, with "yesterday's number" read as "the newest
published day, 1–3 days old".

## Alternatives considered

**Poll every 5 minutes.** The underlying data changes once a day, so 288 reads produce the same number as one; against a rate-limited API it only burns quota and invites a 429. Rejected as pointless load.

**Poll hourly.** More quota for no fresher data than the 2 h interval, and twice the requests for a value that moves once a day. Rejected as waste, not as a bug.

**Poll once a day.** Cheapest, but a single failed poll then means a missing day until the next run, and a 6 h refresh token would expire between runs without `offline_access`. Rejected because it trades a real failure mode for a saving that does not matter.

## Links

- Issues #64 (chunking), #123 (backfill), #125 (data model and lag), #53 (SMS-OTP fallback)
- ADR-0001 — the refresh chain this interval keeps alive; ADR-0002 — the statistics import this window feeds
