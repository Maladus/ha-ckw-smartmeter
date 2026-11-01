# Session lifetime and keep-alive

This page answers: how long does a CKW login hold, and what keeps it alive?

A CKW login is not one token but a chain: a 10-minute access token, a 6-hour refresh token and an SSO
session behind both. The integration only ever sees the first two, and it can keep the chain alive
indefinitely while Home Assistant runs — but not across a longer outage, and not past whatever absolute
limit CKW configured. This page puts numbers on each link, separates what was measured from what is
inferred, and states the keep-alive rule [ADR-0007](decisions/0007-token-lifetime-and-keepalive.md)
sets for the coordinator.

**Sourcing.** Probes were run on 2026-11-01 with `curl`, from a workstation, without credentials. Every
request was a `GET` or a deliberately invalid `POST`: no login was attempted and nothing was created,
refreshed or revoked. No token from a real session was available, so the token lifetimes below rest on
CKW's login guide and on Keycloak's documented behaviour, not on an observed token. Two statements come
from the reporter, not from an instrument: the 2–3 month reauth cadence and the observation that the
official CKW Android app stays logged in for months. Both are labelled where they are used.

| Label | Means |
| --- | --- |
| **measured** | Observed in a response on 2026-11-01, or stated by CKW in the login guide. |
| **derived** | Follows from a measured value plus Keycloak's documented semantics. No new observation. |
| **open** | Not observable without a real client id and a logged-in session. |

## What is observable without a login

Realm metadata is public for both realms. Only the URLs differ between them; the capability sets are
identical.

```bash
curl -sS https://id.ckw.ch/realms/CKW-prod/.well-known/openid-configuration
curl -sS https://id.ckw.ch/realms/CKW-dev/.well-known/openid-configuration
```

Trimmed response for `CKW-prod` (HTTP 200; `CKW-dev` answers the same with `dev` in every URL):

```json
{
  "issuer": "https://id.ckw.ch/realms/CKW-prod",
  "token_endpoint": "https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token",
  "introspection_endpoint": "https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token/introspect",
  "grant_types_supported": ["authorization_code", "client_credentials", "implicit", "password",
    "refresh_token", "urn:ietf:params:oauth:grant-type:device_code",
    "urn:ietf:params:oauth:grant-type:jwt-bearer",
    "urn:ietf:params:oauth:grant-type:token-exchange",
    "urn:ietf:params:oauth:grant-type:uma-ticket",
    "urn:openid:params:grant-type:ciba"],
  "scopes_supported": ["openid", "basic", "service_account", "address", "roles", "acr", "email",
    "profile", "phone", "microprofile-jwt", "web-origins", "offline_access"],
  "token_endpoint_auth_methods_supported": ["private_key_jwt", "client_secret_basic",
    "client_secret_post", "tls_client_auth", "client_secret_jwt"],
  "code_challenge_methods_supported": ["plain", "S256"],
  "response_modes_supported": ["query", "fragment", "form_post", "query.jwt",
    "fragment.jwt", "form_post.jwt", "jwt"]
}
```

Three things this does **not** contain: SSO session idle, SSO session max, and any per-client setting.
Keycloak's discovery document is the OpenID Provider metadata, and session lifetimes are realm
configuration, not provider metadata. They cannot be read from outside, with or without a client id.

The token endpoint itself is reachable without a client secret, which confirms that `oe-wigger` is a
public client. It also tells the client nothing it could act on: the body names the token, not the
reason, and Keycloak routes an unknown, an expired and a revoked refresh token through the same answer.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'grant_type=refresh_token&client_id=oe-wigger&refresh_token=not-a-real-token'
```

```text
400
{"error":"invalid_grant","error_description":"Invalid refresh token"}
```

The control case shows that this answer is about the token, not about the client:

| Probe, all with `refresh_token=not-a-real-token` | HTTP | Body | Reading |
| --- | --- | --- | --- |
| `client_id=oe-wigger` | 400 | `invalid_grant` / `Invalid refresh token` | The client exists, needs no secret, and the grant is evaluated. |
| `client_id=no-such-client-8f3a` | 401 | `invalid_client` / `Invalid client or Invalid client credentials` | The 400 above is not a generic rejection. |
| `client_id=oe-wigger` in `CKW-dev` | 401 | `invalid_client` | `oe-wigger` is not registered in the test realm. |

The same probe set bounds what the client is allowed to do, and it is narrower than the realm
advertises — realm metadata lists grants, not per-client enablement:

| Probe | HTTP | Body | Reading |
| --- | --- | --- | --- |
| `grant_type=password` | 500 | `unknown_error` | No usable path, matching CKW's "not supported" and [ADR-0006](decisions/0006-auth-measured-constraints.md). |
| `grant_type=…device_code` at `/token` | 400 | `invalid_grant` / `Client not allowed OAuth 2.0 Device Authorization Grant` | Device flow is disabled for `oe-wigger`. |
| `POST /protocol/openid-connect/auth/device` | 400 | `unauthorized_client` / `…The flow is disabled for the client.` | Same, from the initiating endpoint. |
| `POST /token/introspect` with the bogus token | 403 | `invalid_request` / `Client not allowed.` | Introspection is not enabled for the client — relevant to [the probe procedure](#probe-procedure-once-a-client-id-exists). |

The legacy OTP endpoint still exists and still fails closed, which is all it is worth knowing:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  https://id.ckw.ch/realms/CKW-prod/otp/login \
  -H 'Content-Type: application/json' \
  --data '{"username":"nobody@example.invalid","password":"x"}'
```

```text
500
{"error":"unknown_error","error_description":"For more on this error consult the server log."}
```

One more non-mutating probe is worth recording because it blocks the flow before any lifetime matters:
an authorize request for `oe-wigger` with Home Assistant's redirect URI returns the login theme's error
page with `"pageId": "error"` and `"summary": "Ungültiger Parameter: redirect_uri"`. No form was
submitted. With a client that exists (see the token probes above), that message means the redirect URI
is **not registered** for it as of 2026-11-01 — the prerequisite tracked as issue #1, not a property of
the token chain ([redirect URIs](../reference/oauth-redirects.md) has the rule and the fixes). The realm
itself accepts
`response_mode=query` (see the metadata above), so the open point in ADR-0006 is about what CKW's
documented URL asks for, not about realm capability.

## The lifetime model in Keycloak terms

A Keycloak refresh token has no lifespan setting of its own. For an online session its expiry derives
from SSO Session Idle (30 min by default), for an offline session from Offline Session Idle (30 days).
The absolute SSO Session Max bounds the session independently and is **not** reflected in
`refresh_expires_in`: Keycloak reports the idle window and then rejects the refresh once the session is
past its maximum. That is why a 6-hour `refresh_expires_in` is a statement about the **session**, not
about the token — and why the value can still read 21 600 on the day the session dies.

| Link | Where it is set | Keycloak default | CKW | Status |
| --- | --- | --- | --- | --- |
| Access token | Realm → Tokens → Access Token Lifespan | 5 min | 600 s (10 min) | measured (login guide) |
| Refresh token (online session) | no own setting; bounded by SSO/Client Session Idle and Max | 30 min idle / 10 h max | 21 600 s (6 h) at issue | measured at issue |
| SSO Session Idle | Realm → Tokens | 30 min | ≥ 6 h, else the token could not report 6 h | derived |
| SSO Session Max | Realm → Tokens | 10 h | at least the longest stretch that polled without a reauth — 2–3 months if polling never paused | derived lower bound, value open |
| Client Session Idle / Max | per client, `0` = inherit realm | 0 / 0 | inherited or overridden — indistinguishable from outside | open |
| Offline Session Idle | Realm → Tokens | 30 d | unknown; a granted offline token would report ≈ 2 592 000 s, not 21 600 s | open |
| Offline Session Max | Realm → Tokens, enforced only if "Offline Session Max Limited" is on | 60 d, limiter **off** | unknown | open |
| Revoke Refresh Token / Max Reuse | Realm → Tokens | off / 0 | unknown — assume rotation | open |

The 6 h figure is the load-bearing measurement, and it cuts both ways. It proves the effective idle
window is at least six hours — Keycloak with default settings would have answered `refresh_expires_in:
1800`. It also means the guide's sample is an **online** session: an offline session created with
`offline_access` reports the offline idle window (30 days by default), two orders of magnitude larger.
The only way a real offline token reports 6 h is a CKW override of the offline idle window, which is
possible but would have to be confirmed rather than assumed.

Two details in the documented response are consistent with a hand-redacted sample rather than a real
one: `session_state` is empty, and so is `scope`. Keycloak sends both on a real exchange. The
`expires_in` and `refresh_expires_in` numbers are therefore **documented by CKW**, not independently
measured, and the scope claim — whether `offline_access` was requested or granted — is unreadable from
that sample.

The lifetime model yields a different worst case than the calendar report suggests. With no absolute
limit in play, the constraint is per outage, not per month:

| Situation | Time until a human must log in | Status |
| --- | --- | --- |
| Home Assistant runs, polls, refresh succeeds | none from the token lifetimes alone | derived |
| Home Assistant off for less than the margin below | none | derived |
| Home Assistant off for longer | one reauth per outage | derived |
| Chain kept alive continuously | at most whatever SSO Session Max / Offline Session Max allows | open |

## Arithmetic of the refresh chain

The access token and the poll interval are mismatched by a factor of twelve, and that decides the whole
design.

| Quantity | Value | Consequence |
| --- | --- | --- |
| Access token | 600 s = 10 min | — |
| Poll interval (ADR-0004) | 7 200 s = 2 h | 12× the access token lifetime |
| Share of the interval a token covers | 600 / 7 200 = 8.3 % | 91.7 % of the time the stored access token is already dead |
| Refresh token | 21 600 s = 6 h | 3 poll intervals |
| Refreshes per day, 2 h poll | 12 | one per poll, not an optimisation |
| Request budget | 24 GETs + 12 token calls per day | negligible against the 4-requests-per-second limit |

So a refresh before **every** request is not caution, it is the only way a request can succeed. Any
design that refreshes "when the token is about to expire" at poll time refreshes on 100 % of polls.

The chain itself: each successful refresh extends the session — and, with rotation on, mints a new
refresh token — to a fresh 6 h window. So what matters is the time since the **last successful
refresh**, not the age of the session. With a 2 h poll, that age is at most 2 h when the chain is used,
leaving at least 4 h of headroom.

| Refresh lifetime | Poll interval | Downtime tolerated, worst case | Downtime tolerated, best case | Note |
| --- | --- | --- | --- | --- |
| 6 h | 2 h (decided) | 4 h | ~6 h | worst case: HA dies just before the poll that would have refreshed |
| 6 h | 1 h | 5 h | ~6 h | more requests, same chain |
| 6 h | 4 h | 2 h | ~6 h | still works, no longer comfortable |
| 6 h | 6 h | 0 | ~6 h | the third refresh lands on the expiry boundary |
| 6 h | 12 h (current option maximum) | 0 — the token is dead 6 h before the next poll | — | the options range cannot exceed 6 h without a separate keep-alive |
| 30 d idle, 60–90 d session max (`offline_access` granted) | 2 h | up to the cap — weeks, not hours | — | the cap, not the token, ends the session: one mandatory reauth at it |

The `offline_access` row is the "survives weeks of HA downtime" case: the refresh token stops tracking
the 6 h SSO idle window and tracks the offline window instead, so a three-week holiday is survivable.
The note in that row is the sting: the absolute cap is *reached* by a chain that never idles, so
`offline_access` converts a fragile 4 h margin into a dependable 2–3 month cadence — it does not make
the login permanent.

Two consequences follow for the refresh call itself:

- **A refresh token may be single-use.** Keycloak can be configured to revoke the presented refresh
  token on every use ("Revoke Refresh Token"). If that is on, the response is the only copy of the
  successor; a crash between receiving it and writing it to the config entry ends the chain. The new
  token must be persisted before the next request — not in a local variable that dies with the
  coroutine.
- **Two refreshes at once lose the chain.** With rotation on, the loser presents a token the winner has
  already revoked and gets `invalid_grant`. Entities reloading in parallel, a manual refresh next to the
  scheduled poll, or a second HA instance restored from a backup are all enough. One lock, one refresh.

## Why "every 2–3 months" is expected

The cadence is a user report, not an instrumented measurement — and it is also what CKW's own client
does. Read it as this realm's normal session lifetime rather than as a defect: a client of this realm
gets on the order of two to three months, and the job is to reach that, not to beat it.

The arithmetic points the same way. An offline Keycloak session has 30 days of idle time and a 60-day
maximum by default; an SSO Session Max configured to about 90 days lands in the same range. Both are
inside "2–3 months". Neither is consistent with the 6 h window our client reports: a 6 h
`refresh_expires_in` is an online session, so `oe-wigger` most likely lacks `offline_access` — see
issue #2. CKW's app is probably on the offline path, our client is not, and the gap between them is the
gap between "months" and "a few hours of outage".

| # | Reading | Fits the 6 h token? | What it predicts |
| --- | --- | --- | --- |
| H1 (leading) | An absolute cap on the session: offline max 60 d, or SSO Session Max ~90 d | yes: the response reports the idle window, so an absolute cap is invisible there | reauth at a fixed distance after the **login**, independent of polls and restarts, with `refresh_expires_in` still reporting 21 600 on the last successful refresh |
| H2 | Ordinary outages: HA off or unreachable for more than the 4–6 h margin | yes | reauth correlates with an update, a power cut or a holiday — and, without `offline_access`, happens far more often than every 2–3 months |
| H3 | Periodic revocation: password change, admin logout, IdP deployment | yes | no pattern; the reauth follows an event at CKW |

H1 is the only reading that produces a *regular* 2–3 month cadence with uninterrupted polling. It also
means the cadence will not be engineered away: a shorter poll interval cannot move an absolute cap, and
`offline_access` under a 60-day cap reaches it rather than removing it. It requires that SSO Session Max
at CKW is far above the 10 h default — a continuously refreshed 6 h token could not survive 60 days
otherwise. That is a derived conclusion, not a measured one.

What the token response cannot show is the cap itself. Keycloak reports the idle window in
`refresh_expires_in`, not the distance to the session maximum, so three refreshes can return 21 600
each and the next one can still fail. A value that counts down would prove a fixed lifespan; a constant
value proves only that the idle window is the nearer bound. An absolute cap therefore shows up in
exactly one place: the wall-clock distance between the login and the reauth that follows it.

Restarts and HA updates are not the cause of a *regular* cadence, as long as they finish within the 4 h
margin — the tokens live in the config entry and survive them (issue #71). They are the cause of the
*irregular* reauth: without `offline_access`, every outage longer than the margin costs a login, which
is why an instance that sleeps reauthenticates far more often than CKW's app.

To confirm which cap applies, record the wall-clock time of every successful refresh and of the
original login, the time of the reauth, and `refresh_expires_in` at each refresh. The
[probe procedure](#probe-procedure-once-a-client-id-exists) turns that into a decision between H1's two
caps and H2/H3; the login→reauth distance is the only measurement that can confirm the cap.

### Reference point: CKW's own app

- **Observed** (user report, not verified by us): the CKW Android app keeps the same account logged in
  for roughly 2–3 months, and appears to perform some kind of key rotation — whether refresh tokens,
  signing keys or credentials is unclear.
- **What it implies**: the realm's own ceiling is on the order of months, not years. A client that
  stays logged in for months on this realm is consistent with an offline session (30 d idle, 60 d max)
  or an SSO Session Max near 90 days, and it makes 2–3 months the realistic target for us too.
- **What it does not imply**: our client inherits nothing. `oe-wigger` is a different OAuth client with
  its own client scopes and client session settings; the app may hold `offline_access` where we do not,
  and a native app can also re-authenticate silently from credentials in the OS keystore, so "no
  visible login" does not prove the session survived. Concrete question for CKW with issue #2: *which
  client scope and session settings does your own app use, and why does it survive months when a 6 h
  refresh token does not?*

## Target we design for

Parity with the CKW app: at worst one browser re-login every ~2–3 months, and earlier only when Home
Assistant was offline long enough to lose the refresh chain — a few hours without `offline_access`,
weeks with it. That is the number to state in the docs and to measure against, not "never".

A login that never expires is not achievable with the flows available to us: CKW caps the session, the
client cannot extend it, and the headless alternatives are refused for this client (password grant
`500`, device grant `unauthorized_client`). A design that promises permanent silence would promise
something CKW's own app does not deliver.

## The keep-alive rule

The rule ADR-0007 sets for the coordinator, stated once here:

1. **Refresh before every request** when the access token has less than 60 s left (`REFRESH_SKEW`),
   plus once at the start of every poll. A refresh that is not needed costs nothing; a request with an
   expired token costs a 401 and a needless reauth path.
2. **Poll every 2 h** (ADR-0004), which keeps the refresh-token age under a third of its lifetime. The
   poll is the keep-alive; no separate timer is needed at 2 h.
3. **Persist the token pair in the config entry immediately after every refresh**, and only then use
   it. One refresh at a time, guarded by a lock, so a rotated refresh token is never overwritten by a
   stale one.
4. **Cold start with a dead refresh token is terminal.** `invalid_grant` on refresh maps to
   `ConfigEntryAuthFailed`, which offers one reauth and nothing else. No retry, no backoff, no second
   attempt with the old token.
5. **Reauth is the browser flow**, started by the user, once. The integration never re-requests an
   authorization code on its own and never loops.
6. **No secret beyond the tokens.** No CKW password, no OTP, no copying a token anywhere; a valid
   refresh token is never replaced by a fresh login.

What this rule does not do, deliberately:

- It does not refresh in a background task racing the poll. Two refresh paths are how a rotation gets
  lost.
- It does not treat `401` from the API as a reason to log in again. It is a reason to *refresh* once;
  only a failed refresh is a reauth.
- It does not promise silence. Parity with CKW's app — one reauth per ~2–3 months at worst — is the
  target; a login that never expires is not achievable with the flows available, and a reauth at CKW's
  cap is expected behaviour, not a bug to hide.

One gap this analysis exposes: the options flow allows 1–12 h, and anything at or above 6 h breaks the
chain between polls without `offline_access`. Either the maximum drops below the refresh lifetime, or a
keep-alive refresh runs independently of the poll. That is a follow-up, not part of this page.

## Probe procedure once a client id exists

Run this when a real client id and one successful login are available. It answers which cap applies,
whether the cause is an outage or a revocation, and turns every "open" above into "measured". It needs
no special tooling: a token, `curl`, and a clock.

1. **Record the login.** Note the wall-clock time, `expires_in`, `refresh_expires_in`, `scope` and
   `session_state` from the authorization-code exchange. A `refresh_expires_in` near 2 592 000 means
   `offline_access` was granted; near 21 600 means it was not.
2. **Decode the access token without verifying it** — if it is a JWT — and confirm `exp - iat = 600`,
   then note `sid`, `azp` and `session_state`. Comparing `sid` before and after a refresh is the
   cheapest way to see whether the session survived or was replaced. An opaque access token is itself a
   finding: it rules out client-side expiry arithmetic.
3. **Refresh three times, ten minutes apart**, and record `expires_in`, `refresh_expires_in` and
   `session_state` each time:

   ```bash
   curl -sS -X POST https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token \
     -H 'Content-Type: application/x-www-form-urlencoded' \
     --data-urlencode 'grant_type=refresh_token' \
     --data-urlencode 'client_id=oe-wigger' \
     --data-urlencode "refresh_token=$REFRESH_TOKEN"
   ```

   Do **not** paste real tokens into a shell that records history or into an issue; use an env var and
   `--data-urlencode`, as above, and unset it afterwards.
4. **Read the trend, not the level.** Keycloak computes `refresh_expires_in` from the idle window, not
   from the distance to SSO Session Max, so the value can read 21 600 on every refresh and the session
   can still die a day later. Three consecutive refreshes therefore detect a **fixed expiry**, not a
   session maximum:

   | Pattern across three refreshes, 10 min apart | Reading |
   | --- | --- |
   | ≈ 21 600 each time | Sliding idle window. No fixed refresh lifespan visible; SSO Session Max is invisible here. |
   | ≈ 21 000, then 20 400 | A fixed deadline counting down by the elapsed time: a fixed refresh lifespan or an offline max. The session dies at that deadline however often it refreshes. |
   | The third refresh fails with `invalid_grant` although the second still reported a large value | The absolute cap is nearer than the idle window; this is the cap showing itself. |
   | ≈ 2 592 000 | An offline session: `offline_access` was granted. |

   A session maximum only becomes visible once the remaining time to it is **shorter than the idle
   window**. So repeat the three refreshes when the session is close to the suspected cap — the day
   before the expected reauth, not right after login — and the reported value starts counting down from
   the true remaining time, which dates the cap exactly. Until then, only step 6 can see it.
5. **Introspect once.** The endpoint is currently refused for `oe-wigger`
   (`403` / `Client not allowed.`), so this step needs CKW to enable introspection or a confidential
   test client. It returns `exp`, `iat`, `active` and `session_state` for the access token, and it is
   the only way to see server-side state rather than a self-reported number:

   ```bash
   curl -sS -X POST https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token/introspect \
     -u 'CLIENT_ID:CLIENT_SECRET' --data-urlencode "token=$ACCESS_TOKEN"
   ```

6. **Correlate one reauth.** When the next reauth happens, compare its timestamp with the last
   successful refresh and with the original login. Hours after the last refresh points at H2 (an
   outage); exactly N days after the login points at H1; no relation to either points at H3.
7. **Repeat in `CKW-dev`** only if that realm has a client for us — `oe-wigger` does not exist there,
   so the test realm currently cannot answer any of this.

## Open questions

- **SSO Session Idle and Max in `CKW-prod`.** Not in the discovery document and not inferable from
  outside. The 6 h refresh token bounds idle from below; the 2–3 month report bounds Max from below.
- **Whether `offline_access` is granted, and with which idle/max.** The guide's 6 h figure suggests it
  is not, or that CKW overrode the offline idle window. Requested in issue #2.
- **Whether refresh tokens rotate.** "Revoke Refresh Token" state is realm configuration. Until it is
  known, the design must assume rotation.
- **Which cap produces the 2–3 month cadence.** Not instrumented: the offline max (60 d) and SSO
  Session Max (~90 d) are both consistent with the report, and neither is visible in the token response.
- **Whether a reauth is a session limit or a revocation.** Only the timestamp correlation in step 6
  separates them.

The open questions about `Revoke Refresh Token` and the realm's session numbers stay open; they are also
on the list of questions to CKW in issue #2, and none of them blocks the design above — the keep-alive
rule works under either answer, and the worst case is a reauth at the cap.

## Related

- [ADR-0007](decisions/0007-token-lifetime-and-keepalive.md) — the decision this analysis supports
- [ADR-0001](decisions/0001-auth-oauth2-vs-pasted-code.md) · [ADR-0006](decisions/0006-auth-measured-constraints.md)
  — browser flow only, no password, no OTP
- [ADR-0004](decisions/0004-poll-interval-and-data-lag.md) — the 2 h poll that doubles as keep-alive
- [API reference](../reference/ckw-openenergy-api.md) · [Measured API behaviour](../reference/ckw-openenergy-api-verification.md)
  — the contract and the probes behind it
- [Data model](data-model.md) — the update cadence, and the option range that conflicts with the 6 h token
- [Conventions](../reference/conventions.md) — redaction rules that apply to every token above
- [Reauthenticate](../how-to/reauthenticate.md) — what a user sees when the chain finally breaks
