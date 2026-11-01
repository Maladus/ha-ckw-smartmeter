"""Tests for ``scripts/next_tasks.py`` (the "what can start next" checker).

Everything here feeds the parser and classifier functions directly: a PLAN
fragment with the same table shape as the real plan, and an issue list shaped
like ``gh issue list --json number,title,state,labels``. No ``gh`` process is
started, so the tests run offline and cannot mutate the tracker.
"""

from __future__ import annotations

from typing import Final

import pytest

from scripts import next_tasks

READY: Final = "status: ready"
BLOCKED: Final = "status: blocked"

# The plan's own tables (decisions, risks, gates) share the Markdown table shape;
# only the task tables have an ID first column and a Deps column. `#99` appears
# in the decision row on purpose: it must not become a task. The escaped pipe in
# row #4 proves a details cell cannot shift the dependency column.
PLAN_FRAGMENT: Final = """
## Decisions (recorded)

| Date | Decision | Answer |
|---|---|---|
| 2026-11-01 | Something decided | **no** (#99) |

## Phase 2 — Authentication

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| ✅ #1 | Foundation | plain | shipped | — | S | AI |
| #2 | Second task | plain | test green | #1 | S | AI |
| #3 | Third task | plain | test green | #2 | S | AI |
| #4 | Range task | escaped pipe `a \\| b` | test green | #1–#3 | M | AI |
| ~~#5~~ | Dropped task | gone | — | — | S | AI |
| #6 | Ready but blocked | plain | test green | #2, #7 | S | AI |
| #7 | Closed, row not done | plain | test green | — | S | AI |
| #8 | No issue at all | plain | test green | — | S | AI |
| ✅ #10 | Done row, open issue | plain | test green | — | S | AI |
| #9 | Ready and startable | plain | test green | #1 | S | AI |
| #11 | Self triggered | plain | test green | #11 | S | AI |
"""


def _issue(number: int, state: str = "OPEN", labels: tuple[str, ...] = ()) -> dict[str, object]:
    """One entry as ``gh issue list --json`` prints it."""
    return {
        "number": number,
        "title": f"issue {number}",
        "state": state,
        "labels": [{"name": name} for name in labels],
    }


ISSUE_PAYLOAD: Final = [
    _issue(1, "CLOSED"),
    _issue(2),
    _issue(3),
    _issue(4),
    _issue(5, "CLOSED"),
    _issue(6, labels=(READY,)),
    _issue(7, "CLOSED"),
    _issue(9, labels=(READY,)),
    _issue(10),
    _issue(11),
]


@pytest.fixture
def report() -> next_tasks.Report:
    """The classification of the fragment above."""
    return next_tasks.build_report(
        next_tasks.parse_plan(PLAN_FRAGMENT),
        next_tasks.parse_issues(ISSUE_PAYLOAD),
    )


def test_parse_plan_reads_only_task_tables() -> None:
    """Decision rows and struck-through rows are not tasks."""
    tasks = next_tasks.parse_plan(PLAN_FRAGMENT)

    assert 99 not in tasks
    assert 5 not in tasks
    assert set(tasks) == {1, 2, 3, 4, 6, 7, 8, 9, 10, 11}


def test_parse_plan_reads_done_marker_dependencies_and_done_when() -> None:
    """✅ marks done, the Deps cell yields numbers, a range expands."""
    tasks = next_tasks.parse_plan(PLAN_FRAGMENT)

    assert tasks[1].done is True
    assert tasks[2].done is False
    assert tasks[1].title == "Foundation"
    assert tasks[4].dependencies == (1, 2, 3)
    assert tasks[6].dependencies == (2, 7)
    # The escaped pipe stays inside the details cell, so "Done when" is intact.
    assert tasks[4].done_when == "test green"


def test_parse_issues_keys_by_number_and_normalises_labels() -> None:
    """State is upper-cased and labels become a set of names."""
    issues = next_tasks.parse_issues(ISSUE_PAYLOAD)

    assert issues[7].state == next_tasks.CLOSED_STATE
    assert issues[9].labels == frozenset({READY})
    assert issues[9].title == "issue 9"


def test_startable_is_ready_first_then_by_number() -> None:
    """Only open tasks without an open dependency can start; #11 waits on nobody."""
    tasks = next_tasks.parse_plan(PLAN_FRAGMENT)
    issues = next_tasks.parse_issues(ISSUE_PAYLOAD)

    startable = next_tasks.build_report(tasks, issues).startable

    assert [(task.number, task.ready) for task in startable] == [(9, True), (2, False), (11, False)]


def test_blocked_names_the_open_dependency(report: next_tasks.Report) -> None:
    """#3 waits on #2, the range task on #2 and #3, #6 only on the open #2."""
    assert {task.number: task.open_dependencies for task in report.blocked} == {
        3: (2,),
        4: (2, 3),
        6: (2,),
    }


def test_ready_label_with_open_dependency_is_drift(report: next_tasks.Report) -> None:
    """A `status: ready` label on a task that still waits is the classic lie."""
    drift = {entry.number: entry.kind for entry in report.drift}

    assert drift[6] == "ready-not-startable"
    detail = next(entry.detail for entry in report.drift if entry.kind == "ready-not-startable")
    assert "#2" in detail


def test_closed_issue_without_done_row_is_drift(report: next_tasks.Report) -> None:
    """A merged task whose plan row still lacks ✅ is drift, and so is the reverse."""
    drift = {entry.number: entry.kind for entry in report.drift}

    assert drift[7] == "closed-not-done"
    assert drift[10] == "done-row-open-issue"
    assert drift[8] == "plan-row-without-issue"


def test_check_exit_code_maps_drift_to_one(report: next_tasks.Report) -> None:
    """--check is the CI gate: 1 for drift, 0 otherwise."""
    assert next_tasks.exit_code(report, check=True) == 1
    assert next_tasks.exit_code(report, check=False) == 0

    clean = next_tasks.Report((), (), ())
    assert next_tasks.exit_code(clean, check=True) == 0


def test_missing_gh_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the gh CLI the check says so and exits 2 instead of crashing."""
    monkeypatch.setattr(next_tasks.shutil, "which", lambda _name: None)

    with pytest.raises(next_tasks.CheckError, match="gh CLI not found"):
        next_tasks.load_issues()


def test_main_exits_two_without_gh(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The message starts with `next_tasks: gh CLI not found`, exit status 2."""
    monkeypatch.setattr(next_tasks.shutil, "which", lambda _name: None)

    assert next_tasks.main([]) == 2
    assert "next_tasks: gh CLI not found" in capsys.readouterr().err
