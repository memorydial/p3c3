#!/usr/bin/env bash
# doctor - diagnose the p3c3 stack in one command. Exits non-zero if a critical check fails.
# Standalone: `bash scripts/doctor.sh`  (or `pai-doctor` after install).
set -uo pipefail

PORT="${PROXY_PORT:-4000}"
MODEL="${CODEX_MODEL:-gpt-5.5}"
SRC="${PROXY_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG="${PROXY_LOG:-$HOME/.p3c3-proxy.log}"
AUTH="${CODEX_AUTH_PATH:-$HOME/.codex/auth.json}"
URL="http://127.0.0.1:${PORT}"
fail=0

ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$1"; fail=1; }
note() { printf '      %s\n' "$1"; }

echo "p3c3 doctor - diagnosing the stack (port ${PORT}, model ${MODEL})"

# 1. tools on PATH
for t in claude uv curl; do
  command -v "$t" >/dev/null 2>&1 && ok "$t on PATH" || bad "$t NOT on PATH"
done
command -v pai-codex >/dev/null 2>&1 && ok "pai-codex launcher on PATH" \
  || note "pai-codex not on PATH yet - open a new shell (it lives in ~/.local/bin)"

# 2. codex auth file
if [ -s "$AUTH" ]; then
  ok "codex auth present ($AUTH)"
else
  bad "codex auth MISSING ($AUTH)"
  note "fix: codex login   (or copy an existing auth.json here)"
fi

# 3. proxy process + liveness
if curl -fsS -m 3 "$URL/health" >/dev/null 2>&1; then
  ok "proxy responding on :$PORT"
else
  bad "proxy DOWN on :$PORT"
  note "fix: pai-proxy start    |    logs: pai-proxy logs"
fi

# 4. /debug readiness - is codex auth actually wired into the running proxy?
dbg="$(curl -fsS -m 3 "$URL/debug" 2>/dev/null || true)"
if [ -n "$dbg" ]; then
  echo "  config: $dbg"
  if echo "$dbg" | grep -q '"codex_auth_present":[[:space:]]*true'; then
    ok "running proxy sees codex auth"
  elif echo "$dbg" | grep -q '"upstream_mode":[[:space:]]*"codex"'; then
    bad "proxy is in codex mode but can't read the codex token"
    note "fix: codex login    then    pai-proxy restart"
  fi
fi

# 5. live turn through the proxy (only if it's up)
if curl -fsS -m 3 "$URL/health" >/dev/null 2>&1; then
  resp="$(curl -fsS -m 60 -X POST "$URL/v1/messages" -H 'content-type: application/json' \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":20,\"system\":\"terse\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: DOCTOR-OK\"}]}" 2>/dev/null || true)"
  if printf '%s' "$resp" | grep -q 'DOCTOR-OK'; then
    ok "live turn through the proxy works"
  else
    bad "live turn failed (token expired, or upstream error)"
    note "look: pai-proxy logs    |    refresh: codex login  then  pai-proxy restart"
  fi
fi

echo ""
if [ "$fail" -eq 0 ]; then echo "✓ all checks passed"; else echo "✗ problems above - follow the fix notes"; fi
exit "$fail"
