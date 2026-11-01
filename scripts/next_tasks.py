#!/usr/bin/env python3
"""Which planned tasks can start now, and where do plan and tracker disagree?

Read-only companion to ``PLAN.md``, section "Issue tracking on GitHub": the plan
holds scope, dependencies and gates, the issue tracker holds state. This script
joins the two and answers three questions.

* **STARTABLE** - open issues whose dependencies are all closed and which are
  not labelled ``status: blocked``. ``status: ready`` first, then by number.
* **BLOCKED** - open issues that still wait on at least one open issue, with the
  numbers that block them.
* **DRIFT** - places where the plan and the tracker contradict each other: an
  issue labelled ``status: ready`` with an open dependency, an issue closed in
  GitHub whose plan row is not ✅, a plan row marked ✅ whose issue is still
  open, or a plan row without any issue.

Sources: the task tables of ``PLAN.md`` (header starts with ``ID`` and has a
``Deps`` column) and one ``gh issue list`` call for number, title, state and
labels. Nothing is written, no issue is touched, and that single ``gh`` call is
the only network access.

Exit status: 0 normally, 1 with ``--check`` when DRIFT is not empty (the CI
gate), 2 when the check cannot run at all (no ``gh``, no PLAN.md, unreadable
output).

Usage::

    mise run next                      # human summary
    mise run next --notify             # ... plus a ping when work can start
    mise run next-check                # CI gate: non-zero on drift
    python scripts/next_tasks.py --json

``--notify`` speaks through the first channel that exists: ``workmux send
<handle>`` (a coordinator agent, default handle ``ckw-homeassistant``), then
``notify-send``, then a plain line on stdout. It only fires when STARTABLE is
not empty and DRIFT is empty, so the nudge never hides a contradiction.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PLAN_PATH: Final[Path] = REPO_ROOT / "PLAN.md"

DONE_MARKER: Final[str] = "✅"
OPEN_STATE: Final[str] = "OPEN"
CLOSED_STATE: Final[str] = "CLOSED"
READY_LABEL: Final[str] = "status: ready"
BLOCKED_LABEL: Final[str] = "status: blocked"
DEFAULT_LIMIT: Final[int] = 5
DEFAULT_NOTIFY_HANDLE: Final[str] = "ckw-homeassistant"
# One page of issues covers the whole tracker (151 task issues in PLAN.md); a
# larger project would need paging, which this repository does not.
ISSUE_LIMIT: Final[int] = 500
GH_FIELDS: Final[str] = "number,title,state,labels"

_TABLE_RULE: Final[re.Pattern[str]] = re.compile(r"^\|[\s:|-]+\|$")
_TASK_ID: Final[re.Pattern[str]] = re.compile(rf"^(?P<done>{DONE_MARKER}\s*)?#(?P<number>\d+)$")
_DROPPED_ID: Final[re.Pattern[str]] = re.compile(r"^~~#\d+~~$")
_ISSUE_REF: Final[re.Pattern[str]] = re.compile(r"#(\d+)")
_ISSUE_RANGE: Final[re.Pattern[str]] = re.compile(r"#(\d+)\s*[–—-]\s*#(\d+)")
_UNESCAPED_PIPE: Final[re.Pattern[str]] = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True, slots=True)
class PlanTask:
    """One row of a task table in ``PLAN.md``."""

    number: int
    title: str
    done_when: str
    dependencies: tuple[int, ...]
    done: bool


@dataclass(frozen=True, slots=True)
class Issue:
    """One issue as ``gh issue list`` reports it."""

    number: int
    title: str
    state: str
    labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class StartableTask:
    """An open issue with no open dependency and no blocking label."""

    number: int
    title: str
    ready: bool


@dataclass(frozen=True, slots=True)
class BlockedTask:
    """An open issue that waits on at least one open issue."""

    number: int
    title: str
    open_dependencies: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Drift:
    """One contradiction between the plan and the issue tracker."""

    kind: str
    number: int
    detail: str


@dataclass(frozen=True, slots=True)
class Report:
    """The three answers, already sorted for printing."""

    startable: tuple[StartableTask, ...]
    blocked: tuple[BlockedTask, ...]
    drift: tuple[Drift, ...]


class CheckError(Exception):
    """The check cannot run; ``main`` prints the message and exits 2."""


def split_row(line: str) -> list[str]:
    """Split one Markdown table row into stripped cells.

    Pipes escaped as ``\\|`` do not separate cells: the plan uses them inside
    code spans (``git log -p \\| grep -i token``), and splitting there would
    shift every later cell of that row, dependencies included.
    """
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in _UNESCAPED_PIPE.split(row)]


def parse_dependencies(cell: str) -> tuple[int, ...]:
    """The issue numbers a dependency cell names, sorted, without duplicates.

    ``#30, #31`` names two issues; ``#48–#53`` names a range and stands for
    every issue in between, so a range expands instead of contributing only its
    end points. ``—`` (no dependency) yields an empty tuple.
    """
    numbers: set[int] = set()
    for low, high in _ISSUE_RANGE.findall(cell):
        numbers.update(range(min(int(low), int(high)), max(int(low), int(high)) + 1))
    numbers.update(int(number) for number in _ISSUE_REF.findall(_ISSUE_RANGE.sub("", cell)))
    return tuple(sorted(numbers))


def _cell(header: list[str], cells: list[str], column: str) -> str:
    """The cell under ``column``, or ``""`` when the row is short or lacks it."""
    if column not in header:
        return ""
    position = header.index(column)
    return cells[position] if position < len(cells) else ""


def _task_from_row(header: list[str], cells: list[str]) -> PlanTask | None:
    """Build a task from one row, or return None for dropped and unknown rows."""
    identifier = _cell(header, cells, "id")
    if _DROPPED_ID.match(identifier):
        return None
    match = _TASK_ID.match(identifier)
    if match is None:
        return None
    return PlanTask(
        number=int(match.group("number")),
        title=_cell(header, cells, "task"),
        done_when=_cell(header, cells, "done when"),
        dependencies=parse_dependencies(_cell(header, cells, "deps")),
        done=match.group("done") is not None,
    )


def parse_plan(text: str) -> dict[int, PlanTask]:
    """All task rows of ``PLAN.md``, keyed by issue number.

    A task table is a Markdown table whose header starts with ``ID`` and carries
    a ``Deps`` column. The plan's other tables (decisions, risks, gates, the
    Phase 9 trigger list without dependencies) are not task tables and are
    skipped. A row whose ID is struck through (``~~#57~~``) is a dropped task,
    not an open one, and is skipped as well. The first row for a number wins.
    """
    tasks: dict[int, PlanTask] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        is_table = (
            line.lstrip().startswith("|")
            and index + 1 < len(lines)
            and _TABLE_RULE.match(lines[index + 1].strip()) is not None
        )
        if not is_table:
            index += 1
            continue

        header = [cell.lower() for cell in split_row(line)]
        is_task_table = bool(header) and header[0] == "id" and "deps" in header and "task" in header
        index += 2
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            cells = split_row(lines[index])
            index += 1
            if not is_task_table:
                continue
            task = _task_from_row(header, cells)
            if task is not None:
                tasks.setdefault(task.number, task)
    return tasks


def parse_issues(payload: Any) -> dict[int, Issue]:
    """Turn ``gh issue list --json number,title,state,labels`` output into issues."""
    issues: dict[int, Issue] = {}
    for entry in payload:
        labels = entry.get("labels") or []
        issues[int(entry["number"])] = Issue(
            number=int(entry["number"]),
            title=str(entry["title"]),
            state=str(entry["state"]).upper(),
            labels=frozenset(
                str(label["name"]) if isinstance(label, dict) else str(label) for label in labels
            ),
        )
    return issues


def load_issues() -> dict[int, Issue]:
    """Read every issue through the ``gh`` CLI - the only network access."""
    gh = shutil.which("gh")
    if gh is None:
        raise CheckError(
            "gh CLI not found (install it from https://cli.github.com and run `gh auth login`)"
        )
    result = subprocess.run(
        [gh, "issue", "list", "--state", "all", "--json", GH_FIELDS, "--limit", str(ISSUE_LIMIT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise CheckError(f"gh issue list failed (exit {result.returncode}): {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CheckError(f"gh issue list returned no JSON: {error}") from error
    return parse_issues(payload)


def _open_dependencies(task: PlanTask, issues: dict[int, Issue]) -> tuple[int, ...]:
    """Dependencies that are not closed; an unknown dependency counts as open.

    A task that names itself (``#56 trigger``) waits on nobody: no issue can be
    its own blocker, so a self-reference is ignored instead of pinning the task
    to itself forever.
    """
    return tuple(
        number
        for number in task.dependencies
        if number != task.number and (number not in issues or issues[number].state != CLOSED_STATE)
    )


def format_numbers(numbers: tuple[int, ...]) -> str:
    """``(30, 31)`` -> ``#30, #31``."""
    return ", ".join(f"#{number}" for number in numbers)


def build_report(tasks: dict[int, PlanTask], issues: dict[int, Issue]) -> Report:
    """Join plan rows and tracker state into startable, blocked and drifted."""
    startable: list[StartableTask] = []
    blocked: list[BlockedTask] = []
    drift: list[Drift] = []

    for task in tasks.values():
        issue = issues.get(task.number)
        if issue is None:
            drift.append(
                Drift(
                    "plan-row-without-issue",
                    task.number,
                    f"plan row '{task.title}' has no issue in the tracker",
                )
            )
            continue

        if issue.state == CLOSED_STATE:
            if not task.done:
                drift.append(
                    Drift(
                        "closed-not-done",
                        task.number,
                        f"closed in GitHub but the plan row '{task.title}' is not {DONE_MARKER}",
                    )
                )
            continue

        if task.done:
            drift.append(
                Drift(
                    "done-row-open-issue",
                    task.number,
                    f"plan row '{task.title}' is {DONE_MARKER} but the issue is still open",
                )
            )
            continue

        open_dependencies = _open_dependencies(task, issues)
        if open_dependencies:
            blocked.append(BlockedTask(task.number, task.title, open_dependencies))
            if READY_LABEL in issue.labels:
                drift.append(
                    Drift(
                        "ready-not-startable",
                        task.number,
                        f"labelled {READY_LABEL} but waiting on "
                        f"{format_numbers(open_dependencies)}",
                    )
                )
            continue

        # An explicitly blocked issue is neither startable nor drift: the label
        # says why it must not be picked up, and blocking on a person or an
        # external party is not a dependency the plan can express.
        if BLOCKED_LABEL in issue.labels:
            continue

        startable.append(StartableTask(task.number, task.title, READY_LABEL in issue.labels))

    startable.sort(key=lambda task: (not task.ready, task.number))
    blocked.sort(key=lambda task: task.number)
    drift.sort(key=lambda entry: (entry.kind, entry.number))
    return Report(tuple(startable), tuple(blocked), tuple(drift))


def as_dict(report: Report) -> dict[str, Any]:
    """The complete report, for ``--json``; ``--limit`` only affects the text."""
    return {
        "startable": [
            {"number": task.number, "title": task.title, "ready": task.ready}
            for task in report.startable
        ],
        "blocked": [
            {
                "number": task.number,
                "title": task.title,
                "open_dependencies": list(task.open_dependencies),
            }
            for task in report.blocked
        ],
        "drift": [
            {"kind": entry.kind, "number": entry.number, "detail": entry.detail}
            for entry in report.drift
        ],
    }


def render(report: Report, limit: int) -> str:
    """The human summary: STARTABLE (limited), BLOCKED (complete), DRIFT."""
    lines = [
        f"next_tasks: {len(report.startable)} startable · "
        f"{len(report.blocked)} blocked · {len(report.drift)} drift"
    ]

    lines += ["", "STARTABLE"]
    if not report.startable:
        lines.append("  none - every open task still waits on another issue")
    else:
        for task in report.startable[:limit]:
            marker = f"  ({READY_LABEL})" if task.ready else ""
            lines.append(f"  #{task.number}  {task.title}{marker}")
        hidden = len(report.startable) - min(limit, len(report.startable))
        if hidden > 0:
            lines.append(f"  ... {hidden} more (mise run next --limit {len(report.startable)})")

    lines += ["", "BLOCKED"]
    if not report.blocked:
        lines.append("  none")
    else:
        for task in report.blocked:
            lines.append(
                f"  #{task.number}  {task.title}  <- {format_numbers(task.open_dependencies)}"
            )

    lines += ["", "DRIFT"]
    if not report.drift:
        lines.append("  none - plan and issue tracker agree")
    else:
        for entry in report.drift:
            lines.append(f"  {entry.kind:<21} #{entry.number}  {entry.detail}")
        lines.append("  fix the label or the plan row - docs/how-to/next-task.md")

    return "\n".join(lines)


def exit_code(report: Report, *, check: bool) -> int:
    """0 normally; 1 for a non-empty DRIFT when ``--check`` is on (CI gate)."""
    if check and report.drift:
        return 1
    return 0


def notification_message(report: Report) -> str:
    """One line for a coordinator ping (``workmux send``)."""
    ready = [task for task in report.startable if task.ready]
    preview = ready[0] if ready else report.startable[0]
    more = f" (+{len(report.startable) - 1} more)" if len(report.startable) > 1 else ""
    return f"next: {len(report.startable)} startable - #{preview.number} {preview.title}{more}"


def _send_workmux(handle: str, message: str) -> bool:
    """Ping the coordinator agent through workmux; False when it is unavailable."""
    workmux = shutil.which("workmux")
    if workmux is None:
        return False
    try:
        result = subprocess.run(
            [workmux, "send", handle, message],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _send_notify_send(report: Report) -> bool:
    """Show a desktop notification; False when notify-send is unavailable."""
    notify_send = shutil.which("notify-send")
    if notify_send is None:
        return False
    ready = [task for task in report.startable if task.ready]
    preview = ready[0] if ready else report.startable[0]
    body = f"#{preview.number} {preview.title}"
    if len(report.startable) > 1:
        body += f"\n+{len(report.startable) - 1} more - mise run next"
    try:
        result = subprocess.run(
            [
                notify_send,
                "--app-name=ckw",
                f"ckw: {len(report.startable)} task(s) can start",
                body,
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def notify(report: Report, handle: str) -> None:
    """Say that work can start, through the first channel that is available.

    ``workmux send <handle>`` pings the coordinator agent and is preferred: a
    scheduled run has to reach a person even when nobody sits at the desktop.
    Without workmux the run falls back to ``notify-send``, and without that to a
    plain line on stdout. A missing tool or a failing send is not an error - the
    check itself still succeeded.
    """
    if not report.startable or report.drift:
        return
    message = notification_message(report)
    if _send_workmux(handle, message) or _send_notify_send(report):
        return
    print(f"next_tasks: {message} (workmux and notify-send unavailable)")


def main(argv: list[str] | None = None) -> int:
    """Parse the flags, run the check and return the exit status."""
    parser = argparse.ArgumentParser(
        prog="next_tasks",
        description=(
            "Which planned tasks can start now, and where do PLAN.md and the "
            "issue tracker disagree?"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help="how many startable tasks to print (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when DRIFT is not empty (the CI gate)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help=("say that work can start: workmux send <handle>, else notify-send, else stdout"),
    )
    parser.add_argument(
        "--notify-handle",
        default=DEFAULT_NOTIFY_HANDLE,
        metavar="NAME",
        help="workmux agent to ping with --notify (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    try:
        text = PLAN_PATH.read_text(encoding="utf-8")
    except OSError as error:
        print(f"next_tasks: cannot read {PLAN_PATH}: {error}", file=sys.stderr)
        return 2

    tasks = parse_plan(text)
    if not tasks:
        print(f"next_tasks: no task tables found in {PLAN_PATH}", file=sys.stderr)
        return 2

    try:
        issues = load_issues()
    except CheckError as error:
        print(f"next_tasks: {error}", file=sys.stderr)
        return 2

    report = build_report(tasks, issues)
    if args.json:
        print(json.dumps(as_dict(report), indent=2, ensure_ascii=False))
    else:
        print(render(report, args.limit))
    if args.notify:
        notify(report, args.notify_handle)
    return exit_code(report, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
