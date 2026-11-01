# Where to get help

This page answers: I have a problem or a question — which channel is the right one, and which one wastes
everyone's time?

The full table, with the reasoning behind each row, is in
[CONTRIBUTING.md](CONTRIBUTING.md#where-to-ask-where-to-report). This page is the short version.

| You have | Go to |
| --- | --- |
| A question about using the integration, or a bug in it | [Open an issue](https://github.com/Maladus/ha-ckw-smartmeter/issues/new/choose) — the forms ask for the version, the `custom_components.ckw` log lines and the diagnostics file |
| A value that looks stale, a rejected login, `403`, `429`, a missing Energy-dashboard statistic | [Debug a connection](docs/how-to/debug-connection.md) first: it maps the symptom to its cause and the fix |
| A login CKW no longer accepts, an API-access, client-id or redirect-URI question | CKW OpenEnergy support, <openenergy@ckw.ch>. This repository cannot grant you API access |
| A feature you miss | The issue forms, after checking [GOAL.md](GOAL.md) for the out-of-scope list and [PLAN.md](PLAN.md) for what is already scheduled |
| A security problem, or a token you leaked | [SECURITY.md](SECURITY.md) — GitHub Security Advisories or e-mail, never a public issue |
| Something to report about how people behave here | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

Never post an access token, a refresh token, an SMS code, your CKW password or a full Zählpunkt
(`deliverypointnumber`) — not in an issue, not in a log excerpt, not in a commit. Redact the Zählpunkt to
its last four characters.

## Related

- [CONTRIBUTING.md](CONTRIBUTING.md) — the long version of this table, plus setup, gates and the
  pull-request checklist.
- [docs/README.md](docs/README.md) — the documentation map, when you would rather read than ask.
- [SECURITY.md](SECURITY.md) — the private reporting channels and what counts as a vulnerability.
