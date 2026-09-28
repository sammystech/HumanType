#!/bin/zsh
# One-command rebuild + install of HumanType.
#   ./build_humantype.sh
# Produces ~/dist/HumanType.app, installs it to /Applications, refreshes
# ~/HumanType.dmg and ~/Downloads/HumanType.dmg, and self-tests the real bundle
# (including the main-thread keystroke path that used to crash on Start).
set -e

cd "$HOME"
VENV="$HOME/humantype-venv"
APP="$HOME/dist/HumanType.app"
EXE="$APP/Contents/MacOS/HumanType"
DMG="$HOME/HumanType.dmg"
INSTALLED="/Applications/HumanType.app"

echo "==> Sanity check: engine + UI import and construct"
"$VENV/bin/python" humantype_app.py --selftest

echo "==> Cleaning previous build"
rm -rf build dist "$DMG"

echo "==> Building app bundle with PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm HumanType.spec

# Prefer a real, stable signing identity (Apple Development / Developer ID) so
# the macOS Accessibility grant survives rebuilds. Fall back to ad-hoc ("-").
SIGN_ID=$(security find-identity -v -p codesigning 2>/dev/null \
          | awk '/Apple Development|Developer ID/{print $2; exit}')
[ -z "$SIGN_ID" ] && SIGN_ID="-"
echo "==> Code signing with identity: $SIGN_ID"
codesign --force --deep --sign "$SIGN_ID" "$APP"
codesign --verify --deep --strict "$APP" && echo "    signature OK"

echo "==> Verifying the bundled binary (main-thread typing path)"
"$EXE" --selftest
"$EXE" --selftest-typing   # exits non-zero if the Start crash path is back

echo "==> Quitting any running copy"
pkill -f "HumanType.app/Contents/MacOS/HumanType" 2>/dev/null || true
sleep 1

echo "==> Installing to /Applications"
rm -rf "$INSTALLED"
cp -R "$APP" "$INSTALLED"
codesign --force --deep --sign "$SIGN_ID" "$INSTALLED"
xattr -dr com.apple.quarantine "$INSTALLED" 2>/dev/null || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$INSTALLED" 2>/dev/null || true

echo "==> Building DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "HumanType" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

echo "==> Copying DMG to ~/Downloads"
cp "$DMG" "$HOME/Downloads/HumanType.dmg"

echo "==> Done. Installed: $INSTALLED"
ls -lh "$DMG" "$HOME/Downloads/HumanType.dmg"
