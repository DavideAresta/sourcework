# Driving SourceWork over A2A

The mesh is eight independently deployable services, each with a published
[A2A](https://a2a-protocol.org) agent card, so any A2A-speaking client can drive
the whole thing — the web UI is one such client, not a ninth agent.

```python
from sourcework.a2a_common import AgentPool
from sourcework.models import InputRef, PRDRequest, PRDResult

async with AgentPool() as pool:
    data = await pool.call("orchestrator", "generate_prd", PRDRequest(
        title="Invoice reconciliation",
        inputs=[
            InputRef(uri="file:///kickoff.vtt"),
            InputRef(uri="file:///rfp.pdf"),
            InputRef(uri="confluence://PRD/393220"),
            InputRef(uri="inline:note", text="Must ship before year-end close."),
        ],
        confluence_queries=['space = PRD AND label = "reconciliation"'],
        template="standard",     # standard | lean | technical | discovery
        review_rounds=1,
        publish=True,
    ))

result = PRDResult.model_validate(data)
print(result.markdown)          # also: result.prd (JSON), result.confluence_storage
```

Or straight JSON-RPC, no SDK:

```bash
curl -s localhost:8000/ -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send",
  "params":{"message":{"role":"ROLE_USER","parts":[{"data":{
     "skill":"generate_prd",
     "payload":{"title":"Demo","inputs":[{"uri":"file:///workspace/notes.md"}]}
  }}]}}}'
```

## The agents and their skills

| Agent | Port | Skills | Does |
|---|---|---|---|
| **Orchestrator** | 8000 | `generate_prd`, `mesh_status` | Routes inputs, sequences the pipeline, runs the review loop |
| **Document Ingestor** | 8001 | `extract_document`, `list_supported_formats` | PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, TXT → evidence |
| **Image Analyst** | 8002 | `analyse_image` | Mockups, screenshots, whiteboards, diagrams → evidence |
| **Meeting Analyst** | 8003 | `extract_transcript`, `meeting_digest` | VTT/SRT/JSON/pasted transcripts → evidence + decision log |
| **Confluence Connector** | 8004 | `search_pages`, `fetch_page`, `publish_prd` | CQL search, page + attachment read, idempotent publish |
| **Requirements Analyst** | 8005 | `analyse_requirements` | Cluster, de-dup, MoSCoW, conflicts, open questions |
| **PRD Writer** | 8006 | `write_prd`, `render_prd` | Narrative + Markdown + Confluence storage XHTML |
| **PRD Critic** | 8007 | `review_prd` | Deterministic traceability checks, then adversarial review |

Every agent serves `/.well-known/agent-card.json`, `/healthz`, and `/docs`
(auto-generated OpenAPI, since the A2A routes are mounted on FastAPI).
