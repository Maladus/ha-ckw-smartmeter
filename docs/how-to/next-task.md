# Find the next task and get reminded

This page answers: which task can start now, and how do I get reminded?

`scripts/next_tasks.py` joins `PLAN.md` with the GitHub issue tracker and prints
three lists: what can start, what is blocked and by what, and where the plan and
the tracker disagree. It only reads: no issue is labelled, closed or commented,
and the single `gh issue list` call is its only network access.

## Run the check

```bash
mise run next                  # STARTABLE, BLOCKED, DRIFT
mise run next --limit 20       # more of STARTABLE (default: 5)
mise run next --json           # complete machine-readable report
mise run next --notify         # ... and ping when work can start
mise run next-check            # CI gate: non-zero when DRIFT is not empty
```

| Section | What it lists |
| --- | --- |
| **STARTABLE** | Open issues whose dependencies are all closed and which are not labelled `status: blocked`. `status: ready` first, then by issue number. |
| **BLOCKED** | Every open issue that still waits on an open issue, with the numbers doing the blocking. |
| **DRIFT** | The disagreements: a `status: ready` label on a blocked task, a closed issue whose plan row is not ✅, a ✅ row whose issue is still open, a plan row without an issue. |

Exit status: `0` on success, `1` when `--check` finds drift, `2` when the check
cannot run at all (no `gh`, no `PLAN.md`, unreadable `gh` output). Without `gh`
the script prints `next_tasks: gh CLI not found` and stops.

## Install the timer

The systemd user timer runs the check every 30 minutes and pings you when
something can start:

```bash
bash scripts/dev/install-timer.sh
systemctl --user list-timers | grep ckw
```

The installer substitutes this checkout's absolute path into
`scripts/dev/systemd/ckw-next.service` and `ckw-next.timer` and writes them into
`~/.config/systemd/user/`, reloads the user manager, enables and starts the
timer, then prints the timer list. It is idempotent: re-run it after a
`git pull` that changed a unit, or after moving the checkout.

The timer fires 5 minutes after boot and then every 30 minutes
(`OnUnitActiveSec=30min`), and `Persistent=true` catches up after the machine was
off. Each tick runs `mise run next --notify` inside the repository.

To run it while you are logged out, enable lingering once:
`loginctl enable-linger "$USER"`. Remove the timer again with
`bash scripts/dev/uninstall-timer.sh`.

### Machines without systemd user units

One crontab line, the same command:

```cron
*/30 * * * * cd /path/to/repo && mise run next --notify >> .dev/next.log 2>&1
```

`.dev/` is gitignored, so the log never reaches a commit.

### Where the notification goes

`--notify` uses the first channel that exists:

1. `workmux send ckw-homeassistant "next: ..."` - reaches the coordinator agent,
   so a scheduled tick pings a person even when nobody sits at the desktop.
2. `notify-send` - a desktop notification, when `workmux` is not installed.
3. stdout - a plain `next_tasks: next: ...` line, which is what a headless CI or
   cron box shows in its log.

It only fires when STARTABLE is not empty **and** DRIFT is empty, so a nudge
never hides a contradiction. `--notify-handle NAME` pings a different workmux
agent; the default is `ckw-homeassistant`.

## Fix DRIFT

| Kind | Meaning | Fix |
| --- | --- | --- |
| `ready-not-startable` | The issue carries `status: ready` but still depends on an open issue. | Either remove the label (back to `status: backlog`) or state in the plan why the dependency no longer blocks. |
| `closed-not-done` | GitHub closed the issue, the plan row has no ✅. | Add ✅ to the plan row in the same pull request that carries the work, or reopen the issue if it is not finished. |
| `done-row-open-issue` | The plan says ✅, the issue is still open. | Close the issue (with the merge that finished it), or remove the ✅ if the work is not done. |
| `plan-row-without-issue` | A task table row names an issue number the tracker does not know. | Create the issue, or correct the number in the plan. |

Fix the plan row first, then the label: the plan is authoritative for scope and
dependencies, the issue for progress. One task, one row, one issue.

The drift list is deliberately complete, not limited: it is the list you work
off, and hiding entries would let a contradiction sit for months. On a fresh
clone it may be long - that is the backlog of labels and rows to reconcile, not
a broken check.

## Why the plan stays authoritative

Dependencies are reviewed in the pull request that changes `PLAN.md`, so the
`Deps` column is the version a second person agreed to. A label is one click and
changes without review, which is exactly how an issue ends up marked ready while
it still waits on an open dependency. The checker therefore trusts the plan for
*what depends on what* and GitHub for *what is open and how it is labelled* -
and reports every place where those two answers disagree.

When they disagree, correct the plan first and let the tracker follow. The
reverse (editing labels until the tool is quiet) hides the disagreement instead
of resolving it.

## In CI

`.github/workflows/plan-drift.yml` runs `python scripts/next_tasks.py --check`
daily at 06:00 UTC, on `workflow_dispatch`, and on every pull request that
touches `PLAN.md`. The token is read-only (`contents: read`, `issues: read`), so
the job can see drift but never fix it. A pull request that ships a task without
marking its row ✅ fails there, in review, not weeks later.

## Related

- [PLAN.md](../../PLAN.md) - the task tables and the "Issue tracking on GitHub"
  conventions this check reads.
- [Write a commit](commits.md) - the commit type for a change that fixes a drift.
- [Documentation map](../README.md) - where this page sits among the quadrants.
