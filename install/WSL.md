# WSL setup: Claude Code on codex (LLM-followable)

How to make a **WSL Ubuntu** distro run Claude Code on OpenAI's codex model, via the P3C3
proxy, with PAI skills. This is the WSL-specific companion to `RUNBOOK.md` (which has the full
per-step detail + troubleshooting). Two steps:

1. **You** auth with codex (one interactive step).
2. **Hand it to the LLM (codex)**: it installs and wires up everything else, then tests.

WSL is *easier* than a headless container: it's a real Linux env with a Windows browser, so
`codex login` works interactively, and everything runs in WSL itself (no Docker, no container,
all on `127.0.0.1`).

---

## Step 1: You: codex CLI + auth (the only manual step)

If you're already running `codex` as your coding agent in WSL, **you've already done this**:
`~/.codex/auth.json` exists and step 1 is complete; skip to step 2.

Otherwise, in the WSL shell:
```bash
# node is needed for the codex CLI; if you don't have it yet:
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/ns.sh && sudo -E bash /tmp/ns.sh && sudo apt-get install -y nodejs
sudo npm install -g @openai/codex
codex login        # opens the Windows browser → ChatGPT account → writes ~/.codex/auth.json
```
That ChatGPT-subscription token is what the proxy will reuse.

---

## Step 2: Hand the LLM (codex) the install

On WSL the happy path is the script. Paste this to codex (or just run it yourself):

> Set up P3C3 on this Ubuntu box, executing every step yourself without asking me to confirm or
> approve anything:
> 1. `git clone https://github.com/memorydial/p3c3 ~/p3c3 && cd ~/p3c3`
> 2. `bash install/ubuntu-setup.sh` (installs Node, uv, bun, Claude Code, the proxy, and the PAI
>    skills, wires Claude Code to codex through the proxy, and self-verifies). Let it run end to
>    end; don't reimplement its steps or edit the proxy.
> 3. When it prints `✓ Claude Code → proxy → codex`, run `pai-codex -p "say hi"` and show the output.
>
> Defaults: model `gpt-5.5`, clone dir `~/p3c3`, my `codex login` is already done. This box is
> x86_64; if a package or path differs, fix it and continue.
> The landmines and troubleshooting are in `install/RUNBOOK.md`.

The repo is **public**, so the clone needs no GitHub auth. To install each piece by hand instead
of via the script, the per-step table (with expected output) is in [`RUNBOOK.md`](RUNBOOK.md).

---

## Step 3: Use it

```bash
exec $SHELL -l     # new shell so ~/.local/bin (the `pai-codex` launcher) is on PATH
pai-codex                 # interactive Claude Code TUI, on codex/gpt-5.5, with PAI skills
pai-codex -p "do X"       # headless one-shot
```

---

## WSL-specific notes (where it differs from the container build)

| Thing | WSL | Why it's handled |
|------|-----|------------------|
| **User** | normal user, not root | script auto-detects and uses `sudo` for apt + global npm; `pai-codex` + proxy log go in `$HOME` (no sudo) |
| **codex login** | interactive works (Windows browser) | no headless workaround needed; that's your step 1 |
| **Topology** | everything in WSL, `127.0.0.1` | no Docker, no `host.docker.internal`, no container exec |
| **Arch** | **x86_64** | all installers (NodeSource/uv/bun/npm) support x86_64; the agent adapts if a package or path differs |
| **PATH** | `~/.local/bin` + `~/.bun/bin` | script appends them to `~/.bashrc`; open a fresh shell after install |
| **Proxy persistence** | systemd user service (auto), else `nohup` | if WSL systemd is on, the installer registers a `p3c3-proxy` user service with linger, so the proxy auto-starts on boot and survives a WSL restart; without systemd it falls back to `nohup` and `pai-codex` self-starts it on use. Enable systemd: put `[boot]` + `systemd=true` in `/etc/wsl.conf`, then `wsl --shutdown` |

## If it breaks

`install/RUNBOOK.md` §troubleshooting documents the failure modes (codex 502 shapes, the
`ANTHROPIC_SMALL_FAST_MODEL` startup hang, token expiry, etc.). The most likely WSL-only snag is
an x86_64 package/path difference in steps 1-4; re-run with the failing command surfaced and
let codex adjust. Refresh an expired token with `codex login`.
