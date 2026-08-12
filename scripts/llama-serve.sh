#!/usr/bin/env bash
# Start llama.cpp's OpenAI-compatible server for PRD Forge.
#
# One model per process, so all four roles in .env point at one alias. To serve
# roles from different models, use scripts/llama-swap.sh instead.
#
#   MODEL=/path/to/model.gguf scripts/llama-serve.sh
#   MODEL=... MMPROJ=... ALIAS=big PORT=8081 CTX=16384 scripts/llama-serve.sh
#
# LLAMA_BIN  directory holding llama-server (default: found on PATH)
# MODEL      the .gguf to serve                       (required)
# MMPROJ     vision projector; empty for text-only    (optional)
set -euo pipefail

if [ -n "${LLAMA_BIN:-}" ]; then
    SERVER="$LLAMA_BIN/llama-server"
elif SERVER="$(command -v llama-server 2>/dev/null)"; then
    LLAMA_BIN="$(dirname "$SERVER")"
else
    echo "llama-server not found. Install llama.cpp and put it on PATH, or set" >&2
    echo "LLAMA_BIN to the directory containing it." >&2
    echo "  https://github.com/ggml-org/llama.cpp/releases" >&2
    exit 1
fi

if [ -z "${MODEL:-}" ]; then
    echo "Set MODEL to a .gguf file. scripts/llama-models.py list shows what you have." >&2
    exit 1
fi
if [ ! -f "$MODEL" ]; then
    echo "No such model file: $MODEL" >&2
    exit 1
fi

ALIAS="${ALIAS:-$(basename "$MODEL" .gguf)}"
PORT="${PORT:-8081}"   # 8080 is the PRD Forge UI
CTX="${CTX:-32768}"

# Prebuilt llama.cpp ships its shared libraries beside the binary.
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$LLAMA_BIN"

args=(
  -m "$MODEL"
  -c "$CTX"
  -ngl 99
  --host "${HOST:-127.0.0.1}"
  --port "$PORT"
  --alias "$ALIAS"
  --jinja
  # No thinking. A hybrid reasoning model otherwise spends the entire output
  # budget in its scratchpad and returns empty content - which PRD Forge
  # correctly reports as "the backend said nothing" and cannot recover from.
  -rea off
  --metrics
)
# The vision role needs a projector. Leave MMPROJ unset for a text-only model.
[ -n "${MMPROJ:-}" ] && args+=(--mmproj "$MMPROJ")

exec "$SERVER" "${args[@]}"
