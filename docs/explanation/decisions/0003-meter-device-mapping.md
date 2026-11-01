# 0003 — One device per delivery point, unique ids from the config entry

- Status: proposed
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

A CKW account holds one or more delivery points (`deliverypointnumber`), each with its own meter, readings and history. Users name meters after what is printed on the device, and CKW may exchange a meter or change its model without the delivery point changing. Home Assistant keys devices, entities, statistics and history by id, so the scheme chosen here is effectively permanent — hard rule 4 makes it append-only.

## Decision

One Home Assistant device per `deliverypointnumber`, named after the meter model plus the last four digits, attached to a service device via `via_device`. The entity unique id is `f"{entry_id}_{deliverypointnumber}_{key}"`, where `key` names the measurement, and it is append-only: existing ids are never recomputed, renamed or reused.

## Consequences

**Positive**

- Per-meter history, statistics and long-term data stay together and survive a meter exchange of the device name, because the unique id does not contain the model.
- A vanished delivery point can be marked stale without touching the others (#134), a new one appears without a reload, and `via_device` groups the meters under one CKW service device.

**Negative**

- Device names carry the meter model, so a model change makes the name wrong until someone renames it; the rename is a user action, not a migration.
- `deliverypointnumber` and `entry_id` are visible in `.storage` and diagnostics, so both must be redacted in fixtures and dumps (hard rule 3).
- Two config entries pointing at the same delivery point would produce two devices; the unique config entry rule (Bronze) has to prevent that, not this id scheme.

**Follow-ups**

- Renaming the scheme, or deriving ids from the meter model, needs a new ADR that supersedes this one.
- #76 registers the devices and entities; #134 covers dynamic and stale delivery points.

## Alternatives considered

**One device for everything.** A single CKW device holding every meter's entities. Rejected: the device page becomes a flat list, per-meter history and statistics lose their anchor, and a vanished meter cannot be marked stale without hiding entities from the meters that are still healthy.

**Entity ids derived from the meter model or the meter number.** Human-readable, but the model and the physical meter change while the delivery point does not — every exchange would orphan history or force a rename of the unique id, which hard rule 4 forbids. Rejected as unstable.

## Links

- Issues #70, #76 (devices/entities), #134 (dynamic and stale devices), #133 (meter exchange); `PLAN.md` → hard rules 3 and 4
- ADR-0002 — the statistics that hang off these entity ids
