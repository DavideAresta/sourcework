# Running SourceWork on your own hardware

Every model can be local. In Settings, choose **llama-cpp** and set its local
server URL and per-role models. It connects directly to
[llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`; LiteLLM is
used as an in-process client library, so no LiteLLM proxy is required:

```bash
SOURCEWORK_LLM__BACKEND=llama-cpp
SOURCEWORK_LLM__LLAMA_CPP_API_BASE=http://127.0.0.1:8081/v1
SOURCEWORK_LLM__LLAMA_CPP_API_KEY=local
SOURCEWORK_LLM__LLAMA_CPP_MODELS__DEFAULT=openai/<model-id>
SOURCEWORK_LLM__LLAMA_CPP_MODELS__REASONING=openai/<model-id>
SOURCEWORK_LLM__TIMEOUT_S=1200                    # minutes per call, not seconds
```

Four things decide whether this works at all, and all four are the difference
between a clean run and a mystifying one:

**Enforce the schema, do not describe it.** `SOURCEWORK_LLM__CONSTRAINED_JSON=1`
(the default) sends the JSON Schema as `response_format`, so a server that
grammar-constrains decoding — llama.cpp, vLLM, Ollama — makes malformed JSON
*impossible* rather than unlikely. On a measured 15-call run this was the
difference between zero retries and a small model spending its whole retry
budget re-answering calls that had already been paid for.

**Give it a real context window.** The analyst's prompt *and its answer* both
grow with the evidence count. A server started at a 4k window silently truncates
and returns something that parses fine and is wrong. Serve at 32k and lower the
slice limits to match:

```bash
SOURCEWORK_LLM__ANALYSIS_BATCH_CHARS=24000
SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS=30
```

**Stop hybrid models from thinking away their output budget.** A reasoning model
at default effort will spend all 8k tokens in its scratchpad and return *empty
content*, which the pipeline can only report as "the backend said nothing".
`-rea off` on llama-server fixes it for most; harmony-format models (gpt-oss)
ignore that flag and need `--chat-template-kwargs '{"reasoning_effort":"low"}'`
instead.

**Let the model fit.** `--fit` only adjusts arguments you did *not* set, so
pinning `-ngl 99` on a model larger than your VRAM turns a slow run into an
out-of-memory crash.


### Several models, one endpoint

`llama-server` serves one model per process, so per-role models need
[llama-swap](https://github.com/mostlygeek/llama-swap) in front. The scripts
handle the wiring:

```bash
export SOURCEWORK_MODEL_DIRS=~/models:/srv/models   # where your GGUFs live
scripts/llama-models.py list                      # what you have, and what fits
scripts/llama-models.py scan                      # generate the serving config
cp scripts/llama-swap.example.yaml scripts/llama-swap.yaml   # then edit its paths
scripts/llama-swap.sh                             # serve them all on :8081
```

`scan` pairs vision projectors with their models, collapses split models to one
entry, and asks for full GPU offload only where it demonstrably fits. Adding a
model is dropping a file into a scanned directory and rescanning — llama-swap
reloads by itself. To fetch one you do not have yet:

```bash
scripts/llama-models.py add unsloth/gemma-3-27b-it-GGUF:Q4_K_M
```

It downloads on first use (llama.cpp resumes and caches; set `HF_TOKEN` for a
gated repo). Anything needing flags the scan cannot infer goes in
`llama-swap.yaml`, which the generator then leaves alone.

### A critic from another family

`SOURCEWORK_LLM__CRITIC_MODEL` exists so the adversarial pass can be a *different
model* from the one that wrote the PRD. A critic trained alongside the writer
finds the same phrasing natural and tends to confirm rather than challenge.
Pointing it at another lineage is the cheapest way to make the review real —
on the demo pack, a Qwen critic reviewing a Gemma-written PRD caught the writer
turning a wireframe's sample data (an order number, product names, a `-15 min`
countdown) into requirements. Left unset it follows the reasoning model, so
nothing changes until you ask for it.


## Large inputs

The analyst is the call that scales worst with input size — its prompt *and*
its answer both grow with the evidence — so above a threshold it analyses the
evidence in slices and merges the results, rather than asking one call to hold
everything. This is backend-independent: the underlying limit is the model's,
not the transport's, and the same corpus times out on the bare CLI.

Two knobs, both in `.env`:

```
SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS=70    # evidence items per slice (0 = off)
SOURCEWORK_LLM__ANALYSIS_BATCH_CHARS=60000 # rendered characters per slice (0 = off)
```

Item count is the one that usually bites: 176 evidence items render to only 45k
characters, well under any prompt limit, while the requirement set covering them
runs past the model's output ceiling. A short prompt is no guarantee of a short
reply. Raise both for a model with a large context and fast output; lower them
if analyst calls still run long.
