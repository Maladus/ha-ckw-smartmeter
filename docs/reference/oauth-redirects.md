# Redirect URIs

This page answers: which redirect URI do I register at CKW, and what happens on the way back?

One value decides whether the login works: the `redirect_uri` Home Assistant sends in the authorize
request has to be registered at CKW character for character. This page lists the three kinds that
exist, what each one depends on, and the symptoms of a mismatch. The steps that *use* this page are
[how-to: request the CKW client](../how-to/request-ckw-client.md); the fields it belongs to are in
[configuration](configuration.md).

## The exact-match rule

CKW runs Keycloak, and Keycloak compares the `redirect_uri` of the authorize request against the
client's registered list as a string. There is no prefix match and no normalisation, so all of these
are different values:

| Sent by Home Assistant | Registered at CKW | Result |
| --- | --- | --- |
| `https://ha.example.ch/auth/external/callback` | `https://ha.example.ch/auth/external/callback` | match |
| `https://ha.example.ch/auth/external/callback` | `https://ha.example.ch/auth/external/callback/` | mismatch, trailing slash |
| `http://ha.example.ch/auth/external/callback` | `https://ha.example.ch/auth/external/callback` | mismatch, scheme |
| `https://ha.example.ch:8123/auth/external/callback` | `https://ha.example.ch/auth/external/callback` | mismatch, port |

Register the **bare URL**: no query string, no fragment, no trailing slash. Keycloak prints its
complaint on its own themed error page, in German, before it shows a login form:

```text
Ungültiger Parameter: redirect_uri
```

That page means the request itself was fine and the client exists — the URL is what CKW does not
know. It is the same message for a typo, for a value that was never sent to CKW, and for a client id
that belongs to a different customer.

## The three kinds

| Kind | What to register | Who it works for | What it depends on | When it breaks |
| --- | --- | --- | --- | --- |
| My Home Assistant service URL | `https://my.home-assistant.io/redirect/oauth` | every installation, with or without Home Assistant Cloud | the instance URL that one browser has stored for `my.home-assistant.io`, and the `my` integration (part of `default_config`) | a browser that has no instance URL stored yet asks once; a browser still pointed at an old instance sends the code to the wrong address; a browser that cannot reach the instance cannot forward the code |
| Own FQDN URL | `https://<fqdn>/auth/external/callback` | installations with an external URL, Cloud or not | the URL staying exactly what **Settings → System → Network** reports | a new domain, a move to a new installation, `http` instead of `https`, a different port, a trailing slash |
| Localhost URL | `http://localhost:8123/auth/external/callback` | a development instance on the same machine as the browser | the browser running on the machine that hosts Home Assistant | any other machine, and any CKW client that refuses a plain-`http` redirect URI |

The My Home Assistant URL is **not** a Cloud feature and needs no Nabu Casa subscription. It is a
static page on the internet that stores one URL in your browser and forwards to it; the Cloud
subscription is only one of the ways an instance can be reachable from outside your network.

## Which one Home Assistant sends

You do not pick this at login time. Home Assistant's OAuth2 helper decides, in
`helpers/config_entry_oauth2_flow.async_get_redirect_uri`:

- if the `my` integration is loaded — it is part of `default_config`, so it is in almost every
  installation — the redirect URI is `https://my.home-assistant.io/redirect/oauth`;
- otherwise it is `<the URL you browse to>/auth/external/callback`.

Both values are fixed constants in the integration's dependency, not fields of this integration: the
only lever is which ones CKW has registered. Registering both is what keeps a login working after the
user's URL changes and what makes the mock or a direct callback usable for development.

## What the My Home Assistant page does

On the way back from CKW the browser lands on `https://my.home-assistant.io/redirect/oauth?code=…&state=…`.
The page's browser title is "Link to OAuth2 Authorize Callback", the visible heading is "Link account
to Home Assistant?", and it explains that "All credentials are stored locally".

| Visit | What the user sees |
| --- | --- |
| First time in this browser | the page asks for the instance URL — `https://<fqdn>`, with the port if it is not 443 — and a **Link account** button. Nothing else |
| After the instance URL is stored | the page names the stored URL and offers **Decline** and **Link account** |
| After **Link account** | the browser is sent to `<instance>/auth/external/callback?code=…&state=…`, which is where Home Assistant completes the flow |

The instance URL is stored in that browser only: no account, no Cloud, and nothing leaves the browser
before the forward. To see or change it later, open <https://my.home-assistant.io/> — the **Home
Assistant instance** card shows the stored URL and lets you replace it. Clearing the site data for
`my.home-assistant.io` has the same effect as a fresh browser: the page asks again.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| CKW shows `Ungültiger Parameter: redirect_uri` | the value Home Assistant sent is not registered for this client | compare it with the list CKW confirmed; the two most common diffs are a trailing slash and `http` against `https` |
| The My Home Assistant page forwards to an old address, or to a page that returns 404 | the browser remembers the instance URL from before the move | open <https://my.home-assistant.io/>, correct the **Home Assistant instance** URL, and repeat the login |
| The My Home Assistant page works in one browser and not in another | the instance URL is stored per browser, not in Home Assistant | link once in the second browser, or register and use the FQDN URL instead |
| `http://localhost:8123/auth/external/callback` is refused by CKW | CKW registers HTTPS redirect URIs; a plain-`http` value is either not registered or rejected | use the mock for development, or the HTTPS callback described in the [dev instance runbook](../../scripts/dev/README.md#known-limit-no-https) |
| `localhost` works while sitting at the dev machine and fails from the laptop | `localhost` on the laptop is the laptop | register the host's reachable URL for that client, or stay on the machine |
| The login worked for months and now fails with `redirect_uri` | the instance moved to a new address while the client still carries the old one | see [A second URI needs CKW](#a-second-uri-needs-ckw) |
| The authorize page says the client is unknown or invalid | a client id problem, not a redirect problem | back to [how-to: request the CKW client](../how-to/request-ckw-client.md) |

The `state` parameter is not a redirect concern: Home Assistant puts the flow id and the redirect URI
into it and rejects a callback whose state it did not issue. A redirect URI mismatch never gets that
far — it fails at CKW, before the code is minted.

## A second URI needs CKW

Neither URI can be added by the user, and no setting in Home Assistant changes which one is sent. Every
additional or replaced redirect URI is a mail to CKW and a wait of days, so plan the set once:

- **Moving to a new domain, or to a new installation.** The old FQDN callback dies with the old
  address; either use the My Home Assistant URL, which never changes, or ask CKW for the new
  `<fqdn>/auth/external/callback` before the move.
- **Enabling Home Assistant Cloud later.** This adds no redirect URI: Home Assistant keeps sending
  the same `my.home-assistant.io/redirect/oauth`, whatever way the instance is reachable. It only
  makes the browser's forward work from outside your network.
- **Switching a Cloud installation to a direct URL.** This needs the FQDN callback registered, because
  Home Assistant stops sending the `my` URL only when the `my` integration is gone.

An already-issued client can carry both values at once. Ask for both in the first mail and the
question does not come back.

## Related

- [How-to: request the CKW client](../how-to/request-ckw-client.md) — the mail that asks for these
  values, and the reply that confirms them.
- [Configuration](configuration.md) — the redirect URI as a parameter of the integration, next to the
  client id and the scopes.
- [Tutorial: getting started](../tutorial/getting-started.md) — the login this page explains, with the
  click path and its expected result.
- [How-to: debug a connection](../how-to/debug-connection.md) — the same mismatch as one row in the
  larger symptom table, next to `403`, `429` and the token failures.
- [Session lifetime](../explanation/session-lifetime.md) — what the redirect URI does *not* influence:
  how long the login and its refresh token last.
- [Documentation map](../README.md) — where this page sits among the quadrants.
