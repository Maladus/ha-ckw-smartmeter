# Reauthenticate CKW

This page answers: the CKW sensors went unavailable and Home Assistant wants me to sign in again —
what exactly do I do, and what do I lose?

Nothing is lost. One browser sign-in repairs the entry, and the entry, its entity ids, its statistic
ids, its imported statistics and its Energy-dashboard placement all survive it.

## What you see

| Symptom | Where |
| --- | --- |
| A **Reauthenticate** notification, and a **Reauthenticate** button on the CKW Smart Meter entry | Settings → Devices & services |
| Every entity of the meter is `unavailable` — including **Last update** | Developer Tools → States, the device page |
| The log shows `CKW rejected the sign-in (<reason>); reauthentication is required` — the message Home Assistant records for the failed poll | `home-assistant.log`, next to the `ConfigEntryAuthFailed` traceback |
| The log names `ConfigEntryAuthFailed`, and the entry's card shows a failed-setup error | `home-assistant.log`, the entry's card |
| A message in the UI: **CKW rejected the sign-in. Please sign in again.** | the reauth dialog, or the setup dialog when a first login was refused |

The last line is the same condition at a different moment: during setup it aborts the flow, during a
poll it marks the entry for reauthentication. Both mean the stored refresh token is dead.

## Fix it

1. Open **Settings → Devices & services**.
2. Find **CKW Smart Meter** — the entry whose title is `<customer id> — <city>` — and choose
   **Reauthenticate**. The notification takes you to the same place.
3. Confirm the **Reauthenticate CKW** form. Its text names the entry: "The sign-in for `<title>` is no
   longer valid."
4. Complete the CKW sign-in in the browser window. Use the **same CKW account** as before; another
   account aborts with "The CKW account signed in does not match the account of this entry."
5. If CKW asks for an SMS code or a second factor, enter it **once** and let the flow finish.
6. Wait for Home Assistant to reload the entry. "Reauthentication was successful." appears, the
   **Reauthenticate** button and the failed-setup error disappear, and the sensors come back on the
   next poll — within the poll interval, two hours by default.

Nothing else has to be reconfigured: the delivery point, the options, the devices and the entities
stay as they were.

## What survives

| Thing | Why it survives |
| --- | --- |
| The config entry, its id and its title | Reauth updates the entry's token in place; it does not create a second entry |
| The devices and the entity ids | They are registered under the entry's unique id, which is the CKW customer id and does not change ([entities](../reference/entities.md)) |
| The imported statistics and the history | They live in the recorder under the statistic id, which is the entity id — untouched by a reauth |
| The Energy dashboard configuration | It points at the same statistic id |
| The backfill offset | `.storage/ckw.statistics.<entry_id>` is keyed by the entry id |

## Why it happens at all

CKW caps how long one sign-in may hold, so a reauth is expected at CKW's own cadence — roughly every
two to three months, and the same cadence the official CKW app shows — and a refresh token that has
died cannot be renewed silently: only a new browser sign-in mints a new one. The measurements, the
arithmetic and the open questions are in
[session lifetime](../explanation/session-lifetime.md).

## Do not

- **Do not remove and re-add the integration.** A new entry gets a new entry id, therefore new unique
  ids, therefore new statistic ids: the old history keeps sitting under the old ids, the new series
  starts at zero, and the Energy dashboard shows a dead source beside a fresh one. Removal keeps the
  statistics by design, which makes the split permanent and invisible
  ([install](install.md#delete-the-leftover-statistics-and-entities)).
- **Do not retry the sign-in in a loop.** Repeated attempts at CKW can trigger its own lockout, and a
  failed reauth costs a full login round trip each time. One attempt, then read the log.
- **Do not lower the poll interval.** A dead refresh token is not a scheduling problem, and a shorter
  interval spends rate limit on the same daily rows — [live values next to the archive](live-and-archive.md).
- **Do not delete the application credential or edit the client id.** If a successful reauth is
  refused again immediately, or the log shows `403` or "The client id validation failed.", the problem
  is the client registration at CKW — see
  [debug a connection](debug-connection.md#403-and-the-client-id-validation).
- **Do not sign in with a different CKW account** to "see whether it works". The flow refuses it and
  the entry keeps pointing at the original account.

## Check that it worked

1. The entry's card in **Settings → Devices & services** shows no failed-setup error and no
   **Reauthenticate** button.
2. **Developer Tools → States**: **Last update** carries a timestamp within the poll interval, and
   **Last published interval** a date 1–3 days behind today.
3. Raise the log level to `debug` ([how](debug-connection.md#raise-the-log-level)) and wait for the
   next poll. The publication line appears again as soon as CKW publishes a day the entry had not
   seen:

   ```text
   new grid data published: <day>, lag <hours> h (<masked delivery point>)
   ```

   The first successful poll after a reauth only re-establishes the baseline, so this line may be
   absent once and show up with the next published day. The debug line
   `statistics: imported <n> row(s) for <delivery point>` confirms the import is writing again.
4. The backfill, if it was interrupted, resumes at its stored offset instead of restarting: look for
   `backfill: <start>..<end> done, <n>/<total>` ([backfill history](backfill-history.md)).

## Related

- [Session lifetime](../explanation/session-lifetime.md) — why the chain breaks after months, and the
  keep-alive rule that keeps it alive in between.
- [Debug a connection](debug-connection.md) — the symptom table this page expands, plus `403`,
  `429`, the log levels and the bug-report checklist.
- [Install](install.md) — what removal does to the statistics, if a reinstall is ever the answer.
- [Entities](../reference/entities.md) — the entity, unique and statistic ids that survive a reauth.
- [Request the CKW client](request-ckw-client.md) — the client id and redirect URI a refused reauth
  may point at.
- [Backfill history](backfill-history.md) — re-importing the days an outage cost.
