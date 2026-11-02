# Security policy

This page answers: how do I report a security problem in this integration, which behaviour counts as one,
and which report is really a known limit of the CKW data?

The integration never stores your CKW password. It signs in through CKW Single Sign-On, keeps the
resulting OAuth2 tokens in your local Home Assistant configuration and refreshes them silently. A
diagnostics export masks the Zählpunkt and drops every token, the customer id, e-mail and phone number.
That is the surface this policy is about.

## Supported versions

| Version | Supported | What that means |
| --- | --- | --- |
| 0.1.x | yes | Security fixes land on `main` and in the next release of this line |
| anything older | no | Upgrade to the newest 0.1.x before reporting; the older line is not patched |

Problems found on `main` are worth reporting even when no release carries them yet.

## Reporting a vulnerability

Use a private channel. A public issue, a pull request or a discussion comment is not one — a leaked token
in a public thread is a security incident of its own.

1. **GitHub Security Advisories (preferred):**
   <https://github.com/Maladus/ha-ckw-smartmeter/security/advisories/new> — private, keeps the report and
   the fix in one place, and lets a fix be released before the details are public.
2. **E-mail:** <martin.wigger@konplan.com> with `security` in the subject, when GitHub is not an option
   for you.

Say what the flaw is, which version you saw it in, how to reproduce it, and what an attacker gains.
Describe a suspected token leak by *where* it appears — the log line, the diagnostics key, the file — and
never paste the token itself; revoke it first, then report.

This is a single-maintainer project, so expect an acknowledgement within a few days rather than an
instant hotfix. A fix ships through a GitHub Security Advisory with a `CHANGELOG.md` entry; the report
stays confidential until that release.

## Scope

In scope, because they are the boundaries of the token handling and the data the integration publishes:

- **Token handling** — an access or refresh token that reaches a log line, a diagnostics export, an
  exception message, a repair issue, a `CHANGELOG.md` entry or a commit; a token written outside the
  config entry; a refresh that survives the reauth it should have triggered.
- **The reauth flow** — a flow that accepts a login or a code it should reject, a config flow that sends
  data to a host other than the CKW identity provider and the OpenEnergy API, a redirect URI that
  accepts a forged callback.
- **Diagnostics redaction** — a diagnostics dump that contains the full `deliverypointnumber`, the
  customer id, an address, an e-mail address, a phone number or a token, or one whose masking can be
  undone.
- **Credential prompts** — the integration stores no CKW password and asks for no SMS code in normal
  operation. A code path that starts collecting either one is in scope.
- A dependency whose vulnerability reaches any of the above.

Out of scope, because nothing in the integration can fix it and no report changes it:

- **Data lag.** The newest value is 1–3 days old by design: CKW reads the meter once a day and publishes
  late. See [data model](docs/explanation/data-model.md).
- **CKW-side caps and outages** — a read once per day, rate limits and `429` quotas, a publication delay,
  a client id CKW revokes or never grants, an unreachable CKW API. Those are CKW support questions,
  <openenergy@ckw.ch>, not vulnerabilities.
- The mock API and the fake Keycloak under `scripts/dev/` accept unsigned tokens on purpose. They are a
  development aid that runs on localhost, are never part of the shipped integration, and are not exposed
  to the network by this repository.

## Secret scanning

Three layers watch this repository for a leaked credential (#118), and all three are on:

| Layer | Where | What it catches |
| --- | --- | --- |
| Local hooks | `gitleaks` in the `pre-commit` stage (staged diff) and the `pre-push` stage (full history), gate G-3 | a token in the commit you are about to write, and one already in your local history |
| CI job | [`Secrets`](.github/workflows/secrets.yml), `gitleaks detect --redact` on every push to `main`, every pull request and weekly | a leak that reached the repository from a machine whose hooks were bypassed, with the finding reported redacted |
| GitHub | secret scanning with push protection, enabled on this public repository | a pushed CKW token, Home Assistant token or private key, rejected before it lands in a branch |

`.gitleaks.toml` holds the CKW token rules and the allowlist for the obviously fake fixtures
(`CH0{10,}`). A finding is handled in this order: **rotate first** (reauthenticate the integration, revoke
the credential at CKW), then remove it from the branch, then report the path it took through a private
advisory. Rewriting history does not make a published token safe.

## Never paste a credential into an issue

**Never paste an access token, a refresh token, an SMS code, your CKW password or a full Zählpunkt
(`deliverypointnumber`) into an issue, a pull request, a discussion, a log excerpt or a commit.** Redact
the Zählpunkt to its last four characters. If a token has already been published, treat it as leaked:
reauthenticate, then report the path it took.

## Related

- [CONTRIBUTING.md](CONTRIBUTING.md) — "Where to ask, where to report": question, CKW support and
  security report, side by side.
- [Debug a connection](docs/how-to/debug-connection.md) — what the integration already redacts, what the
  diagnostics export removes, and what an issue should contain instead of a token.
- [Conventions](docs/reference/conventions.md) — the redaction rules every change has to respect.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — the conduct expected in the spaces this policy protects.
