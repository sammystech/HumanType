#!/bin/zsh
# Notarize a Developer ID-signed HumanType.app and staple the ticket to it.
#   tools/notarize.sh dist/HumanType.app
#
# Goes through the Apple account signed in to Xcode (Settings > Accounts, team
# CH6CSBA54G), the same route as the team's Xcode projects, so no app-specific
# password or notarytool profile is needed. HumanType isn't an Xcode project,
# so the app is wrapped in a minimal .xcarchive that `xcodebuild -exportArchive`
# accepts. Xcode re-signs it, uploads it, and once Apple approves it,
# `-exportNotarizedApp` hands back the stapled copy, which replaces the input.
set -e

APP="${1:A}"
TEAM_ID="${TEAM_ID:-CH6CSBA54G}"
WAIT_MIN="${NOTARY_WAIT_MIN:-30}"
WORK="${0:A:h:h}/build/notary"
ARCHIVE="$WORK/HumanType.xcarchive"
die() { echo "!! $*"; exit 1; }

[ -d "$APP" ] || die "No app at $APP"
SIGNER=$(codesign -dvv "$APP" 2>&1 | sed -n 's/^Authority=//p' | head -1)
[[ "$SIGNER" == "Developer ID Application:"* ]] \
    || die "Notarization needs a Developer ID signature; $APP is signed by: ${SIGNER:-nothing}"
VERSION=$(defaults read "$APP/Contents/Info.plist" CFBundleShortVersionString)

rm -rf "$WORK"
mkdir -p "$ARCHIVE/Products/Applications"
ditto "$APP" "$ARCHIVE/Products/Applications/HumanType.app"
cat > "$ARCHIVE/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>ApplicationProperties</key><dict>
        <key>ApplicationPath</key><string>Applications/HumanType.app</string>
        <key>Architectures</key><array><string>arm64</string></array>
        <key>CFBundleIdentifier</key><string>com.humantype.app</string>
        <key>CFBundleShortVersionString</key><string>$VERSION</string>
        <key>CFBundleVersion</key><string>$VERSION</string>
        <key>SigningIdentity</key><string>$SIGNER</string>
        <key>Team</key><string>$TEAM_ID</string>
    </dict>
    <key>ArchiveVersion</key><integer>2</integer>
    <key>CreationDate</key><date>$(date -u +%Y-%m-%dT%H:%M:%SZ)</date>
    <key>Name</key><string>HumanType</string>
    <key>SchemeName</key><string>HumanType</string>
</dict></plist>
PLIST
cat > "$WORK/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>method</key><string>developer-id</string>
    <key>destination</key><string>upload</string>
    <key>signingStyle</key><string>automatic</string>
    <key>teamID</key><string>$TEAM_ID</string>
</dict></plist>
PLIST

echo "==> Submitting $VERSION to Apple for notarization (via Xcode's account)"
xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist "$WORK/ExportOptions.plist" \
    -exportPath "$WORK/upload-receipt" -allowProvisioningUpdates > "$WORK/export.log" 2>&1 \
    || { grep -iE "error" "$WORK/export.log" | sort -u | head -20; die "Upload failed. Log: $WORK/export.log"; }

for attempt in $(seq 1 $(( WAIT_MIN * 60 / 25 ))); do    # every 25s
    if xcodebuild -exportNotarizedApp -archivePath "$ARCHIVE" -exportPath "$WORK/notarized" \
            > "$WORK/notarize.log" 2>&1; then
        echo "    notarized after ~$((attempt * 25))s"
        break
    fi
    grep -q "No Accounts" "$WORK/notarize.log" \
        && die "Xcode is signed out. Xcode > Settings > Accounts > + > Apple ID (team $TEAM_ID), then release again."
    if grep -qiE "invalid|rejected|not accepted" "$WORK/notarize.log"; then
        grep -iE "error|invalid|reject" "$WORK/notarize.log" | head -10
        die "Apple rejected the submission. Log: $WORK/notarize.log"
    fi
    [ "$attempt" -eq $(( WAIT_MIN * 60 / 25 )) ] \
        && die "Not notarized after $WAIT_MIN minutes; Apple is still processing. Run the release again later."
    sleep 25
done

NOTARIZED="$WORK/notarized/HumanType.app"
xcrun stapler validate "$NOTARIZED" >/dev/null 2>&1 || die "No notarization ticket stapled to the app."
ASSESS=$(spctl -a -t exec -vv "$NOTARIZED" 2>&1 || true)
echo "$ASSESS" | grep -q "source=Notarized Developer ID" \
    || { echo "$ASSESS"; die "Gatekeeper doesn't accept this as a notarized Developer ID app."; }
rm -rf "$APP"
ditto "$NOTARIZED" "$APP"
echo "    Gatekeeper: accepted (Notarized Developer ID), ticket stapled"
