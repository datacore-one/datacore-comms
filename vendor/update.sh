#!/bin/sh
# Refresh the vendored packs from upstream, then review the diff before committing.
#   NEWSJACK_VERSION=v0.1.26 CLAUDE_SEO_REF=v2.6.3 sh vendor/update.sh
# newsjack: the official installer fetches the checksum-verified release (CLI to ~/.newsjack/bin) with
#           NEWSJACK_INSTALL_SKILLS=0, so it never copies skills into ~/.claude/skills; we copy them here.
# claude-seo: shallow clone of the given ref, minus screenshots/tests/CI.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

curl -fsSL https://raw.githubusercontent.com/elvisun/newsjack/main/install.sh -o "$tmp/install.sh"
NEWSJACK_INSTALL_SKILLS=0 NEWSJACK_RUN_SETUP=0 NEWSJACK_COLOR=never ${NEWSJACK_VERSION:+NEWSJACK_VERSION=$NEWSJACK_VERSION} sh "$tmp/install.sh" >/dev/null
b="$HOME/.newsjack/newsjack"
rm -rf "$here/newsjack/skills"
rsync -a "$b/skills" "$b/LICENSE" "$b/VERSION" "$b/COMMIT" "$b/skills-manifest.json" "$here/newsjack/"

git clone -q --depth 1 ${CLAUDE_SEO_REF:+--branch "$CLAUDE_SEO_REF"} https://github.com/AgriciDaniel/claude-seo "$tmp/seo"
git -C "$tmp/seo" rev-parse HEAD > "$tmp/seo-commit"
rsync -a --delete --exclude screenshots --exclude tests --exclude .github --exclude .devcontainer \
  --exclude .git --exclude __pycache__ "$tmp/seo/" "$here/claude-seo/"

echo "newsjack $(cat "$here/newsjack/VERSION") $(cat "$here/newsjack/COMMIT")"
echo "claude-seo $(cat "$tmp/seo-commit")  (record both in vendor/README.md, review git diff, then run vendor/link.sh)"
