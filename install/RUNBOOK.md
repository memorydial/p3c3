# RUNBOOK: Claude Code on codex, WSL Ubuntu (LLM-followable)

Goal: on a **WSL Ubuntu** distro (a normal-user Linux box; also works in any fresh Ubuntu
container), get **Claude Code driving OpenAI's codex model (gpt-5.5) through a local pydantic
proxy, with PAI skills on top**.

```
Claude Code ──Anthropic API──▶ proxy 127.0.0.1:4000 ──OpenAI Responses──▶ codex (chatgpt.com/backend-api/codex)
  + PAI skills                       (PROXY_UPSTREAM_MODE=codex)            auth: ~/.codex/auth.json (ChatGPT sub)
```

This runbook is for an AI agent (or a human) to execute step by step. Each step has an
**expected output** to self-check against. The automated version is `install/ubuntu-setup.sh`;
this doc explains each step, the one branch point (codex auth), and troubleshooting.

Supported on `ubuntu:24.04` (root and non-root/sudo). On x86_64 hosts the same installers apply;
the agent adapts per §Common pitfalls if a package or path differs.

> **For the agent: execute autonomously.** The happy path is just `bash install/ubuntu-setup.sh`.
> It is non-interactive and makes every decision. Do NOT stop to ask the user to confirm
> steps, pick a directory/model, or approve commands. Run it, then report. Use the defaults in
> §Common pitfalls. Only escalate to the user on a blocker you genuinely cannot resolve after
> trying (and after checking §troubleshooting).

---

## Prerequisites (your part: two things)

1. **codex auth**: run `codex login` once (interactive; on WSL it opens your Windows browser).
   It writes `~/.codex/auth.json`, which the proxy reads. If you already run codex as your agent
   in this WSL, it's done. See §codex-auth.
2. **The repo** (public: no GitHub auth needed):
   `git clone https://github.com/memorydial/p3c3 ~/p3c3 && cd ~/p3c3`. The installer runs from
   inside the clone and finds the proxy at the repo root.

Everything else the script installs from the internet.

---

## Steps (what `ubuntu-setup.sh` does, with expected output)

| # | Step | Command (essence) | Expected |
|---|------|-------------------|----------|
| 1 | base pkgs | `apt-get install curl git ripgrep python3 python3-venv xz-utils unzip` | `Python 3.12.x` |
| 2 | Node 22 | NodeSource setup (download-then-run) + `apt-get install nodejs` | `node v22.x / npm 10.x` |
| 3 | uv + bun | astral uv installer + bun installer (download-then-run) | `uv 0.11.x / bun 1.3.x` |
| 4 | CLIs | `npm i -g @anthropic-ai/claude-code @openai/codex` | `claude 2.1.x / codex-cli 0.13x` |
| 5 | codex auth | ensure `~/.codex/auth.json` (see §codex-auth) | `found ~/.codex/auth.json` |
| 6 | proxy | `uv sync` in the cloned repo root | `proxy synced` |
| 7 | PAI | clone PAI, copy `Packs/<N>/src` → `~/.claude/skills/<N>` (skip Interceptor) | `installed N PAI skills` |
| 8 | config | `~/.claude/settings.json` allow-list + `~/.local/bin/pai-codex` launcher | `wrote settings.json and pai-codex` |
| 9 | proxy up | `PROXY_UPSTREAM_MODE=codex uvicorn server:app --host 127.0.0.1 --port 4000` | `{"ok":true} (proxy up)` |
| 10 | verify | curl the proxy + `pai-codex -p` | `✓ codex via proxy` and `✓ Claude Code → proxy → codex` |

Run it from the cloned repo: `bash install/ubuntu-setup.sh`. Re-runnable.

---

## §codex-auth: the one branch point

The proxy reads `~/.codex/auth.json` (the OpenAI `codex` CLI's ChatGPT-subscription token:
`tokens.access_token` + `tokens.account_id` + `tokens.refresh_token`).

**WSL (the normal path):** just run `codex login`: on WSL it opens your Windows browser for the
ChatGPT OAuth and writes the file. If you already run codex in this WSL, it's already there.
```bash
codex login
```

**Headless container fallback** (no browser/loopback reachable, e.g. a remote container): on a
machine where you've run `codex login`, copy the file in:
```bash
docker cp ~/.codex/auth.json <container>:/root/.codex/auth.json && docker exec <container> chmod 600 /root/.codex/auth.json
```

The token auto-refreshes via its `refresh_token` when the `codex` CLI runs (the proxy itself does
not refresh it); if the proxy starts returning 401/502 after a long idle, refresh it
(`codex login` again, or run any `codex exec "ok"` turn).

---

## Using it

```bash
pai-codex                 # interactive Claude Code TUI, on codex/gpt-5.5, with PAI skills
pai-codex -p "do X"       # headless one-shot
```
`pai-codex` is a launcher the installer wrote to `~/.local/bin/pai-codex`: it sets `ANTHROPIC_BASE_URL`
(the proxy), the codex model, and the small-fast model, then runs `claude`.

---

## Common pitfalls: things to avoid (read before you start)

- **Don't ask the user to confirm steps.** The installer is non-interactive by design. Run it.
- **Don't reimplement the script by hand.** `bash install/ubuntu-setup.sh` already does all 10
  steps in the right order. Run it; do not execute the steps individually.
- **Don't patch the proxy for codex 400/502 errors.** It normalizes codex's required fields
  (instructions, `store:false`, stream-only, role-typed `input_text`/`output_text`, Cloudflare
  headers, omitted `max_output_tokens`). A 400/502 usually means stale code or an expired token:
  update/re-clone, or refresh the token.
- **Never use `claude --bare`.** It forces `ANTHROPIC_API_KEY` auth and bypasses the proxy
  routing: wrong backend, and real billing.
- **Don't forget `ANTHROPIC_SMALL_FAST_MODEL`.** If you launch `claude` by hand instead of via
  `pai-codex`, set it to the codex model: otherwise the background model 404s and startup
  **hangs** (exit 124). The `pai-codex` launcher sets it for you; prefer the launcher.
- **Don't `curl | bash`.** Download-then-run (`curl -o file && bash file`). The script does this.
- **Don't `sudo` the uv/bun installers.** They install into `$HOME`; only apt + global `npm`
  need sudo (the script applies sudo exactly where needed when non-root).
- **Don't claim success without the verify turn.** "It installed" ≠ "it works": run
  `pai-codex -p "say hi"` and confirm a real reply.
- **Don't bind the proxy to `0.0.0.0`** on a shared/WSL host unless you mean to: `127.0.0.1`
  keeps it local (the script default).
- **Don't re-clone PAI or overwrite `~/.codex/auth.json`** if present: both are idempotent
  guards in the script; respect them.

## Troubleshooting (common failure modes)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `claude` **hangs / exit 124** on first turn | background "small/fast" model defaults to a Claude haiku name the backend 404s | set `ANTHROPIC_SMALL_FAST_MODEL` to the codex model (the `pai-codex` launcher already does) |
| `ConnectionRefused` from Claude Code | proxy not running | `curl localhost:4000/health`; restart step 9; check `~/.p3c3-proxy.log` |
| 502 `{"detail":"Stream must be set to true"}` | non-stream request to stream-only codex | the proxy force-streams and aggregates; update to the latest version |
| 502 `Invalid value: 'output_text'/'input_text'` | role-typed content parts | the proxy types parts by role (assistant→output_text, user/system→input_text); update to the latest version |
| 502 `Instructions are required` / `Store must be set to false` | codex required fields | the proxy sets these in codex mode; update to the latest version |
| 401 / sudden 502s after idle | codex token expired | refresh: `codex login` or replace `~/.codex/auth.json` (§codex-auth) |
| `--dangerously-skip-permissions cannot be used with root` | container runs as root | use the settings.json allow-list (installer writes it); don't pass that flag |
| Cloudflare 403 | missing codex headers | proxy sends `originator: codex_cli_rs` + UA + `ChatGPT-Account-ID`; ensure codex mode is on |
| PAI skill voice errors | macOS/Pulse curls in some packs | harmless fire-and-forget no-ops; ignore (Interceptor pack is skipped) |
| task stops early / hits max turns on gpt-5.5 | the model re-verifies and exhausts the default turn budget | raise Claude Code's `--max-turns` |

Proxy debug: restart step 9 with `PROXY_DEBUG=1`: it logs request shapes + upstream error
bodies to `~/.p3c3-proxy.log` (never tokens or prompt content).

---

## Out of scope

- A separate agent harness for the codex token: not needed; the proxy reads codex's own `~/.codex/auth.json`.
- Anything beyond the public PAI skill **Packs + Tools**: the public PAI repo ships Packs and
  Tools; this installer sets up the public PAI skill Packs + bun for Tools, nothing else.
- An OpenAI API-key path: this runbook uses the ChatGPT **subscription** via the codex CLI. For
  the pay-per-token API-key variant, point the proxy at OpenAI and alias the models, no code change:
  ```bash
  export PROXY_UPSTREAM_MODE=openai
  export OPENAI_BASE_URL=https://api.openai.com/v1
  export OPENAI_API_KEY=sk-...
  export PROXY_MODEL_MAP='{"sonnet-codex":"gpt-5.1-codex","opus-codex":"gpt-5.1-codex-max"}'
  ```
  then launch against a mapped alias (`claude --model sonnet-codex`).
