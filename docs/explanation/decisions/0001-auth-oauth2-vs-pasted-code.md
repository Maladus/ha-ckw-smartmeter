# 0001 — Authenticate with OAuth2 authorization code and PKCE

- Status: proposed
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

The CKW OpenEnergy API sits behind the CKW identity provider (Keycloak, realm `CKW-prod`); every request needs an access token. The integration has to obtain that token during setup and renew it without user interaction, on a Home Assistant instance the user cannot always reach from a terminal, and without storing a password. CKW's API terms allow one read per meter per day, which bounds how often a token may need a human.

## Decision

OAuth2 authorization code with PKCE, implemented through Home Assistant's `application_credentials` platform. CKW registers `oe-wigger` as a public client (no client secret) with the redirect URIs `https://my.home-assistant.io/redirect/oauth` and `https://<fqdn>/auth/external/callback`. The authorization code is exchanged by Home Assistant's `OAuth2Session`, which also refreshes the token pair.

## Consequences

**Positive**

- No client secret and no password is stored, so nothing sensitive can leak from `.storage` or logs; refresh, expiry and reauth state are Home Assistant's code, not ours.
- The standard reauth flow applies: `ConfigEntryAuthFailed` takes the user to CKW by a button, and any future CKW scope (`offline_access`, extra audiences) is an option in the flow, not a refactor.

**Negative**

- Setup and every reauth need a browser that can reach both the HA instance and CKW; a headless first setup is impossible.
- Without the `offline_access` scope the refresh token lives 6 h. A HA instance that is off for longer needs a manual reauth; the 2 h poll (ADR-0004) only keeps the chain alive while it runs.
- The whole flow depends on CKW registering two redirect URIs — external work we do not control.

**Follow-ups**

- Confirm `offline_access` in #2; if it is refused, the 6 h caveat goes into `data-model.md` (#125).
- #46–#48 implement flow and reauth; #50 (pasted code) and #53 (password + SMS-OTP) stay open as fallbacks only.

## Alternatives considered

**(a) Pasted redirect URL, as the BKW integration does.** Home Assistant shows the login URL, the user copies the resulting `redirect?code=…` URL back into a form. Rejected: one manual copy step per login, it cannot be tested without a real browser session, and the authorization code ends up in shell history and terminal scrollback. Kept as fallback #50 in case CKW refuses to register callbacks.

**(b) Password + SMS-OTP grant.** `POST /realms/CKW-prod/otp/login`, then `grant_type=password` with the returned `code`. Works headless, but sends an SMS per login and asks the user to type the CKW password into Home Assistant — the exact thing we refuse to store or log (hard rule 2). Rejected for that reason; kept as fallback #53 for instances that cannot reach a browser.

**(c) A bearer token in a `rest:` sensor.** No refresh at all: the token dies within 10 minutes, so the sensor is dead for 23 h 50 min of every day. Rejected as unusable, not merely inconvenient.

## Links

- Issues #1 (client registration), #2 (`offline_access`), #46–#48 (flow), #50, #53 (fallbacks); `PLAN.md` → "ADR process" and risk register
- ADR-0004 — the poll interval that keeps the 6 h refresh token alive
