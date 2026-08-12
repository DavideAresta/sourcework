#!/usr/bin/env python3
"""Fail if a dependency's licence would undermine this project's own.

SourceWork is MIT. That promise is only as strong as its dependency tree: a
copyleft package arriving through a transitive version bump changes what
downstream users are allowed to do, and it should break a build rather than
surface in a lawyer's email years later.

    pip-licenses --format=csv --with-system > licences.csv
    scripts/check_licences.py licences.csv

Written as a script with tests rather than a grep in two CI files, because the
grep it replaces silently passed a package licensed "GNU General Public License
v3": the pattern only knew the abbreviation, and a second filter dropped any
row that merely mentioned MIT. A licence gate that cannot fail is worse than no
gate at all - it is the same green tick, minus the checking.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

# Spelled-out names as well as abbreviations: pip-licenses reports whatever the
# package's own metadata says, and that is not a controlled vocabulary.
COPYLEFT = re.compile(
    # `[-v0-9.]*` so GPLv2, GPL-3.0 and GPL3 all match; the leading \b is what
    # keeps LGPL out, since there is no boundary between its L and its G.
    r"\bA?GPL[-v0-9.]*|GNU General Public|GNU Affero|Affero General Public"
    r"|\bSSPL\b|Server Side Public|Commons Clause|\bBUSL\b|Business Source",
    re.IGNORECASE,
)

# The lesser licences are compatible with linking from an MIT project, and their
# names contain the strings above.
# `[-v0-9.]*` for the same reason the copyleft pattern needs it: "LGPLv3" has
# no word boundary after the final L.
LESSER = re.compile(r"\bLGPL[-v0-9.]*|GNU Lesser|GNU Library", re.IGNORECASE)

# "MIT OR GPL-2.0" lets the user take the MIT branch, so it is not a problem -
# but it is a choice someone made, and it gets said out loud rather than passed
# in silence.
PERMISSIVE = re.compile(
    r"\bMIT\b|\bBSD\b|Apache|\bISC\b|Python Software Foundation|\bPSF\b|Unlicense|Zlib",
    re.IGNORECASE,
)


UNREADABLE = {"", "UNKNOWN", "UNKNOWN LICENSE", "NONE", "OTHER/PROPRIETARY LICENSE"}


def classify(licence: str) -> str:
    """``blocked``, ``unverified``, ``dual``, ``lesser``, or ``ok``.

    "Unknown" is not "permissive". A package whose metadata states no licence is
    precisely the one worth a human look, and passing it silently is how a gate
    reports success for something nobody checked.
    """
    if licence.strip().upper() in UNREADABLE:
        return "unverified"
    # Tested before the copyleft pattern, not after. "LGPL-3.0" does not match
    # COPYLEFT at all - there is no word boundary inside "LGPL" - so checking it
    # second reached the right verdict for the wrong reason, and left this branch
    # unreachable for the very strings it was written for.
    #
    # Not a blocker: linkable from an MIT project. Said out loud anyway, because
    # a *bundled* binary carries a relink obligation that importing a library
    # does not, and a desktop installer is exactly that.
    if LESSER.search(licence) and not re.search(r"\bA?GPL-?[0-9]", licence, re.IGNORECASE):
        return "lesser"
    if not COPYLEFT.search(licence):
        return "ok"
    if PERMISSIVE.search(licence):
        return "dual"
    return "blocked"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    path = Path(argv[1])
    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return 2

    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        # An empty report means the licence step did not run, not that the tree
        # is clean. Passing here would make the gate report success for a
        # pipeline that never checked anything.
        print(f"{path} has no rows - pip-licenses did not produce a report.", file=sys.stderr)
        return 2

    blocked, dual, unverified, lesser = [], [], [], []
    for row in rows:
        licence = (row.get("License") or "").strip()
        name = f"{row.get('Name', '?')} {row.get('Version', '')}".strip()
        verdict = classify(licence)
        if verdict == "blocked":
            blocked.append((name, licence))
        elif verdict == "dual":
            dual.append((name, licence))
        elif verdict == "unverified":
            unverified.append((name, licence or "(no licence in metadata)"))
        elif verdict == "lesser":
            lesser.append((name, licence))

    for name, licence in dual:
        print(f"note: {name} is dual-licensed ({licence}) - taking the permissive option.")
    for name, licence in lesser:
        print(f"note: {name} is {licence}. Fine to import; if you bundle it in a "
              "distributable binary, users must be able to replace it.")

    if unverified:
        print(f"\n{len(unverified)} dependenc(ies) state no licence:", file=sys.stderr)
        for name, licence in unverified:
            print(f"  {name:40} {licence}", file=sys.stderr)
        print(
            "\nUnknown is not permissive. Check each by hand and add it to ALLOWLIST once "
            "you have, or drop it.",
            file=sys.stderr,
        )

    if blocked or unverified:
        if blocked:
            print(f"\n{len(blocked)} copyleft dependenc(ies) in an MIT project:", file=sys.stderr)
            for name, licence in blocked:
                print(f"  {name:40} {licence}", file=sys.stderr)
            print(
                "\nThis changes what downstream users may do. Replace the dependency, or "
                "change this project's licence deliberately.",
                file=sys.stderr,
            )
        return 1

    print(f"{len(rows)} dependencies checked, no copyleft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
