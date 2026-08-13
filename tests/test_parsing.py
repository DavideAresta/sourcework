"""Parsers: transcripts, documents, Confluence storage format."""

from __future__ import annotations

from sourcework.confluence import storage
from sourcework.ingest import documents, transcripts

VTT = b"""WEBVTT

00:00:04.000 --> 00:00:19.000
<v Priya Raman>Finance reconciles invoices by hand.

00:00:19.000 --> 00:00:33.000
<v Marco Bianchi>How many invoices are we talking about?
"""

SRT = b"""1
00:00:01,000 --> 00:00:04,000
Elena Fischer: One in seven invoices does not match.

2
00:00:05,000 --> 00:00:08,000
We need a tolerance.
"""

PLAIN = b"""[00:12:34] Priya: We agreed on a one percent tolerance.
[00:12:50] Marco: Reason codes must be a fixed list.
"""

JSON_EXPORT = (
    b'{"segments":[{"start":12.5,"speaker":"Priya","text":"Lock in the tolerance."},'
    b'{"start":30,"speaker":"Marco","text":"Fixed list of reason codes."}]}'
)


class TestTranscripts:
    def test_vtt_speakers_and_timestamps(self):
        cues = transcripts.parse(VTT, filename="kickoff.vtt")
        assert len(cues) == 2
        assert cues[0].speaker == "Priya Raman"
        assert cues[0].start == "00:00:04"
        assert "reconciles" in cues[0].text

    def test_vtt_cue_identifiers_are_not_evidence(self):
        # Numbered cue identifiers are standard WebVTT and what most tools emit.
        # Treated as spoken content they became one junk item per cue - "1",
        # "2", "3" - each carrying the previous cue's timestamp, so they looked
        # like citable evidence.
        numbered = b"""WEBVTT

1
00:00:04.000 --> 00:00:19.000
Priya Raman: Finance reconciles invoices by hand.

2
00:00:21.000 --> 00:00:38.000
Marco Bianchi: How many invoices are we talking about?
"""
        cues = transcripts.parse(numbered, filename="kickoff.vtt")
        assert len(cues) == 2
        assert [c.text for c in cues] == [
            "Finance reconciles invoices by hand.",
            "How many invoices are we talking about?",
        ]
        assert cues[0].start == "00:00:04"
        assert cues[1].speaker == "Marco Bianchi"

    def test_vtt_without_blank_separators_keeps_its_text(self):
        # Dropping the identifier must not eat a cue body in files that run the
        # cues together.
        packed = b"""WEBVTT
1
00:00:04.000 --> 00:00:19.000
Priya Raman: First thing.
2
00:00:21.000 --> 00:00:38.000
Marco Bianchi: Second thing.
"""
        cues = transcripts.parse(packed, filename="a.vtt")
        assert [c.text for c in cues] == ["First thing.", "Second thing."]

    def test_srt_name_prefix_becomes_speaker(self):
        cues = transcripts.parse(SRT, filename="a.srt")
        assert cues[0].speaker == "Elena Fischer"
        assert cues[0].start == "00:00:01"
        assert cues[1].speaker is None

    def test_plain_bracket_format(self):
        cues = transcripts.parse(PLAIN, filename="notes.txt")
        assert [c.speaker for c in cues] == ["Priya", "Marco"]
        assert cues[0].start == "00:12:34"

    def test_json_export_with_float_seconds(self):
        cues = transcripts.parse(JSON_EXPORT, filename="export.json")
        assert cues[0].start == "00:00:12"
        assert cues[1].speaker == "Marco"

    def test_blocks_carry_locators(self):
        cues = transcripts.parse(VTT, filename="k.vtt")
        blocks = transcripts.to_blocks(cues, window=1)
        assert len(blocks) == 2
        assert blocks[0][0].startswith("00:00:04")
        assert "Priya Raman" in blocks[0][1]


class TestDocuments:
    def test_markdown_splits_on_headings(self):
        data = b"# Title\n\nIntro line here that is long enough.\n\n## Scope\n\nIn scope: matching."
        blocks, warnings = documents.extract(data, "text/markdown", "spec.md")
        assert not warnings
        assert [b[0] for b in blocks] == ["Title", "Scope"]

    def test_csv_batches_rows(self):
        data = b"a,b\n" + b"\n".join(b"1,2" for _ in range(250))
        blocks, _ = documents.extract(data, "text/csv", "d.csv")
        assert len(blocks) == 2
        assert blocks[0][0].startswith("rows 1-")

    def test_unknown_binary_falls_back_to_text(self):
        blocks, _ = documents.extract(b"plain paragraph text", "application/octet-stream", "x.bin")
        assert blocks[0][0] == "para 1"

    def test_chunking_never_splits_a_block(self):
        blocks = [(f"p.{i}", "x" * 5000) for i in range(5)]
        batches = documents.chunk(blocks, max_chars=12000)
        assert sum(len(b) for b in batches) == 5
        assert all(len(b) >= 1 for b in batches)


class TestStorageFormat:
    def test_escaping_is_applied(self):
        assert storage.esc('a & b < c "d"') == "a &amp; b &lt; c &quot;d&quot;"

    def test_cdata_guard(self):
        out = storage.code_block("before ]]> after")
        assert "]]]]><![CDATA[>" in out

    def test_markdown_subset(self):
        out = storage.paragraphs("Hello **world** and `code`.\n\n- one\n- two")
        assert "<strong>world</strong>" in out
        assert "<code>code</code>" in out
        assert out.count("<li>") == 2

    def test_storage_round_trip_to_blocks(self):
        xhtml = "<p>intro</p><h2>Scope</h2><p>in scope</p><h2>Risks</h2><p>a risk</p>"
        blocks = storage.storage_to_blocks(xhtml)
        assert [b[0] for b in blocks] == ["preamble", "heading: Scope", "heading: Risks"]
        assert blocks[1][1] == "in scope"

    def test_storage_reader_decodes_entities(self):
        blocks = storage.storage_to_blocks("<h2>T</h2><p>a &amp; b</p>")
        assert blocks[0][1] == "a & b"


# --- locators are a promise a reader can check ----------------------------


def test_a_claim_can_cite_the_line_it_came_from_not_the_block_it_sat_in():
    """A transcript block is a window of 25 cues carrying the first cue's
    locator. Without the wider allow-list the finest attribution possible was
    one point per twenty-five, and a model that correctly cited the line it read
    had that answer thrown away for not matching a block marker."""
    from sourcework.agents.extraction import _resolve_locator

    cues = {"00:00:04 Priya Raman", "00:12:40 Tom Achebe", "00:31:02 Priya Raman"}

    assert _resolve_locator("00:12:40", cues) == "00:12:40 Tom Achebe"
    assert _resolve_locator("00:12:40 Tom Achebe", cues) == "00:12:40 Tom Achebe"
    assert _resolve_locator("[[00:31:02 Priya Raman]]", cues) == "00:31:02 Priya Raman"


def test_a_locator_naming_nothing_real_resolves_to_nothing():
    """Worse than an empty cell: a locator that resolves to the wrong place is
    precise, confident and wrong, and the empty cell is the one a reader
    questions."""
    from sourcework.agents.extraction import _resolve_locator

    known = {"p.1", "p.2"}

    assert _resolve_locator("p.9", known) is None
    assert _resolve_locator("somewhere in the appendix", known) is None
    assert _resolve_locator(None, known) is None
    assert _resolve_locator("", known) is None


def test_case_and_stray_brackets_do_not_lose_a_good_locator():
    from sourcework.agents.extraction import _resolve_locator

    known = {"heading: Scope", "p.4"}

    assert _resolve_locator("Heading: scope", known) == "heading: Scope"
    assert _resolve_locator("  [[p.4]] ", known) == "p.4"


async def test_evidence_from_a_multi_block_batch_is_not_all_pinned_to_the_first():
    """The bug this pair of fixes exists for.

    Found by generating a real PRD from the demo pack and checking it: all 62
    claims from a 31-cue meeting carried the same locator - the first thing said
    by the first speaker. Any claim whose locator did not match a block marker
    fell back to the batch's *first* block, so a whole batch was attributed to
    whatever happened to be at the top of it.
    """
    from sourcework.agents.extraction import extract_evidence
    from sourcework.models import Modality, SourceDocument

    blocks = [("p.1", "Opening remarks."), ("p.2", "Returns are free."), ("p.3", "Refunds in 5 days.")]
    claims = [
        {"text": "Returns are free.", "locator": "p.2"},
        {"text": "Refunds take five days.", "locator": "p.3"},
        {"text": "Something the model could not place.", "locator": "appendix"},
    ]

    class FakeLLM:
        async def structured(self, system, user, model, **kw):  # noqa: ANN001
            return model.model_validate({"summary": "s", "items": claims, "warnings": []})

    source = SourceDocument(
        id="src-1", uri="file:///a.pdf", title="A", modality=Modality.DOCUMENT
    )
    evidence, _, _ = await extract_evidence(FakeLLM(), source, blocks)

    assert [e.locator for e in evidence] == ["p.2", "p.3", None]


async def test_a_single_block_batch_still_gets_its_locator():
    """When the batch *is* one block, every claim in it demonstrably came from
    that block - so the fallback is knowledge, not a guess."""
    from sourcework.agents.extraction import extract_evidence
    from sourcework.models import Modality, SourceDocument

    class FakeLLM:
        async def structured(self, system, user, model, **kw):  # noqa: ANN001
            return model.model_validate(
                {"summary": "s", "items": [{"text": "A claim.", "locator": None}], "warnings": []}
            )

    source = SourceDocument(
        id="src-1", uri="file:///a.pdf", title="A", modality=Modality.DOCUMENT
    )
    evidence, _, _ = await extract_evidence(FakeLLM(), source, [("p.7", "Only block.")])

    assert [e.locator for e in evidence] == ["p.7"]


async def test_a_claim_quoted_from_one_block_is_placed_in_that_block():
    """A model that cites `BR-04` has named something real and unmatchable - the
    reference lives inside the page, and the page is what a reader needs. The
    claim text is usually lifted near-verbatim, and that can be checked."""
    from sourcework.agents.extraction import extract_evidence
    from sourcework.models import Modality, SourceDocument

    blocks = [
        ("p.1", "Returns are handled manually today by a shared mailbox."),
        ("p.2", "BR-04: a printable return label must be generated immediately."),
    ]

    class FakeLLM:
        async def structured(self, system, user, model, **kw):  # noqa: ANN001
            return model.model_validate({"summary": "", "warnings": "", "items": [
                {"text": "a printable return label must be generated immediately",
                 "locator": "BR-04"},
            ]} | {"warnings": []})

    source = SourceDocument(id="s", uri="file:///a.pdf", title="A", modality=Modality.DOCUMENT)
    evidence, _, _ = await extract_evidence(FakeLLM(), source, blocks)

    assert [e.locator for e in evidence] == ["p.2"]


def test_a_sentence_in_two_blocks_places_a_claim_in_neither():
    """This cannot tell them apart, and a locator nobody can stand behind is
    worse than none."""
    from sourcework.agents.extraction import _locate_by_text

    repeated = "the refund is issued within five business days of booking-in"
    blocks = [("p.1", f"Summary: {repeated}."), ("p.4", f"Service level: {repeated}.")]

    assert _locate_by_text(repeated, blocks) is None
    assert _locate_by_text(repeated, [blocks[0]]) == "p.1"


def test_a_fragment_too_short_to_identify_anything_places_nothing():
    """"Yes." appears in every block of a transcript. Matching on it would put a
    claim wherever the shortest coincidence happened to be."""
    from sourcework.agents.extraction import _locate_by_text

    assert _locate_by_text("Agreed.", [("00:01:00 A", "Agreed. And the rest of it.")]) is None
