# Request the CKW client id

This page answers: how do I get the OpenEnergy client id this integration needs, what do I send CKW,
and what do I do with the answer?

The client id is the only prerequisite you cannot do yourself. CKW issues it per customer, together
with the redirect URI your Home Assistant answers on, and only then can the integration start a
login. Access is free of charge and it is the consent Art. 8a–8d StromVV requires before your
Netzbetreiber may pass consumption data to a third party — see
[the API reference](../reference/ckw-openenergy-api.md#support).

Expect days, not minutes: the form and the mail go to a human at CKW.

## Before you send

- [ ] A CKW Single Sign-On account at [id.ckw.ch](https://id.ckw.ch). This is the account you sign in
      with later; it is not the same thing as the client id.
- [ ] Your delivery point (Zählpunkt) — the 33-character number on your CKW bill.
- [ ] The redirect URI your Home Assistant answers on, from the table below.
- [ ] **Settings → System → Network** open, so you can copy the exact URL Home Assistant reports.

## Pick the redirect URI

CKW registers a redirect URI per client, and the login only returns to an exact match. Pick the row
that describes where your instance runs:

| Where Home Assistant runs | Redirect URI to register |
| --- | --- |
| Home Assistant Cloud (Nabu Casa) | `https://my.home-assistant.io/redirect/oauth` |
| Your own instance, reachable from the internet | `https://<your-fqdn>/auth/external/callback` |
| The local dev instance (`mise run dev-up`) | `http://localhost:8123/auth/external/callback` |

The path `/auth/external/callback` is fixed — Home Assistant's OAuth2 helper always completes there —
and `<your-fqdn>` is exactly the URL under **Settings → System → Network**, without a trailing slash.
A URL that differs in a subdomain, a port or a trailing slash is not the same redirect URI to CKW.

The third row is for testing the browser login against CKW from the development container. CKW
expects an HTTPS redirect URI and the dev instance serves plain HTTP, so register it only if CKW
confirms it; for day-to-day development the mock below is the faster route
([dev instance runbook](../../scripts/dev/README.md#known-limit-no-https)).

## Send the request

1. Open <https://www.ckw.ch/openenergy> and fill in the access form. It asks for your contact details,
   the delivery point and what you intend to do with the data; write "private use in my own Home
   Assistant installation" and repeat the redirect URI from above.
2. Send the form. It ends in a request at CKW, not in an automatic grant.
3. Send the mail below to **<openenergy@ckw.ch>**, with the same redirect URI. The form alone leaves
   the technical half unsaid, and the mail is what the answer is written against.

The mail has to carry four things, or CKW answers with a question instead of a client id:

- the request for an OpenEnergy **client id** for a Home Assistant installation,
- every **redirect URI** you want registered, spelled out in full,
- the **client scope `offline_access`**, without which the login dies after a few hours instead of
  lasting months ([why](../explanation/session-lifetime.md)),
- the **session-lifetime questions**: SSO Session Idle and Max, Offline Session Idle and Max, and
  whether *Revoke Refresh Token* is enabled in the realm.

Never put your CKW password, an SMS code or a token in this mail. CKW does not need any of them, and
an e-mail is not a place for a secret.

### German draft

```text
Betreff: OpenEnergy-API – Client-ID für meine Home-Assistant-Installation

Sehr geehrte Damen und Herren

Ich lese meinen CKW-Smart-Meter über die OpenEnergy-API in meiner eigenen
Home-Assistant-Installation aus (Zählpunkt <Zählpunkt>, Kunde <Kundennummer>).
Bitte richten Sie mir dafür einen OpenEnergy-Client ein:

- Client-Typ: öffentlicher Client (public client) mit PKCE, ohne Client-Secret
- Client-ID-Wunsch: oe-<name>
- Redirect-URIs:
  - https://<fqdn>/auth/external/callback
  - https://my.home-assistant.io/redirect/oauth
- Client-Scopes: openid und offline_access (offline_access ist für den
  Token-Refresh zwischen zwei Abfragen nötig)

Zusätzlich drei Fragen zur Sitzungsdauer, damit ich meinen Nutzern die richtige
Erwartung nennen kann:

1. Wie sind SSO Session Idle und SSO Session Max im Realm CKW-prod gesetzt?
2. Wie sind Offline Session Idle und Offline Session Max gesetzt, und ist
   "Offline Session Max Limited" aktiv?
3. Ist "Revoke Refresh Token" im Realm aktiv, rotiert ein Refresh-Token also
   bei jeder Nutzung?

Falls es für Tests einen Client im Realm CKW-dev gibt, wäre ich für einen
Zugang ebenfalls dankbar.

Freundliche Grüsse
<Name>
<Telefon>
```

### English draft

```text
Subject: OpenEnergy API — client id for my Home Assistant installation

Dear Sir or Madam

I read my CKW smart meter through the OpenEnergy API from my own Home Assistant
installation (delivery point <Zählpunkt>, customer <customer number>). Please
create an OpenEnergy client for it:

- Client type: public client with PKCE, no client secret
- Client id: oe-<name> would be ideal
- Redirect URIs:
  - https://<fqdn>/auth/external/callback
  - https://my.home-assistant.io/redirect/oauth
- Client scopes: openid and offline_access (offline_access is what keeps the
  login alive between two polls)

I also have three questions about the session lifetime, so I can tell my users
what to expect:

1. What are SSO Session Idle and SSO Session Max set to in the CKW-prod realm?
2. What are Offline Session Idle and Offline Session Max set to, and is
   "Offline Session Max Limited" enabled?
3. Is "Revoke Refresh Token" enabled, i.e. does a refresh token rotate on every
   use?

If there is a client for testing in the CKW-dev realm, I would be grateful for
access to that as well.

Kind regards
<Name>
<Phone>
```

Replace every `<…>` placeholder before sending, and delete the lines that do not apply — for
example the `my.home-assistant.io` redirect if you run your own instance.

## What the reply contains

A complete answer names five things:

| Part of the answer | What it is |
| --- | --- |
| A client id such as `oe-wigger` | The identifier you paste into Home Assistant. The `oe-wigger` in this repository is the maintainer's own client, not a shared one |
| The realm `CKW-prod` on `id.ckw.ch` | The identity provider the integration already points at; nothing to configure |
| The registered redirect URIs | They must match your instance exactly, or the login ends on CKW's "Ungültiger Parameter: redirect_uri" page |
| The granted scopes, `offline_access` included | The one scope the integration asks for on top of `openid` |
| A link to the OpenEnergy Client API documentation | The contract this repository transcribes in [the API reference](../reference/ckw-openenergy-api.md) |

The answer also settles the session questions, or says that CKW will not disclose them; either is
workable, and [session lifetime](../explanation/session-lifetime.md) explains what stays open then.

What is **not** part of the answer, and what you must not wait for:

- **No client secret.** CKW registers this integration as a public Keycloak client, PKCE replaces the
  secret, and Home Assistant stores an empty one ([configuration](../reference/configuration.md)).
  If CKW offers a secret anyway, ignore it — the integration never sends it.
- **No password and no SMS code.** The sign-in happens in the browser, at CKW, once per reauth; the
  integration never sees either ([ADR-0001](../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md),
  [ADR-0006](../explanation/decisions/0006-auth-measured-constraints.md)).
- **No PKCE verifier from CKW.** Home Assistant generates one per login and sends its `S256`
  challenge; there is nothing to configure.

## While you wait

The integration is not blocked on CKW for development. Two stand-ins behave like the real thing:

1. **The mock API and fake Keycloak.** `mise run dev-mock` serves the measured contract and a login
   endpoint on port 8787 that consents instantly; any client id is accepted
   ([mock README](../../scripts/dev/mock/README.md)). Point the dev instance at it with the `CKW_*`
   entries in `scripts/dev/.env` ([dev instance runbook](../../scripts/dev/README.md#mock-or-production))
   and the whole flow — login, poll, statistics import, backfill — can be exercised without CKW.
2. **The `CKW-dev` realm.** CKW's test realm at
   `https://id.ckw.ch/realms/CKW-dev` answers like production and offers `offline_access` too. It
   needs a client of its own: `oe-wigger` is not registered there, so a login attempt stops at
   `invalid_client`. Ask for a dev client in the same mail.

## Use the client id

1. Go to **Settings → Devices & services → the three-dot menu → Application credentials → Add
   application credentials → CKW Smart Meter**.
2. Paste the client id and leave the secret empty.
3. Continue with [tutorial: getting started](../tutorial/getting-started.md) — it takes the same
   client id from here.

You should now see one **CKW Smart Meter** row in the Application credentials list, and the
integration in **Add integration**. The client id is configuration, not a credential: it identifies
the client, and on its own it grants nothing without your CKW sign-in.

## Related

- [API reference, "Support"](../reference/ckw-openenergy-api.md#support) — the contact address, the
  access form and the legal basis, stated once.
- [Configuration](../reference/configuration.md) — the application credential, the redirect URI and
  the `openid offline_access` scopes as fields.
- [Session lifetime](../explanation/session-lifetime.md) — why `offline_access` and the session
  questions decide whether the login lasts hours or months.
- [Tutorial: getting started](../tutorial/getting-started.md) — the click path that follows this
  page, from install to a populated Energy dashboard.
- [Install](install.md) — the code side, and what removal costs afterwards.
- [Reauthenticate](reauthenticate.md) — what to do when the login CKW issued finally expires.
