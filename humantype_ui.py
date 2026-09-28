#!/usr/bin/env python3
"""Native macOS menu-bar UI for HumanType.

A small AppKit app: lives in the menu bar, opens a Liquid Glass window with a
paste box, a speed slider, a realism picker, Start / Pause-Resume / Stop / Quit
buttons, snippets, and click-to-record global shortcuts. All the typing logic
lives in humantype.py; this file is just the interface.

The look is built from the real macOS 26+ glass material (NSGlassEffectView and
glass-bezel buttons) floating over a slowly drifting colour field, because glass
only reads as glass when there is something behind it to refract. AppKit gives
an app the new design only when its main executable links the macOS 26+ SDK;
build_humantype.sh checks that for the bundle."""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
import urllib.parse

import objc
from AppKit import (
    NSAnimationContext,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSControlSizeLarge,
    NSControlSizeRegular,
    NSControlSizeSmall,
    NSEventMaskKeyDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
    NSEvent,
    NSFont,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
    NSImageLeading,
    NSImageOnly,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSPasteboard,
    NSPasteboardTypeString,
    NSScrollView,
    NSSearchField,
    NSSegmentedControl,
    NSSlider,
    NSStatusBar,
    NSSwitch,
    NSTextAlignmentCenter,
    NSTextAlignmentRight,
    NSTextField,
    NSTextView,
    NSVariableStatusItemLength,
    NSView,
    NSVisualEffectBlendingModeWithinWindow,
    NSVisualEffectMaterialMenu,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
    NSWorkspace,
)
from Foundation import (
    NSMakePoint,
    NSObject,
    NSRunLoopCommonModes,
    NSThread,
    NSUserDefaults,
    NSValue,
)
from Quartz import (
    CABasicAnimation,
    CAGradientLayer,
    CAMediaTimingFunction,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    CFRunLoopRemoveSource,
    CGEventMaskBit,
    CGEventTapCreate,
    CGEventTapEnable,
    kCAMediaTimingFunctionEaseInEaseOut,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGEventKeyDown,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    kCGSessionEventTap,
)

try:
    from Quartz import kCFRunLoopCommonModes
except ImportError:  # older PyObjC exposes it under CoreFoundation
    from CoreFoundation import kCFRunLoopCommonModes
from pynput.keyboard import Controller

import humantype as ht

# ---------------------------------------------------------------------------
# App version + updates
# ---------------------------------------------------------------------------
APP_VERSION = "1.0.0"

# Updates ship as GitHub Releases: release.sh bumps APP_VERSION, builds the DMG,
# tags vX.Y.Z and attaches the DMG. Every installed copy polls the latest one.
GITHUB_REPO     = "sammystech/HumanType"
UPDATE_URL      = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_INTERVAL = 6 * 3600   # re-check while running; menu-bar apps run for days


def _version_tuple(v):
    """Convert "1.2.3" to (1, 2, 3) for comparisons."""
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0, 0, 0)


def fetch_latest_release():
    """The newest GitHub release as {"version", "download_url", "release_notes"},
    or None when there is no release (or it has no DMG attached)."""
    req = urllib.request.Request(UPDATE_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"HumanType/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:      # repo has no releases yet
            return None
        raise
    dmg = next((a.get("browser_download_url") for a in data.get("assets", [])
                if a.get("name", "").endswith(".dmg")), None)
    if not dmg:
        return None
    return {"version": data.get("tag_name", "0.0.0").lstrip("v"),
            "download_url": dmg,
            "release_notes": (data.get("body") or "").strip()
                             or "Improvements and bug fixes."}


# ---------------------------------------------------------------------------
# License system — replace VALIDATE_URL with your deployed Vercel URL.
# ---------------------------------------------------------------------------
VALIDATE_URL = "https://YOUR-PROJECT.vercel.app/api/validate"
BUY_URL      = "https://YOUR_STRIPE_PAYMENT_LINK"

# The activation gate only switches on once a real license server is deployed:
# with the placeholder URL no key can ever validate, so nobody could get in.
LICENSING_ENABLED = "YOUR-PROJECT" not in VALIDATE_URL


# ---------------------------------------------------------------------------
# Liquid Glass primitives
# ---------------------------------------------------------------------------
try:
    GlassEffectView = objc.lookUpClass("NSGlassEffectView")                  # macOS 26+
    GlassEffectContainerView = objc.lookUpClass("NSGlassEffectContainerView")
except objc.nosuchclass_error:
    GlassEffectView = GlassEffectContainerView = None
HAS_GLASS = GlassEffectView is not None

BEZEL_GLASS   = 16   # NSBezelStyleGlass
BEZEL_ROUNDED = 1    # NSBezelStyleRounded: the pre-26 fallback
TINT_PRIMARY  = 2    # NSTintProminencePrimary
BORDER_CIRCLE = 3    # NSControlBorderShapeCircle


def reduce_motion():
    try:
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


class ColorView(NSView):
    """A layer-backed fill whose NSColor re-resolves on every light/dark switch
    (a CGColor set once on a layer freezes at its creation-time appearance)."""

    def wantsUpdateLayer(self):
        return True

    def updateLayer(self):
        color = getattr(self, "_color", None)
        if color is not None:
            self.layer().setBackgroundColor_(color.CGColor())

    def viewDidChangeEffectiveAppearance(self):
        objc.super(ColorView, self).viewDidChangeEffectiveAppearance()
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_color(self, color):
        self._color = color
        self.setNeedsDisplay_(True)


def color_view(rect, color, radius=0.0):
    v = ColorView.alloc().initWithFrame_(rect)
    v.setWantsLayer_(True)
    v.layer().setCornerRadius_(radius)
    v.set_color(color)
    return v


class FlippedView(NSView):
    """Top-down coordinates, so a scrolling list starts at its first row."""

    def isFlipped(self):
        return True


# (x, y as fractions of the view, diameter as a fraction of its width,
#  colour, seconds for one drift leg)
AURORA_BLOBS = (
    (0.08, 0.88, 0.95, NSColor.systemBlueColor,   17.0),
    (0.98, 0.60, 0.85, NSColor.systemPurpleColor, 23.0),
    (0.30, 0.06, 0.80, NSColor.systemTealColor,   19.0),
    (0.85, 0.02, 0.60, NSColor.systemPinkColor,   29.0),
)


class AuroraView(NSView):
    """The ambient backdrop: soft colour fields drifting slowly behind the
    glass, which is what gives the glass something to bend."""

    def wantsUpdateLayer(self):
        return True

    def viewDidChangeEffectiveAppearance(self):
        objc.super(AuroraView, self).viewDidChangeEffectiveAppearance()
        self.setNeedsDisplay_(True)

    def updateLayer(self):
        dark = (self.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]) == NSAppearanceNameDarkAqua)
        self.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        alpha = 0.42 if dark else 0.30
        for blob, color in getattr(self, "_blobs", ()):
            blob.setColors_([color.colorWithAlphaComponent_(alpha).CGColor(),
                             color.colorWithAlphaComponent_(0.0).CGColor()])


def aurora_view(rect):
    v = AuroraView.alloc().initWithFrame_(rect)
    v.setWantsLayer_(True)
    v.layer().setMasksToBounds_(True)
    w, h = rect.size.width, rect.size.height
    still = reduce_motion()
    blobs = []
    for i, (rx, ry, rd, color, secs) in enumerate(AURORA_BLOBS):
        d = rd * w
        blob = CAGradientLayer.layer()
        blob.setType_("radial")
        blob.setStartPoint_((0.5, 0.5))
        blob.setEndPoint_((1.0, 1.0))
        blob.setBounds_(((0, 0), (d, d)))
        blob.setPosition_((rx * w, ry * h))
        if not still:
            drift = CABasicAnimation.animationWithKeyPath_("position")
            drift.setFromValue_(NSValue.valueWithPoint_(NSMakePoint(rx * w, ry * h)))
            drift.setToValue_(NSValue.valueWithPoint_(NSMakePoint(
                rx * w + (0.16 if i % 2 else -0.16) * w, ry * h + 0.10 * h)))
            drift.setDuration_(secs)
            drift.setAutoreverses_(True)
            drift.setRepeatCount_(1e9)
            drift.setTimingFunction_(
                CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut))
            blob.addAnimation_forKey_(drift, "drift")
        v.layer().addSublayer_(blob)
        blobs.append((blob, color()))
    v._blobs = blobs
    v.setNeedsDisplay_(True)
    return v


def glass_panel(rect, radius=22.0, tint=None):
    """A real Liquid Glass panel (NSGlassEffectView). Returns (panel, content):
    add subviews to `content`, whose coordinates are local to the panel."""
    content = NSView.alloc().initWithFrame_(
        NSMakeRect(0, 0, rect.size.width, rect.size.height))
    if HAS_GLASS:
        panel = GlassEffectView.alloc().initWithFrame_(rect)
        panel.setCornerRadius_(radius)
        if tint is not None:
            panel.setTintColor_(tint)
        panel.setContentView_(content)
    else:  # pre-26: a frosted blur with the same geometry
        panel = NSVisualEffectView.alloc().initWithFrame_(rect)
        panel.setMaterial_(NSVisualEffectMaterialMenu)
        panel.setBlendingMode_(NSVisualEffectBlendingModeWithinWindow)
        panel.setState_(NSVisualEffectStateActive)
        panel.setWantsLayer_(True)
        panel.layer().setCornerRadius_(radius)
        panel.layer().setMasksToBounds_(True)
        panel.addSubview_(content)
    return panel, content


def glass_group(rect, spacing=12.0):
    """NSGlassEffectContainerView: glass controls inside it render as one
    material and melt into each other when close. Returns (group, content)."""
    content = NSView.alloc().initWithFrame_(
        NSMakeRect(0, 0, rect.size.width, rect.size.height))
    if not HAS_GLASS:
        content.setFrame_(rect)
        return content, content
    group = GlassEffectContainerView.alloc().initWithFrame_(rect)
    group.setSpacing_(spacing)
    group.setContentView_(content)
    return group, content


def set_symbol(button, name, *, only=False, label=None):
    """Put an SF Symbol on a button, leading its title (or alone)."""
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, label)
    if img is None:
        return
    button.setImage_(img)
    button.setImagePosition_(NSImageOnly if only else NSImageLeading)
    button.setImageHugsTitle_(True)


def _typo_label(rate):
    return f"{rate * 100:.0f}%" if rate > 0.0005 else "Preset"


class LicenseManager:
    """Stores and validates HumanType license keys."""

    @staticmethod
    def is_licensed():
        return bool(NSUserDefaults.standardUserDefaults()
                    .boolForKey_("ht_licensed"))

    @staticmethod
    def stored_key():
        return NSUserDefaults.standardUserDefaults().stringForKey_("ht_license_key") or ""

    @staticmethod
    def store(key, email=""):
        d = NSUserDefaults.standardUserDefaults()
        d.setBool_forKey_(True,  "ht_licensed")
        d.setObject_forKey_(key, "ht_license_key")
        if email:
            d.setObject_forKey_(email, "ht_license_email")

    @staticmethod
    def validate(key):
        """Call the validation endpoint. Returns (valid: bool, info: str)."""
        try:
            body = urllib.parse.urlencode({"key": key}).encode()
            req  = urllib.request.Request(
                VALIDATE_URL, data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read())
            if data.get("valid"):
                return True, data.get("email", "")
            return False, data.get("message", "Invalid license key.")
        except Exception as exc:
            return False, f"Could not reach license server — check internet connection. ({exc})"


# ---------------------------------------------------------------------------
# Activation window (shown on first launch; blocks main UI until licensed)
# ---------------------------------------------------------------------------

class LicenseWindowController(NSObject):

    def init(self):
        self = objc.super(LicenseWindowController, self).init()
        if self is None:
            return None
        self._on_activated = None
        self._build()
        return self

    @objc.python_method
    def show(self, on_activated):
        self._on_activated = on_activated
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build(self):
        W2, H2 = 440, 340
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskFullSizeContentView)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W2, H2), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("Activate HumanType")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(NSWindowTitleHidden)
        self.window.setMovableByWindowBackground_(True)
        self.window.setReleasedWhenClosed_(False)

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W2, H2))
        root.addSubview_(aurora_view(root.bounds()))
        self.window.setContentView_(root)
        cw, ch = W2 - 40, H2 - 64
        card, c = glass_panel(NSMakeRect(20, 20, cw, ch), radius=26)
        root.addSubview_(card)

        def lbl(text, x, y, w, h, bold=False, size=13, gray=False):
            f = NSTextField.labelWithString_(text)
            f.setFrame_(NSMakeRect(x, y, w, h))
            f.setFont_(NSFont.systemFontOfSize_weight_(
                size, NSFontWeightBold if bold else NSFontWeightRegular))
            if gray:
                f.setTextColor_(NSColor.secondaryLabelColor())
            return f

        c.addSubview_(lbl("Activate HumanType", 22, ch - 50, cw - 44, 30, bold=True, size=22))
        c.addSubview_(lbl("Enter the license key from your purchase email.",
                          22, ch - 72, cw - 44, 18, gray=True, size=12))

        c.addSubview_(lbl("License key", 22, 150, 120, 16, gray=True, size=11))
        self.key_field = NSTextField.alloc().initWithFrame_(NSMakeRect(22, 114, cw - 44, 32))
        self.key_field.setBezelStyle_(BEZEL_ROUNDED)   # NSTextFieldRoundedBezel
        self.key_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(13, NSFontWeightRegular))
        self.key_field.setPlaceholderString_("HT-XXXXXXXX-XXXXXXXX-XXXXXXXX")
        c.addSubview_(self.key_field)

        self.act_btn = NSButton.buttonWithTitle_target_action_(
            "Activate", self, b"activate:")
        self.act_btn.setFrame_(NSMakeRect(22, 64, cw - 44, 40))
        self.act_btn.setControlSize_(NSControlSizeLarge)
        self.act_btn.setFont_(NSFont.systemFontOfSize_weight_(14, NSFontWeightSemibold))
        self.act_btn.setBezelColor_(NSColor.controlAccentColor())
        if HAS_GLASS:
            self.act_btn.setBezelStyle_(BEZEL_GLASS)
            self.act_btn.setTintProminence_(TINT_PRIMARY)
        c.addSubview_(self.act_btn)

        buy_btn = NSButton.buttonWithTitle_target_action_(
            "Don't have a key? Buy HumanType — $14.99", self, b"buy:")
        buy_btn.setFrame_(NSMakeRect(22, 34, cw - 44, 22))
        buy_btn.setBordered_(False)
        buy_btn.setContentTintColor_(NSColor.controlAccentColor())
        c.addSubview_(buy_btn)

        self.status_lbl = lbl("", 22, 10, cw - 44, 18, gray=True, size=12)
        c.addSubview_(self.status_lbl)

        # Pre-fill if a key was entered before
        saved = LicenseManager.stored_key()
        if saved:
            self.key_field.setStringValue_(saved)

    def activate_(self, sender):
        key = str(self.key_field.stringValue()).strip()
        if not key:
            self.status_lbl.setStringValue_("Please enter your license key.")
            return
        self.act_btn.setEnabled_(False)
        self.act_btn.setTitle_("Validating…")
        self.status_lbl.setStringValue_("")
        threading.Thread(target=self._validate_bg, args=(key,), daemon=True).start()

    @objc.python_method
    def _validate_bg(self, key):
        valid, info = LicenseManager.validate(key)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"_done:", [valid, key, info], False)

    def _done_(self, args):
        valid, key, info = args[0], args[1], args[2]
        if valid:
            LicenseManager.store(key, info)
            self.window.orderOut_(None)
            if self._on_activated:
                self._on_activated()
        else:
            self.act_btn.setEnabled_(True)
            self.act_btn.setTitle_("Activate")
            self.status_lbl.setStringValue_(info)

    def buy_(self, sender):
        from AppKit import NSWorkspace
        from Foundation import NSURL
        NSWorkspace.sharedWorkspace().openURL_(
            NSURL.URLWithString_(BUY_URL))


# Window geometry.
W, H = 620, 680     # window size
M = 24              # page margin
TOP = H - 64        # top of the page area, just under the floating tab bar

# (title, SF Symbol, page key, action) for the floating glass tab bar.
TABS = (
    ("Type",     "keyboard",         "type",     b"showTabType:"),
    ("Snippets", "doc.on.clipboard", "snippets", b"showTabSnippets:"),
    ("Settings", "gearshape",        "settings", b"showTabSettings:"),
)

SPEED_WPM = {"Slow": 35, "Normal": 55, "Fast": 80, "Extra Fast": 120}

# Realism profiles: (typo_rate, speed_variation_scale, extra_pauses)
REALISM = {
    "Minimal":  (0.00, 0.60, False),
    "Natural":  (0.02, 1.00, True),
    "Pro":      (0.04, 1.25, True),
}

# US-ANSI virtual key codes -> printable label, for showing the chosen binds.
KEYNAMES = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C",
    9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T",
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9",
    26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "O", 32: "U", 33: "[",
    34: "I", 35: "P", 36: "Return", 37: "L", 38: "J", 39: "'", 40: "K",
    41: ";", 42: "\\", 43: ",", 44: "/", 45: "N", 46: "M", 47: ".", 48: "Tab",
    49: "Space", 50: "` (backtick)", 51: "Delete", 53: "Esc", 65: ".", 67: "*", 69: "+",
    75: "/", 76: "Enter", 78: "-", 123: "←", 124: "→", 125: "↓", 126: "↑",
}


def binding_label(binding):
    keycode, mods = binding
    out = ""
    if mods & kCGEventFlagMaskControl:
        out += "⌃"   # ⌃
    if mods & kCGEventFlagMaskAlternate:
        out += "⌥"   # ⌥
    if mods & kCGEventFlagMaskShift:
        out += "⇧"   # ⇧
    if mods & kCGEventFlagMaskCommand:
        out += "⌘"   # ⌘
    return out + KEYNAMES.get(keycode, f"key{keycode}")


class AppController(NSObject):

    # --- lifecycle ----------------------------------------------------------

    def init(self):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.state = None
        self._tap = None            # global hotkey event tap (main run loop)
        self._tap_source = None
        self._tap_callback = None   # kept alive so the C callback isn't GC'd
        self.typist = None
        self._typing_gen = None     # active steps() generator while typing
        self._countdown_left = 0
        self._monitor = None
        self._record_button = None  # button currently awaiting a key press
        self._record_which = None   # which binding is being recorded
        self._asked_ax = False      # whether we've shown the Accessibility prompt
        d = NSUserDefaults.standardUserDefaults()
        # Did we just relaunch ourselves to apply a permission? (avoids loops)
        self._just_relaunched = bool(d.boolForKey_("relaunched_for_perm"))
        if self._just_relaunched:
            d.removeObjectForKey_("relaunched_for_perm")
        self.pause_binding = self._load_binding(d, "pause", ht.DEFAULT_PAUSE_BINDING)
        self.stop_binding = self._load_binding(d, "stop", ht.DEFAULT_STOP_BINDING)
        self.start_stop_binding = self._load_binding(
            d, "start_stop", ht.DEFAULT_START_STOP_BINDING)
        self.lock_binding = self._load_binding(d, "lock", ht.DEFAULT_LOCK_BINDING)
        self._kbd_locked = False
        # Settings (loaded fresh from defaults each time, saved on change)
        self._current_wpm       = int(d.integerForKey_("ht_wpm") or 55)
        if self._current_wpm < 10: self._current_wpm = 55
        self._countdown_secs    = int(d.integerForKey_("ht_countdown") or 5)
        if self._countdown_secs < 1: self._countdown_secs = 5
        self._realism_name      = d.stringForKey_("ht_realism") or "Natural"
        if self._realism_name not in REALISM: self._realism_name = "Natural"
        self._auto_paste        = bool(d.boolForKey_("ht_auto_paste"))
        self._strip_fmt         = bool(d.boolForKey_("ht_strip_fmt"))
        # Text progress tracking
        self._total_steps = 0
        self._done_steps  = 0
        self._active_tab  = "type"   # "type" | "snippets" | "settings"
        return self

    @objc.python_method
    def _load_binding(self, d, prefix, default):
        if d.objectForKey_(prefix + "_kc") is None:
            return default
        return (int(d.integerForKey_(prefix + "_kc")),
                int(d.integerForKey_(prefix + "_mods")))

    @objc.python_method
    def _save_binding(self, prefix, binding):
        d = NSUserDefaults.standardUserDefaults()
        d.setInteger_forKey_(binding[0], prefix + "_kc")
        d.setInteger_forKey_(binding[1], prefix + "_mods")

    def applicationDidFinishLaunching_(self, notification):
        self._build_main_menu()
        self._build_status_item()
        self._build_window()
        self._install_global_tap()

        self._update_info = None       # filled by background update check
        self._skipped_version = None   # "Later" on this version: don't re-ask
        # Check for updates quietly a few seconds after launch, then periodically.
        self._schedule(b"_checkForUpdates:", 4.0)
        if LicenseManager.is_licensed() or not LICENSING_ENABLED:
            self.showWindow_(None)
        else:
            # Show activation screen; main window opens after successful activation.
            self._license_ctrl = LicenseWindowController.alloc().init()
            self._license_ctrl.show(lambda: self.showWindow_(None))
        # Record this real (LaunchServices) launch's trust state for diagnostics.
        try:
            from ApplicationServices import AXIsProcessTrusted
            with open("/tmp/humantype_perm.txt", "w") as fh:
                fh.write(str(bool(AXIsProcessTrusted())))
        except Exception:
            pass

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return False  # keep living in the menu bar after the window closes

    # ── Auto-update (GitHub Releases) ─────────────────────────────────────

    def _checkForUpdates_(self, _):
        """Scheduled on the main run loop: shortly after launch, then every
        UPDATE_INTERVAL. The network call runs on a background thread."""
        threading.Thread(target=self._fetch_update_info, daemon=True).start()
        self._schedule(b"_checkForUpdates:", UPDATE_INTERVAL)

    @objc.python_method
    def _fetch_update_info(self):
        """Background: fetch the latest release and compare with APP_VERSION."""
        try:
            info = fetch_latest_release()
        except Exception:
            return   # silent — the next scheduled check tries again
        if info and _version_tuple(info["version"]) > _version_tuple(APP_VERSION):
            self._update_info = info
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_offerUpdate:", info, False)

    def _offerUpdate_(self, info):
        """Main thread: badge the UI, then ask once per version — but never
        mid-run, where a modal alert would take the keystrokes meant for the
        target app. The menu-bar item stays available either way."""
        self._showUpdateBadge_(info)
        if info.get("version") == self._skipped_version or self._typing_gen is not None:
            return
        self._promptUpdate_(info)

    def _showUpdateBadge_(self, info):
        """Main thread: point the version label and menu item at the update."""
        version = info.get("version", "?") if info else "?"
        if hasattr(self, "_version_lbl"):
            self._version_lbl.setStringValue_(
                f"HumanType {APP_VERSION} · version {version} is available")
            self._version_lbl.setTextColor_(NSColor.controlAccentColor())
        if getattr(self, "_update_menu_item", None) is not None:
            self._update_menu_item.setTitle_(f"Install Update {version}…")

    def checkForUpdatesManually_(self, sender):
        """Menu item — install a known update, or check now and report back."""
        if self._update_info:
            self._promptUpdate_(self._update_info)
            return
        self._set_status("Checking for updates…")
        threading.Thread(target=self._check_manually_bg, daemon=True).start()

    @objc.python_method
    def _check_manually_bg(self):
        try:
            info = fetch_latest_release()
        except Exception as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_updateCheckFailed:", str(e), False)
            return
        if info and _version_tuple(info["version"]) > _version_tuple(APP_VERSION):
            self._update_info = info
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_showUpdateBadge:", info, False)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_promptUpdate:", info, False)
        else:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_noUpdateFound:", None, False)

    def _promptUpdate_(self, info):
        from AppKit import NSAlert, NSAlertFirstButtonReturn
        version = info.get("version", "?")
        notes   = info.get("release_notes", "Improvements and bug fixes.")
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"HumanType {version} is available")
        alert.setInformativeText_(
            f"You have {APP_VERSION}. What's new:\n\n{notes}\n\n"
            "It downloads in the background, then HumanType reopens on the new version.")
        alert.addButtonWithTitle_("Download & Install")
        alert.addButtonWithTitle_("Later")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() == NSAlertFirstButtonReturn:
            self._start_update_download(info)
        else:
            self._skipped_version = version

    def _noUpdateFound_(self, _):
        self._set_status(f"You're up to date (v{APP_VERSION}).")

    def _updateCheckFailed_(self, err):
        self._set_status(f"Update check failed: {err}")

    @objc.python_method
    def _start_update_download(self, info):
        url = info.get("download_url", "")
        if not url:
            self._set_status("That release has no DMG attached.")
            return
        self._set_status("Downloading update…")
        threading.Thread(target=self._download_update_bg,
                         args=(url, info.get("version", "?")),
                         daemon=True).start()

    @objc.python_method
    def _download_update_bg(self, url, version):
        """Download the new DMG and trigger the install shim."""
        tmp_dmg = f"/tmp/HumanType_update_{version}.dmg"
        try:
            def reporthook(count, block_size, total_size):
                if total_size > 0:
                    pct = min(100, int(count * block_size * 100 / total_size))
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        b"_updateDownloadProgress:", pct, False)
            urllib.request.urlretrieve(url, tmp_dmg, reporthook)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_installUpdate:", tmp_dmg, False)
        except Exception as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_updateCheckFailed:", f"Download failed: {e}", False)

    def _updateDownloadProgress_(self, pct):
        self._set_status(f"Downloading update… {pct}%")

    def _installUpdate_(self, tmp_dmg):
        """Write a self-deleting shell shim that waits for this app to quit,
        then mounts the DMG, swaps in the new .app, and relaunches."""
        from Foundation import NSBundle
        app_path = str(NSBundle.mainBundle().bundlePath())
        # Copy beside the old bundle, then swap: ditto *over* the old bundle
        # would merge, leaving stale files inside that break its signature.
        shim = f"""\
#!/bin/bash
# HumanType auto-updater shim — runs after the app quits
while pgrep -f "HumanType.app/Contents/MacOS" > /dev/null 2>&1; do sleep 0.3; done
sleep 0.5
MNT=$(hdiutil attach "{tmp_dmg}" -nobrowse -readonly 2>/dev/null | grep -o '/Volumes/.*' | head -1)
if [ -z "$MNT" ]; then exit 1; fi
NEW="$MNT/HumanType.app"
APP="{app_path}"
# Never replace a working app with a bundle whose signature doesn't verify.
if codesign --verify --deep --strict "$NEW" 2>/dev/null; then
    rm -rf "$APP.new"
    ditto "$NEW" "$APP.new" && rm -rf "$APP" && mv "$APP.new" "$APP"
fi
hdiutil detach "$MNT" -force -quiet 2>/dev/null
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
rm -f "{tmp_dmg}"
sleep 0.5
open "$APP"
rm -- "$0"   # self-delete
"""
        shim_path = "/tmp/humantype_updater.sh"
        with open(shim_path, "w") as fh:
            fh.write(shim)
        os.chmod(shim_path, 0o755)
        import subprocess
        subprocess.Popen(["/bin/bash", shim_path])
        self._set_status("Installing update — relaunching shortly…")
        # Give the shim a moment to start waiting, then quit this process
        self._schedule(b"end:", 1.2)

    # ── UI construction ───────────────────────────────────────────────────

    @objc.python_method
    def _build_main_menu(self):
        """Accessory (menu-bar) apps have no main menu, so the standard editing
        key equivalents (⌘V/⌘C/⌘X/⌘A/⌘Z) are never delivered to the text view —
        that's why Cmd+V didn't paste. Installing an Edit menu wires them back up
        through the responder chain. The menu bar itself stays hidden."""
        main = NSMenu.alloc().init()

        app_item = NSMenuItem.alloc().init()
        main.addItem_(app_item)
        app_menu = NSMenu.alloc().init()
        q = app_menu.addItemWithTitle_action_keyEquivalent_(
            "Quit HumanType", b"end:", "q")
        q.setTarget_(self)
        app_item.setSubmenu_(app_menu)

        edit_item = NSMenuItem.alloc().init()
        main.addItem_(edit_item)
        edit = NSMenu.alloc().initWithTitle_("Edit")
        edit.addItemWithTitle_action_keyEquivalent_("Undo", b"undo:", "z")
        redo = edit.addItemWithTitle_action_keyEquivalent_("Redo", b"redo:", "z")
        redo.setKeyEquivalentModifierMask_(
            NSEventModifierFlagCommand | NSEventModifierFlagShift)
        edit.addItem_(NSMenuItem.separatorItem())
        edit.addItemWithTitle_action_keyEquivalent_("Cut", b"cut:", "x")
        edit.addItemWithTitle_action_keyEquivalent_("Copy", b"copy:", "c")
        edit.addItemWithTitle_action_keyEquivalent_("Paste", b"paste:", "v")
        edit.addItemWithTitle_action_keyEquivalent_("Select All", b"selectAll:", "a")
        edit_item.setSubmenu_(edit)

        NSApplication.sharedApplication().setMainMenu_(main)

    @objc.python_method
    def _build_status_item(self):
        bar = NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        btn = self.status_item.button()
        # Use the crisp 'keyboard' SF Symbol as a template image (adapts to the
        # menu bar's light/dark tint) instead of an emoji glyph that renders as
        # a fallback box on some systems.
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "keyboard", "HumanType")
        if img is not None:
            img.setTemplate_(True)
            btn.setImage_(img)
            btn.setTitle_("")
        else:
            btn.setTitle_("⌨")

        menu = NSMenu.alloc().init()

        def item(title, sel):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            it.setTarget_(self)
            menu.addItem_(it)
            return it

        item("Show HumanType", b"showWindow:")
        menu.addItem_(NSMenuItem.separatorItem())
        item("Start typing", b"start:")
        item("Pause / Resume", b"togglePause:")
        item("Stop typing", b"stop:")
        menu.addItem_(NSMenuItem.separatorItem())

        # Speed submenu — change speed from the menu bar without opening the window.
        speed_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Speed", None, "")
        speed_submenu = NSMenu.alloc().initWithTitle_("Speed")
        self._speed_menu_items = {}
        for name in ("Slow", "Normal", "Fast", "Extra Fast"):
            si = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                name, b"setSpeedFromMenu:", "")
            si.setTarget_(self)
            speed_submenu.addItem_(si)
            self._speed_menu_items[name] = si
        speed_item.setSubmenu_(speed_submenu)
        menu.addItem_(speed_item)
        self._sync_speed_menu()

        menu.addItem_(NSMenuItem.separatorItem())
        # Keyboard lock toggle — keep a reference so we can show its on/off state.
        self.lock_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Lock keyboard", b"toggleLock:", "")
        self.lock_menu_item.setTarget_(self)
        menu.addItem_(self.lock_menu_item)
        menu.addItem_(NSMenuItem.separatorItem())
        if LICENSING_ENABLED:
            item("Enter License Key", b"showLicense:")
        self._update_menu_item = item("Check for Updates", b"checkForUpdatesManually:")
        menu.addItem_(NSMenuItem.separatorItem())
        item("Quit & Reopen (apply permission)", b"relaunch:")
        item("Quit HumanType", b"end:")
        self.status_item.setMenu_(menu)

    # =========================================================================
    # Liquid Glass UI primitives
    # =========================================================================

    @objc.python_method
    def _label(self, text, rect, *, bold=False, size=13, gray=False,
               dim=False, align=0, color=None, weight=None):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(rect)
        if weight is None:
            weight = NSFontWeightBold if bold else NSFontWeightRegular
        lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        if color:
            lbl.setTextColor_(color)
        elif dim:
            lbl.setTextColor_(NSColor.tertiaryLabelColor())
        elif gray:
            lbl.setTextColor_(NSColor.secondaryLabelColor())
        if align:
            lbl.setAlignment_(align)
        return lbl

    @objc.python_method
    def _glass_button(self, title, sel, rect, *, symbol=None, prominent=False,
                      size=13, icon_only=False):
        """A Liquid Glass capsule button (NSBezelStyleGlass). `prominent` tints
        the glass with the accent colour, for the one primary action."""
        btn = NSButton.buttonWithTitle_target_action_(title, self, sel)
        btn.setBezelStyle_(BEZEL_GLASS if HAS_GLASS else BEZEL_ROUNDED)
        h = rect.size.height
        btn.setControlSize_(NSControlSizeLarge if h >= 34
                            else NSControlSizeRegular if h >= 26
                            else NSControlSizeSmall)
        btn.setFrame_(rect)
        btn.setFont_(NSFont.systemFontOfSize_weight_(
            size, NSFontWeightSemibold if prominent else NSFontWeightMedium))
        if symbol:
            set_symbol(btn, symbol, only=icon_only, label=title)
        if icon_only:
            btn.setToolTip_(title)
            if HAS_GLASS:
                btn.setBorderShape_(BORDER_CIRCLE)
        if prominent:
            btn.setBezelColor_(NSColor.controlAccentColor())
            if HAS_GLASS:
                btn.setTintProminence_(TINT_PRIMARY)
        return btn

    @objc.python_method
    def _button(self, title, sel, rect, *, accent=False):
        """Compat wrapper used by the shortcut rows."""
        return self._glass_button(title, sel, rect, prominent=accent)

    @objc.python_method
    def _section_header(self, text, parent, x, y, w):
        lbl = NSTextField.labelWithString_(text.upper())
        lbl.setFrame_(NSMakeRect(x, y, w, 14))
        lbl.setFont_(NSFont.systemFontOfSize_weight_(10.5, NSFontWeightSemibold))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        parent.addSubview_(lbl)

    @objc.python_method
    def _separator(self, parent, x, y, w):
        parent.addSubview_(color_view(NSMakeRect(x, y, w, 0.5),
                                      NSColor.separatorColor()))

    @objc.python_method
    def _page_title(self, parent, title, subtitle):
        """iOS-style large title under the tab bar."""
        parent.addSubview_(self._label(
            title, NSMakeRect(M, TOP - 40, 240, 36), size=28,
            weight=NSFontWeightBold))
        parent.addSubview_(self._label(
            subtitle, NSMakeRect(M + 1, TOP - 60, W - 2*M, 18), size=12, gray=True))

    @objc.python_method
    def _add_slider(self, parent, rect, lo, hi, val, sel):
        sl = NSSlider.alloc().initWithFrame_(rect)
        sl.setMinValue_(lo)
        sl.setMaxValue_(hi)
        sl.setFloatValue_(val)
        sl.setTarget_(self)
        sl.setAction_(sel)
        parent.addSubview_(sl)
        return sl

    # =========================================================================
    # Window build
    # =========================================================================

    @objc.python_method
    def _build_window(self):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskFullSizeContentView)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("HumanType")
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(NSWindowTitleHidden)
        self.window.setMovableByWindowBackground_(True)
        self.window.center()

        # Layer 0: the drifting colour field the glass refracts.
        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        root.addSubview_(aurora_view(root.bounds()))
        self.window.setContentView_(root)

        # Pages, then the floating tab bar above them.
        self._tab_type = self._page(root)
        self._build_type_page(self._tab_type)
        self._tab_snippets = self._page(root)
        self._build_snippets_page(self._tab_snippets)
        self._tab_settings = self._page(root)
        self._build_settings_panel(self._tab_settings)
        self._build_tab_bar(root)
        self._show_tab("type", animate=False)

    @objc.python_method
    def _page(self, root):
        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        root.addSubview_(v)
        return v

    @objc.python_method
    def _build_tab_bar(self, root):
        """A floating glass capsule with a sliding selection lens: the iOS 26/27
        tab bar, moved to the top of a Mac window."""
        seg_w, seg_h, pad = 116, 34, 4
        bar_w, bar_h = len(TABS) * seg_w + 2 * pad, seg_h + 2 * pad
        bar, content = glass_panel(
            NSMakeRect((W - bar_w) / 2, H - bar_h - 12, bar_w, bar_h),
            radius=bar_h / 2)
        self._tab_lens = color_view(
            NSMakeRect(pad, pad, seg_w, seg_h),
            NSColor.labelColor().colorWithAlphaComponent_(0.11), seg_h / 2)
        content.addSubview_(self._tab_lens)
        self._nav_btns = {}
        for i, (title, sym, key, sel) in enumerate(TABS):
            btn = NSButton.buttonWithTitle_target_action_(title, self, sel)
            btn.setBordered_(False)
            btn.setFrame_(NSMakeRect(pad + i * seg_w, pad, seg_w, seg_h))
            btn.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
            set_symbol(btn, sym)
            content.addSubview_(btn)
            self._nav_btns[key] = btn
        root.addSubview_(bar)

    # ── Type page ────────────────────────────────────────────────────────

    @objc.python_method
    def _build_type_page(self, tp):
        #  18  status + ETA
        #  44  progress capsule           h=5
        #  62  glass action group         h=44
        # 122  speed / realism glass card h=96
        # 230  stats + Save as Snippet
        # 258  text card bottom … TOP-72 text card top
        self._page_title(tp, "Type", "Paste text, click into your target, watch it type.")
        self._lock_lbl = self._label(
            "", NSMakeRect(M + 120, TOP - 32, W - 2*M - 120, 18), size=12,
            weight=NSFontWeightSemibold, color=NSColor.systemOrangeColor(),
            align=NSTextAlignmentRight)
        tp.addSubview_(self._lock_lbl)

        # ── Glass text card
        TT, TB = TOP - 72, 258
        cw, ch, pad = W - 2*M, TT - TB, 12
        card, cc = glass_panel(NSMakeRect(M, TB, cw, ch), radius=24)
        tp.addSubview_(card)
        # Placeholder sits *under* the transparent text view, so clicks on it
        # still land in the editor.
        self._placeholder = self._label(
            "Paste or type what you want typed…",
            NSMakeRect(pad + 9, ch - pad - 30, cw - 2*pad - 20, 20), size=15, dim=True)
        cc.addSubview_(self._placeholder)
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(pad, pad, cw - 2*pad, ch - 2*pad))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)
        tv = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        tv.setFont_(NSFont.systemFontOfSize_(15))
        tv.setTextColor_(NSColor.labelColor())
        tv.setInsertionPointColor_(NSColor.controlAccentColor())
        tv.setRichText_(False)
        tv.setDrawsBackground_(False)
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setAutomaticTextReplacementEnabled_(False)
        tv.setAllowsUndo_(True)
        tv.setVerticallyResizable_(True)
        tv.setMaxSize_(NSMakeSize(cw - 2*pad, 1e7))
        tv.setTextContainerInset_((6, 8))
        tv.setDelegate_(self)          # textDidChange_ keeps the stats live
        scroll.setDocumentView_(tv)
        self.text_view = tv
        cc.addSubview_(scroll)

        # ── Stats row
        self._stats_lbl = self._label(
            "0 words · 0 chars", NSMakeRect(M + 4, 230, 240, 16), size=11, gray=True)
        tp.addSubview_(self._stats_lbl)
        tp.addSubview_(self._glass_button(
            "Save as Snippet", b"saveSnippet:",
            NSMakeRect(W - M - 150, 225, 150, 26), symbol="bookmark", size=12))

        # ── Speed + realism in one inset-grouped glass card
        ctl, c = glass_panel(NSMakeRect(M, 122, cw, 96), radius=22)
        tp.addSubview_(ctl)
        c.addSubview_(self._label("Speed", NSMakeRect(18, 63, 70, 18),
                                  size=13, weight=NSFontWeightMedium))
        self._wpm_slider = self._add_slider(
            c, NSMakeRect(96, 62, cw - 96 - 100, 20),
            10, 200, self._current_wpm, b"wpmSliderChanged:")
        self._wpm_lbl = self._label(
            f"{self._current_wpm} wpm", NSMakeRect(cw - 94, 63, 76, 18),
            size=13, weight=NSFontWeightSemibold,
            color=NSColor.controlAccentColor(), align=NSTextAlignmentRight)
        c.addSubview_(self._wpm_lbl)
        self._separator(c, 18, 48, cw - 36)
        c.addSubview_(self._label("Realism", NSMakeRect(18, 15, 70, 18),
                                  size=13, weight=NSFontWeightMedium))
        self._realism_seg = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            list(REALISM), 0, self, b"realismChanged:")   # 0 = select-one
        self._realism_seg.setFrame_(NSMakeRect(96, 11, 250, 26))
        c.addSubview_(self._realism_seg)
        self._realism_hint = self._label(
            "", NSMakeRect(cw - 188, 15, 170, 18), size=12, gray=True,
            align=NSTextAlignmentRight)
        c.addSubview_(self._realism_hint)
        self._sync_realism()

        # ── Glass action group: controls in one container share a single
        # material and melt together, like an iOS toolbar.
        gap, bh = 10, 44
        pw = 128
        sw = cw - bh - 2*pw - 3*gap
        grp, g = glass_group(NSMakeRect(M, 62, cw, bh), spacing=gap)
        self.start_btn = self._glass_button(
            "Start", b"start:", NSMakeRect(0, 0, sw, bh),
            symbol="play.fill", prominent=True, size=15)
        self.pause_btn = self._glass_button(
            "Pause", b"togglePause:", NSMakeRect(sw + gap, 0, pw, bh),
            symbol="pause.fill", size=14)
        self.stop_btn = self._glass_button(
            "Stop", b"stop:", NSMakeRect(sw + pw + 2*gap, 0, pw, bh),
            symbol="stop.fill", size=14)
        self.end_btn = self._glass_button(
            "Quit HumanType", b"end:", NSMakeRect(cw - bh, 0, bh, bh),
            symbol="power", icon_only=True)
        for b in (self.start_btn, self.pause_btn, self.stop_btn, self.end_btn):
            g.addSubview_(b)
        tp.addSubview_(grp)

        # ── Thin progress capsule
        pb_y, pb_h = 44, 5
        tp.addSubview_(color_view(
            NSMakeRect(M, pb_y, cw, pb_h),
            NSColor.labelColor().colorWithAlphaComponent_(0.08), pb_h / 2))
        self._prog_fill = color_view(NSMakeRect(M, pb_y, 0, pb_h),
                                     NSColor.controlAccentColor(), pb_h / 2)
        tp.addSubview_(self._prog_fill)
        self._prog_max_w = cw

        # ── Status + ETA
        self.status = self._label(
            "Ready — paste text above and press Start.",
            NSMakeRect(M + 4, 18, cw - 134, 16), size=11, gray=True)
        tp.addSubview_(self.status)
        self._eta_lbl = self._label(
            "", NSMakeRect(W - M - 124, 18, 120, 16), size=11, gray=True,
            align=NSTextAlignmentRight)
        tp.addSubview_(self._eta_lbl)

        self._prefill_from_clipboard()
        self._set_running(False)

    # ── Snippets page ────────────────────────────────────────────────────

    @objc.python_method
    def _build_snippets_page(self, sp):
        self._page_title(sp, "Snippets", "Save texts you type often, load them in one click.")
        top = TOP - 72
        self._snip_search = NSSearchField.alloc().initWithFrame_(
            NSMakeRect(M, top - 32, W - 2*M, 32))
        self._snip_search.setControlSize_(NSControlSizeLarge)
        self._snip_search.setPlaceholderString_("Search snippets")
        self._snip_search.setTarget_(self)
        self._snip_search.setAction_(b"snippetSearchChanged:")
        sp.addSubview_(self._snip_search)

        cw, bottom = W - 2*M, 72
        ch = top - 46 - bottom
        card, c = glass_panel(NSMakeRect(M, bottom, cw, ch), radius=24)
        sp.addSubview_(card)
        self._snip_empty = NSTextField.wrappingLabelWithString_("")
        self._snip_empty.setFrame_(NSMakeRect(40, ch / 2 - 20, cw - 80, 40))
        self._snip_empty.setAlignment_(NSTextAlignmentCenter)
        self._snip_empty.setFont_(NSFont.systemFontOfSize_(13))
        self._snip_empty.setTextColor_(NSColor.secondaryLabelColor())
        c.addSubview_(self._snip_empty)
        snip_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 8, cw, ch - 16))
        snip_scroll.setHasVerticalScroller_(True)
        snip_scroll.setAutohidesScrollers_(True)
        snip_scroll.setDrawsBackground_(False)
        snip_scroll.setBorderType_(0)
        self._snip_list_view = FlippedView.alloc().initWithFrame_(
            snip_scroll.contentView().bounds())
        snip_scroll.setDocumentView_(self._snip_list_view)
        c.addSubview_(snip_scroll)
        self._snip_scroll = snip_scroll

        self._snip_count = self._label("", NSMakeRect(M + 4, 32, 200, 16),
                                       size=11, gray=True)
        sp.addSubview_(self._snip_count)
        sp.addSubview_(self._glass_button(
            "Save Current Text", b"saveSnippet:",
            NSMakeRect(W - M - 190, 22, 190, 36), symbol="plus", size=13))
        self._rebuild_snippet_list("")

    # ── Settings page ────────────────────────────────────────────────────

    @objc.python_method
    def _build_settings_panel(self, st):
        self._page_title(st, "Settings", "Tune the typing and set your global shortcuts.")
        y = TOP - 72                     # top of the next section
        cw, row_h = W - 2*M, 38

        def card(title, n_rows):
            """Section header + inset-grouped glass card; returns (content, h)."""
            nonlocal y
            self._section_header(title, st, M + 16, y - 14, 200)
            h = row_h * n_rows
            panel, c = glass_panel(NSMakeRect(M, y - 20 - h, cw, h), radius=18)
            st.addSubview_(panel)
            for i in range(1, n_rows):
                self._separator(c, 16, h - i * row_h, cw - 32)
            y -= 20 + h + 14
            return c, h

        def row(h, i):                   # local bottom edge of row i (0 = top)
            return h - (i + 1) * row_h

        def slider_row(c, ry, label, lo, hi, cur, fmt, sel):
            c.addSubview_(self._label(label, NSMakeRect(16, ry + 10, 150, 18), size=13))
            sl = self._add_slider(c, NSMakeRect(170, ry + 9, cw - 170 - 92, 20),
                                  lo, hi, cur, sel)
            val = self._label(fmt(cur), NSMakeRect(cw - 84, ry + 10, 68, 18),
                              size=13, gray=True, align=NSTextAlignmentRight)
            c.addSubview_(val)
            return sl, val

        def switch_row(c, ry, label, on, sel):
            c.addSubview_(self._label(label, NSMakeRect(16, ry + 10, cw - 100, 18), size=13))
            sw = NSSwitch.alloc().initWithFrame_(NSMakeRect(cw - 16 - 42, ry + 8, 42, 22))
            sw.setState_(1 if on else 0)
            sw.setTarget_(self)
            sw.setAction_(sel)
            c.addSubview_(sw)
            return sw

        def shortcut_row(c, ry, label, binding, sel):
            c.addSubview_(self._label(label, NSMakeRect(16, ry + 10, 200, 18), size=13))
            btn = self._button(binding_label(binding), sel,
                               NSMakeRect(cw - 16 - 130, ry + 5, 130, 28))
            c.addSubview_(btn)
            return btn

        c, h = card("Typing", 2)
        self._st_wpm_sl, self._st_wpm_lbl = slider_row(
            c, row(h, 0), "Speed", 10, 200, self._current_wpm,
            lambda v: f"{int(v)} wpm", b"settingsWpmChanged:")
        self._st_countdown_sl, self._st_countdown_lbl = slider_row(
            c, row(h, 1), "Countdown", 2, 20, self._countdown_secs,
            lambda v: f"{int(v)}s", b"settingsCountdownChanged:")
        self._st_countdown_sl.setAltIncrementValue_(1)

        c, h = card("Realism", 1)
        override = NSUserDefaults.standardUserDefaults().floatForKey_("ht_typo_override")
        self._st_typo_sl, self._st_typo_lbl = slider_row(
            c, row(h, 0), "Typo rate", 0.0, 0.15, override,
            _typo_label, b"settingsTypoChanged:")

        c, h = card("Behaviour", 2)
        self._st_auto_paste = switch_row(
            c, row(h, 0), "Auto-paste clipboard text on open",
            self._auto_paste, b"settingsAutoPasteToggled:")
        self._st_strip_fmt = switch_row(
            c, row(h, 1), "Flatten line breaks and extra spaces",
            self._strip_fmt, b"settingsStripFmtToggled:")

        c, h = card("Shortcuts", 4)
        self.start_stop_rec = shortcut_row(
            c, row(h, 0), "Start / Stop", self.start_stop_binding, b"recordStartStop:")
        self.pause_rec = shortcut_row(
            c, row(h, 1), "Pause / Resume", self.pause_binding, b"recordPause:")
        self.stop_rec = shortcut_row(
            c, row(h, 2), "Stop", self.stop_binding, b"recordStop:")
        self.lock_rec = shortcut_row(
            c, row(h, 3), "Lock keyboard", self.lock_binding, b"recordLock:")

        self._version_lbl = self._label(
            f"HumanType {APP_VERSION}", NSMakeRect(M, 18, cw, 16), size=11,
            dim=True, align=NSTextAlignmentCenter)
        st.addSubview_(self._version_lbl)

    # ── Tab switching ────────────────────────────────────────────────────

    @objc.python_method
    def _show_tab(self, name, animate=True):
        self._active_tab = name
        panels = {"type": self._tab_type,
                  "snippets": self._tab_snippets,
                  "settings": self._tab_settings}
        for k, p in panels.items():
            p.setHidden_(k != name)
        # Slide the lens under the selected tab with a slight overshoot — the
        # "liquid" settle of the iOS tab bar — and tint that tab like iOS does.
        target = self._nav_btns[name].frame()
        if animate and not reduce_motion():
            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(0.34)
            ctx.setTimingFunction_(
                CAMediaTimingFunction.functionWithControlPoints____(0.3, 1.3, 0.5, 1.0))
            self._tab_lens.animator().setFrame_(target)
            NSAnimationContext.endGrouping()
        else:
            self._tab_lens.setFrame_(target)
        for k, btn in self._nav_btns.items():
            btn.setContentTintColor_(NSColor.labelColor() if k == name
                                     else NSColor.secondaryLabelColor())

    def showTabType_(self, sender):
        self._show_tab("type")

    def showTabSnippets_(self, sender):
        self._show_tab("snippets")
        self._rebuild_snippet_list(str(self._snip_search.stringValue()))

    def showTabSettings_(self, sender):
        self._show_tab("settings")

    # ── Speed / realism controls ─────────────────────────────────────────

    @objc.python_method
    def _apply_wpm(self, wpm):
        """The one place a speed change lands: both sliders, their labels,
        the saved default and the menu-bar Speed submenu."""
        self._current_wpm = max(10, int(wpm))
        for sl, lbl in ((getattr(self, "_wpm_slider", None), getattr(self, "_wpm_lbl", None)),
                        (getattr(self, "_st_wpm_sl", None), getattr(self, "_st_wpm_lbl", None))):
            if sl is not None:
                sl.setIntValue_(self._current_wpm)
            if lbl is not None:
                lbl.setStringValue_(f"{self._current_wpm} wpm")
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            self._current_wpm, "ht_wpm")
        self._sync_speed_menu()

    def wpmSliderChanged_(self, sender):
        self._apply_wpm(sender.intValue())

    def settingsWpmChanged_(self, sender):
        self._apply_wpm(sender.intValue())

    def realismChanged_(self, sender):
        # Picking a preset takes over from any manual typo rate in Settings.
        self._realism_name = list(REALISM)[int(sender.selectedSegment())]
        d = NSUserDefaults.standardUserDefaults()
        d.setObject_forKey_(self._realism_name, "ht_realism")
        d.removeObjectForKey_("ht_typo_override")
        if hasattr(self, "_st_typo_sl"):
            self._st_typo_sl.setFloatValue_(0.0)
            self._st_typo_lbl.setStringValue_(_typo_label(0.0))
        self._sync_realism()

    @objc.python_method
    def _typo_rate(self):
        """Effective typo rate: the Settings override if set, else the preset."""
        override = NSUserDefaults.standardUserDefaults().floatForKey_("ht_typo_override")
        if override > 0:
            return override
        return REALISM.get(self._realism_name, REALISM["Natural"])[0]

    @objc.python_method
    def _sync_realism(self):
        if not hasattr(self, "_realism_seg"):
            return
        self._realism_seg.setSelectedSegment_(list(REALISM).index(self._realism_name))
        rate = self._typo_rate()
        self._realism_hint.setStringValue_(
            f"~{rate * 100:.0f}% typos, self-fixed" if rate else "No typos")

    def settingsCountdownChanged_(self, sender):
        self._countdown_secs = max(1, int(sender.intValue()))
        self._st_countdown_lbl.setStringValue_(f"{self._countdown_secs}s")
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            self._countdown_secs, "ht_countdown")

    def settingsTypoChanged_(self, sender):
        # Applied to the next session; 0 hands control back to the preset.
        rate = max(0.0, min(0.15, float(sender.floatValue())))
        NSUserDefaults.standardUserDefaults().setFloat_forKey_(rate, "ht_typo_override")
        self._st_typo_lbl.setStringValue_(_typo_label(rate))
        self._sync_realism()

    def settingsAutoPasteToggled_(self, sender):
        self._auto_paste = bool(sender.state())
        NSUserDefaults.standardUserDefaults().setBool_forKey_(
            self._auto_paste, "ht_auto_paste")

    def settingsStripFmtToggled_(self, sender):
        self._strip_fmt = bool(sender.state())
        NSUserDefaults.standardUserDefaults().setBool_forKey_(
            self._strip_fmt, "ht_strip_fmt")

    # ── Snippets ─────────────────────────────────────────────────────────

    @objc.python_method
    def _load_snippets(self):
        raw = NSUserDefaults.standardUserDefaults().stringForKey_("ht_snippets") or "[]"
        try:
            return json.loads(raw)
        except Exception:
            return []

    @objc.python_method
    def _save_snippets(self, snips):
        NSUserDefaults.standardUserDefaults().setObject_forKey_(
            json.dumps(snips, ensure_ascii=False), "ht_snippets")

    @objc.python_method
    def _rebuild_snippet_list(self, query):
        sv = self._snip_list_view
        for sub in list(sv.subviews()):
            sub.removeFromSuperview()
        snips = self._load_snippets()
        q = query.lower().strip()
        visible = [(i, s) for i, s in enumerate(snips)
                   if not q or q in s.get("title", "").lower()
                   or q in s.get("text", "").lower()]
        clip = self._snip_scroll.contentView().bounds().size
        width, row_h = clip.width, 64
        sv.setFrame_(NSMakeRect(0, 0, width, max(clip.height, len(visible) * row_h)))
        text_w = width - 20 - 150
        for n, (orig_i, s) in enumerate(visible):
            row = NSView.alloc().initWithFrame_(NSMakeRect(0, n * row_h, width, row_h))
            if n:
                self._separator(row, 20, row_h - 0.5, width - 40)
            title = self._label(s.get("title") or "Untitled",
                                NSMakeRect(20, 33, text_w, 18), size=13,
                                weight=NSFontWeightSemibold)
            preview = self._label(" ".join(s.get("text", "").split())[:160],
                                  NSMakeRect(20, 13, text_w, 16), size=11, gray=True)
            for lbl in (title, preview):
                lbl.setLineBreakMode_(NSLineBreakByTruncatingTail)
                row.addSubview_(lbl)
            load = self._glass_button("Load", b"loadSnippetRow:",
                                      NSMakeRect(width - 138, 17, 80, 30),
                                      symbol="arrow.up.doc", size=12)
            trash = self._glass_button("Delete Snippet", b"deleteSnippetRow:",
                                       NSMakeRect(width - 50, 17, 30, 30),
                                       symbol="trash", icon_only=True)
            for b in (load, trash):
                b.setTag_(orig_i)
                row.addSubview_(b)
            sv.addSubview_(row)
        self._snip_empty.setHidden_(bool(visible))
        self._snip_empty.setStringValue_(
            "No matching snippets." if snips else
            "No snippets yet.\nUse “Save as Snippet” on the Type tab.")
        count = len(snips)
        self._snip_count.setStringValue_(f"{count} snippet{'' if count == 1 else 's'}")

    def snippetSearchChanged_(self, sender):
        self._rebuild_snippet_list(str(sender.stringValue()))

    def saveSnippet_(self, sender):
        text = str(self.text_view.string()).strip()
        if not text:
            self._set_status("Nothing to save — paste some text first.")
            return
        snips = self._load_snippets()
        # Use first line as title, truncated
        title = text.split("\n")[0][:50] or "Snippet"
        snips.insert(0, {"title": title, "text": text})
        self._save_snippets(snips)
        self._rebuild_snippet_list("")
        self._set_status(f"Saved snippet: {title}")

    def deleteSnippetRow_(self, sender):
        idx = int(sender.tag())
        snips = self._load_snippets()
        if 0 <= idx < len(snips):
            snips.pop(idx)
            self._save_snippets(snips)
            self._rebuild_snippet_list(str(self._snip_search.stringValue()))

    def loadSnippetRow_(self, sender):
        idx = int(sender.tag())
        snips = self._load_snippets()
        if 0 <= idx < len(snips):
            self.text_view.setString_(snips[idx].get("text", ""))
            self._show_tab("type")
            self._update_stats()

    # ── Stats / progress helpers ─────────────────────────────────────────

    def textDidChange_(self, notification):
        self._update_stats()

    @objc.python_method
    def _update_stats(self):
        text = str(self.text_view.string())
        words = len(text.split()) if text.strip() else 0
        chars = len(text)
        self._stats_lbl.setStringValue_(f"{words:,} words · {chars:,} chars")
        self._placeholder.setHidden_(chars > 0)

    @objc.python_method
    def _update_progress(self, done, total):
        if total <= 0:
            frac = 0.0
        else:
            frac = min(1.0, done / total)
        new_w = frac * self._prog_max_w
        old = self._prog_fill.frame()
        self._prog_fill.setFrame_(NSMakeRect(old.origin.x, old.origin.y,
                                             new_w, old.size.height))
        # ETA
        if frac > 0.01 and self._current_wpm > 0:
            remaining_chars = total - done
            secs = (remaining_chars / 5.0) / self._current_wpm * 60
            if secs >= 60:
                eta = f"~{int(secs/60)}m {int(secs%60)}s left"
            else:
                eta = f"~{int(secs)}s left"
            self._eta_lbl.setStringValue_(eta)
        else:
            self._eta_lbl.setStringValue_("")

    # ── Prefill / clipboard ───────────────────────────────────────────────

    @objc.python_method
    def _prefill_from_clipboard(self):
        try:
            d = NSUserDefaults.standardUserDefaults()
            draft = d.stringForKey_("draft_text")
            if draft:
                self.text_view.setString_(draft)
                d.removeObjectForKey_("draft_text")
                self._update_stats()
                return
        except Exception:
            pass
        if self._auto_paste:
            try:
                pb = NSPasteboard.generalPasteboard()
                s = pb.stringForType_(NSPasteboardTypeString)
                if s:
                    text = s
                    if self._strip_fmt:
                        text = " ".join(text.split())
                    self.text_view.setString_(text)
                    self._update_stats()
            except Exception:
                pass

    # ── Helpers ───────────────────────────────────────────────────────────

    @objc.python_method
    def _set_status(self, text):
        self.status.setStringValue_(text)

    @objc.python_method
    def _set_pause_btn(self, paused):
        self.pause_btn.setTitle_("Resume" if paused else "Pause")
        set_symbol(self.pause_btn, "play.fill" if paused else "pause.fill")

    @objc.python_method
    def _set_running(self, running):
        self.start_btn.setEnabled_(not running)
        self.pause_btn.setEnabled_(running)
        self.stop_btn.setEnabled_(running)
        self._set_pause_btn(False)
        if not running:
            self._update_progress(0, 1)
            self._eta_lbl.setStringValue_("")

    # ── Speed menu sync ───────────────────────────────────────────────────

    @objc.python_method
    def _sync_speed_menu(self):
        if not getattr(self, "_speed_menu_items", None):
            return
        wpm = getattr(self, "_current_wpm", 55)
        # Find closest named speed
        closest = min(SPEED_WPM, key=lambda k: abs(SPEED_WPM[k] - wpm))
        for name, si in self._speed_menu_items.items():
            si.setState_(1 if name == closest else 0)

    def setSpeedFromMenu_(self, sender):
        name = str(sender.title())
        self._apply_wpm(SPEED_WPM.get(name, 55))
        self._set_status(f"Speed set to {name} ({self._current_wpm} wpm).")

    # ── Actions ───────────────────────────────────────────────────────────

    def showLicense_(self, sender):
        if not hasattr(self, "_license_ctrl") or self._license_ctrl is None:
            self._license_ctrl = LicenseWindowController.alloc().init()
        self._license_ctrl.show(lambda: None)

    def showWindow_(self, sender):
        self._sync_speed_menu()
        self._update_stats()
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def start_(self, sender):
        if self._typing_gen is not None:
            self._set_status("Already typing…")
            return
        self._cancel_typing_timers()
        text = str(self.text_view.string())
        if self._strip_fmt:
            text = " ".join(text.split())
        if not text.strip():
            self._set_status("Nothing to type — paste some text first.")
            return

        if not self._is_trusted():
            self._save_draft()
            if not self._asked_ax:
                self._asked_ax = True
                self._prompt_accessibility()
                self._open_accessibility_settings()
                self._set_status("Switch ON HumanType in Accessibility, then click Start again.")
            elif not self._just_relaunched:
                self._set_status("Applying permission — reopening HumanType…")
                self._relaunch()
            else:
                self._open_accessibility_settings()
                self._set_status("Still not detected — remove HumanType from Accessibility, reopen, re-add.")
            return

        wpm = self._current_wpm
        mistake_rate = self._typo_rate()

        self.state = ht.TypingState()

        # Build the typing engine + its step generator.  All keystrokes fire on
        # the main run loop (countdownTick: → typeStep:) — TSM requires main thread.
        self.typist = ht.Typist(
            ht._CTypesKeyboard(), self.state, 60.0 / (wpm * 5.0), mistake_rate)
        self._typing_gen = self.typist.steps(text)
        self._countdown_left = self._countdown_secs
        # Progress tracking: count how many generator steps the text would take
        # (rough: len(text) * 1.05 for typo overhead)
        self._total_steps = max(1, int(len(text) * 1.05))
        self._done_steps  = 0

        self._set_running(True)
        self._set_status(f"Click your target box — typing starts in {self._countdown_secs}s…")
        self.window.orderOut_(None)  # get out of the way so the target gets focus
        self._schedule(b"countdownTick:", 1.0)

    @objc.python_method
    def _cancel_typing_timers(self):
        # Cancel ONLY the countdown/typing timers — not cosmetic syncLockUI:/
        # syncPauseUI: or a queued start: (the broad target-only cancel would
        # drop those too, leaving the lock icon/menu stale while keys are live).
        for sel in (b"countdownTick:", b"typeStep:"):
            NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                self, sel, None)

    @objc.python_method
    def _schedule(self, selector, delay):
        # Schedule the next tick on the MAIN run loop, in common modes so it keeps
        # firing even while a menu is open or the window is being dragged. Every
        # keystroke must post from the main thread (pynput's calls hit Text
        # Services Manager APIs that crash off-main), and this is what guarantees it.
        self.performSelector_withObject_afterDelay_inModes_(
            selector, None, float(max(0.0, delay)), [NSRunLoopCommonModes])

    @objc.python_method
    def _is_trusted(self):
        """Whether this app currently has Accessibility permission (no prompt).
        Best-effort: if the check is unavailable, assume True so we never block
        typing on the check itself."""
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return True

    @objc.python_method
    def _prompt_accessibility(self):
        """Show the standard macOS Accessibility prompt, which also registers the
        app in the list under its real (certificate-based) identity."""
        try:
            from ApplicationServices import (
                AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt)
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception:
            pass

    @objc.python_method
    def _save_draft(self):
        try:
            d = NSUserDefaults.standardUserDefaults()
            d.setObject_forKey_(str(self.text_view.string()), "draft_text")
        except Exception:
            pass

    def relaunch_(self, sender):
        self._relaunch()

    @objc.python_method
    def _relaunch(self):
        """Quit and relaunch a fresh instance, so a just-granted Accessibility
        permission actually takes effect (macOS applies it only to new launches)."""
        self._save_draft()
        try:
            d = NSUserDefaults.standardUserDefaults()
            d.setBool_forKey_(True, "relaunched_for_perm")
            d.synchronize()
            import subprocess
            from Foundation import NSBundle
            path = NSBundle.mainBundle().bundlePath()
            subprocess.Popen(["/usr/bin/open", "-n", path])
        except Exception:
            pass
        NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def _open_accessibility_settings(self):
        try:
            from AppKit import NSWorkspace
            from Foundation import NSURL
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security"
                "?Privacy_Accessibility"))
        except Exception:
            pass

    @objc.python_method
    def _install_global_tap(self):
        """Install one always-on CGEventTap on the MAIN run loop.

        Handles these binds (physical key presses only — our own injected
        keystrokes always pass straight through so the typed text still lands):
          • Start/Stop   — works any time (idle → starts typing; typing → stops)
          • Lock keyboard — toggles swallowing ALL physical keys (mouse still works)
          • Pause/Resume  — only while typing
          • Stop          — only while typing (emergency stop)

        We deliberately do NOT use pynput's Listener: it calls Text Services
        Manager APIs on a background thread, which crash under a live AppKit run
        loop. Our tap callback runs on the main thread — safe by construction."""
        from Quartz import (CGEventGetIntegerValueField,
                            kCGEventSourceUnixProcessID,
                            kCGKeyboardEventAutorepeat,
                            kCGKeyboardEventKeycode, CGEventGetFlags)

        def tap_callback(proxy, etype, event, refcon):
            if etype in (kCGEventTapDisabledByTimeout,
                         kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    CGEventTapEnable(self._tap, True)
                return event
            if etype != kCGEventKeyDown:
                return event
            # Our own injected keystrokes carry a non-zero process id — always let
            # them through (this is what reaches the document while locked).
            if CGEventGetIntegerValueField(event, kCGEventSourceUnixProcessID) != 0:
                return event

            autorepeat = bool(CGEventGetIntegerValueField(
                event, kCGKeyboardEventAutorepeat))
            keycode = int(CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode))
            mods = int(CGEventGetFlags(event)) & ht.MOD_MASK
            key = (keycode, mods)
            typing = self._typing_gen is not None and self.state is not None

            if not autorepeat:
                # Lock toggle — always active, checked first so it works even
                # while the keyboard is locked.
                if key == self.lock_binding:
                    self._kbd_locked = not self._kbd_locked
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"syncLockUI:", None, 0.0, [NSRunLoopCommonModes])
                    return None
                # Start/Stop — always active (so it can start from any app).
                if key == self.start_stop_binding:
                    if self._typing_gen is None:
                        # Schedule via the run-loop so we don't act inside the tap.
                        self.performSelector_withObject_afterDelay_inModes_(
                            b"start:", None, 0.0, [NSRunLoopCommonModes])
                    elif self.state is not None:
                        self.state.stop()
                    return None   # swallow — never reaches the focused app/site
                # Pause / emergency-stop only act (and are only swallowed) while
                # typing, so these keys still work normally elsewhere when idle.
                if typing and key == self.pause_binding:
                    self.state.toggle_pause()
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"syncPauseUI:", None, 0.0, [NSRunLoopCommonModes])
                    return None
                if typing and key == self.stop_binding:
                    self.state.stop()
                    return None
            else:
                # Autorepeat from HOLDING a hotkey: don't re-fire the action, but
                # still swallow the repeats so they can't leak into the page.
                if key in (self.lock_binding, self.start_stop_binding):
                    return None
                if typing and key in (self.pause_binding, self.stop_binding):
                    return None

            # Keyboard lock: swallow every remaining physical key — including
            # autorepeat from a held/stray key — so a paw or a leaning finger
            # can't leak into the document during a take. Mouse is unaffected, so
            # you can always click the menu-bar lock to unlock.
            if self._kbd_locked:
                return None

            return event

        self._tap_callback = tap_callback
        tap = CGEventTapCreate(
            kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
            CGEventMaskBit(kCGEventKeyDown), tap_callback, None)
        if not tap:
            self._tap = None
            return
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        self._tap = tap
        self._tap_source = source

    def syncPauseUI_(self, _):
        """Sync the Pause button label after a hotkey-driven pause toggle."""
        if self.state is None:
            return
        self._set_pause_btn(self.state.paused)
        self._set_status("Paused." if self.state.paused else "Typing…")

    def toggleLock_(self, sender):
        self._kbd_locked = not self._kbd_locked
        self.syncLockUI_(None)

    def syncLockUI_(self, _):
        """Reflect the keyboard-lock state in the menu, menu-bar icon, status."""
        if getattr(self, "lock_menu_item", None) is not None:
            self.lock_menu_item.setState_(1 if self._kbd_locked else 0)
            self.lock_menu_item.setTitle_(
                "Unlock keyboard" if self._kbd_locked else "Lock keyboard")
        # Swap the menu-bar glyph to a lock so the locked state is obvious.
        try:
            name = "lock.fill" if self._kbd_locked else "keyboard"
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                name, "HumanType")
            if img is not None:
                img.setTemplate_(True)
                self.status_item.button().setImage_(img)
        except Exception:
            pass
        if self._kbd_locked:
            self._set_status("Keyboard locked — physical keys ignored.")
            if hasattr(self, "_lock_lbl"):
                self._lock_lbl.setStringValue_("Keyboard locked · unlock from the menu bar")
        else:
            self._set_status("Keyboard unlocked.")
            if hasattr(self, "_lock_lbl"):
                self._lock_lbl.setStringValue_("")

    @objc.python_method
    def _teardown_global_tap(self):
        if self._tap is not None:
            try:
                CGEventTapEnable(self._tap, False)
                if self._tap_source is not None:
                    CFRunLoopRemoveSource(
                        CFRunLoopGetMain(), self._tap_source, kCFRunLoopCommonModes)
            except Exception:
                pass
        self._tap = None
        self._tap_source = None
        self._tap_callback = None

    def countdownTick_(self, _):
        if not NSThread.isMainThread():       # never advance typing off-main
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"countdownTick:", None, False)
            return
        if self.state is None or self.state.stopped:
            self._finish(False)
            return
        self._countdown_left -= 1
        if self._countdown_left > 0:
            self._set_status(f"Typing starts in {self._countdown_left}s…")
            self._schedule(b"countdownTick:", 1.0)
        else:
            self._set_status("Typing…")
            self._schedule(b"typeStep:", 0.0)

    def typeStep_(self, _):
        # One keystroke per tick, on the main thread. Returning quickly keeps the
        # UI (and the Stop/Pause buttons) responsive between characters. The guard
        # below makes it impossible to emit a keystroke off the main thread.
        if not NSThread.isMainThread():
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"typeStep:", None, False)
            return
        if self._typing_gen is None:
            return
        if self.state.stopped:
            self._finish(False)
            return
        if self.state.paused:
            self._schedule(b"typeStep:", 0.12)
            return
        try:
            delay = next(self._typing_gen)
        except StopIteration:
            self._finish(True)
            return
        except Exception:
            self._finish(False)
            return
        self._done_steps += 1
        # Update progress bar every ~10 steps so it stays smooth without overhead
        if self._done_steps % 10 == 0:
            self._update_progress(self._done_steps, self._total_steps)
        self._schedule(b"typeStep:", float(max(0.0, delay)))

    @objc.python_method
    def _finish(self, finished):
        if self._typing_gen is None:
            return  # already finished
        # Cancel any queued countdownTick_/typeStep_ timers from this session
        # before nulling state. Without this, a stale timer from a previous
        # session can fire against a freshly-started new session, skipping a
        # countdown second or injecting a phantom keystroke into the new run.
        self._cancel_typing_timers()
        self._typing_gen = None
        self.typist = None
        # Leave the always-on tap running; just reset state.
        self.state = None
        # Reset permission flag so if Accessibility is later revoked the user
        # gets the prompt again rather than a silent auto-relaunch.
        self._asked_ax = False
        self._set_running(False)
        self._set_status("Done." if finished else "Stopped.")

    def togglePause_(self, sender):
        if self.state is None or self._typing_gen is None:
            return
        self.state.toggle_pause()
        self._set_pause_btn(self.state.paused)
        self._set_status("Paused." if self.state.paused else "Typing…")

    def stop_(self, sender):
        if self.state is not None:
            self.state.stop()
        self._set_status("Stopping…")

    def end_(self, sender):
        if self.state is not None:
            self.state.stop()
        self._teardown_global_tap()
        NSApplication.sharedApplication().terminate_(None)

    # --- keybind recording --------------------------------------------------

    def recordStartStop_(self, sender):
        self._record("start_stop", self.start_stop_rec)

    def recordPause_(self, sender):
        self._record("pause", self.pause_rec)

    def recordStop_(self, sender):
        self._record("stop", self.stop_rec)

    def recordLock_(self, sender):
        self._record("lock", self.lock_rec)

    @objc.python_method
    def _bindings(self):
        return {"start_stop": self.start_stop_binding, "pause": self.pause_binding,
                "stop": self.stop_binding, "lock": self.lock_binding}

    @objc.python_method
    def _record(self, which, button):
        # If another record session is already waiting, cancel it and restore
        # that button's label so it doesn't stay stuck at "Press keys…".
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        if getattr(self, "_record_button", None) is not None:
            self._record_button.setTitle_(
                binding_label(self._bindings()[self._record_which]))
        self._record_button = button
        self._record_which = which

        prev_label = binding_label(self._bindings()[which])
        button.setTitle_("Press keys…")

        def handler(event):
            keycode = int(event.keyCode())
            mods = int(event.modifierFlags()) & ht.MOD_MASK
            if self._monitor is not None:
                NSEvent.removeMonitor_(self._monitor)
                self._monitor = None
            self._record_button = None

            # Escape (keycode 53) cancels — restore the previous label unchanged.
            if keycode == 53 and mods == 0:
                button.setTitle_(prev_label)
                self._set_status("Shortcut recording cancelled.")
                return None

            binding = (keycode, mods)
            # Reject a combo already assigned to another action — otherwise the
            # tap's first-match-wins order would silently disable one of them.
            taken = {k: v for k, v in self._bindings().items() if k != which}
            if binding in taken.values():
                button.setTitle_(prev_label)
                self._set_status("That shortcut is already in use — pick another.")
                return None
            if which == "pause":
                self.pause_binding = binding
            elif which == "stop":
                self.stop_binding = binding
            elif which == "lock":
                self.lock_binding = binding
            else:
                self.start_stop_binding = binding
            self._save_binding(which, binding)
            button.setTitle_(binding_label(binding))
            return None  # swallow so the key doesn't land in the text box

        self._monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, handler)


class _NullKB:
    """A keyboard that swallows everything — lets the self-test drive the full
    typing engine without emitting real keystrokes."""
    def type(self, ch): pass
    def press(self, key): pass
    def release(self, key): pass


class _SelfTest(NSObject):
    """Headless harness: from a main-run-loop timer (exactly how the UI drives
    typing) it (1) constructs a real pynput Controller — whose __init__ calls
    get_unicode_to_keycode_map(), the Text Services Manager path that crashed
    off the main thread in the old build — and (2) runs the whole step engine.
    Success here proves the Start crash is gone. No keystrokes are emitted."""

    def applicationDidFinishLaunching_(self, _):
        self.performSelector_withObject_afterDelay_inModes_(
            b"run:", None, 0.1, [NSRunLoopCommonModes])

    def run_(self, _):
        on_main = bool(NSThread.isMainThread())
        try:
            Controller()  # <-- the call that asserted/crashed off the main thread
            state = ht.TypingState()
            engine = ht.Typist(_NullKB(), state, 0.001, 0.3)
            steps = sum(1 for _delay in engine.steps("Self-test 123. Typed OK!"))
            # Confirm the Accessibility-trust API is bundled (used to guide the
            # user on Start). Non-prompting variant so the test stays silent.
            from ApplicationServices import AXIsProcessTrusted
            ax = bool(AXIsProcessTrusted())
            # Exercise the global-hotkey tap on the MAIN thread — the path that
            # replaced pynput's background Listener (which crashed off-main).
            tap = CGEventTapCreate(
                kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
                CGEventMaskBit(kCGEventKeyDown), lambda p, t, e, r: e, None)
            tap_ok = bool(tap)
            if tap:
                CGEventTapEnable(tap, False)
            print(f"typing-selftest OK  (main_thread={on_main}, "
                  f"Controller built, {steps} engine steps clean, "
                  f"AX-api=ok trusted={ax}, hotkey-tap={'ok' if tap_ok else 'needs-perm'})")
            code = 0
        except Exception as exc:  # pragma: no cover
            print(f"typing-selftest FAIL: {exc!r}")
            code = 1
        sys.stdout.flush()
        os._exit(code)


def selftest_typing():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    runner = _SelfTest.alloc().init()
    app.setDelegate_(runner)
    app.run()


class _FlowTest(NSObject):
    """End-to-end check of the real Start flow under a live NSApplication: it
    installs the main-thread hotkey tap, builds a real pynput Controller, and
    drives the full step engine through the same timer loop the app uses — but
    types into its OWN focused text field. Surviving to the end proves the Start
    path no longer crashes; if Accessibility is granted to this process, the
    field also ends up holding exactly what was typed."""

    def applicationDidFinishLaunching_(self, _):
        self.target = "Romeo & Juliet's Test 123"  # caps + curly apostrophe
        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 380, 80), NSWindowStyleMaskTitled,
            NSBackingStoreBuffered, False)
        self.field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 26, 340, 24))
        self.win.contentView().addSubview_(self.field)
        self.win.center()
        self.win.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.win.makeFirstResponder_(self.field)

        self.state = ht.TypingState()
        self._tap = None
        self._cb = lambda p, t, e, r: e          # passthrough; just exercise the tap
        tap = CGEventTapCreate(
            kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
            CGEventMaskBit(kCGEventKeyDown), self._cb, None)
        if tap:
            src = CFMachPortCreateRunLoopSource(None, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetMain(), src, kCFRunLoopCommonModes)
            CGEventTapEnable(tap, True)
            self._tap = tap
        self.typist = ht.Typist(ht._CTypesKeyboard(), self.state, 0.004, 0.0)
        self.gen = self.typist.steps(self.target)
        self.performSelector_withObject_afterDelay_inModes_(
            b"tick:", None, 0.4, [NSRunLoopCommonModes])

    def tick_(self, _):
        if not NSThread.isMainThread():
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"tick:", None, False)
            return
        try:
            next(self.gen)
        except StopIteration:
            self.performSelector_withObject_afterDelay_inModes_(
                b"check:", None, 0.3, [NSRunLoopCommonModes])
            return
        except Exception as exc:
            print(f"flow-selftest FAIL (crash in engine): {exc!r}")
            sys.stdout.flush()
            os._exit(2)
        self.performSelector_withObject_afterDelay_inModes_(
            b"tick:", None, 0.02, [NSRunLoopCommonModes])

    def check_(self, _):
        got = str(self.field.stringValue())
        tap = "ok" if self._tap else "needs-perm"
        if got == self.target:
            print(f"flow-selftest OK  (typed & verified \"{got}\" into a real "
                  f"field, hotkey-tap={tap}) — Start flow works end-to-end")
        elif got == "":
            print("flow-selftest NO-CRASH  (full Start flow ran to completion; no "
                  "text landed because this process lacks Accessibility — the "
                  f"app will once granted; hotkey-tap={tap})")
        else:
            print(f"flow-selftest NO-CRASH  (ran clean; field got \"{got}\", "
                  f"hotkey-tap={tap})")
        sys.stdout.flush()
        os._exit(0)


def selftest_flow():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    runner = _FlowTest.alloc().init()
    app.setDelegate_(runner)
    app.run()


def main():
    if "--selftest-typing" in sys.argv:
        selftest_typing()
        return
    if "--selftest-flow" in sys.argv:
        selftest_flow()
        return
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = AppController.alloc().init()
    app.setDelegate_(controller)
    app.run()


if __name__ == "__main__":
    main()
