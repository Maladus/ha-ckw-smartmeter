#!/usr/bin/env python3
"""Add a Co-Authored-By trailer for the agent that produced a commit.

Git calls this from the commit-msg stage with the path of the message file as
its only argument:

    python3 scripts/hooks/co_author.py .git/COMMIT_EDITMSG

The trailer is derived from the environment of the agent session:

    Co-Authored-By: pi (<PI_MODEL>) <noreply@pi.local>

Rules (issue #143):

* Both PI_PROVIDER and PI_MODEL must be set. The hook is silent when they are
  not, so a human commit started from a plain shell stays untouched.
  PI_PROVIDER decides *whether* this is an agent session; it is never printed,
  because the provider in a trailer only ever duplicated the model name (and a
  provider is a deployment detail of the model, not a different author).
* A message that already carries any Co-Authored-By line is never modified, so
  a human co-author (or an amend) is respected and repeated runs are no-ops.
* The trailer is appended after the last trailer block, separated from the
  message body by one blank line; without a trailer block it starts a new one.
  Lines git treats as comments (the editor's help text, the scissors line) are
  left below the trailer.
* The exit status is 0 unless the message file cannot be read or written, which
  is the only case worth aborting a commit for.
"""

from __future__ import annotations

import os
import re
import sys

TRAILER_KEY = "Co-Authored-By:"
TRAILER_EMAIL = "<noreply@pi.local>"

# A git trailer is "<token>: <value>"; the token may not contain whitespace.
TRAILER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:")


def model_only(model: str, provider: str) -> str:
    """Drop a leading "<provider>/" so the trailer can never name a provider.

    The session normally exports a bare model id, but a fully qualified
    "<provider>/<model>" must not leak the provider into the commit message.
    """
    prefix = f"{provider}/"
    if model.lower().startswith(prefix.lower()):
        stripped = model[len(prefix) :]
        if stripped:
            return stripped
    return model


def build_trailer(model: str) -> str:
    return f"{TRAILER_KEY} pi ({model}) {TRAILER_EMAIL}"


def has_co_author(lines: list[str]) -> bool:
    return any(line.lstrip().lower().startswith(TRAILER_KEY.lower()) for line in lines)


def content_end(lines: list[str]) -> int:
    """Index after the last message line, i.e. before a trailing comment block."""
    end = len(lines)
    while end > 0 and lines[end - 1].startswith("#"):
        end -= 1
    return end


def insert_trailer(lines: list[str], trailer: str) -> list[str]:
    """Put `trailer` after the last trailer block, keeping comments last."""
    end = content_end(lines)
    body = lines[:end]
    comments = lines[end:]

    # Drop trailing blank lines; they are re-created by the insertion below.
    while body and not body[-1].strip():
        body.pop()

    # Is the last paragraph a trailer block? Then the new trailer continues it,
    # otherwise it starts a new one after a blank line.
    last_paragraph: list[str] = []
    for line in reversed(body):
        if not line.strip():
            break
        last_paragraph.insert(0, line)
    continues_block = bool(last_paragraph) and all(
        TRAILER_LINE.match(line) for line in last_paragraph
    )

    if body:
        if not continues_block:
            body.append("")
        body.append(trailer)
    else:
        body.append(trailer)

    # Keep git's own comment block (editor help, scissors line) readable; git
    # strips these lines before the message is stored.
    if comments and body[-1].strip():
        body.append("")

    return body + comments


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("co_author: no commit message file given, nothing to do", file=sys.stderr)
        return 0

    model = os.environ.get("PI_MODEL", "").strip()
    provider = os.environ.get("PI_PROVIDER", "").strip()
    if not model or not provider:
        return 0

    path = argv[1]
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        print(f"co_author: cannot read {path}: {error}", file=sys.stderr)
        return 1

    lines = text.splitlines()
    if has_co_author(lines):
        return 0

    updated = insert_trailer(lines, build_trailer(model_only(model, provider)))

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(updated) + "\n")
    except OSError as error:
        print(f"co_author: cannot write {path}: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
