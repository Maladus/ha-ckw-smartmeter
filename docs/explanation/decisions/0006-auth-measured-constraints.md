# 0006 — Restrict authentication to the browser authorization-code flow

- Status: accepted
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

The CKW OpenEnergy portal documents the login as `grant_type:authorization_code` and states that the
password grant is not supported. The realm metadata still advertises `password`, which made the
password + SMS-OTP fallback look viable when ADR-0001 was written. Measurement on 2026-11-01 shows it
has no implementation path ([verification](../../reference/ckw-openenergy-api-verification.md),
discrepancy 5).

The authorize URL CKW documents asks for `response_mode=fragment`. Home Assistant completes the flow
at `/auth/external/callback` and reads the authorization code from the query string, so a fragment
response would never reach it.

## Decision

We pursue only the browser authorization-code flow with PKCE, implemented through Home Assistant's
`application_credentials` platform. CKW must register the redirect URI. Before implementation we verify
that `response_mode` is `query` for `/auth/external/callback`; if Keycloak insists on `fragment`, the
pasted-code fallback is required.

## Consequences

**Positive**

- One login path and one refresh path: Home Assistant's `OAuth2Session` handles the code exchange, token
  lifetimes and reauth, as ADR-0001 already chose.
- No password and no OTP is stored or typed into Home Assistant, so the hard rule on secrets still
  holds.
- The client stays a public client with no secret, so `.storage` and logs contain nothing reusable.

**Negative**

- Setup and every reauth need a browser that can reach both the Home Assistant instance and CKW; a
  headless first setup is impossible.
- The flow depends on CKW registering the redirect URI — external work we do not control.
- If CKW cannot return `response_mode=query`, every login costs a manual copy of the redirect URL and
  the authorization code lands in browser history and terminal scrollback.

**Follow-ups**

- #53 (password + SMS-OTP) is closed as not planned.
- #50 (pasted code) stays open as the mitigation if CKW cannot register the callback or cannot return
  `query`.
- #1 and #2 confirm the client registration and `offline_access`; ADR-0001 keeps the reasoning for the
  browser flow and now records that its fallback list is partially superseded.

## Alternatives considered

**Password + SMS-OTP grant.** The old fallback: `POST /otp/login`, then `grant_type=password` with the
returned OTP. Rejected because the portal says the password grant is not supported, so there is no
implementation path even though the realm advertises it; building on an undocumented grant would break
without notice. It also puts the CKW password into Home Assistant, which the project refuses.

**Device authorization grant.** The realm advertises it, but CKW has not confirmed it for our client,
and no device-flow client registration is documented. Rejected for now: no confirmed path, and it
would need the same CKW registration work as the callback.

**Pasted-code flow** (ADR-0001 alternative a, issue #50). Home Assistant shows the login URL and the
user copies the resulting `redirect?code=…` URL back into a form. Rejected as the default because it
adds a manual copy step to every login and the authorization code ends up in shell history and terminal
scrollback. Kept only as the fallback when CKW cannot register the callback or return `query`.

## Links

- Issues #1 (client registration), #2 (`offline_access`), #46–#48 (flow), #50 (pasted code), #53
  (password + SMS-OTP, closed as not planned); `PLAN.md` rows #145 and #146
- [ADR-0001](0001-auth-oauth2-vs-pasted-code.md) — the browser flow and the fallbacks this decision
  partially supersedes
- [Measured API behaviour](../../reference/ckw-openenergy-api-verification.md) — the refused password grant
  and the `response_mode=fragment` problem
