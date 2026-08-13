# A real PRD

`returns-portal.md` and its two companions were produced by one command over
[`../demo_pack/`](../demo_pack/), and committed **unedited** — including the
places the model got it wrong, which is the point of shipping a real one:

```bash
sourcework generate "Project Boomerang: Returns Portal" \
  -i examples/demo_pack/01-business-requirements.pdf \
  -i examples/demo_pack/02-it-architecture-constraints.pdf \
  -i examples/demo_pack/03-kickoff-meeting.vtt \
  -i examples/demo_pack/04-refinement-notes.md \
  -i examples/demo_pack/05-returns-screen-wireframe.png \
  --review-rounds 1 -o examples/sample_output/returns-portal.md
```

| File | What it is |
|---|---|
| `returns-portal.md` | the PRD a reader gets |
| `returns-portal.json` | the same thing structured — requirements, evidence, citations, usage |
| `returns-portal.storage.xhtml` | Confluence storage format, what `--publish` sends |

Run on `opencode-cli` against `deepseek-v4-pro`, about 45 minutes: five sources
ingested concurrently, evidence analysed in four slices, drafted, reviewed by a
critic (`needs_revision`, 10 findings) and revised once.

## What to look at

**The Traceability section** at the bottom. 170 evidence items, each with the
position a reader can go and check — `p.1` for a page, `00:06:06 Lena Fischer`
for a meeting. That table is the product.

**REQ-032**, tagged `derived`. Nobody said "16,400". Two people said "about
8,200 returns per month" and "return volume more than doubles in January"; the
number is arithmetic, and the tag is the document admitting it.

**The Conflicts section.** The two PDFs disagree on purpose — the demo pack is
built that way — and the conflict is recorded rather than silently resolved in
favour of whichever source was read last.

## What is not fixed

Twenty of the sixty-eight document-sourced evidence items have no locator. The
model cited something the code could not tie to a position, and an empty cell is
deliberate: a locator that resolves to the wrong page is worse than none,
because the wrong one gets believed.

Everything here is synthetic. See [../README.md](../README.md).
