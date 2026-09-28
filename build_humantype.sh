#!/bin/zsh
# One-command rebuild + install of HumanType.
#   ./build_humantype.sh
# Produces dist/HumanType.app and the drag-to-install dist/HumanType.dmg,
# installs the app to /Applications (skip with INSTALL=0), and self-tests the
# real bundle (including the main-thread keystroke path that used to crash on
# Start).
#
# The first run creates .venv and compiles PyInstaller's bootloader from
# source. That matters: the bootloader becomes the app's main executable, and
# AppKit only gives an app the Liquid Glass design when that executable links
# the macOS 26+ SDK. PyPI's prebuilt bootloader links an older SDK, which drops
# the whole app into the pre-Tahoe look. The check after the build enforces it.
set -e

cd "${0:A:h}"
VENV="${VENV:-$PWD/.venv}"
APP="dist/HumanType.app"
EXE="$APP/Contents/MacOS/HumanType"
DMG="dist/HumanType.dmg"
INSTALLED="/Applications/HumanType.app"

if [ ! -x "$VENV/bin/pyinstaller" ]; then
  echo "==> Creating $VENV (compiles PyInstaller's bootloader; takes a minute)"
  "${PYTHON:-python3}" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  PYINSTALLER_COMPILE_BOOTLOADER=1 "$VENV/bin/pip" install -q \
      --no-binary pyinstaller -r requirements.txt
fi

echo "==> Sanity check: engine + UI import and construct"
"$VENV/bin/python" humantype_app.py --selftest

echo "==> Cleaning previous build"
rm -rf build dist

echo "==> Building app bundle with PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm HumanType.spec

SDK=$(otool -l "$EXE" | awk '/LC_BUILD_VERSION/{f=1} f && $1=="sdk"{print $2; exit}')
if [ "${SDK%%.*}" -lt 26 ]; then
  echo "!! The app's executable links the macOS $SDK SDK, so it would run in"
  echo "!! compatibility mode without Liquid Glass. Rebuild the venv:"
  echo "!!     rm -rf $VENV && ./build_humantype.sh"
  exit 1
fi
echo "    executable links the macOS $SDK SDK (Liquid Glass enabled)"

# Sign with a stable identity (Apple Development / Developer ID) so the macOS
# Accessibility grant survives rebuilds AND in-app updates. Ad-hoc ("-") works
# but every new build then needs the permission granted again.
SIGN_ID="${SIGN_ID:-$(security find-identity -v -p codesigning 2>/dev/null \
          | awk '/Apple Development|Developer ID/{print $2; exit}')}"
[ -z "$SIGN_ID" ] && SIGN_ID="-"
echo "==> Code signing with identity: $SIGN_ID"
codesign --force --deep --sign "$SIGN_ID" "$APP"
codesign --verify --deep --strict "$APP" && echo "    signature OK"

echo "==> Verifying the bundled binary (main-thread typing path)"
"$EXE" --selftest
"$EXE" --selftest-typing   # exits non-zero if the Start crash path is back

echo "==> Building drag-to-install DMG"
"$VENV/bin/python" tools/make_dmg_background.py
"$VENV/bin/dmgbuild" -s dmg_settings.py -D app="$APP" "HumanType" "$DMG"
# Run the in-app updater's own gate on the app inside the DMG: installed copies
# refuse any update whose bundle fails this, so a DMG that fails it strands them.
MNT=$(hdiutil attach "$DMG" -nobrowse -readonly 2>/dev/null | grep -o '/Volumes/.*' | head -1)
if ! codesign --verify --deep --strict "$MNT/HumanType.app"; then
  hdiutil detach "$MNT" -force -quiet
  echo "!! The app inside $DMG fails strict signature verification; the updater would reject it."
  exit 1
fi
hdiutil detach "$MNT" -quiet
echo "    DMG passes the updater's signature check"

if [ "${INSTALL:-1}" = 1 ]; then
  echo "==> Quitting any running copy"
  pkill -f "HumanType.app/Contents/MacOS/HumanType" 2>/dev/null || true
  sleep 1

  echo "==> Installing to /Applications"
  rm -rf "$INSTALLED"
  ditto "$APP" "$INSTALLED"
  xattr -dr com.apple.quarantine "$INSTALLED" 2>/dev/null || true
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$INSTALLED" 2>/dev/null || true
  echo "==> Done. Installed: $INSTALLED"
else
  echo "==> Done (INSTALL=0: left /Applications alone)"
fi
ls -lh "$DMG"
