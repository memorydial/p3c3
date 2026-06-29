#!/usr/bin/env bash
# ============================================================================
# P3C3 installer - Claude Code on OpenAI codex, for Ubuntu (WSL or container)
# ----------------------------------------------------------------------------
# Turns a fresh Ubuntu (ubuntu:24.04 container OR a WSL Ubuntu distro) into:
# Claude Code (the real CLI) driving OpenAI's codex model (gpt-5.5) through a
# local pydantic proxy, with the public PAI skill Packs installed on top.
#
#   Claude Code ──Anthropic API──▶ proxy (127.0.0.1:4000) ──Responses API──▶ codex
#                 + PAI skills                  (codex mode)        (chatgpt.com/backend-api/codex)
#
# Works as root (containers, no sudo) AND as a normal user (WSL, uses sudo for
# apt + global npm). Idempotent-ish: safe to re-run.
# Supported on ubuntu:24.04 (root and non-root/sudo); the same installers apply on x86_64.
# The agent adapts per install/RUNBOOK.md + install/WSL.md if a package or path differs.
# ============================================================================
set -euo pipefail

# ---- config (override via env) ---------------------------------------------
CODEX_MODEL="${CODEX_MODEL:-gpt-5.5}"
PROXY_PORT="${PROXY_PORT:-4000}"
PROXY_SRC="${PROXY_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"   # defaults to this repo root (install/ is one level down)
PROXY_REPO="${PROXY_REPO:-}"                            # optional git URL to clone the proxy from
PAI_REPO="${PAI_REPO:-https://github.com/danielmiessler/Personal_AI_Infrastructure}"
HOME_DIR="${HOME:-/root}"
export PATH="$HOME_DIR/.local/bin:$HOME_DIR/.bun/bin:$PATH"

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"   # non-root (WSL) needs sudo for apt + global npm; root (container) doesn't
BIN_DIR="$HOME_DIR/.local/bin"; mkdir -p "$BIN_DIR"
PROXY_LOG="$HOME_DIR/.p3c3-proxy.log"          # user-writable (not /var/log - non-root can't write there)

say() { printf '\n\033[1;36m[%s]\033[0m %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 1. base packages ------------------------------------------------------
say 1 "Base packages (curl, git, ripgrep, python3, bun/uv deps)"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq curl git ripgrep python3 python3-venv ca-certificates xz-utils unzip >/dev/null
python3 --version

# ---- 2. Node 22 (Claude Code + codex CLI) ----------------------------------
say 2 "Node.js 22"
if ! have node || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
  # download-then-run instead of curl|bash, so the script can be inspected before running
  curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource.sh
  $SUDO -E bash /tmp/nodesource.sh >/dev/null 2>&1
  $SUDO apt-get install -y -qq nodejs >/dev/null
fi
echo "node $(node -v) / npm $(npm -v)"

# ---- 3. uv (proxy) + bun (PAI Tools) - install into $HOME, no sudo ----------
say 3 "uv + bun"
if ! have uv; then
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh && sh /tmp/uv-install.sh >/dev/null 2>&1
fi
if ! have bun; then
  curl -fsSL https://bun.sh/install -o /tmp/bun-install.sh && bash /tmp/bun-install.sh >/dev/null 2>&1
fi
echo "uv $(uv --version) / bun $(bun --version)"

# ---- 4. Claude Code + OpenAI codex CLI (global npm needs sudo when non-root)-
say 4 "Claude Code + codex CLI (npm -g)"
$SUDO npm install -g @anthropic-ai/claude-code @openai/codex >/dev/null 2>&1
echo "claude $(claude --version) / codex $(codex --version 2>/dev/null || echo present)"

# ---- 5. codex auth (ChatGPT subscription) ----------------------------------
# On WSL you've usually already done this (you're running codex as your agent),
# so ~/.codex/auth.json exists. Otherwise: `codex login` (interactive - works on
# WSL via the Windows browser) OR drop an existing auth.json in (headless container).
say 5 "codex auth"
if [ -s "$HOME_DIR/.codex/auth.json" ]; then
  chmod 600 "$HOME_DIR/.codex/auth.json"
  echo "found $HOME_DIR/.codex/auth.json (using it)"
else
  echo "!! No $HOME_DIR/.codex/auth.json."
  echo "   Run:  codex login            (interactive, ChatGPT account)"
  echo "   OR copy an existing auth.json to $HOME_DIR/.codex/auth.json (headless)."
  echo "   Then re-run this script."
  exit 2
fi

# ---- 6. the proxy ----------------------------------------------------------
say 6 "p3c3 proxy"
if [ ! -d "$PROXY_SRC" ] || [ ! -f "$PROXY_SRC/pyproject.toml" ]; then
  if [ -n "$PROXY_REPO" ]; then
    git clone --depth 1 "$PROXY_REPO" "$PROXY_SRC" >/dev/null 2>&1
  else
    echo "!! Proxy source not at $PROXY_SRC and PROXY_REPO unset."
    echo "   Run this script from inside a clone of the p3c3 repo, or set PROXY_REPO=<git url>."
    exit 2
  fi
fi
( cd "$PROXY_SRC" && uv sync -q )
echo "proxy synced at $PROXY_SRC"

# ---- 7. PAI skill Packs (public) -------------------------------------------
say 7 "PAI skill Packs"
[ -d "$HOME_DIR/.pai-src" ] || git clone --depth 1 "$PAI_REPO" "$HOME_DIR/.pai-src" >/dev/null 2>&1
mkdir -p "$HOME_DIR/.claude/skills" "$HOME_DIR/.claude/Tools"
n=0
for d in "$HOME_DIR"/.pai-src/Packs/*/; do
  name=$(basename "$d")
  [ "$name" = "Interceptor" ] && continue   # macOS-host-only
  if [ -d "$d/src" ]; then
    rm -rf "$HOME_DIR/.claude/skills/$name"
    cp -r "$d/src" "$HOME_DIR/.claude/skills/$name"
    n=$((n+1))
  fi
done
cp "$HOME_DIR"/.pai-src/Tools/*.ts "$HOME_DIR/.claude/Tools/" 2>/dev/null || true
echo "installed $n PAI skills (Interceptor skipped: macOS-only; Pulse/voice curls are fire-and-forget no-ops here)"

# ---- 8. Claude Code config -------------------------------------------------
# acceptEdits + allow-list = tools run without per-call prompts. Required under
# root (Claude Code refuses bypassPermissions as root); harmless as a normal user.
say 8 "Claude Code settings + pai-codex launcher"
mkdir -p "$HOME_DIR/.claude"
cat > "$HOME_DIR/.claude/settings.json" <<'JSON'
{
  "permissions": { "defaultMode": "acceptEdits", "allow": ["Write", "Read", "Edit", "Bash"] }
}
JSON

# --- pai-proxy: manage the proxy (systemd user service if installed, else nohup) ---
cat > "$BIN_DIR/pai-proxy" <<EOF
#!/usr/bin/env bash
# pai-proxy: manage the p3c3 proxy. Uses the systemd user service if installed
# (survives reboot), else a nohup process. 'start' is idempotent + waits for /health.
set -uo pipefail
SRC="${PROXY_SRC}"
PORT="${PROXY_PORT}"
LOG="${PROXY_LOG}"
UNIT="p3c3-proxy.service"
URL="http://127.0.0.1:\${PORT}"

_up() { curl -fsS -m 2 "\$URL/health" >/dev/null 2>&1; }
_systemd() { systemctl --user is-enabled "\$UNIT" >/dev/null 2>&1; }
_wait_up() { for _ in \$(seq 1 30); do _up && return 0; sleep 0.5; done; return 1; }

start() {
  if _up; then echo "proxy already up on :\$PORT"; return 0; fi
  if _systemd; then
    systemctl --user start "\$UNIT"
  else
    ( cd "\$SRC" && PROXY_UPSTREAM_MODE=codex PROXY_READ_TIMEOUT=300 \\
        nohup uv run uvicorn server:app --host 127.0.0.1 --port "\$PORT" >>"\$LOG" 2>&1 & )
  fi
  if _wait_up; then echo "proxy up on :\$PORT"; return 0; fi
  echo "proxy FAILED to come up:"
  if _systemd; then journalctl --user -u "\$UNIT" -n 15 --no-pager 2>/dev/null; else tail -n 15 "\$LOG" 2>/dev/null; fi
  return 1
}
stop() {
  if _systemd; then systemctl --user stop "\$UNIT" && echo "proxy stopped (systemd)"; return 0; fi
  if pkill -f "uvicorn server:app" >/dev/null 2>&1; then
    for _ in \$(seq 1 20); do pgrep -f "uvicorn server:app" >/dev/null 2>&1 || break; sleep 0.25; done
    echo "proxy stopped"
  else
    echo "no proxy running"
  fi
}
status() {
  if _systemd; then echo "systemd: \$(systemctl --user is-active "\$UNIT" 2>/dev/null) (\$UNIT)"; fi
  if _up; then echo "UP   :\$PORT"; curl -fsS "\$URL/debug" 2>/dev/null && echo; else echo "DOWN :\$PORT"; return 1; fi
}
logs() {
  if _systemd; then journalctl --user -u "\$UNIT" -n "\${1:-40}" --no-pager 2>/dev/null
  else tail -n "\${1:-40}" "\$LOG" 2>/dev/null || echo "no log at \$LOG"; fi
}
case "\${1:-status}" in
  start)   start ;;
  stop)    stop ;;
  restart) if _systemd; then systemctl --user restart "\$UNIT"; _wait_up && echo "proxy restarted on :\$PORT" || echo "restart failed"; else stop; sleep 1; start; fi ;;
  status)  status ;;
  logs)    logs "\${2:-40}" ;;
  *) echo "usage: pai-proxy {start|stop|restart|status|logs}"; exit 1 ;;
esac
EOF
chmod +x "$BIN_DIR/pai-proxy"

# --- pai-codex: bring the proxy up, THEN run Claude Code on codex. Refuse if it can't start. ---
cat > "$BIN_DIR/pai-codex" <<EOF
#!/usr/bin/env bash
# Only launch Claude Code if the proxy is actually running - otherwise every turn
# would just connection-refuse. 'pai-proxy start' is idempotent (starts if down).
set -uo pipefail
if ! "${BIN_DIR}/pai-proxy" start; then
  echo "pai-codex: proxy is not running - not starting Claude Code." >&2
  echo "  diagnose: pai-doctor   |   logs: pai-proxy logs" >&2
  exit 1
fi
exec env \\
  ANTHROPIC_BASE_URL="http://127.0.0.1:${PROXY_PORT}" \\
  ANTHROPIC_AUTH_TOKEN=proxy \\
  ANTHROPIC_API_KEY= \\
  ANTHROPIC_MODEL="${CODEX_MODEL}" \\
  ANTHROPIC_SMALL_FAST_MODEL="${CODEX_MODEL}" \\
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \\
  claude --model "${CODEX_MODEL}" "\$@"
EOF
chmod +x "$BIN_DIR/pai-codex"

# --- pai-doctor: one-command diagnosis of the whole stack ---
cat > "$BIN_DIR/pai-doctor" <<EOF
#!/usr/bin/env bash
exec env PROXY_PORT="${PROXY_PORT}" CODEX_MODEL="${CODEX_MODEL}" PROXY_SRC="${PROXY_SRC}" PROXY_LOG="${PROXY_LOG}" \\
  bash "${PROXY_SRC}/scripts/doctor.sh" "\$@"
EOF
chmod +x "$BIN_DIR/pai-doctor"
# make sure ~/.local/bin + ~/.bun/bin are on PATH in future shells
if ! grep -q '.local/bin:.*\.bun/bin' "$HOME_DIR/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"' >> "$HOME_DIR/.bashrc"
fi
echo "wrote $HOME_DIR/.claude/settings.json and $BIN_DIR/pai-codex"

# ---- 9. start the proxy: systemd user service (survives reboot) if available, else nohup ----
say 9 "Start proxy (codex mode, 127.0.0.1:${PROXY_PORT})"
UV_BIN="$(command -v uv || echo "$HOME_DIR/.local/bin/uv")"
if systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$HOME_DIR/.config/systemd/user"
  cat > "$HOME_DIR/.config/systemd/user/p3c3-proxy.service" <<EOF
[Unit]
Description=P3C3 proxy (Claude Code on OpenAI codex)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROXY_SRC}
Environment=PATH=${BIN_DIR}:/usr/local/bin:/usr/bin:/bin
Environment=PROXY_UPSTREAM_MODE=codex
Environment=PROXY_READ_TIMEOUT=300
ExecStart=${UV_BIN} run uvicorn server:app --host 127.0.0.1 --port ${PROXY_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now p3c3-proxy.service >/dev/null 2>&1 || true
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true   # boot-start, not just on login
  sleep 3
  if curl -fsS "localhost:${PROXY_PORT}/health" >/dev/null 2>&1; then
    echo "proxy running as a systemd user service (auto-starts on boot; survives WSL restart)"
  else
    echo "systemd unit installed but not healthy yet; check: systemctl --user status p3c3-proxy"
  fi
else
  echo "systemd user instance not available; starting with nohup (pai-codex self-starts it on use)."
  echo "  For WSL boot-persistence: add a [boot] section with systemd=true to /etc/wsl.conf,"
  echo "  run 'wsl --shutdown', reopen WSL, and re-run this script."
  "$BIN_DIR/pai-proxy" restart
fi

# ---- 10. verify end-to-end -------------------------------------------------
say 10 "Verify: codex via proxy, then Claude Code"
curl -s --max-time 60 -X POST "localhost:${PROXY_PORT}/v1/messages" \
  -H 'content-type: application/json' \
  -d "{\"model\":\"${CODEX_MODEL}\",\"max_tokens\":20,\"system\":\"terse\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: PROXY-OK\"}]}" \
  | grep -o 'PROXY-OK' && echo "  ✓ codex via proxy" || echo "  ✗ proxy/codex failed - see $PROXY_LOG + RUNBOOK §troubleshooting"

"$BIN_DIR/pai-codex" -p "Reply with exactly the word INSTALL-OK and nothing else." --max-turns 1 2>/dev/null \
  | grep -o 'INSTALL-OK' && echo "  ✓ Claude Code → proxy → codex" || echo "  ✗ Claude Code turn failed"

say DONE "Use:  pai-codex            (interactive Claude Code on codex - auto-starts the proxy)
        pai-codex -p \"...\"     (headless)     [open a new shell first so ~/.local/bin is on PATH]
   Manage proxy:  pai-proxy {start|stop|restart|status|logs}
   Diagnose:      pai-doctor
   Skills: $(ls "$HOME_DIR/.claude/skills" | wc -l) PAI packs in ~/.claude/skills
   Proxy log: $PROXY_LOG  |  refresh codex token: codex login (or replace ~/.codex/auth.json)"
