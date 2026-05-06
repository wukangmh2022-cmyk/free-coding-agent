#!/bin/sh
set -eu

BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18765/v1}"
PROVIDER_ID="${CODEX_LOCAL_PROVIDER_ID:-deepseek-local}"
WIRE_API="${CODEX_LOCAL_WIRE_API:-responses}"

if [ "${1:-}" = "exec" ]; then
    shift
    translated_args=""
    for arg in "$@"; do
        if [ "$arg" = "--experimental-json" ]; then
            arg="--json"
        fi
        translated_args="$translated_args '$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")'"
    done
    # Codex CLI renamed `--experimental-json` to `--json`; normalize while
    # preserving original argument boundaries for older callers.
    eval "set --${translated_args}"
    exec codex exec \
        --config "model_provider=\"$PROVIDER_ID\"" \
        --config "model_providers.$PROVIDER_ID.name=\"DeepSeek Local\"" \
        --config "model_providers.$PROVIDER_ID.base_url=\"$BASE_URL\"" \
        --config "model_providers.$PROVIDER_ID.env_key=\"CODEX_API_KEY\"" \
        --config "model_providers.$PROVIDER_ID.wire_api=\"$WIRE_API\"" \
        "$@"
fi

exec codex "$@"
