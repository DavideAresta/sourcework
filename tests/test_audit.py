"""The audit bundle: one zip that says what happened, with digests that make
after-the-fact edits visible. If the digests stop matching the contents, the
bundle's one guarantee is gone - these tests exist to notice that.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from sourcework import __version__, audit
from sourcework.ui.store import Run, now_iso


def _run() -> Run:
    return Run(
        id="r1",
        title="Invoice matching",
        status="ok",
        created_at=now_iso(),
        finished_at=now_iso(),
        request={"title": "Invoice matching", "llm": {"backend": "claude-code"}},
        result={
            "markdown": "# Invoice matching",
            "prd": {
                "title": "Invoice matching",
                "sources": [{"id": "src-1", "checksum": "abc"}],
                "evidence": [{"id": "ev-1", "text": "two hours"}],
            },
            "review": {"standards": "ISO/IEC/IEEE 29148; EARS off"},
        },
        events=[{"seq": 0, "kind": "done", "message": "Finished"}],
        usage={"litellm": {"tokens": 100}},
    )


def _unzip(bundle: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        return {name: z.read(name) for name in z.namelist()}


def test_the_bundle_carries_everything_an_audit_asks_for():
    members = _unzip(audit.build_bundle(_run()))
    for name in (
        "manifest.json",
        "request.json",
        "result.json",
        "evidence.json",
        "sources.json",
        "events.json",
        "prd.md",
    ):
        assert name in members, name
    request = json.loads(members["request.json"])
    assert request["llm"]["backend"] == "claude-code"


def test_every_member_matches_its_manifest_digest():
    members = _unzip(audit.build_bundle(_run()))
    manifest = json.loads(members["manifest.json"])
    for name, digest in manifest["members"].items():
        assert hashlib.sha256(members[name]).hexdigest() == digest, name


def test_the_bundle_digest_covers_the_whole_set():
    members = _unzip(audit.build_bundle(_run()))
    manifest = json.loads(members["manifest.json"])
    expected = hashlib.sha256(
        "".join(manifest["members"][n] for n in sorted(manifest["members"])).encode("ascii")
    ).hexdigest()
    assert manifest["bundle_digest"] == expected


def test_an_edit_invalidates_the_manifest():
    members = _unzip(audit.build_bundle(_run()))
    manifest = json.loads(members["manifest.json"])
    tampered = members["result.json"] + b" "
    assert hashlib.sha256(tampered).hexdigest() != manifest["members"]["result.json"]


def test_the_manifest_says_what_produced_the_run():
    manifest = json.loads(_unzip(audit.build_bundle(_run()))["manifest.json"])
    assert manifest["backend"] == "claude-code"
    assert manifest["sourcework_version"] == __version__
    assert manifest["standards"].startswith("ISO/IEC/IEEE 29148")
    assert manifest["usage"] == {"litellm": {"tokens": 100}}


def test_a_run_without_a_result_still_bundles():
    run = _run()
    run.result = None
    members = _unzip(audit.build_bundle(run))
    assert json.loads(members["result.json"]) == {}
    assert "prd.md" not in members
