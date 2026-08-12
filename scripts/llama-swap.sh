#!/usr/bin/env bash
# Serve every local model behind one endpoint, swapped on demand.
#
# Replaces llama-serve.sh when you want different models per role: point
# PRDFORGE_LLM__API_BASE at http://127.0.0.1:8081/v1 and set each role to the
# model id it should use. `openai/<id>` for ids listed in llama-swap.yaml.
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
BIN="${LLAMA_SWAP_BIN:-$(command -v llama-swap || true)}"
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
    echo "llama-swap not found. Put it on PATH or set LLAMA_SWAP_BIN." >&2
    echo "  https://github.com/mostlygeek/llama-swap/releases" >&2
    exit 1
fi
CONFIG="${LLAMA_SWAP_CONFIG:-$HERE/llama-swap.yaml}"
if [ ! -f "$CONFIG" ]; then
    echo "No $CONFIG yet. Copy the example and edit the two paths in \`macros\`:" >&2
    echo "  cp $HERE/llama-swap.example.yaml $CONFIG" >&2
    exit 1
fi
CONFIG_DIR="${LLAMA_SWAP_CONFIG_DIR:-$HERE/llama-swap.d}"
LISTEN="${LISTEN:-127.0.0.1:8081}"

mkdir -p "$CONFIG_DIR"

# -watch-config so `llama-models.py scan` takes effect without a restart, and
# -config-dir so the generated models sit beside the hand-tuned ones instead of
# overwriting them.
exec "$BIN" --config "$CONFIG" --config-dir "$CONFIG_DIR" \
    --watch-config --listen "$LISTEN"
