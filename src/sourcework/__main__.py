"""``python -m sourcework`` - the same entry point as the ``sourcework`` script.

Exists so a launcher that does not know where the console script landed (a
virtualenv's ``bin``, a pinned interpreter, a desktop shell that only knows a
Python it can run) can still start the app as ``python -m sourcework app``. The
two routes are the same code: :func:`sourcework.cli.main` owns the argument
parsing for both.
"""

from __future__ import annotations

from sourcework.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
