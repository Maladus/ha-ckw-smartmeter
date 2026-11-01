# Debug a connection

This page answers: when a CKW sensor is unavailable, a value stops moving or the log shows an error,
what is the cause and which steps fix it?

Symptom first. Each section names what you see, why it happens and what to do. The explanations behind
the delay and the login live in [data model](../explanation/data-model.md) and
[session lifetime](../explanation/session-lifetime.md); this page only acts on them.

## Quick symptom table

| Symptom | Most likely cause | Go to |
| --- | --- | --- |
| Values are `unavailable` or stopped changing | Normal 1–3 day publication lag, or a failed poll | [Values look stale](#values-look-stale-or-are-unavailable) |
| A repair issue says CKW has not published since a date | CKW stopped publishing, or the meter vanished | [Values look stale](#values-look-stale-or-are-unavailable) |
| A "Reauthenticate" notification, or `ConfigEntryAuthFailed` in the log | The refresh token is dead; a real sign-in is needed | [The login is rejected](#the-login-is-rejected) |
| `403` or "The client id validation failed" in the log | The CKW client id lost its API role or audience | [`403` and the client id validation](#403-and-the-client-id-validation) |
| `429` or a quota message | The integration or a running backfill hit CKW's rate limit | [`429` and quota](#429-and-quota) |
| The 730-day history is incomplete after a restart | The backfill resumed, or the option is off | [The backfill did not finish](#the-backfill-did-not-finish) |
| A CKW sensor is missing from the Energy dashboard | Wrong statistic id, no recorder, or no imported rows | [Statistics missing in the Energy dashboard](#statistics-missing-in-the-energy-dashboard) |
| You need more detail for a bug report | The default log is too quiet | [Raise the log level](#raise-the-log-level) |

## Values look stale or are unavailable

The newest CKW value is normally **1–3 days old**. CKW reads the meter once a day and publishes late,
so a flat tail or a day that is missing at the right edge of the graph is expected. A value that has
not moved since yesterday is not a failure by itself.

1. Open **Developer Tools → States** and find the meter's diagnostic entities, for example
   `sensor.smart_1234_last_update` and `sensor.smart_1234_last_published_interval`.
   - `last_update` is when the last poll succeeded. It should be within the poll interval (2 h by
     default).
   - `last_published_interval` is the end of the newest day CKW has published. It should be 1–3 days
     behind today.
2. Open **Consumption total** and read its `publication_delay_h` attribute. That is the age of the
   newest published interval in hours. 24–72 h is normal; it moves when CKW publishes.
3. Search the log for the coordinator's publication line:

   ```text
   new grid data published: <day>, lag <hours> h (<masked delivery point>)
   ```

   One line per newly published day at `INFO` is normal. No new line for several days is the signal to
   keep reading.
4. Check whether a repair issue is open under **Settings → System → Repairs**. The integration raises
   `ckw_<entry_id>_stale_publication` when the newest published interval has not advanced for **4
   days**. That is a real stall, not the normal lag.
5. Check the log for the matching warning. A lag above **96 h** logs:

   ```text
   the newest CKW interval for <delivery point> is <n> h old, more than the expected 96 h:
   CKW has not published grid data yet
   ```

   A stall of 4 days logs:

   ```text
   the newest CKW interval for <delivery point> has not advanced for 4 days;
   check whether CKW stopped publishing
   ```

If `last_update` is current and only the published day is old, nothing is broken: wait for CKW. If
`last_update` itself is old, every sensor of that meter is unavailable and the poll is failing — read
the warning once, then continue with the login, `403` or `429` section below.

If `last_published_interval` is old **and** a repair issue is open, do not reinstall the integration
and do not trigger a manual backfill first. Check the CKW portal for the meter. If the portal also has
no newer day, the data is missing at CKW; the issue clears by itself when CKW publishes again.

## The login is rejected

Symptom: a **Reauthenticate** notification, a repair entry, an entity that went unavailable with
`ConfigEntryAuthFailed` in the log, or a sign-in error in the UI. The access token is refreshed
silently, but the refresh token eventually dies (CKW caps a session at roughly 2–3 months) and a dead
refresh token cannot be refreshed. Only a new browser sign-in repairs it.

1. Open **Settings → Devices & services**.
2. Find the **CKW Smart Meter** entry. It shows a **Reauthenticate** button (or a notification sends
   you there).
3. Choose **Reauthenticate**, confirm the **Reauthenticate CKW** form, then complete the CKW sign-in in
   the browser window. Use the same CKW account as the entry; a different account aborts with
   "The CKW account signed in does not match the account of this entry."
4. Wait for the entry to reload. The sensors that were unavailable come back on the next poll, and the
   imported history is kept.

If CKW asks for an SMS code or a one-time password, enter it **once** and let the flow finish. Do not
repeat the sign-in, do not add the integration a second time and do not delete the entry to "reset"
it: those actions do not refresh a dead token, and repeated sign-in attempts can trigger CKW's own
lockout. A second entry is also refused because the CKW customer id is the unique id.

A successful reauth replaces the token pair; nothing else needs to be reconfigured. If the same
notification returns immediately after a successful browser sign-in, read the log for the next section
instead of trying again. The same repair, step by step and with what it keeps: [reauthenticate](reauthenticate.md).

## `403` and the client id validation

Symptom: the log or the error message contains `403` or **"The client id validation failed."**

- During a **token refresh**, a `403` means the login chain is dead: use
  [The login is rejected](#the-login-is-rejected) once.
- If the `403` survives a successful reauth, or the log shows the client-id sentence on a normal API
  call, the **client id** is the problem, not the Home Assistant login. The client id CKW issued has
  lost its API audience or role, or its registration no longer matches the redirect URI.

Do not reinstall the integration, do not delete the application credential and do not retry the sign-in
in a loop. Contact CKW at **<openenergy@ckw.ch>**, quote the client id and the sentence from the log,
and ask them to check the API role and audience of that client. The support address and the
registration path are in [the API reference](../reference/ckw-openenergy-api.md#support); the
application credential itself is described in [configuration](../reference/configuration.md).

## `429` and quota

Symptom: the log shows `429`, "Quota has been exceeded" or `CKW is rate limiting requests`. The public
CKW host allows about **four requests per second**, and the integration paces itself against the
`x-ratelimit-*` headers: it waits for the window rather than retrying in a loop.

1. Read the warning. A single `429` followed by a successful next poll is the normal, paced behaviour.
2. If `429` keeps returning, check whether the **730-day backfill** is running. The log shows chunks:

   ```text
   backfill: <start>..<end> done, <n>/<total>
   ```

   A backfill requests 30 days per chunk with a one-second pause, so it is the expected reason for
   sustained quota use on a fresh setup.
3. To stop the backfill from starting on the next setup, open the entry's **Configure** dialog and
   turn off **Import the 730-day history on setup**. The change takes effect on the next reload or
   restart; a backfill already running is not cancelled by the option alone.
4. To resume later, either turn the option back on and reload, or call the **`ckw.backfill`** action
   with `days: 730`. A resumed job continues at its stored offset; `force: true` restarts the sum from
   the first imported day and is only for repairing a damaged series.
5. If `429` appears without a backfill, leave the poll interval at its default. Lowering it to chase
   freshness returns the same daily rows and only spends quota — see
   [live values next to the archive](live-and-archive.md).

The fields of the action are in [configuration](../reference/configuration.md#action-ckwbackfill).

## The backfill did not finish

Symptom: the Energy dashboard has only a few days or months of history, or the log's chunk counter
stopped after a restart. This is usually a resume, not a loss.

The backfill stores its offset in `.storage/ckw.statistics.<entry_id>`. It saves after every chunk, so
a restart, a Home Assistant shutdown or a failed chunk resumes at the first day that was not imported
yet instead of starting over.

1. Search the log for the resume line at `INFO` after setup:

   ```text
   statistics: starting the 730-day backfill for <title> at <date>
   ```

   The value after "at" is the first day still owed (or "the first day" on a fresh job). If the job is
   already done, the line is absent.
2. At `DEBUG`, the preparation line shows the stored state:

   ```text
   statistics: ckw.statistics.<entry_id> state for <title> is pending, next day <date> (<n>/<total>)
   ```

   `done` instead of `pending` means the job finished. The file is bookkeeping only; do not edit it by
   hand.
3. Watch the `INFO` chunk lines while it runs, and the final line:

   ```text
   backfill: finished <n>/<total> chunk(s) for <title>, <rows> row(s) imported
   ```

4. If a chunk failed, the log says where it stopped and that the offset is kept:

   ```text
   backfill: stopped at <date>, <n>/<total> chunk(s) done; the offset is kept
   ```

   Fix the cause (login, `429`, connection) and reload the entry or call `ckw.backfill` again. It
   continues from that date.

5. To see what actually arrived, open **Developer Tools → Statistics** and search for the meter's
   total statistic, for example `sensor.smart_1234_consumption_total`. The rows are the imported days,
   not the backfill's internal cursor.

## Statistics missing in the Energy dashboard

Symptom: the CKW device and its sensors exist, but the Energy dashboard's electricity picker does not
list them, or a graph stays empty.

1. Confirm the **statistic id**. It is the entity id of **Consumption total**, for example
   `sensor.smart_1234_consumption_total`. The Energy dashboard reads statistics, not states; a renamed
   entity can therefore leave its history under the old id.
2. Check that the **recorder** is running. The integration declares `after_dependencies: ["recorder"]`
   but does not start it for you; without the recorder there are no long-term statistics and no Energy
   dashboard data.
3. Open **Developer Tools → Statistics**, search for the statistic id and check that rows exist and
   that the sum is present. An empty result means the import never wrote a row: run `ckw.backfill` for
   that entry.
4. Add the sensor in **Settings → Dashboards → Energy → Electricity grid**. Add **Consumption total**,
   not the HT/NT sensors and not **Consumption yesterday**; those are subsets or single daily values
   and double-count if added beside the total. The eligible entities are listed in
   [entities](../reference/entities.md#energy-dashboard).
5. Do not expect `recorder.purge_keep_days` to fix missing long-term statistics. That setting controls
   how long entity **states** are kept and matters for the opt-in quarter-hour sensor; the daily rows
   imported as long-term statistics are not removed by it. If you raised it for the quarter-hour
   sensor, see [keep the quarter-hour history](keep-quarter-hour-history.md).
6. If you deleted the statistic in **Developer Tools → Statistics** by mistake, run `ckw.backfill`
   with `force: true` to rebuild the cumulative sum for that statistic id.

## Raise the log level

The integration logs under the `custom_components.ckw` logger. Without a setting, `WARNING` and above
reach `home-assistant.log`; the useful `INFO` and `DEBUG` lines do not.

To change it without a restart, call the action from **Developer Tools → Actions**:

```yaml
action: logger.set_level
data:
  custom_components.ckw: debug
```

To keep it across restarts, add this to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.ckw: debug
```

Set it back to `info` or `warning` afterwards; debug logs every poll and every merge decision.

| Level | What it tells you | Examples |
| --- | --- | --- |
| `INFO` | Normal milestones and the one-time recovery of a meter | New grid data published with lag; a meter came back or went stale; the backfill started, finished a chunk or finished; the sign-in was rejected (an `ERROR` line from the backfill) |
| `WARNING` | A poll or a chunk failed, or a threshold was crossed | First failed poll; lag above 96 h; no new day for 4 days; a backfill chunk failed; `429`; a second backfill was refused |
| `DEBUG` | Repetition and detail, safe to leave off | Repeated failures with the same cause; rate-limit waits; corrected intervals; statistics imports that were unchanged or skipped; repair issues raised or cleared; the stored backfill offset |
| `ERROR` | The backfill cannot continue without you | CKW rejected the sign-in; the backfill asks for reauthentication |

### What is redacted

- A delivery point (`deliverypointnumber`, Zählpunkt) appears only as its **last 4 characters**, for
  example `1234`; the full 33-character number is never logged.
- Access tokens, refresh tokens, the CKW password and SMS codes are never logged. The OAuth2 helper
  stores them in the config entry, and the integration only reads them through the session.
- The customer id and addresses are not logged either.

The [conventions](../reference/conventions.md#redaction) list the same set and the diagnostics rule.

### Download diagnostics

The integration ships a diagnostics download: **Settings → Devices & services → CKW Smart Meter →
three-dot menu → Download diagnostics**. The dump masks the delivery point to its last four characters,
replaces the customer id with a short hash, and drops e-mail, phone and every token, keeping the
two token booleans and the expiry time only. It also carries the coordinator's health and the
backfill's progress under `statistics` (`done`, `running`, `days`, `next_start`, `chunks`). If the menu
entry is absent, the entry is not loaded — check the entry's card first.

## What to attach to a bug report

Open an issue at <https://github.com/Maladus/ha-ckw-smartmeter/issues> with:

- The symptom in one sentence: which entity is wrong, and since when.
- The output of **Developer Tools → States** for **Consumption total** and
  **Last published interval**, including `publication_delay_h`, `last_published_interval`,
  `quality_intervals` and `stale_since` if present.
- The relevant **debug** log lines from at least one full poll, including the `new grid data
  published` line, the failure warning and any `backfill:` line.
- The Home Assistant version, the integration version from **Settings → Devices & services → CKW Smart
  Meter**, and whether **Import the 730-day history on setup** is on.
- The first four characters of the Zählpunkt and the entry title, so the meter can be identified
  without publishing the full number.
- For a `403`, the client-id sentence, sent to CKW at <openenergy@ckw.ch> rather than posted in a public
  issue.

Never attach an access token, a refresh token, a client secret or the full Zählpunkt.

## Related

- [Reauthenticate](reauthenticate.md) — the symptom of a rejected login from the user's side: what
  survives, and the three actions not to take.
- [Quality scale](../reference/quality-scale.md) — the `docs-troubleshooting` rule this page closes.
- [Data model](../explanation/data-model.md) — why 1–3 days is normal and what the quality flags mean.
- [Session lifetime](../explanation/session-lifetime.md) — why a refresh token dies and why a browser
  sign-in is the only repair.
- [Configuration](../reference/configuration.md) — the poll interval, the backfill option and the
  `ckw.backfill` action.
- [Entities](../reference/entities.md) — the entity and statistic ids, the attributes and the Energy
  dashboard eligibility.
- [Examples](../reference/examples.md) — automations that watch the lag for you.
- [Keep the quarter-hour history](keep-quarter-hour-history.md) — the retention setting named above.
- [Live values next to the archive](live-and-archive.md) — why lowering the poll interval does not
  make the data fresher.
