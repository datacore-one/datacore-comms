#!/bin/sh
# Make the vendored skill packs live in Claude Code from this module (the one place they live).
#   newsjack  -> each skill linked as ~/.claude/skills/comms:<name> (same convention as comms:engage)
#   claude-seo -> registered as a Claude Code plugin from this folder (local marketplace)
# Re-run after vendor/update.sh or on a new host. Safe to repeat.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
mkdir -p "$dest"

for d in "$here"/newsjack/skills/*/; do
  name=$(basename "$d")
  ln -sfn "${d%/}" "$dest/comms:$name"
done
# Shared doctrine files the skills read at ../ETHICS.md and ../WHY-NOT-SPAM.md resolve inside the vendor tree.
echo "linked $(ls -d "$here"/newsjack/skills/*/ | wc -l | tr -d ' ') newsjack skills into $dest as comms:<name>"

if command -v claude >/dev/null 2>&1 && [ "${SKIP_PLUGIN:-0}" != "1" ]; then
  claude plugin marketplace add "$here/claude-seo" >/dev/null 2>&1 || true
  claude plugin install claude-seo@agricidaniel-claude-seo >/dev/null 2>&1 || true
  claude plugin list 2>/dev/null | grep -q "claude-seo@agricidaniel-claude-seo" && echo "claude-seo plugin registered from $here/claude-seo"
fi
