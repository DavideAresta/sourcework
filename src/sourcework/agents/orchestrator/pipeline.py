"""The PRD pipeline.

    discover -> route -> ingest (fan-out) -> analyse -> write -> review -> revise -> publish

Routing is by modality, and every input is dispatched to whichever agent
advertises the matching skill. Ingestion runs concurrently because it is the
slow, I/O-and-tokens-bound stage and the inputs are independent; everything
after it is a barrier, because you cannot analyse requirements from half the
evidence.

A failing input does not fail the run. It is recorded as a warning and the PRD
is generated from what did arrive, with the gap visible in the stats. Losing one
of twelve documents should not cost the user the other eleven.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sourcework import checkpoint, engine, publishers, stream
from sourcework.a2a_common import AgentPool, RemoteAgentError
from sourcework.agents.schemas import (
    AnalyseRequest,
    ConfluenceFetchRequest,
    ConfluenceSearchRequest,
    ConfluenceSearchResult,
    ExtractRequest,
    PublishRequest,
    PublishResult,
    ReviewRequest,
    ReviewResponse,
    WriteRequest,
    WriteResult,
)
from sourcework.confluence.client import ConfluenceClient
from sourcework.ingest.fetch import guess_modality
from sourcework.models import (
    ExtractionResult,
    InputRef,
    Modality,
    PRDBaseline,
    PRDRequest,
    PRDResult,
    RequirementSet,
    SourceDocument,
)

logger = logging.getLogger(__name__)

#: modality -> (agent name, skill id)
ROUTES: dict[Modality, tuple[str, str]] = {
    Modality.DOCUMENT: ("ingestion", "extract_document"),
    Modality.SPREADSHEET: ("ingestion", "extract_document"),
    Modality.FREETEXT: ("ingestion", "extract_document"),
    Modality.IMAGE: ("vision", "analyse_image"),
    Modality.TRANSCRIPT: ("transcript", "extract_transcript"),
    Modality.CONFLUENCE: ("confluence", "fetch_page"),
}

MAX_CONCURRENT_INGEST = 6


@dataclass
class RunLog:
    """What happened, for the stats block and for debugging a bad PRD."""

    routed: dict[str, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "routed": self.routed,
            "failures": self.failures,
            "warnings": self.warnings,
            "timings_s": {k: round(v, 2) for k, v in self.timings.items()},
        }


def classify(ref: InputRef) -> Modality:
    if ref.modality:
        return ref.modality
    if ref.uri.startswith("confluence://") or "/wiki/spaces/" in ref.uri:
        return Modality.CONFLUENCE
    if ref.text is not None and ref.uri.startswith("inline:"):
        return Modality.FREETEXT
    return guess_modality(ref.uri, ref.media_type)


def _parse_ingest(data: object) -> tuple[list[ExtractionResult], dict[str, str]]:
    """The ingest checkpoint: what was extracted, and what routed it there.

    Raises on anything else, which is how a checkpoint written by an older build
    is recognised - :meth:`Checkpoint.load` treats a parse failure as "recompute
    this stage" rather than as an error.
    """
    if not isinstance(data, dict):
        raise TypeError("ingest checkpoint predates the routing map")
    return (
        [ExtractionResult.model_validate(e) for e in data["extractions"]],
        dict(data.get("routed") or {}),
    )


def _extraction_failed(extraction) -> bool:  # noqa: ANN001 - the A2A extraction shape
    """Did the model call fail, as opposed to finding nothing worth quoting?

    The extraction agents record a ``batch N failed:`` warning when a call
    raises and then carry on with the next source, so the evidence count on its
    own cannot tell "the backend is down" from "this page was a cover sheet".
    """
    return any("failed" in str(w) and str(w).startswith("batch ") for w in extraction.warnings)


async def run(request: PRDRequest, pool: AgentPool, *, notify=None) -> PRDResult:  # noqa: ANN001
    """Execute the full pipeline. ``notify`` is an optional async progress sink."""
    log = RunLog()
    saved = checkpoint.Checkpoint(run_id=request.run_id, resume=request.resume)
    checkpoint.prune()
    # Every stage fingerprint starts here: which models answered is part of what
    # produced the artifact, so changing the backend invalidates the lot.
    config_fp = request.llm.model_dump(mode="json") if request.llm else None

    async def say(message: str) -> None:
        logger.info(message)
        if notify is not None:
            await notify(message)

    def relay(agent: str):  # noqa: ANN202
        """Forward a specialist's own progress into this run's stream.

        Without it the analyst's nine-minute call is a single line saying
        "Normalising requirements" followed by silence, which is
        indistinguishable from a hung run - and the agents were already
        emitting the detail, it just had nowhere to go.
        """

        async def _relay(message: str) -> None:
            narration = stream.decode(message)
            if narration is not None:
                # Machine-readable, and the marker has to stay at position 0 -
                # so it passes through re-stamped with the agent that produced
                # it rather than prefixed. It bypasses `say` so the run log is
                # not asked to transcribe thousands of tokens of the model
                # thinking out loud; `Progress` declines to log it for the same
                # reason on the way out.
                if notify is not None:
                    await notify(
                        stream.encode(
                            str(narration.get("kind") or "text"),
                            str(narration.get("text") or ""),
                            agent=agent,
                        )
                    )
                return
            await say(f"[{agent}] {message}")

        return _relay

    clock = _Clock(log.timings)

    # -- 0. discovery ------------------------------------------------------
    with clock("discovery"):
        available = {
            name: skills
            for name, skills in (await pool.discover()).items()
            if name != "orchestrator"
        }
    missing = [a for a in ("requirements", "writer") if a not in available]
    if missing:
        raise RuntimeError(f"Required agents unreachable: {missing}. Found: {sorted(available)}")
    await say(f"Mesh online: {', '.join(f'{k}({len(v)})' for k, v in sorted(available.items()))}")

    # The mesh answering says nothing about whether a model will. Checked here,
    # before a single source is read, because the alternative is what this
    # replaces: the first thing a dead model server meets is the extraction of
    # source 1, which retries its way through the full timeout before failing,
    # and then does it again for every remaining source. The same fact, many
    # minutes later, wearing five stack traces.
    unusable = await engine.preflight()
    if unusable:
        raise RuntimeError(
            f"{unusable}. Nothing was read, so no evidence was lost - "
            "start the model server (or pick a backend that is running) and run this again."
        )

    # -- 1. expand Confluence queries into concrete inputs -----------------
    inputs = list(request.inputs)
    if request.confluence_queries:
        if "confluence" not in available:
            log.warnings.append("Confluence queries requested but the agent is unreachable.")
        else:
            with clock("confluence_search"):
                inputs.extend(await _expand_queries(request.confluence_queries, pool, say, log))

    baseline = request.baseline
    if not inputs and baseline is None:
        raise RuntimeError("No usable inputs. Provide files, URIs or Confluence queries.")

    # -- 2. ingest, concurrently ------------------------------------------
    extractions: list[ExtractionResult] = []
    if baseline is not None and baseline.evidence:
        # Carried, not re-read. Re-ingesting the original sources would spend
        # the tokens again AND mint new evidence ids, breaking every citation
        # in the PRD the reader already has.
        extractions.append(_carried(baseline))
        await say(
            f"Carrying forward {len(baseline.evidence)} evidence item(s) from "
            f"{len(baseline.sources)} existing source(s)"
        )

    if inputs:
        ingest_fp = checkpoint.digest(
            [checkpoint.input_identity(ref) for ref in inputs], config_fp
        )
        stored = saved.load("ingest", ingest_fp, _parse_ingest)
        if stored is not None:
            reused, routed = stored
            extractions += reused
            # Restored with the evidence, because the stats block says which
            # agent handled which input and a resumed run that reported nothing
            # routed would be describing work that did happen as work that did
            # not.
            log.routed.update(routed)
            await say(
                f"Reusing {sum(len(e.evidence) for e in reused)} evidence item(s) already "
                f"extracted from {len(reused)} source(s) by the interrupted run"
            )
        else:
            await say(f"Ingesting {len(inputs)} new input(s)")
            with clock("ingest"):
                fresh = await _ingest(inputs, pool, available, say, relay, log)
            saved.save("ingest", ingest_fp, {"extractions": fresh, "routed": dict(log.routed)})
            extractions += fresh

    if not extractions:
        raise RuntimeError(
            "Every input failed to ingest: " + "; ".join(log.failures[:5])
        )
    evidence_count = sum(len(e.evidence) for e in extractions)
    # A source whose extraction *call* failed is not a source that said nothing.
    # Both land on zero evidence, and the difference is the whole diagnosis: one
    # is a document worth re-reading, the other is a backend that is down.
    broken = [e for e in extractions if not e.evidence and _extraction_failed(e)]
    await say(
        f"{evidence_count} evidence item(s) from {len(extractions)} source(s)"
        + (f" - {len(broken)} failed to extract" if broken else "")
    )
    if evidence_count == 0:
        if broken:
            reason = broken[0].warnings[0] if broken[0].warnings else "no detail recorded"
            raise RuntimeError(
                f"Extraction failed on {len(broken)} of {len(extractions)} source(s), so there is "
                f"no evidence to build from. That is a backend problem, not a problem with the "
                f"documents. First failure: {reason}"
            )
        raise RuntimeError("Sources parsed but yielded no evidence; nothing to build a PRD from.")

    # -- 3. requirements ---------------------------------------------------
    analyse_request = AnalyseRequest.from_extractions(
        request.title,
        extractions,
        instructions=request.extra_instructions,
        audience=request.audience,
        # Present, this turns the analyst's job from "produce a requirement
        # set" into "produce the next version of this one".
        prior=baseline.requirements if baseline else None,
        # So the analyst can save each slice of the evidence it gets through,
        # below the granularity this checkpoint works at.
        run_id=request.run_id,
        resume=request.resume,
        estimate=request.estimate,
    )
    # The whole request, so a changed audience or instruction invalidates the
    # stored requirements as surely as changed evidence would - minus the two
    # fields that name the run rather than describe the work, which would
    # otherwise make every fingerprint unique to its run for no reason.
    analyse_fp = checkpoint.digest(
        analyse_request.model_dump(mode="json", exclude={"run_id", "resume"}), config_fp
    )
    requirement_set = saved.load("analyse", analyse_fp, RequirementSet.model_validate)
    if requirement_set is None:
        await say("Normalising requirements")
        with clock("analyse"):
            analysis = await pool.call(
                "requirements", "analyse_requirements", analyse_request,
                on_progress=relay("analyst"),
            )
        requirement_set = RequirementSet.model_validate(analysis)
        saved.save("analyse", analyse_fp, requirement_set)
    else:
        await say("Reusing the requirements from the interrupted run")
    await say(
        f"{len(requirement_set.requirements)} requirement(s), "
        f"{len(requirement_set.conflicts)} conflict(s), "
        f"{len(requirement_set.open_questions)} open question(s)"
    )

    # -- 4. write, review, revise -----------------------------------------
    write_request = WriteRequest(
        title=request.title,
        requirement_set=requirement_set,
        sources=[e.source for e in extractions],
        evidence=[i for e in extractions for i in e.evidence],
        audience=request.audience,
        template=request.template,
        instructions=request.extra_instructions,
    )

    review: ReviewResponse | None = None
    written: WriteResult | None = None

    for round_no in range(max(1, request.review_rounds + 1)):
        label = "Drafting" if round_no == 0 else f"Revising (round {round_no})"
        # The request carries the previous round's revision notes, so this
        # fingerprint chains: a different review produces a different draft key
        # and no round can be reused out of the context that produced it.
        write_fp = checkpoint.digest(write_request.model_dump(mode="json"), config_fp)
        written = saved.load(f"write:{round_no}", write_fp, WriteResult.model_validate)
        if written is None:
            await say(label)
            with clock(f"write_{round_no}"):
                written = WriteResult.model_validate(
                    await pool.call(
                        "writer", "write_prd", write_request, on_progress=relay("writer")
                    )
                )
            saved.save(f"write:{round_no}", write_fp, written)
        else:
            await say(f"{label}: reusing the draft from the interrupted run")

        if round_no >= request.review_rounds or "critic" not in available:
            break

        review_request = ReviewRequest(prd=written.prd, markdown=written.markdown)
        review_fp = checkpoint.digest(review_request.model_dump(mode="json"), config_fp)
        review = saved.load(f"review:{round_no}", review_fp, ReviewResponse.model_validate)
        if review is None:
            await say("Reviewing")
            with clock(f"review_{round_no}"):
                review = ReviewResponse.model_validate(
                    await pool.call(
                        "critic", "review_prd", review_request, on_progress=relay("critic")
                    )
                )
            saved.save(f"review:{round_no}", review_fp, review)
        else:
            await say("Reusing the review from the interrupted run")
        blocking = review.report.blocking
        await say(f"Review: {review.verdict}, {len(blocking)} blocking finding(s)")
        if not blocking:
            break
        write_request = write_request.model_copy(
            update={
                "revision_notes": [
                    f"[{f.severity.value}] {f.location}: {f.detail}"
                    + (f" Fix: {f.suggested_fix}" if f.suggested_fix else "")
                    for f in blocking
                ]
            }
        )

    assert written is not None  # noqa: S101 - loop always runs at least once

    if review is not None:
        # The writer renders before the review exists, so the artifacts it
        # returned carry no review section. Re-render with it attached: the
        # renderers are pure functions of the prd + review, and a shipped PRD
        # should carry its own verdict - including which standards the quality
        # rules were checked against.
        from sourcework.confluence.storage import render_prd
        from sourcework.render import to_markdown

        written = WriteResult(
            prd=written.prd,
            markdown=to_markdown(written.prd, review.report),
            confluence_storage=render_prd(written.prd, review.report),
            summary=written.summary,
        )

    # -- 5. publish --------------------------------------------------------
    published_url = None
    if request.publish:
        # Which agent, looked up rather than named. Publishing was already an
        # A2A call to an agent that advertises `publish_prd`, so a second
        # destination is a second agent - the same thing every modality already
        # is, and the reason ROUTES exists a few lines up.
        target = publishers.get(request.publish_to)
        if target is None:
            log.warnings.append(
                f"Publish requested to unknown target {request.publish_to!r}. "
                f"Known: {sorted(publishers.targets())}."
            )
        elif target.agent not in available:
            log.warnings.append(
                f"Publish requested but the {target.name} agent is unreachable."
            )
        elif target.skill not in available[target.agent]:
            # Caught here rather than at the call, which is the same rule
            # `pool.call(strict=True)` applies: a missing skill is a
            # configuration error and should read like one.
            log.warnings.append(
                f"The {target.agent} agent does not advertise {target.skill}."
            )
        else:
            await say(f"Publishing to {target.name}")
            with clock("publish"):
                try:
                    result = PublishResult.model_validate(
                        await pool.call(
                            target.agent,
                            target.skill,
                            PublishRequest(
                                title=request.title,
                                storage_xhtml=written.confluence_storage,
                                markdown=written.markdown,
                                space_key=request.confluence_space_key,
                                parent_id=request.confluence_parent_id,
                                options=request.publish_options,
                            ),
                        )
                    )
                    published_url = result.url
                    await say(result.summary)
                except RemoteAgentError as exc:
                    log.failures.append(f"publish: {exc.detail}")
                    await say(f"Publish failed: {exc.detail}")

    for extraction in extractions:
        log.warnings.extend(f"{extraction.source.title}: {w}" for w in extraction.warnings)

    if saved.reused:
        # In the stats rather than only in the log: a reader is entitled to know
        # which parts of this document were produced by the run they are looking
        # at and which were carried over from an earlier attempt.
        log.warnings.append(
            "Resumed from an interrupted run; these stages were not recomputed: "
            + ", ".join(saved.reused)
        )
    # There is a result now, so there is nothing left to resume; refining it is
    # what a baseline is for. Across every scope, or the analyst's slices would
    # outlive the run they belonged to by the whole retention period.
    checkpoint.discard(request.run_id)

    return PRDResult(
        prd=written.prd,
        markdown=written.markdown,
        confluence_storage=written.confluence_storage,
        review=review.report if review else None,
        published_url=published_url,
        stats={
            "sources": len(extractions),
            "evidence": evidence_count,
            "requirements": len(requirement_set.requirements),
            # What the run actually cost, gathered from every agent that
            # answered. The orchestrator makes no model calls itself, so
            # without this the only honest total it could report is zero.
            "usage": pool.usage.as_dict(),
            **log.as_dict(),
        },
    )


# ---------------------------------------------------------------------------


async def _expand_queries(queries: list[str], pool: AgentPool, say, log: RunLog) -> list[InputRef]:  # noqa: ANN001
    refs: list[InputRef] = []
    seen: set[str] = set()
    for cql in queries:
        try:
            found = ConfluenceSearchResult.model_validate(
                await pool.call("confluence", "search_pages", ConfluenceSearchRequest(cql=cql))
            )
        except RemoteAgentError as exc:
            log.failures.append(f"confluence search {cql!r}: {exc.detail}")
            continue
        await say(f"CQL `{cql}` matched {len(found.hits)} page(s)")
        for hit in found.hits:
            if not hit.page_id or hit.page_id in seen:
                continue
            seen.add(hit.page_id)
            refs.append(
                InputRef(
                    uri=f"confluence://{hit.space_key or 'UNKNOWN'}/{hit.page_id}",
                    title=hit.title,
                    modality=Modality.CONFLUENCE,
                )
            )
    return refs


async def _ingest(
    inputs: list[InputRef],
    pool: AgentPool,
    available: dict[str, list[str]],
    say,  # noqa: ANN001
    relay,  # noqa: ANN001
    log: RunLog,
) -> list[ExtractionResult]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_INGEST)

    async def one(ref: InputRef) -> ExtractionResult | None:
        modality = classify(ref)
        agent, skill_id = ROUTES.get(modality, ROUTES[Modality.DOCUMENT])
        label = ref.title or ref.uri
        log.routed[label] = f"{agent}.{skill_id}"

        if agent not in available:
            log.failures.append(f"{label}: {agent} agent unreachable")
            return None
        if skill_id not in available[agent]:
            log.failures.append(f"{label}: {agent} does not advertise {skill_id}")
            return None

        payload = (
            ConfluenceFetchRequest(uri=ref.uri, include_attachments=True)
            if modality is Modality.CONFLUENCE
            else ExtractRequest(ref=ref)
        )
        async with semaphore:
            try:
                data = await pool.call(
                    agent, skill_id, payload, on_progress=relay(label)
                )
            except RemoteAgentError as exc:
                log.failures.append(f"{label}: {exc.detail}")
                await say(f"Skipping {label}: {exc.detail}")
                return None
            except Exception as exc:  # noqa: BLE001
                log.failures.append(f"{label}: {type(exc).__name__}: {exc}")
                return None
        try:
            result = ExtractionResult.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.failures.append(f"{label}: malformed response: {exc}")
            return None
        await say(f"{label}: {len(result.evidence)} evidence item(s)")
        return result

    gathered = await asyncio.gather(*(one(ref) for ref in inputs))
    return [g for g in gathered if g is not None]


class _Clock:
    def __init__(self, sink: dict[str, float]) -> None:
        self.sink = sink

    def __call__(self, label: str) -> _Clock:
        self.label = label
        return self

    def __enter__(self) -> None:
        import time

        self._start = time.perf_counter()

    def __exit__(self, *exc: object) -> None:
        import time

        self.sink[self.label] = time.perf_counter() - self._start


def resolve_confluence_uri(uri: str) -> tuple[str | None, str | None]:
    """Exposed for callers that want to pre-validate a Confluence input."""
    return ConfluenceClient.parse_confluence_uri(uri)


def _carried(baseline: PRDBaseline) -> ExtractionResult:
    """The prior run's evidence, as one synthetic extraction.

    Wrapping it in an ExtractionResult means the rest of the pipeline cannot
    tell a carried source from a freshly read one - the analyst, writer and
    critic all keep working on plain evidence, and none of them needs to know
    that refinement exists.
    """
    return ExtractionResult(
        source=baseline.sources[0]
        if len(baseline.sources) == 1
        else SourceDocument(
            id="src-carried",
            uri=f"sourcework://run/{baseline.run_id or 'previous'}",
            title=f"Evidence carried from the previous version ({len(baseline.sources)} source(s))",
            modality=Modality.FREETEXT,
        ),
        evidence=baseline.evidence,
        summary="Carried forward from the previous version of this PRD.",
    )
