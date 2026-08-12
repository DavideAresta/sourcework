"""Is this PRD done? The one question the rest of the system never answers."""

from __future__ import annotations

from prdforge import readiness


def _prd(*, questions=(), conflicts=(), requirements=()):
    return {
        "requirements": {
            "requirements": list(requirements),
            "open_questions": list(questions),
            "conflicts": list(conflicts),
        }
    }


def _review(*findings, verdict="approved"):
    return {"findings": list(findings), "verdict": verdict}


def _finding(severity, detail="something", location="REQ-001"):
    return {"severity": severity, "category": "unsupported", "location": location, "detail": detail}


class TestAssess:
    def test_a_clean_reviewed_prd_is_ready(self):
        result = readiness.assess(_prd(), _review())
        assert result.state == "ready"
        assert result.ready is True
        assert "Ready" in result.headline()

    def test_an_unreviewed_prd_is_never_ready(self):
        # Silence from a critic that never looked is not a clean bill of health,
        # and this is the single most misleading thing the dashboard could say.
        result = readiness.assess(_prd(), None)
        assert result.state == "unreviewed"
        assert result.ready is False
        assert "Not reviewed" in result.headline()

    def test_a_known_blocker_outranks_never_having_been_reviewed(self):
        # An unanswered blocking question is a fact about the document whether
        # or not a critic looked; filing it under "not reviewed" would bury it.
        prd = _prd(questions=[{"question": "Are marketplace returns in scope?", "blocking": True}])
        result = readiness.assess(prd, None)
        assert result.state == "needs_work"
        assert result.reviewed is False
        assert "no review round ran" in result.headline()

    def test_blocking_findings_stop_it(self):
        result = readiness.assess(_prd(), _review(_finding("blocker"), _finding("major")))
        assert result.state == "needs_work"
        assert result.counts["review"] == 2

    def test_minor_findings_do_not_stop_it(self):
        # Worth fixing, not worth blocking a handover.
        result = readiness.assess(_prd(), _review(_finding("minor"), _finding("nit")))
        assert result.ready is True

    def test_an_unanswered_blocking_question_stops_it(self):
        # A document can be beautifully written and still be waiting on Legal.
        prd = _prd(questions=[
            {"question": "Are marketplace returns in scope?", "blocking": True},
            {"question": "Which colour?", "blocking": False},
        ])
        result = readiness.assess(prd, _review())
        assert result.state == "needs_work"
        assert result.counts["question"] == 1
        assert "marketplace" in result.blockers[0].detail

    def test_a_recorded_conflict_stops_it(self):
        # The analyst deliberately does not pick a winner, so a conflict that is
        # still recorded is a decision somebody still owes.
        prd = _prd(conflicts=[{"description": "5 days vs T+7", "requirement_ids": ["REQ-002"]}])
        result = readiness.assess(prd, _review())
        assert result.state == "needs_work"
        assert result.blockers[0].location == "REQ-002"

    def test_the_headline_counts_each_kind(self):
        prd = _prd(questions=[{"question": "q", "blocking": True}],
                   conflicts=[{"description": "c"}])
        headline = readiness.assess(prd, _review(_finding("blocker"))).headline()
        assert "1 blocking finding(s)" in headline
        assert "1 unanswered blocking question(s)" in headline
        assert "1 unresolved conflict(s)" in headline

    def test_junk_in_the_stored_json_does_not_crash_it(self):
        assert readiness.assess(None, None).state == "unreviewed"
        assert readiness.assess({}, {"findings": ["not a dict", None]}).state == "ready"


class TestChains:
    def _run(self, id_, parent=None, status="ok", created="2026-01-01T00:00:00+00:00"):
        return {"id": id_, "parent_id": parent, "status": status, "created_at": created}

    def test_unrelated_runs_are_separate_prds(self):
        groups = readiness.chains([self._run("a"), self._run("b")])
        assert len(groups) == 2

    def test_a_refinement_chain_is_one_prd(self):
        # Three runs, one deliverable.
        runs = [
            self._run("v1", created="2026-01-01T00:00:00+00:00"),
            self._run("v2", parent="v1", created="2026-01-02T00:00:00+00:00"),
            self._run("v3", parent="v2", created="2026-01-03T00:00:00+00:00"),
        ]
        groups = readiness.chains(runs)
        assert len(groups) == 1
        assert groups[0]["versions"] == 3
        # The newest finished version is what someone would hand over.
        assert groups[0]["head"]["id"] == "v3"

    def test_the_head_is_the_newest_finished_version(self):
        runs = [
            self._run("v1", created="2026-01-01T00:00:00+00:00"),
            self._run("v2", parent="v1", status="running", created="2026-01-02T00:00:00+00:00"),
        ]
        group = readiness.chains(runs)[0]
        assert group["head"]["id"] == "v1"
        assert group["in_flight"]["id"] == "v2"

    def test_a_failed_refinement_does_not_hide_the_good_version(self):
        runs = [
            self._run("v1", created="2026-01-01T00:00:00+00:00"),
            self._run("v2", parent="v1", status="failed", created="2026-01-02T00:00:00+00:00"),
        ]
        assert readiness.chains(runs)[0]["head"]["id"] == "v1"

    def test_a_dangling_parent_does_not_lose_the_run(self):
        # The parent was deleted from history; the child is still a PRD.
        groups = readiness.chains([self._run("v2", parent="gone")])
        assert len(groups) == 1 and groups[0]["head"]["id"] == "v2"

    def test_a_cycle_does_not_hang(self):
        # Cannot happen through the UI; a restored backup should not spin.
        runs = [self._run("a", parent="b"), self._run("b", parent="a")]
        assert len(readiness.chains(runs)) >= 1
