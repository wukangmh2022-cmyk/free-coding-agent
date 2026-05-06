# 00agent2 compatibility runner

This is a preserved copy of the historical `00agent2.py` runner used for agent/web-research comparisons.
It defaults to the Agent Qt OpenAI-compatible provider at `http://127.0.0.1:18765/v1` so it can be run while the app provider is open.

## Quick start

```bash
cd /Users/pippo/github-repo/free-coding-agent
python3 -m venv .venv-agent2
source .venv-agent2/bin/activate
pip install openai-agents prompt_toolkit tiktoken httpx openai duckduckgo_search
OPENAI_BASE_URL=http://127.0.0.1:18765/v1 \
OPENAI_API_KEY=sk-placeholder \
OPENAI_MODEL=DeepSeekV4-thinking \
CODEX_MODEL=DeepSeekV4-thinking \
PYCLI_MODEL_PROFILE=deepseek-thinking \
AGENT_TARGET_WORKSPACE="$PWD/tmp/agent2_workspace" \
python3 scripts/agent2_compat/00agent2.py
```

Useful knobs:

- `OPENAI_BASE_URL`: lead model OpenAI-compatible endpoint. Default: `http://127.0.0.1:18765/v1`.
- `OPENAI_MODEL`: lead model. Default: `DeepSeekV4-thinking`.
- `CODEX_MODEL`: Codex sub-agent model. Default follows the profile defaults.
- `AGENT_TARGET_WORKSPACE`: exact workspace used by Codex subtasks. Default: `./agent2_workspace` from the process cwd.
- `PYCLI_MODEL_PROFILE`: `/model` profile on startup, for example `deepseek`, `deepseek-thinking`, `doubao`, `xiaomi`.

The important behavior to compare is that `00agent2` can route from a search-style user request into `codex_web`/`codex`, where the sub-agent performs real site crawling and file writes instead of stopping at a few DDGS results.
