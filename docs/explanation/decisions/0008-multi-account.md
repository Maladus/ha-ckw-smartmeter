# 0008 — Support multiple CKW accounts as separate config entries

- Status: accepted
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

A Home Assistant instance can serve more than one CKW customer: a household with a
second meter at another address, a family member's account, or a landlord who keeps
an eye on several properties. The integration therefore cannot assume that the first
successfully authenticated customer is the only one.

Home Assistant already isolates config entries: each entry owns its own options,
devices, entities and history. The OAuth2 helper stores the token per entry, and
reauth targets exactly one entry. The open question is what one entry represents and
how a second account is kept from colliding with the first.

The CKW customer payload carries a stable customer number (`customer_number`, or
the user id when the customer number is absent) and the delivery points
(`deliverypointnumber`) below it. Both are returned by `GET /smartmeter/customer`;
neither depends on the local installation.

## Decision

One config entry represents **one CKW customer**. The customer id is the config
entry's unique id, so the same customer cannot be added twice and a second customer
creates a second entry. The entry title is `<customer id> — <city>`; the user picks a
delivery point when the account has more than one usable electricity meter, and that
delivery point is stored in the entry data.

Everything downstream stays scoped to the entry: entity unique ids are
`f"{entry_id}_{deliverypointnumber}_{key}"`, one coordinator instance runs per entry,
each entry refreshes its own token, and reauth or deletion of one entry leaves the
others untouched. This is the identity scheme already chosen in
[ADR-0003](0003-meter-device-mapping.md), now applied across accounts.

## Consequences

**Positive**

- A second CKW account is an ordinary second setup; no menu, no account switcher and
  no shared state to keep consistent.
- Statistics, entities, options and deletion stay isolated per customer, so removing
  one account cannot orphan or rewrite another account's history.
- Reauth has an obvious target: the entry whose token expired, not "the CKW
  integration" as a whole.
- The unique id uses the customer number from CKW, not the e-mail address or the
  entry title, so a renamed title does not create a duplicate.

**Negative**

- The same physical delivery point can still be configured twice through two
  different CKW customers, because uniqueness is per customer. Preventing that would
  require a second unique key on the delivery point; the entry-id-based entity ids
  would then need their own decision.
- Each entry adds an OAuth2 session and, later, a coordinator and its own poll
  traffic. Two accounts poll independently, which is correct for the API but means
  the rate limit is not shared globally.
- The title carries the customer id, which is personal data; diagnostics and
  fixtures must keep redacting it.

**Follow-ups**

- The config flow implements this scheme through `async_set_unique_id(customer_id)`
  and `_abort_if_unique_id_configured()` (#49, #151).
- The coordinator is created per entry and must not assume a singleton (#71); the
  architecture note below records that.
- If delivery points ever need to be unique across customers, that is a new ADR that
  supersedes this one and [ADR-0003](0003-meter-device-mapping.md).

## Alternatives considered

**One entry holding every CKW account.** A single setup with a list of customers and
a menu to switch between them. Rejected: it duplicates the isolation Home Assistant
already provides, makes reauth ambiguous, and turns deletion of one account into a
partial-data problem inside one entry. It also breaks the existing entity-id scheme,
which assumes an `entry_id`.

**One entry per delivery point, sharing an account.** A customer with three meters
would add the integration three times and authenticate three times. Rejected: the
OAuth consent and the token refresh would be repeated for the same account, and the
meter picker would have to be bypassed to express the choice.

**Reject a second account and keep one entry per Home Assistant instance.** Rejected:
it forces users with two CKW accounts to run two Home Assistant instances, which is
a far larger burden than the small amount of per-entry state this decision adds.

## Links

- Issues #49 (unique config entry), #151 (this ADR), #50 (meter picker), #51 (reauth),
  #52 (reconfigure); `PLAN.md` → "Decisions"
- [ADR-0003](0003-meter-device-mapping.md) — one device per delivery point, entity ids
  from the config entry
- [ADR-0001](0001-auth-oauth2-vs-pasted-code.md) — OAuth2 tokens are stored per config
  entry
