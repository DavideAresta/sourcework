

# --- the merge pass failing must not discard the slices --------------------


def _draft(*statements: str, question: str | None = None):
    from sourcework.agents.requirements.agent import (
        DraftQuestion,
        DraftRequirement,
        RequirementDraft,
    )

    return RequirementDraft(
        requirements=[
            DraftRequirement(title=s[:20], statement=s, evidence_ids=[f"e{i}"])
            for i, s in enumerate(statements)
        ],
        open_questions=[DraftQuestion(question=question)] if question else [],
    )


def test_a_failed_merge_keeps_every_requirement_the_slices_produced():
    """The merge is the last call of the longest phase. A timeout there used to
    throw away every requirement already extracted - the most expensive
    possible moment to lose the work."""
    from sourcework.agents.requirements.agent import _apply_merge, _keyed, _merge_in_code

    drafts = [_draft("orders are returnable", "refunds take 5 days"), _draft("labels are free")]
    keyed = _keyed(drafts)
    merged = _apply_merge(keyed, drafts, _merge_in_code(keyed, drafts, why="Timeout"))

    assert [r.statement for r in merged.requirements] == [
        "orders are returnable",
        "refunds take 5 days",
        "labels are free",
    ]
    assert "consolidation pass did not run" in merged.summary
    assert "Timeout" in merged.summary


def test_the_code_merge_folds_only_what_it_can_prove_is_a_duplicate():
    """Identical statements fold and their citations combine. A paraphrase does
    not - that is the judgement the model was there for, and guessing at it
    would merge two different needs under one citation."""
    from sourcework.agents.requirements.agent import _apply_merge, _keyed, _merge_in_code

    drafts = [_draft("Returns  are   FREE"), _draft("returns are free")]
    keyed = _keyed(drafts)
    keyed["D-002"].evidence_ids = ["e9"]
    merged = _apply_merge(keyed, drafts, _merge_in_code(keyed, drafts, why="x"))

    assert len(merged.requirements) == 1
    # Both slices' citations survive the fold; that is why the merge is applied
    # in code rather than re-emitted by the model.
    assert merged.requirements[0].evidence_ids == ["e0", "e9"]

    apart = [_draft("returns are free"), _draft("no charge for returns")]
    keyed = _keyed(apart)
    assert len(_apply_merge(keyed, apart, _merge_in_code(keyed, apart, why="x")).requirements) == 2


def test_the_code_merge_keeps_questions_from_every_slice_deduplicated():
    """The model gets to drop a question another slice answered; nothing here
    knows which those are, so an extra question beats a lost one."""
    from sourcework.agents.requirements.agent import _keyed, _merge_in_code

    drafts = [_draft("a", question="which carrier?"), _draft("b", question="Which  carrier?")]
    decision = _merge_in_code(_keyed(drafts), drafts, why="x")

    assert [q.question for q in decision.open_questions] == ["which carrier?"]
