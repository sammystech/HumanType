#!/bin/zsh
# Ship an update to everyone running HumanType.
#   ./release.sh 1.2.0 "What changed, in a sentence or two"
#
# Bumps APP_VERSION, builds and self-tests the app, commits, tags v1.2.0,
# pushes, and publishes a GitHub Release with HumanType.dmg attached. Every
# installed copy checks the latest release a few seconds after launch and every
# 6 hours, then offers Download & Install. Menu bar > Check for Updates checks
# right away.
#
# Set INSTALL=0 to publish without also replacing your own /Applications copy.
set -e
cd "${0:A:h}"

VERSION="$1"
NOTES="$2"
if [[ ! "$VERSION" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' || -z "$NOTES" ]]; then
  echo 'usage: ./release.sh X.Y.Z "release notes"'
  exit 1
fi
CURRENT=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' humantype_ui.py)
# Installed copies only move to a strictly higher version.
python3 -c 'import sys; t = lambda v: tuple(map(int, v.split("."))); sys.exit(t(sys.argv[2]) <= t(sys.argv[1]))' \
    "$CURRENT" "$VERSION" || { echo "Version $VERSION must be higher than the current $CURRENT."; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Commit or stash your changes first: releases are built from a clean tree."; exit 1; }
[[ "$(git branch --show-current)" == main ]] || { echo "Releases go out from main."; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first."; exit 1; }

echo "==> $CURRENT -> $VERSION"
sed -i '' "s/^APP_VERSION = \".*\"/APP_VERSION = \"$VERSION\"/" humantype_ui.py
TRAPZERR() { git checkout -- humantype_ui.py; echo "!! Release failed; version bump reverted."; }

./build_humantype.sh

git commit -q -am "Release v$VERSION"
git tag -a "v$VERSION" -m "HumanType $VERSION"
unfunction TRAPZERR
git push -q origin main "v$VERSION"
gh release create "v$VERSION" dist/HumanType.dmg \
    --title "HumanType $VERSION" --notes "$NOTES"
echo "==> Released v$VERSION. Installed copies will offer it on their next check."
