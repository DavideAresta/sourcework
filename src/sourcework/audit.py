"""A run's audit bundle: one zip that says exactly what happened.

The run store already keeps everything an auditor asks about - what was asked
for, what came back, what it cost, every progress line - but it keeps it as a
SQLite row on one machine. This module packs a run into a single file that can
be attached to a ticket or an audit without that machine.

The manifest carries a SHA-256 digest of every other member, so a bundle that
has been edited after the fact no longer matches its own manifest. That is
tamper-evidence, not tamper-proofing - the bundle makes the claim, the hashes
let anyone check it.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from sourcework import __version__


def build_bundle(run: Any) -> bytes:  # noqa: ANN401 - ui.store.Run, imported lazily there
    """Serialise one run as a zip. ``run`` is a :class:`sourcework.ui.store.Run`,
    imported structurally to keep this module free of the UI's import graph."""
    result = run.result or {}
    prd = result.get("prd") or {}

    members: dict[str, bytes] = {
        "request.json": _json(_redact_request(run.request)),
        "result.json": _json(result),
        "events.json": _json(run.events),
        # Evidence and sources are also inside result.json; pulled out because
        # they are the two things an audit most often greps for, and asking an
        # auditor to navigate a nested PRD blob for them is unkind.
        "evidence.json": _json(prd.get("evidence") or []),
        "sources.json": _json(prd.get("sources") or []),
    }
    if result.get("markdown"):
        members["prd.md"] = result["markdown"].encode("utf-8")

    digests = {name: _sha256(data) for name, data in sorted(members.items())}
    manifest = {
        "run_id": run.id,
        "title": run.title,
        "status": run.status,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "sourcework_version": __version__,
        # What produced it. The request's llm block is the per-run choice; the
        # configured default is what the mesh was started with.
        "backend": (run.request.get("llm") or {}).get("backend") or "(configured default)",
        "models": (run.request.get("llm") or {}).get("models") or {},
        "standards": (result.get("review") or {}).get("standards") or "",
        "usage": run.usage,
        "approval": run.approval,
        # The integrity claim: each member's digest, then one digest over the
        # whole set. Verify by recomputing; a single edited byte in any member
        # changes both.
        "members": digests,
        "bundle_digest": _sha256(
            "".join(digests[name] for name in sorted(digests)).encode("ascii")
        ),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", _json(manifest))
        for name, data in sorted(members.items()):
            bundle.writestr(name, data)
    return buffer.getvalue()


def _redact_request(request: dict[str, Any]) -> dict[str, Any]:
    """A copy of the request with inline bytes and absolute local paths removed.

    The bundle is made to be attached to a ticket. Base64 of the original
    document and the operator's home-directory paths should not travel with it;
    the evidence and the rendered PRD keep every quote that was actually cited,
    so nothing an auditor greps for is lost.
    """
    redacted: dict[str, Any] = json.loads(json.dumps(request, default=str))
    for item in redacted.get("inputs", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("content_b64"):
            item["content_b64"] = "(redacted: inline bytes)"
        uri = item.get("uri")
        if isinstance(uri, str) and uri.startswith("file://"):
            item["uri"] = f"file://…/{Path(uri).name}"
    return redacted


def _json(value: Any) -> bytes:  # noqa: ANN401
    return json.dumps(value, indent=2, default=str).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
