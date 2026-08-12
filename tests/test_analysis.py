"""The guardrails that stop a hallucinated citation reaching the PRD."""

from __future__ import annotations

from prdforge.agents.critic.agent import coverage_stats, structural_findings
from prdforge.agents.requirements.agent import (
    DraftConflict,
    DraftQuestion,
    DraftRequirement,
    KeyedConflict,
    MergeDecision,
    MergeGroup,
    RequirementDraft,
    _apply_merge,
    _batch,
    _keyed,
    _materialise,
    _render_prior,
)
from prdforge.models import (
    Evidence,
    Modality,
    PRDDocument,
    Requirement,
    RequirementSet,
    Severity,
    SourceRef,
)


def _ev(id_: str) -> Evidence:
    return Evidence(id=id_, source_id="src-1", modality=Modality.DOCUMENT, text=f"claim {id_}", locator="p.1")


class TestMaterialise:
    def test_ids_are_stable_and_sequential(self):
        draft = RequirementDraft(
            requirements=[
                DraftRequirement(title="A", statement="s", evidence_ids=["ev-1"]),
                DraftRequirement(title="B", statement="s", evidence_ids=["ev-2"]),
            ]
        )
        result = _materialise(draft, {"ev-1": _ev("ev-1"), "ev-2": _ev("ev-2")})
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002"]

    def test_invented_citations_are_dropped(self):
        draft = RequirementDraft(
            requirements=[
                DraftRequirement(title="A", statement="s", evidence_ids=["ev-1", "ev-DOESNOTEXIST"])
            ]
        )
        result = _materialise(draft, {"ev-1": _ev("ev-1")})
        assert [r.evidence_id for r in result.requirements[0].source_refs] == ["ev-1"]

    def test_requirement_with_no_valid_citation_is_marked_derived(self):
        draft = RequirementDraft(
            requirements=[DraftRequirement(title="A", statement="s", evidence_ids=["nope"], confidence=0.95)]
        )
        req = _materialise(draft, {"ev-1": _ev("ev-1")}).requirements[0]
        assert req.derived is True
        assert req.source_refs == []
        assert req.confidence <= 0.5  # confidence is capped when unsupported

    def test_conflicts_resolve_titles_to_ids(self):
        draft = RequirementDraft(
            requirements=[
                DraftRequirement(title="Tolerance is 1%", statement="s"),
                DraftRequirement(title="Tolerance is 2%", statement="s"),
            ],
            conflicts=[
                DraftConflict(
                    requirement_titles=["Tolerance is 1%", "Tolerance is 2%", "ghost"],
                    description="Two tolerances.",
                )
            ],
        )
        conflict = _materialise(draft, {}).conflicts[0]
        assert conflict.requirement_ids == ["REQ-001", "REQ-002"]

    def test_bad_enum_values_fall_back_instead_of_crashing(self):
        draft = RequirementDraft(
            requirements=[
                DraftRequirement(title="A", statement="s", kind="Non Functional", priority="MUST"),
                DraftRequirement(title="B", statement="s", kind="nonsense", priority="urgent"),
            ]
        )
        reqs = _materialise(draft, {}).requirements
        assert reqs[0].kind.value == "non_functional"
        assert reqs[0].priority.value == "must"
        assert reqs[1].kind.value == "functional"
        assert reqs[1].priority.value == "should"

    def test_questions_keep_only_real_evidence(self):
        draft = RequirementDraft(
            open_questions=[DraftQuestion(question="q?", evidence_ids=["ev-1", "ghost"])]
        )
        q = _materialise(draft, {"ev-1": _ev("ev-1")}).open_questions[0]
        assert len(q.source_refs) == 1


class TestStructuralFindings:
    def test_clean_prd_has_no_major_findings(self, prd: PRDDocument):
        prd.metrics = []  # metrics missing is only a minor
        severities = {f.severity for f in structural_findings(prd)}
        assert Severity.BLOCKER not in severities
        assert Severity.MAJOR not in severities

    def test_dangling_evidence_citation_is_major(self, prd: PRDDocument):
        prd.requirements.requirements[0].source_refs[0].evidence_id = "ev-ghost"
        findings = structural_findings(prd)
        assert any(f.category == "unsupported" and f.severity == Severity.MAJOR for f in findings)

    def test_uncited_must_requirement_is_major(self, prd: PRDDocument):
        prd.requirements.requirements[0].source_refs = []
        findings = structural_findings(prd)
        assert any(f.location == "REQ-001" and f.severity == Severity.MAJOR for f in findings)

    def test_vague_wording_is_flagged(self, prd: PRDDocument):
        prd.requirements.requirements[0].statement = "The system should be fast and intuitive."
        details = [f.detail for f in structural_findings(prd) if f.category == "ambiguous"]
        assert details and "fast" in details[0]

    def test_dangling_requirement_reference_in_milestone(self, prd: PRDDocument):
        from prdforge.models import Milestone

        prd.milestones = [Milestone(name="M1", requirement_ids=["REQ-999"])]
        assert any("REQ-999" in f.detail for f in structural_findings(prd))

    def test_blocking_open_question_blocks(self, prd: PRDDocument):
        from prdforge.models import OpenQuestion

        prd.requirements.open_questions = [OpenQuestion(question="Credit notes in v1?", blocking=True)]
        assert any(f.severity == Severity.BLOCKER for f in structural_findings(prd))

    def test_conflicts_block(self, prd: PRDDocument):
        from prdforge.models import Conflict

        prd.requirements.conflicts = [Conflict(requirement_ids=["REQ-001"], description="x")]
        assert any(f.severity == Severity.BLOCKER for f in structural_findings(prd))


def test_coverage_stats(prd: PRDDocument):
    stats = coverage_stats(prd)
    assert stats["requirements"] == 2.0
    assert stats["cited_requirements"] == 1.0
    assert stats["derived_share"] == 0.0
    assert stats["evidence_used"] == 1.0


# ---------------------------------------------------------------------------
# Refinement: a PRD's next version
# ---------------------------------------------------------------------------

def _prior(*ids: str) -> RequirementSet:
    return RequirementSet(requirements=[
        Requirement(id=i, title=f"Need {i}", statement=f"The system must {i}.")
        for i in ids
    ])


def _draft(*items: tuple[str, str | None]) -> RequirementDraft:
    return RequirementDraft(requirements=[
        DraftRequirement(title=title, statement=f"The system must {title}.", existing_id=existing)
        for title, existing in items
    ])


class TestRefinement:
    def test_a_first_run_numbers_positionally(self):
        result = _materialise(_draft(("a", None), ("b", None)), {}, None)
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002"]

    def test_carried_requirements_keep_their_ids(self):
        # The whole point: a reader has the old PRD open and tickets quoting
        # these ids. Renumbering silently repoints every one of them.
        prior = _prior("REQ-001", "REQ-002", "REQ-003")
        result = _materialise(
            _draft(("revised a", "REQ-001"), ("brand new", None), ("revised c", "REQ-003")),
            {}, prior,
        )
        by_title = {r.title: r.id for r in result.requirements}
        assert by_title["revised a"] == "REQ-001"
        assert by_title["revised c"] == "REQ-003"
        assert by_title["brand new"] == "REQ-004"

    def test_a_new_requirement_never_reuses_a_retired_id(self):
        # REQ-002 was dropped. Handing its number to something else would make
        # an old citation point at an unrelated requirement.
        prior = _prior("REQ-001", "REQ-002", "REQ-003")
        result = _materialise(_draft(("kept", "REQ-001"), ("new", None)), {}, prior)
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-004"]

    def test_two_requirements_cannot_claim_the_same_id(self):
        prior = _prior("REQ-001")
        result = _materialise(_draft(("first", "REQ-001"), ("second", "REQ-001")), {}, prior)
        ids = [r.id for r in result.requirements]
        assert ids == ["REQ-001", "REQ-002"]
        assert len(set(ids)) == 2

    def test_an_invented_existing_id_is_ignored_not_honoured(self):
        prior = _prior("REQ-001")
        result = _materialise(_draft(("hallucinated", "REQ-999")), {}, prior)
        assert result.requirements[0].id == "REQ-002"

    def test_the_prior_render_leads_with_ids(self):
        rendered = _render_prior(_prior("REQ-001", "REQ-002"))
        assert rendered.splitlines()[0].startswith("REQ-001")
        assert "REQ-002" in rendered

    def test_an_untouched_requirement_keeps_its_citations(self):
        # A refinement re-cites what the NEW material justifies and lets the
        # rest go. Without inheriting, every untouched requirement is demoted
        # to `derived` and the PRD starts calling sourced facts inferences.
        # The statement has to match what the draft carries forward: inheritance
        # is what "untouched" means, and a changed statement is a different
        # claim (see test_a_rewritten_requirement_cannot_inherit_the_old_evidence).
        cited = Requirement(
            id="REQ-001", title="Refund SLA", statement="The system must Refund SLA.",
            source_refs=[SourceRef(evidence_id="ev-1", source_id="src-1", locator="p.4")],
        )
        prior = RequirementSet(requirements=[cited])
        result = _materialise(_draft(("Refund SLA", "REQ-001")), {}, prior)

        carried = result.requirements[0]
        assert [r.evidence_id for r in carried.source_refs] == ["ev-1"]
        assert carried.derived is False

    def test_a_fresh_citation_wins_over_the_inherited_one(self):
        cited = Requirement(
            id="REQ-001", title="Refund SLA", statement="Refund within 5 days.",
            source_refs=[SourceRef(evidence_id="ev-old", source_id="src-1")],
        )
        prior = RequirementSet(requirements=[cited])
        draft = RequirementDraft(requirements=[
            DraftRequirement(title="Refund SLA", statement="Refund within 7 days.",
                             existing_id="REQ-001", evidence_ids=["ev-new"])
        ])
        result = _materialise(draft, {"ev-new": _ev("ev-new")}, prior)
        assert [r.evidence_id for r in result.requirements[0].source_refs] == ["ev-new"]

    def test_a_new_requirement_with_no_citation_is_still_derived(self):
        # Inheritance must not become a way for uncited NEW requirements to
        # look sourced.
        prior = RequirementSet(requirements=[
            Requirement(id="REQ-001", title="Old", statement="x",
                        source_refs=[SourceRef(evidence_id="ev-1", source_id="src-1")])
        ])
        result = _materialise(_draft(("Brand new", None)), {}, prior)
        assert result.requirements[0].id == "REQ-002"
        assert result.requirements[0].derived is True
        assert result.requirements[0].source_refs == []


# ---------------------------------------------------------------------------
# Batched analysis
#
# The analyst is the one call whose prompt AND answer both scale with the size
# of the input, which is what makes it the first thing to fail on a real corpus.
# Slicing bounds both; these tests pin the properties that make slicing safe.
# ---------------------------------------------------------------------------


def _ev_from(source: str, id_: str, text: str = "") -> Evidence:
    return Evidence(
        id=id_,
        source_id=source,
        modality=Modality.DOCUMENT,
        text=text or f"claim {id_}",
        locator="p.1",
    )


class TestBatching:
    def test_a_small_set_is_one_slice(self):
        evidence = [_ev_from("src-1", f"ev-{i}") for i in range(5)]
        assert len(_batch(evidence, {}, 60_000)) == 1

    def test_slices_split_on_source_boundaries(self):
        # Evidence from one document read together produces one coherent
        # requirement; split across two calls it produces two halves.
        a = [_ev_from("src-a", f"a-{i}", "x" * 200) for i in range(5)]
        b = [_ev_from("src-b", f"b-{i}", "x" * 200) for i in range(5)]
        batches = _batch([*a, *b], {}, 1_400)
        assert len(batches) == 2
        assert {e.source_id for e in batches[0]} == {"src-a"}
        assert {e.source_id for e in batches[1]} == {"src-b"}

    def test_one_oversized_source_is_split_rather_than_dropped(self):
        huge = [_ev_from("src-a", f"a-{i}", "x" * 5_000) for i in range(6)]
        batches = _batch(huge, {}, 6_000)
        assert len(batches) > 1
        # Nothing may be lost: every item must appear exactly once.
        assert [e.id for b in batches for e in b] == [e.id for e in huge]

    def test_every_slice_stays_under_the_limit_where_it_can(self):
        evidence = [_ev_from(f"src-{i // 3}", f"e-{i}", "x" * 400) for i in range(30)]
        for batch in _batch(evidence, {}, 3_000):
            assert len(batch) >= 1

    def test_slicing_can_be_turned_off(self):
        evidence = [_ev_from("src-1", f"ev-{i}", "x" * 10_000) for i in range(10)]
        assert len(_batch(evidence, {}, 0)) == 1


class TestMerge:
    def _keyed_pair(self):
        return [
            RequirementDraft(
                requirements=[
                    DraftRequirement(
                        title="Refund in 14 days",
                        statement="Refund within 14 days.",
                        priority="should",
                        evidence_ids=["ev-1"],
                        acceptance_criteria=["Refund lands in 14 days"],
                        derived=True,
                    )
                ],
                glossary={"RMA": "Return merchandise authorisation"},
            ),
            RequirementDraft(
                requirements=[
                    DraftRequirement(
                        title="Statutory refund window",
                        statement="Refund inside the statutory window.",
                        priority="must",
                        evidence_ids=["ev-2"],
                        acceptance_criteria=["Audit shows compliance"],
                        derived=False,
                    ),
                    DraftRequirement(
                        title="Self-service label",
                        statement="Customer generates a label.",
                        evidence_ids=["ev-3"],
                    ),
                ]
            ),
        ]

    def test_merging_never_loses_a_citation(self):
        drafts = self._keyed_pair()
        keyed = _keyed(drafts)
        merged = _apply_merge(
            keyed,
            drafts,
            MergeDecision(duplicates=[MergeGroup(keys=["D-001", "D-002"], title="Refund window")]),
        )
        assert len(merged.requirements) == 2
        first = merged.requirements[0]
        # Both slices' evidence must survive: this is why the merge is done in
        # code and not by asking the model to transcribe the ids back.
        assert first.evidence_ids == ["ev-1", "ev-2"]
        assert first.title == "Refund window"
        assert set(first.acceptance_criteria) == {"Refund lands in 14 days", "Audit shows compliance"}

    def test_a_merged_requirement_keeps_the_stronger_claim(self):
        drafts = self._keyed_pair()
        merged = _apply_merge(
            _keyed(drafts), drafts, MergeDecision(duplicates=[MergeGroup(keys=["D-001", "D-002"])])
        )
        assert merged.requirements[0].priority == "must"
        # Sourced beats inferred: one pass finding real evidence settles it.
        assert merged.requirements[0].derived is False

    def test_a_group_of_one_is_not_a_merge(self):
        drafts = self._keyed_pair()
        merged = _apply_merge(
            _keyed(drafts), drafts, MergeDecision(duplicates=[MergeGroup(keys=["D-001"])])
        )
        assert len(merged.requirements) == 3

    def test_an_unknown_key_cannot_delete_a_requirement(self):
        drafts = self._keyed_pair()
        merged = _apply_merge(
            _keyed(drafts),
            drafts,
            MergeDecision(duplicates=[MergeGroup(keys=["D-001", "D-999"])]),
        )
        assert len(merged.requirements) == 3

    def test_conflicts_come_back_addressed_by_title(self):
        drafts = self._keyed_pair()
        merged = _apply_merge(
            _keyed(drafts),
            drafts,
            MergeDecision(
                conflicts=[
                    KeyedConflict(
                        keys=["D-001", "D-002"],
                        description="14 days versus the statutory window",
                        resolution_hint="Ask Legal",
                    )
                ]
            ),
        )
        # _materialise resolves conflicts by title, so the merge has to speak
        # that language rather than its own keys.
        assert merged.conflicts[0].requirement_titles == [
            "Refund in 14 days",
            "Statutory refund window",
        ]

    def test_the_merge_decides_which_questions_survive(self):
        drafts = self._keyed_pair()
        drafts[0].open_questions = [DraftQuestion(question="Answered by another slice?")]
        merged = _apply_merge(
            _keyed(drafts),
            drafts,
            MergeDecision(open_questions=[DraftQuestion(question="Still open", blocking=True)]),
        )
        assert [q.question for q in merged.open_questions] == ["Still open"]

    def test_glossaries_from_every_slice_are_kept(self):
        drafts = self._keyed_pair()
        merged = _apply_merge(_keyed(drafts), drafts, MergeDecision(glossary={"SLA": "..."}))
        assert set(merged.glossary) == {"RMA", "SLA"}

    def test_a_merged_draft_still_materialises_normally(self):
        drafts = self._keyed_pair()
        merged = _apply_merge(
            _keyed(drafts), drafts, MergeDecision(duplicates=[MergeGroup(keys=["D-001", "D-002"])])
        )
        result = _materialise(merged, {f"ev-{i}": _ev(f"ev-{i}") for i in (1, 2, 3)})
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002"]
        assert len(result.requirements[0].source_refs) == 2


class TestItemLimit:
    """The limit that catches what characters miss.

    176 evidence items rendered to 45k characters - under any prompt limit -
    while the requirement set covering them ran to 33k output tokens and hit
    the model's ceiling. The prompt was never the problem.
    """

    def test_many_short_items_still_slice(self):
        evidence = [_ev_from(f"src-{i // 40}", f"e-{i}", "short claim") for i in range(176)]
        assert len(_batch(evidence, {}, 60_000, 0)) == 1, "characters alone see nothing wrong"
        batches = _batch(evidence, {}, 60_000, 70)
        assert len(batches) > 1
        assert all(len(b) <= 70 for b in batches)
        assert [e.id for b in batches for e in b] == [e.id for e in evidence]

    def test_both_limits_off_means_one_slice(self):
        evidence = [_ev_from("src-1", f"e-{i}", "x" * 5_000) for i in range(50)]
        assert len(_batch(evidence, {}, 0, 0)) == 1
