#!/usr/bin/env bash
# Drive the real Claude Code CLI through the proxy from inside a Docker container.
# Run on the host with the test container up.
set -euo pipefail

CONTAINER="${TEST_CONTAINER:-p3c3-ubuntu}"
PROXY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-ollama}"   # ollama | codex

# --- 0. Claude Code present in the container? (idempotent install) ---
docker exec "$CONTAINER" sh -c 'command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code'
docker exec "$CONTAINER" claude --version

# Root container: allow tools via settings (bypassPermissions is refused under root).
docker exec "$CONTAINER" sh -c 'mkdir -p /root/.claude && printf "%s" "{\"permissions\":{\"defaultMode\":\"acceptEdits\",\"allow\":[\"Write\",\"Read\",\"Edit\",\"Bash\"]}}" > /root/.claude/settings.json'

# --- 1. Start the proxy on the host (0.0.0.0 so the container can reach it) ---
pkill -f "uvicorn server:app" 2>/dev/null || true
sleep 1
if [ "$MODE" = "codex" ]; then
  # Freshen the codex OAuth token by running one codex turn first.
  docker exec "$CONTAINER" sh -c 'command -v codex >/dev/null && codex exec "say ok" >/dev/null 2>&1 || true'
  ( cd "$PROXY_DIR" && PROXY_UPSTREAM_MODE=codex PROXY_READ_TIMEOUT=300 \
      uv run uvicorn server:app --host 0.0.0.0 --port 4000 >/tmp/acp.log 2>&1 & )
  MODEL=gpt-5.5
else
  ( cd "$PROXY_DIR" && OPENAI_BASE_URL=http://localhost:11434/v1 PROXY_READ_TIMEOUT=300 \
      uv run uvicorn server:app --host 0.0.0.0 --port 4000 >/tmp/acp.log 2>&1 & )
  MODEL="${OLLAMA_MODEL:-qwen2.5}"
fi
sleep 4
curl -s localhost:4000/health && echo " (proxy up, mode=$MODE, model=$MODEL)"

# --- 2. Drive Claude Code in the container through the proxy ---
run_claude() {
  docker exec \
    -e ANTHROPIC_BASE_URL=http://host.docker.internal:4000 \
    -e ANTHROPIC_AUTH_TOKEN=proxy -e ANTHROPIC_API_KEY= \
    -e ANTHROPIC_MODEL="$MODEL" -e ANTHROPIC_SMALL_FAST_MODEL="$MODEL" \
    -e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    "$CONTAINER" sh -c "cd /tmp && timeout 240 claude -p \"$1\" --model $MODEL --max-turns ${2:-1} --output-format json"
}

echo "--- plain text ---"
run_claude "Reply with exactly the word PROXY-OK and nothing else." 1
echo; echo "--- tool round-trip ---"
docker exec "$CONTAINER" sh -c 'rm -f /tmp/ct.txt'
run_claude "Use Write to create /tmp/ct.txt containing TOOLS-OK, then Read it back." 8
echo; docker exec "$CONTAINER" sh -c 'cat /tmp/ct.txt && echo "  <- file written via proxy"'

pkill -f "uvicorn server:app" 2>/dev/null || true
echo "done (proxy stopped)"
