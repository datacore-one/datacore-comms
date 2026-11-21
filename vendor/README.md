# Vendored skill packs

Third-party skills the comms pipeline runs, kept inside this module so the module is the one place they
live. Where each runs in the pipeline, and what it may never do: `../docs/visibility-and-pr-stack.md`.

| Pack | Upstream | Pinned | License | Made live by |
|---|---|---|---|---|
| `newsjack/` (35 PR and AI-visibility skills) | github.com/elvisun/newsjack | v0.1.25, commit f68db4a7e89ad93e7fdbb3111ba9dda16dcd4719 | MIT (`newsjack/LICENSE`) | `link.sh`: each skill linked as `~/.claude/skills/comms:<name>` |
| `claude-seo/` (29 skills, 20 agents, scripts, one hook) | github.com/AgriciDaniel/claude-seo | 2.6.2, commit 468ed5b0d9e3249394783710e54abb02837e326b | MIT (`claude-seo/LICENSE`) | `link.sh`: registered as a Claude Code plugin from this folder |

Not vendored, installed per host:
- **The newsjack CLI** (a prebuilt, checksum-verified binary at `~/.newsjack/bin/newsjack`). It is a platform
  binary, like ffmpeg. `update.sh` installs or refreshes it with skill copying switched off.
- **claude-seo's Python runtime and headless browser**, created by `claude-seo setup` in the user's application
  data folder.

Monitor profiles, briefs and run data are content and live in the space
(`[space]/1-tracks/comms/newsjack/`), never here.

Changes from upstream: claude-seo ships without `screenshots/`, `tests/` and CI folders. Nothing else is
edited. Do not edit vendored files in place; fix upstream or patch through `update.sh`.

## New host
```sh
# 1. the newsjack CLI only (skills come from this folder); review install.sh first
curl -fsSL https://raw.githubusercontent.com/elvisun/newsjack/main/install.sh -o /tmp/nj.sh
NEWSJACK_VERSION=v0.1.25 NEWSJACK_INSTALL_SKILLS=0 NEWSJACK_RUN_SETUP=0 sh /tmp/nj.sh
# 2. make the vendored packs live in Claude Code
sh vendor/link.sh
# 3. once per host: claude-seo's Python runtime and Chromium
sh vendor/claude-seo/scripts/claude-seo setup
```

To move to a newer upstream version, run `update.sh` (it pulls the latest unless you pin
`NEWSJACK_VERSION` / `CLAUDE_SEO_REF`), review the git diff, update the table above, then run `link.sh`.

```sh
NEWSJACK_VERSION=v0.1.26 CLAUDE_SEO_REF=<tag> sh vendor/update.sh
```
