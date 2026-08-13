"""Intermediate state, so an interrupted run resumes instead of starting over.

A run is minutes of model calls arranged as a chain, and until now every one of
those minutes lived in local variables inside :func:`~.pipeline.run`. A timeout
in the last call of the analysis phase unwound the stack and took 180 evidence
items and 82 requirements with it - work that had already succeeded and had
already been paid for.

So each stage writes what it produced to a file keyed by run id, and a resume
reads it back instead of making the call again.

Three rules, each of which exists because the obvious alternative is wrong.

**Always write; only read when asked.** Cancelling a run is a decision - most
often "I picked the wrong model" - and a resume that happened automatically
would quietly rebuild the PRD from output the user had just rejected. Writing
costs nothing and keeps the option open; reading is an explicit request.

**Every stage carries a fingerprint of what produced it.** Change the model, or
edit a source document, and the stored evidence no longer corresponds to what a
fresh run would produce. A stage whose fingerprint no longer matches is
discarded and recomputed, so a resumed run cannot become a PRD that is half one
configuration and half another. This system's whole claim is that a document
can be traced to what it was built from; a silently mixed provenance would be a
worse failure than losing the work.

**Artifacts are stored, never recipes for rebuilding them.** Evidence ids are
minted randomly (:class:`~sourcework.models.Evidence`), so re-extracting the
same document produces the same claims under different ids and breaks every
citation in a PRD that has already been written. The stored bytes *are* the
state; nothing here re-derives anything.

Checkpoints are deleted when a run finishes, because a finished run has a
result and refining it is what :class:`~sourcework.models.PRDBaseline` is for.

Two processes save state for one run - the orchestrator its stages, the analyst
its evidence slices - so each writes its own file under a *scope*. Sharing one
file would mean two processes doing read-modify-write on it, and the only thing
keeping that safe today is a call-ordering invariant in a different process.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from sourcework import paths
from sourcework.models import InputRef

logger = logging.getLogger(__name__)

T = TypeVar("T")

FORMAT_VERSION = 1
DIRNAME = "checkpoints"

RETENTION_DAYS = 14
"""How long an unclaimed checkpoint is kept.

A finished run deletes its own; these are the ones nobody came back for. They
hold the full text of every source that was ingested, so keeping them forever
would make a directory that only grows out of runs the user has forgotten
about. Two weeks is long enough that "I will get back to it on Monday" works
and short enough that the disk does not fill with abandoned attempts.
"""


def directory() -> Path:
    return paths.workspace() / DIRNAME


def digest(*parts: object) -> str:
    """A short, stable hash of anything JSON-serialisable.

    ``sort_keys`` because a dict that round-trips through Pydantic can come back
    with its keys in a different order, and a fingerprint that changed for that
    reason would discard good work on every resume.
    """
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def input_identity(ref: InputRef) -> list[object]:
    """What makes an input *this* input, for fingerprinting.

    A local file also contributes its size and mtime: the path is unchanged when
    somebody edits the document and re-runs, and reusing evidence extracted from
    the previous contents would attribute quotes to a file that no longer
    contains them. Contents are not hashed - these are documents, some of them
    large, and this runs on every stage boundary.
    """
    identity: list[object] = [ref.uri, ref.title, ref.media_type, ref.text, ref.notes]
    path = _local_path(ref.uri)
    if path is not None:
        try:
            stat = path.stat()
            identity += [stat.st_size, stat.st_mtime_ns]
        except OSError:
            identity.append("unstattable")
    return identity


def _local_path(uri: str) -> Path | None:
    if uri.startswith("file://"):
        return Path(uri[7:])
    if "://" not in uri:
        return Path(uri)
    return None


def _files_for(run_id: str | None) -> list[Path]:
    """Every scope's file for one run."""
    if not run_id:
        return []
    try:
        return sorted(directory().glob(f"{run_id}.json")) + sorted(
            directory().glob(f"{run_id}.*.json")
        )
    except OSError:  # pragma: no cover - unreadable workspace
        return []


def saved_runs() -> list[str]:
    """Every run with state on disk, most recently written first.

    The UI has a history to pick a run out of; the CLI does not, so it resolves
    a bare ``--resume`` to the first of these.
    """
    try:
        files = list(directory().glob("*.json"))
    except OSError:  # pragma: no cover - unreadable workspace
        return []
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    # `run-abc.analyst.json` belongs to the same run as `run-abc.json`; the
    # scope is an implementation detail of who wrote it, not a separate run.
    seen: list[str] = []
    for path in files:
        run_id = path.name.split(".")[0]
        if run_id not in seen:
            seen.append(run_id)
    return seen


def prune(max_age_days: int = RETENTION_DAYS) -> int:
    """Delete checkpoints nobody came back for. Returns how many went.

    Called when a run starts rather than on a timer: this is a desktop
    application that is not running most of the time, and the moment it is
    about to write a new one is exactly when the old ones are worth counting.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        candidates = list(directory().glob("*.json"))
    except OSError:  # pragma: no cover - unreadable workspace
        return 0
    for path in candidates:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:  # pragma: no cover - vanished under us, or read-only
            continue
    if removed:
        logger.info("discarded %d checkpoint(s) older than %d days", removed, max_age_days)
    return removed


def saved_stages(run_id: str | None) -> list[str]:
    """Which stages ``run_id`` has on disk, across every scope.

    Empty means there is nothing to resume - either the run never got past its
    first call, or it finished and cleaned up after itself. The UI asks this to
    decide whether resuming is even on offer, rather than offering it always and
    failing once the user has clicked.

    Scoped stages are reported as ``analyst/slice:2``, so "3 of 5 slices done"
    is visible rather than hidden under an ``analyse`` stage that never
    completed.
    """
    stages: list[str] = []
    for path in _files_for(run_id):
        scope = path.name.split(".")[1] if path.name.count(".") > 1 else ""
        stored = _read_file(path).get("stages")
        if isinstance(stored, dict):
            stages += [f"{scope}/{name}" if scope else name for name in stored]
    return stages


def discard(run_id: str | None) -> None:
    """Forget everything saved for a run, in every scope.

    Called when the run produced a result and when it is deleted. Per-scope
    ``clear`` would leave the analyst's slices behind for the retention period,
    long after the run they belonged to stopped existing.
    """
    for path in _files_for(run_id):
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - permissions
            logger.warning("checkpoint %s: could not delete %s", run_id, path.name)


@dataclass
class Checkpoint:
    """The saved stages of one run.

    Disabled - every method a no-op - when the caller supplied no run id, which
    is the case for anything that has nowhere to resume *to*.
    """

    run_id: str | None = None
    scope: str = ""
    """Which process this file belongs to. The orchestrator uses the default;
    the analyst uses its own, so neither can lose the other's write."""

    resume: bool = False
    reused: list[str] = field(default_factory=list)
    """Stages that came from disk rather than from a model. Reported in the
    run's stats: a reader of the PRD is entitled to know which parts of it were
    produced during the run they are looking at."""

    @property
    def path(self) -> Path | None:
        if not self.run_id:
            return None
        name = f"{self.run_id}.{self.scope}.json" if self.scope else f"{self.run_id}.json"
        return directory() / name

    # -- reading -----------------------------------------------------------

    def load(self, stage: str, fingerprint: str, parse: Callable[[Any], T]) -> T | None:
        """The stored artifact for ``stage``, or ``None`` to compute it again.

        ``None`` for every reason it could fail - not asked for, not present,
        fingerprint moved on, file corrupt, shape no longer parses. Recomputing
        is always correct and merely slow, so nothing here raises.
        """
        if not self.resume or self.path is None:
            return None

        stored = self._read().get("stages", {}).get(stage)
        if not isinstance(stored, dict):
            return None
        if stored.get("fingerprint") != fingerprint:
            logger.info("checkpoint %s: %s is stale, recomputing", self.run_id, stage)
            return None

        try:
            value = parse(stored.get("data"))
        except Exception:  # noqa: BLE001 - a checkpoint from an older schema
            logger.warning("checkpoint %s: %s no longer parses, recomputing", self.run_id, stage)
            return None

        self.reused.append(stage)
        return value

    # -- writing -----------------------------------------------------------

    def save(self, stage: str, fingerprint: str, payload: Any) -> None:  # noqa: ANN401
        """Record what ``stage`` produced.

        Failures are logged and swallowed. A full disk should not fail a run
        that is otherwise going fine - the checkpoint is insurance, and
        insurance that can cause the accident is worse than none.
        """
        if self.path is None:
            return
        try:
            document = self._read()
            document["version"] = FORMAT_VERSION
            document["run_id"] = self.run_id
            document.setdefault("stages", {})[stage] = {
                "fingerprint": fingerprint,
                "data": _jsonable(payload),
            }
            self._write(document)
        except Exception:  # noqa: BLE001
            logger.warning("checkpoint %s: could not save %s", self.run_id, stage, exc_info=True)

    def clear(self) -> None:
        """Drop the file. Called when the run finished and has a real result."""
        if self.path is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - permissions
            logger.warning("checkpoint %s: could not delete", self.run_id)

    # -- file --------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        return _read_file(self.path) if self.path else {}

    def _write(self, document: dict[str, Any]) -> None:
        assert self.path is not None  # noqa: S101 - guarded by every caller
        paths.ensure(self.path.parent)
        # Written beside the target and renamed: a crash mid-write must not
        # leave a truncated file where the resume expects its own state.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, self.path)


def _read_file(path: Path) -> dict[str, Any]:
    """One checkpoint file, or ``{}`` for every reason it might not be usable."""
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("checkpoint %s: unreadable, starting fresh", path.name)
        return {}
    if not isinstance(document, dict) or document.get("version") != FORMAT_VERSION:
        return {}
    return document


def _jsonable(payload: Any) -> Any:  # noqa: ANN401
    """Models to dicts, all the way down.

    Recurses through lists *and* dicts: a stage that stores more than one thing
    hands over a dict of models, and a version of this that only walked lists
    would leave them in place to fail at ``json.dumps`` - inside the try that
    swallows save failures, so the stage would simply never be written and
    nothing would say why.
    """
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(payload, list):
        return [_jsonable(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _jsonable(value) for key, value in payload.items()}
    return payload
