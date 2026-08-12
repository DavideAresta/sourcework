# Example inputs

Everything here is **synthetic**. "Nordwind Retail Group", "Project Boomerang",
and every person named in these files are invented for demonstration. No real
company, document or engagement is represented, and no personal data is present.

## `demo_pack/` — the realistic one

Five inputs of four different kinds, which is the point: the pipeline has to
route each to the right agent and reconcile what they disagree about.

| File | What it exercises |
|---|---|
| `01-business-requirements.pdf` | PDF text extraction, page locators |
| `02-it-architecture-constraints.pdf` | a second source that *contradicts* the first |
| `03-kickoff-meeting.vtt` | transcript ingestion, timestamp locators, speaker attribution |
| `04-refinement-notes.md` | markdown, heading locators |
| `05-returns-screen-wireframe.png` | the vision agent, and a wireframe's sample data |

The wireframe is deliberately included. Its mock content — an order number,
product names, a countdown — is *sample data*, not requirements, and a weaker
model will faithfully turn it into requirements. That failure showing up in the
critic's findings is the demo working, not the demo broken.

```bash
python scripts/demo.py            # uses sample_inputs/, stub model, no keys
```

## `sample_inputs/` — the fast one

A transcript and a markdown note. Enough to prove the wiring end to end in well
under a minute, which is what `scripts/demo.py` and CI use.
