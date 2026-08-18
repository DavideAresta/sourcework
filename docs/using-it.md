# Using SourceWork

## The web UI

`sourcework ui` serves a browser front end on :8080. It is an A2A client,
not a ninth agent — the mesh runs fine without it.

- **New run** — drag files in, add URIs, notes and CQL, watch progress stream
  live while it works.
- **Model output** — the reasoning and the prose as the model produces them,
  in their own panel below the progress log. See *Watching the model work*.
- **Result** — the PRD rendered, plus a Requirements view showing each one next
  to the evidence that licenses it (and flagging the ones with none), the
  evidence table, the critic's findings, and per-backend token/cost totals.
- **History** — past runs, kept in SQLite; downloads and Confluence publishing.
- **Settings** — a form over `.env`, with the backends this machine can
  actually use probed live.

**Pick the backend per run.** The model controls on the run form become an
override that travels inside the A2A request to every agent, so one run can go
to `claude-code` and the next to `opencode-cli` with nothing restarted — and
the run records what produced it. Leaving a control on "configured default"
omits it, so the environment still decides.

Saved settings are different: the agents read their configuration once, at
start-up, so the settings page says *restart the mesh* rather than pretending
otherwise. Secrets are shown masked and a masked value is never written back.


## Resuming an interrupted run

A run that dies — a timeout, a cancel, a restart of the app — keeps whatever it
had already finished. Open it and press **Resume**: it picks up from the last
completed stage instead of re-reading every document and re-analysing every
piece of evidence.

Resuming is never automatic, because cancelling a run usually means the
configuration was wrong, and quietly reusing what that configuration produced
would hand back the document you had just rejected. Any stage whose inputs have
changed since — a different backend, an edited source file — is recomputed
regardless, and whatever *was* reused is recorded in the run's stats.

A finished run has nothing to resume. That one wants **Refine**.

From the command line the same thing is `--resume`:

```bash
sourcework generate "Returns" -i docs/*.pdf --resume
```

A run that fails or that you interrupt with Ctrl-C prints what survived and how
to continue it, because a terminal has no history to discover that from:

```
Interrupted.

2 stage(s) survived (ingest, analyse). Re-run the same command with --resume to
continue from there, or --resume run-98d617b1bb87 to name it explicitly.
```

Resuming works below the stage level too: the analyst saves each slice of the
evidence as it finishes, so an interrupted analysis costs the slices still in
flight rather than all of them. What survived is listed by name —
`ingest, analyst/slice:140ef0f6, analyst/slice:f7b34642`.

Ctrl-C cancels the run itself, not just the command watching it — the mesh
stops and the model subprocess is killed. SIGTERM does the same, so a process
manager quitting the app does not leave a run billing in the background. What
finished is kept, so cancelling and resuming is a normal thing to do.

If a run is somehow still going — a client that vanished without cancelling —
resuming is refused rather than allowed to collide:

```
Run run-50d7670154eb is still going. Interrupting this command did not stop it -
the orchestrator carries on and will finish on its own. Wait for it, or cancel
it, then resume.
```

Bare `--resume` takes the most recent saved run. Checkpoints nobody comes back
for are discarded after two weeks - they hold the full text of every source
that was ingested.

## Refining a PRD

A PRD is never finished on the first pass — it ends by telling you what it
could not determine. The **Refine** tab on a finished run is where you answer
that:

- **Open questions** get an answer box each. Your answer becomes a new source,
  so the requirement it justifies can *cite* it like any other evidence.
- **New requirements and decisions** as free text, one per line.
- **New documents, transcripts or images** — a follow-up meeting, an addendum.

It produces a **new run**, not an edit. The old PRD stays exactly as it was and
the new one records the version it came from, so you can always see what
changed and why. Three things it gets right:

- **`REQ-` ids survive.** Carried requirements keep their id even when the
  wording changes — the id identifies the need, not the sentence. New needs are
  numbered above the highest ever issued, so a retired id is never reused and
  a ticket quoting `REQ-014` never silently repoints.
- **Evidence is carried, not re-read.** Re-ingesting the original sources would
  cost the tokens again and mint new evidence ids, breaking every citation in
  the document you already have.
- **Untouched requirements keep their citations.** The analyst re-cites what the
  new material justifies and lets the rest go; the previous version's citations
  are inherited so nothing sourced gets quietly demoted to `derived`.

Answered questions drop off, resolved conflicts get applied, and anything still
open stays open.

## Requirements quality

The critic runs a deterministic rule pack before the model review — the rules
follow ISO/IEC/IEEE 29148's characteristics of well-formed requirements and the
INCOSE writing guide: escape clauses ("as appropriate", "TBD"), open-ended
lists ("such as"), absolutes ("never", "100%"), compound statements, a priority
that disagrees with its own modal verb, requirements with nothing measurable
anywhere, and terms used but never defined. Findings flow through the normal
revision loop, and the review's coverage stats carry a `quality_clean` score —
the share of requirements no rule fired on. The review section of the PRD
states what it was checked against.

**EARS syntax** is opt-in (`SOURCEWORK_QUALITY__EARS=1`, or the settings page):
the analyst then writes requirements in the five EARS shapes (ubiquitous,
When…, While…, If…then…, Where…) and the critic flags statements that take
none of them. Off by default — conforming phrasing is a team choice, not a
defect.

**Effort estimation** is opt-in per run (the checkbox on the run form,
`estimate: true` on the request, `--estimate` on the CLI): each requirement
gets a T-shirt size and a one-line rationale, rendered with `≈` because an
estimate is model inference, never something a source said. Milestone rollups
are counted in code. Off by default, because a consumer that re-estimates —
a codegen pipeline, JIRA with story points — should not pay for numbers it
will recompute.

## Signing off, auditing, forgetting

A finished run can be **approved or rejected** from its view. The record is
kept, not enforced — this is single-operator software — and the history is
append-only: a rejected-then-approved run shows both. The decision becomes the
PRD's status, so the Confluence lozenge says what was decided.

The **audit bundle** (the button on the run view, or
`GET /api/runs/{id}/audit`) packs one run into a zip: the request, the result,
evidence, sources, every progress event, and a manifest naming the backend,
models, version and standards basis — with a SHA-256 per member and a
whole-bundle digest, so an edited bundle no longer matches its own manifest.

**Retention** is `SOURCEWORK_RUNS__RETENTION_DAYS` (settings, "History"):
finished runs older than the limit are deleted when the UI starts. Deleting a
run by hand returns an erasure record naming what was removed and what was
left — uploaded files stay in the shared workspace, and the record says so
rather than claiming otherwise.

