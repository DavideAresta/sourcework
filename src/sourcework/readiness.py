"""Is this PRD done?

The critic already answers a narrower question - "what is wrong with this
document" - and the pipeline already records open questions and conflicts. What
nobody answers is the one a product owner actually asks: *can I hand this to a
team?* This module answers it, in one place, so the dashboard, the API and any
future CLI cannot disagree about what "ready" means.

Four things stand between a PRD and ready, and they come from different places:

* **blocking review findings** - the critic's blockers and majors, which
  include its deterministic checks (an uncited `must`, a dangling citation).
* **blocking open questions** - the analyst saying the team cannot build
  without an answer. A document can be beautifully written and still be
  waiting on Legal.
* **recorded conflicts** - two sources demanding incompatible things. The
  analyst deliberately does not pick a winner, so an unresolved conflict is a
  decision someone still owes.
* **never reviewed** - and this is the one worth being pedantic about. A run
  with `review_rounds=0` has no findings, and "no findings" is not the same as
  "nothing wrong". Absence of evidence is reported as `unreviewed`, never as
  ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BLOCKING_SEVERITIES = {"blocker", "major"}
"""What the critic itself treats as blocking (``ReviewReport.blocking``).
Minors and nits are worth fixing and do not stop a handover."""


@dataclass
class Blocker:
    """One reason a PRD is not ready, in the reader's terms."""

    kind: str
    """review | question | conflict"""
    detail: str
    location: str | None = None
    severity: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "location": self.location,
            "severity": self.severity,
        }


@dataclass
class Readiness:
    state: str
    """ready | needs_work | unreviewed"""
    blockers: list[Blocker] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    verdict: str | None = None
    reviewed: bool = True

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def headline(self) -> str:
        if self.state == "unreviewed":
            return "Not reviewed - run a review round before trusting this."
        if self.ready:
            return "Ready: no blocking findings, questions or conflicts."
        parts = [
            f"{self.counts[k]} {label}"
            for k, label in (
                ("review", "blocking finding(s)"),
                ("question", "unanswered blocking question(s)"),
                ("conflict", "unresolved conflict(s)"),
            )
            if self.counts.get(k)
        ]
        # Say so when nobody reviewed: the list below is what we happen to know,
        # not the result of anyone looking for problems.
        suffix = "" if self.reviewed else " (and no review round ran)"
        return "Needs work: " + ", ".join(parts) + suffix

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ready": self.ready,
            "headline": self.headline(),
            "verdict": self.verdict,
            "counts": self.counts,
            "reviewed": self.reviewed,
            "blockers": [b.as_dict() for b in self.blockers],
        }


def assess(prd: dict[str, Any] | None, review: dict[str, Any] | None) -> Readiness:
    """Judge a finished run's PRD. Takes plain dicts - this runs over stored
    JSON in the UI, not over live model objects."""
    prd = prd or {}
    requirements = (prd.get("requirements") or {}) if isinstance(prd.get("requirements"), dict) else {}

    blockers: list[Blocker] = []

    for finding in (review or {}).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("severity", "")).lower() in BLOCKING_SEVERITIES:
            blockers.append(Blocker(
                kind="review",
                detail=str(finding.get("detail") or finding.get("category") or "unspecified"),
                location=finding.get("location"),
                severity=finding.get("severity"),
            ))

    for question in requirements.get("open_questions") or []:
        if isinstance(question, dict) and question.get("blocking"):
            blockers.append(Blocker(
                kind="question",
                detail=str(question.get("question") or "unspecified"),
                location=question.get("why_it_matters"),
            ))

    for conflict in requirements.get("conflicts") or []:
        if not isinstance(conflict, dict):
            continue
        blockers.append(Blocker(
            kind="conflict",
            detail=str(conflict.get("description") or "unspecified"),
            location=", ".join(conflict.get("requirement_ids") or []) or None,
        ))

    counts = {
        kind: sum(1 for b in blockers if b.kind == kind)
        for kind in ("review", "question", "conflict")
    }

    # Precedence matters. Known blockers win: an unanswered blocking question is
    # a fact about the document whether or not a critic ever looked, and filing
    # it under "not reviewed" would bury it. Only a document with nothing known
    # against it lands on `unreviewed` - and it still is not `ready`, because
    # silence from a critic that never looked is not a clean bill of health.
    if blockers:
        state = "needs_work"
    elif review is None:
        state = "unreviewed"
    else:
        state = "ready"

    return Readiness(
        state=state,
        blockers=blockers,
        counts=counts,
        verdict=(review or {}).get("verdict"),
        reviewed=review is not None,
    )


def chains(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse runs into PRDs.

    A refinement is a new run, so a PRD that has been revised twice is three
    rows in the history and one document on the dashboard. Runs are grouped by
    following ``parent_id`` to its root; the newest finished run in a group is
    the version that counts, because that is the one someone would hand over.
    """
    by_id = {r["id"]: r for r in runs}

    def root_of(run: dict[str, Any]) -> str:
        seen: set[str] = set()
        current = run
        while True:
            parent_id = current.get("parent_id")
            # A cycle cannot happen through the UI, but a hand-edited store or
            # a restored backup should not hang the dashboard.
            if not parent_id or parent_id not in by_id or parent_id in seen:
                return current["id"]
            seen.add(parent_id)
            current = by_id[parent_id]

    groups: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault(root_of(run), []).append(run)

    out: list[dict[str, Any]] = []
    for root_id, members in groups.items():
        ordered = sorted(members, key=lambda r: r.get("created_at") or "", reverse=True)
        finished = [r for r in ordered if r.get("status") == "ok"]
        head = finished[0] if finished else ordered[0]
        out.append({
            "root_id": root_id,
            "head": head,
            "versions": len(members),
            # Shown when the newest version is still running or failed: the
            # last good document is what a reader would actually open.
            "in_flight": next((r for r in ordered if r.get("status") in ("running", "queued")), None),
            "history": ordered,
        })
    return sorted(out, key=lambda g: g["head"].get("created_at") or "", reverse=True)
