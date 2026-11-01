# 0007 — Keep the session alive with the poll, and reauthenticate only on a dead refresh token

- Status: proposed
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

The CKW login is a Keycloak session behind a 10-minute access token and a 6-hour refresh token
([measured and derived numbers](../session-lifetime.md)). Home Assistant's `OAuth2Session` refreshes and
stores the pair; the user only sees a browser login when the chain breaks. Today the chain breaks at
least in one situation nobody designed for and possibly in one nobody has measured:

- A refresh token that is six hours old is dead. A Home Assistant instance that is off for longer than
  the margin the poll leaves cannot refresh and needs a human — the 2 h poll (ADR-0004) only keeps the
  chain alive while it runs.
- The user reports being sent back to the CKW login roughly every 2–3 months, which a 6 h token does
  not explain as long as polling continues. An absolute cap on the session (SSO Session Max, or an
  offline session max) or a revocation fits; nothing has been instrumented to tell which. The cap is
  invisible in the token response — `refresh_expires_in` always reports the idle window — so only the
  wall-clock interval between login and reauth can reveal it.
- CKW's own Android app keeps users logged in for roughly 2–3 months (user report, unverified by us).
  That is the behaviour CKW treats as normal for a client of this realm, so it sets the target: parity
  with the app, not a login without end.
- The options flow allows a poll interval up to 12 h — twice the refresh-token lifetime — so a user who
  picks it breaks the chain every cycle without noticing why.
- Keycloak may rotate refresh tokens on use ("Revoke Refresh Token"). If it does, a lost response or two
  concurrent refreshes end the session, and the failure looks like an unexplained reauth.

`offline_access` is advertised by both realms, no offline token appears in the documented sample (6 h,
not the 30 d an offline session reports), and the scope was requested from CKW in issue #2 but not
answered. The realm does not advertise session lengths at all, so the values cannot be read from
outside.

## Decision

We keep the session alive with the poll and refresh on demand:

1. The coordinator polls every 2 h (ADR-0004) and refreshes the access token before **every** API
   request when it has less than 60 s left, plus once at the start of each poll.
2. Refresh runs under one single-flight lock per config entry and stores the new token pair in the
   config entry before it is used.
3. A refresh that fails with `invalid_grant` maps to `ConfigEntryAuthFailed` and starts exactly one
   reauth. There is no retry, no backoff, and no automatic authorization request.
4. We ask CKW for `offline_access` on `oe-wigger` (issue #2) as the explicit path to that parity: the
   scope, not the poll, is what makes weeks of downtime survivable.
5. The target is parity with the CKW app: at worst one browser re-login per ~2–3 months, and earlier
   only when Home Assistant was offline long enough to lose the refresh chain. The acceptance criterion
   is "one reauth per ~2–3 months, never a login loop", not "no reauth at all" — CKW caps the session
   and no client can extend it.

## Consequences

**Positive**

- One refresh path, deterministic: the access token never survives to the next poll, because it covers
  only 8 % of the interval, so one refresh per poll is the design, not a heuristic, and there is no
  second code path to test.
- The single-flight lock removes the two realistic ways to lose a rotated refresh token: parallel
  entities refreshing, and a manual refresh overlapping the scheduled one.
- A dead refresh token produces one reauth, not a login loop and not a request storm against the
  identity provider — which keeps the rate-limit and lockout risk out of the auth path.
- Reauth becomes a designed step rather than a failure: a clear notification, a one-click browser flow,
  no repeated logins and no data loss, because the rolling 7-day window and the statistics import resume
  where they stopped once the entry reloads.
- The failure modes are quantified instead of felt, so the user-facing promises can be exact — and the
  promise is now the one CKW's own app keeps.

Worst case for a user, with the numbers this decision is based on:

| Situation | Time until a human must log in |
| --- | --- |
| HA restart, update or reboot under 4 h | none |
| HA off for 4–6 h | one reauth |
| HA off for more than 6 h | one reauth |
| HA off for weeks, `offline_access` granted (30 d idle, 60–90 d cap) | none until the cap, then one reauth |
| HA polling continuously, absolute session cap at ~60–90 d | one reauth at the cap |
| CKW revokes the session (password change, admin action) | one reauth, immediately |
| Poll interval set to 6 h or more without `offline_access` | one reauth per cycle |

**Negative**

- A login that never expires is not achievable with the flows available: CKW caps the session and the
  alternatives that could sidestep the browser are refused for this client. The integration will ask a
  human roughly quarterly, for as long as it exists.

- Without `offline_access`, four hours of downtime is the hard limit. A weekend trip with the instance
  powered off costs a login, and no client-side change can avoid it.
- The 2 h poll is doing two jobs — data freshness and session keep-alive — so the interval can no
  longer be chosen for data alone. The 1–12 h option range has to be capped below the refresh lifetime,
  or a keep-alive refresh has to run independently of the poll; the second is more code for a setting
  nobody needs.
- The 2–3 month reauth is not fixed by this decision. If it is an absolute cap, it is CKW's setting to
  change, and until then the honest documentation says so.
- Persisting tokens in the config entry makes the entry the single point of failure: restoring a backup
  onto a second instance gives two writers and, with rotation on, one of them loses the chain.
- Refreshing on every request means one Keycloak call per poll, every 2 h per instance, forever. It is
  negligible in volume but it is a dependency on the identity provider that a read-only API call would
  not have.

**Follow-ups**

- #2 — request `offline_access` for `oe-wigger`, and ask in the same mail for the configured SSO
  Session Idle / Max so the 2–3 month question can be answered without waiting. Neither value is
  observable from the client, and an absolute cap never appears in `refresh_expires_in`.
- #51 — implement reauth on `ConfigEntryAuthFailed` from a failed refresh, once, and treat the reauth
  UX as a feature: a notification that says what happened and what to do, one browser flow, no repeated
  prompts. A reauth every ~2–3 months is the steady state this ADR designs for.
- `README.md` and `docs/explanation/data-model.md` — state the expected cadence (on the order of months,
  earlier after a long outage) so a reauth is not experienced as a surprise or as a bug.
- #53 — cap the poll-interval option below the refresh lifetime (or add a keep-alive task); the current
  1–12 h range contradicts this decision.
- #71 — persist the token pair atomically in the coordinator's refresh path, under the lock.
- #122 — state the 4 h outage rule and the expected reauth at CKW's cap in the reauth how-to.
- #120 — ask for both redirect URIs and `offline_access` in one request; the authorize probe shows the
  Home Assistant redirect URI is not registered yet.
- #148 — run the [probe procedure](../session-lifetime.md#probe-procedure-once-a-client-id-exists) once
  a client id exists, and replace every "open" in the lifetime table with a measurement. Record the
  login and reauth timestamps: an absolute cap is invisible in the token response, so the login→reauth
  interval is the only evidence for it, and `refresh_expires_in` has to be sampled close to the cap
  before it counts down.

## Alternatives considered

**(a) `offline_access` only, no keep-alive discipline.** Request the scope, rely on the offline session
window and drop the refresh-per-request rule. Rejected: it does not remove the absolute cap (60 days by
default, if the limiter is on), it does nothing until CKW grants the scope, and a poll every 2 h has to
refresh anyway because the access token dies after 10 minutes. It is an improvement, not a substitute.

**(b) Device authorization grant.** The realm advertises `urn:ietf:params:oauth:grant-type:device_code`.
Measured on 2026-11-01, both the token endpoint and the device endpoint refuse it for `oe-wigger`:
`Client not allowed OAuth 2.0 Device Authorization Grant` and `unauthorized_client`. Rejected: it needs
the same CKW registration work as the callback, gives a worse UX (a code typed into a browser), and
still ends at the same session caps.

**(c) Store the password and use the password grant.** Headless, no browser at reauth. Rejected on three
counts: CKW documents the grant as unsupported and the endpoint answers `500`; hard rule 2 forbids
persisting the CKW password; and a stored password turns every reauth failure into a credential
exposure. It is also the path that made the OTP fallback look viable in ADR-0001, which ADR-0006
already retired.

**(d) Poll every 5 minutes.** 288 token refreshes and 576 GETs per day, so the refresh token is never
more than 5 minutes old and downtime tolerance approaches the full 6 h. Rejected: ADR-0004 already
refused 5-minute polling for data that changes once a day, the API terms allow one read per meter per
day, and it buys less than two extra hours of outage tolerance at 24× the request volume against a
rate-limited API.

**(e) Take the ~2–3 months without asking for `offline_access`.** Keep the poll and the refresh
discipline, skip the CKW request, accept the cadence. Rejected: without the scope, any outage longer
than ~4 h forces a reauth, so an instance that sleeps re-authenticates far more often than CKW's app —
and if the cap is 90 days, a sleeping instance never reaches the app's cadence at all. The ask is one
email; it is the difference between "months" and "hours".

**(f) Accept a re-login every 2–3 months and change nothing.** No diagnosis, no keep-alive discipline,
no documentation. Rejected: the cadence is acceptable, the blindness is not. Users cannot tell an
expected cap from a broken chain, the 4 h outage case stays unexplained, and a rotated refresh token
can still be lost without a trace.

## Links

- [Session lifetime and keep-alive](../session-lifetime.md) — the measured probes, the lifetime table,
  the downtime arithmetic and the probe procedure this ADR relies on
- [ADR-0001](0001-auth-oauth2-vs-pasted-code.md) — OAuth2 + PKCE, no stored password
- [ADR-0004](0004-poll-interval-and-data-lag.md) — the 2 h interval that also keeps the chain alive
- [ADR-0006](0006-auth-measured-constraints.md) — browser flow only; password and OTP paths retired
- Issues #2 (`offline_access`), #51 (reauth), #53 (options range), #71 (setup/unload and persistence),
  #120 (client request), #122 (reauth how-to), #148 (probe once a client exists)
