#!/usr/bin/env python3
"""Native macOS menu-bar UI for HumanType.

A small AppKit app: lives in the menu bar, opens a Liquid Glass window with a
paste box, a speed slider, a realism picker, Start / Pause-Resume / Stop / Quit
buttons, snippets, and click-to-record global shortcuts. All the typing logic
lives in humantype.py; this file is just the interface.

The look (since 1.5): a near-black theme with a warm glow, dark bordered cards and
a white pill for the main action. Earlier versions used the macOS 26+ glass material (NSGlassEffectView and
glass-bezel buttons) floating over a slowly drifting colour field, because glass
only reads as glass when there is something behind it to refract. AppKit gives
an app the new design only when its main executable links the macOS 26+ SDK;
build_humantype.sh checks that for the bundle."""

import json
import math
import os
import random
import ssl
import subprocess
import sys
import threading
import time
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
    NSFontWeightLight,
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
    NSFloatingWindowLevel,
    NSNormalWindowLevel,
)
from Foundation import (
    NSActivityLatencyCritical,
    NSBundle,
    NSActivityUserInitiated,
    NSMakePoint,
    NSObject,
    NSProcessInfo,
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
    kCGEventFlagsChanged,
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
APP_VERSION = "1.6.1"

# Updates ship as GitHub Releases: release.sh bumps APP_VERSION, builds the DMG,
# tags vX.Y.Z and attaches the DMG. Every installed copy polls the latest one.
GITHUB_REPO     = "sammystech/HumanType"
# HUMANTYPE_UPDATE_URL points a test build at a fake release feed.
UPDATE_URL      = (os.environ.get("HUMANTYPE_UPDATE_URL")
                   or f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
UPDATE_INTERVAL = 6 * 3600   # re-check while running; menu-bar apps run for days

# HTTPS trust. The bundled OpenSSL only looks for Homebrew's cert.pem, which
# most Macs don't have, so every request would fail certificate verification
# there. Verify against certifi's CA roots, shipped inside the app instead.
try:
    import certifi
    CA_FILE = certifi.where()
except ImportError:        # running from source without certifi
    CA_FILE = None
TLS = ssl.create_default_context(cafile=CA_FILE)


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
        with urllib.request.urlopen(req, timeout=8, context=TLS) as resp:
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


# ── Theme: near-black, a warm glow at the top, dark cards with hairline
# borders, white pill for the main action. (In the spirit of Sentient OS.)
def _rgb(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


THEME_BG = _rgb(0.024, 0.024, 0.027)
THEME_CARD = _rgb(1, 1, 1, 0.045)
THEME_BORDER = _rgb(1, 1, 1, 0.09)
THEME_BUTTON = _rgb(1, 1, 1, 0.07)
THEME_ACCENT = _rgb(0.66, 0.56, 1.0)              # soft violet
SPECTRUM = (_rgb(0.43, 0.86, 0.55), _rgb(0.33, 0.78, 0.93),
            _rgb(0.36, 0.55, 1.0), _rgb(0.72, 0.45, 1.0))


class GlowBackdrop(NSView):
    """Near-black canvas with a soft amber glow spilling from the top and a
    faint violet one low on the left."""

    def drawRect_(self, dirty):
        from AppKit import NSGradient, NSRectFill
        b = self.bounds()
        THEME_BG.setFill()
        NSRectFill(b)
        w, h = b.size.width, b.size.height
        for (cx, cy, r, color) in ((w * 0.62, h + 40, w * 0.75, _rgb(0.95, 0.62, 0.28, 0.20)),
                                   (w * 0.12, -60, w * 0.6, _rgb(0.55, 0.42, 1.0, 0.10))):
            g = NSGradient.alloc().initWithStartingColor_endingColor_(
                color, color.colorWithAlphaComponent_(0.0))
            g.drawFromCenter_radius_toCenter_radius_options_(
                NSMakePoint(cx, cy), 0, NSMakePoint(cx, cy), r, 0)


def aurora_view(rect):
    v = GlowBackdrop.alloc().initWithFrame_(rect)
    return v


class CardView(NSView):
    """A dark card with a hairline border."""

    def wantsUpdateLayer(self):
        return True

    def updateLayer(self):
        L = self.layer()
        L.setBackgroundColor_(getattr(self, "_fill", THEME_CARD).CGColor())
        L.setBorderColor_(THEME_BORDER.CGColor())
        L.setBorderWidth_(1.0)


def glass_panel(rect, radius=22.0, tint=None):
    """A dark bordered card. Returns (panel, content): add subviews to
    `content`, whose coordinates are local to the panel."""
    panel = CardView.alloc().initWithFrame_(rect)
    panel.setWantsLayer_(True)
    if radius < rect.size.height / 2 - 1:      # cards stay crisp; pills stay pills
        radius = min(radius, 16.0)
    panel.layer().setCornerRadius_(radius)
    panel.layer().setMasksToBounds_(True)
    if tint is not None:
        panel._fill = tint
    content = NSView.alloc().initWithFrame_(
        NSMakeRect(0, 0, rect.size.width, rect.size.height))
    panel.addSubview_(content)
    panel.setNeedsDisplay_(True)
    return panel, content


class ThemeButton(NSButton):
    """Flat themed button: dark with a hairline border, or (prominent) a white
    pill with black text. Dims when disabled."""

    @objc.python_method
    def style(self, prominent, radius):
        self._prominent = prominent
        self.setBordered_(False)
        self.setWantsLayer_(True)
        L = self.layer()
        L.setCornerRadius_(radius)
        L.setBackgroundColor_((NSColor.whiteColor() if prominent else THEME_BUTTON).CGColor())
        L.setBorderWidth_(0.0 if prominent else 1.0)
        L.setBorderColor_(THEME_BORDER.CGColor())
        self.setContentTintColor_(NSColor.blackColor() if prominent else NSColor.whiteColor())
        self._ink()

    @objc.python_method
    def _ink(self):
        from AppKit import (NSAttributedString, NSForegroundColorAttributeName,
                            NSFontAttributeName)
        color = NSColor.blackColor() if getattr(self, "_prominent", False) \
            else _rgb(1, 1, 1, 0.9)
        self.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            str(self.title()), {NSForegroundColorAttributeName: color,
                                NSFontAttributeName: self.font()}))

    def setTitle_(self, t):
        objc.super(ThemeButton, self).setTitle_(t)
        if hasattr(self, "_prominent"):
            self._ink()

    def setEnabled_(self, on):
        objc.super(ThemeButton, self).setEnabled_(on)
        self.setAlphaValue_(1.0 if on else 0.38)


class SpectrumSliderCell(objc.lookUpClass("NSSliderCell")):
    """Slider whose filled track is the green→blue→violet spectrum."""

    def drawBarInside_flipped_(self, rect, flipped):
        from AppKit import NSBezierPath, NSGradient
        h = 5.0
        bar = NSMakeRect(rect.origin.x, rect.origin.y + (rect.size.height - h) / 2,
                         rect.size.width, h)
        _rgb(1, 1, 1, 0.12).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar, h / 2, h / 2).fill()
        lo, hi = self.minValue(), self.maxValue()
        frac = 0.0 if hi <= lo else (self.doubleValue() - lo) / (hi - lo)
        filled = NSMakeRect(bar.origin.x, bar.origin.y, max(h, bar.size.width * frac), h)
        NSGradient.alloc().initWithColors_(list(SPECTRUM)).drawInBezierPath_angle_(
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(filled, h / 2, h / 2), 0)


def glass_group(rect, spacing=12.0):
    """NSGlassEffectContainerView: glass controls inside it render as one
    material and melt into each other when close. Returns (group, content)."""
    content = NSView.alloc().initWithFrame_(rect)
    return content, content
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
            with urllib.request.urlopen(req, timeout=12, context=TLS) as resp:
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
    ("Calibrate", "waveform", "calibrate", b"showTabCalibrate:"),
    ("Settings", "gearshape",        "settings", b"showTabSettings:"),
)

SPEED_WPM = {"Slow": 35, "Normal": 55, "Fast": 80, "Extra Fast": 120}

# Realism profiles: (typo_rate, speed_variation_scale, extra_pauses)
REALISM = {
    "Minimal":  (0.00, 0.60, False),
    "Natural":  (0.02, 1.00, True),
    "Pro":      (0.04, 1.25, True),
    "You":      (0.02, 1.00, True),     # filled in by Calibrate (ht_calibration)
}

CALIBRATE_PROMPTS = (
    "Describe a place you'd love to go back to, and why it stuck with you.",
    "Write about a skill you picked up recently and how you learned it.",
    "Tell the story of a small win you had this week.",
    "Explain how you'd spend a perfect free Saturday.",
    "Describe something you changed your mind about, and what did it.",
)
CALIBRATE_KEYS = 120        # keystrokes before the first match


def calibration_from_keys(events, final_text):
    """events: [(time, keycode)] of key presses while calibrating. Returns the
    measured profile, or None if there isn't enough typing yet."""
    if len(events) < CALIBRATE_KEYS:
        return None
    gaps = [b[0] - a[0] for a, b in zip(events, events[1:])]
    active = [g for g in gaps if g <= 3.0]              # longer = stepped away
    if not active:
        return None
    keys = [g for g in active if g <= 1.0]
    mean_key = sum(keys) / len(keys) if keys else 0.2
    var = (sum((g - mean_key) ** 2 for g in keys) / len(keys)) ** 0.5 if keys else 0.1
    letters = max(1, sum(1 for _, kc in events if kc != 51))
    corrections, prev = 0, None
    for _, kc in events:                                # a run of ⌫ = one fix
        if kc == 51 and prev != 51:
            corrections += 1
        prev = kc
    minutes = sum(active) / 60.0
    wpm = (len(final_text) / 5.0) / minutes if minutes > 0 else 40
    return {
        "wpm": int(max(10, min(200, round(wpm)))),
        "avg_key": round(mean_key, 3),
        "variation": round(max(0.2, min(0.9, var / mean_key if mean_key else 0.5)), 3),
        "pause_rate": round(max(0.0, min(0.08, sum(1 for g in active if g > 1.0) / len(active))), 4),
        "typo": round(max(0.0, min(0.12, corrections / letters)), 4),
        "corrections": corrections,
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
    -1: "", 75: "/", 76: "Enter", 122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5",
    97: "F6", 98: "F7", 100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12", 78: "-", 123: "←", 124: "→", 125: "↓", 126: "↑",
}


# On Apple-silicon MacBooks F6 is the Do Not Disturb (moon) key: macOS acts on it
# below the level any app can see, so it never arrives as F6. While it's needed
# as HumanType's lock key, the built-in keyboard is told to send a plain F6
# instead (HID usage 0x01/0x9B → keyboard F6, 0x07/0x3F); afterwards it's put back.
KEY_F6 = 97
DND_AS_F6 = ('{"UserKeyMapping":[{"HIDKeyboardModifierMappingSrc":0x10000009B,'
             '"HIDKeyboardModifierMappingDst":0x70000003F}]}')

# Bump when the default shortcuts change and everyone should move to them.
SHORTCUTS_VERSION = 2

# Bindings that act in every app even when idle, and the modifiers they need.
# Lock isn't here: a bare lock key (Tab) is fine because it only acts during a
# run or while locked (see _lock_live), so it types normally the rest of the time.
ALWAYS_ON = ("start_stop", "type_paste", "destruct")
HOTKEY_MODS = (ht.kCGEventFlagMaskCommand | ht.kCGEventFlagMaskControl
               | ht.kCGEventFlagMaskAlternate)


class NotchUpdatePanel:
    """Mac ID's update notice: a black panel that grows out of the notch (or a
    small pill under the menu bar on notchless screens) on Mac ID's open spring,
    then fades its contents in: app icon, "HumanType X is available", and
    Update now / Later, which call updateNow: / updateLater: on the target.
    Closing runs the reverse on a no-bounce spring. Never becomes key."""

    W, BODY = 380, 78
    # Mac ID's NotchGeometry springs (SwiftUI response / dampingFraction).
    OPEN_SPRING, CLOSE_SPRING = (0.45, 0.7), (0.45, 1.0)

    def __init__(self):
        self.panel = None
        self.body = None        # the black shape (a CALayer we animate)
        self.content = None     # icon, labels, buttons
        self.closed = None      # (rect, radius) the shape grows from

    @staticmethod
    def _spring(key, src, dst, response, damping):
        from Quartz import CASpringAnimation
        anim = CASpringAnimation.animationWithKeyPath_(key)
        # SwiftUI's spring(response:dampingFraction:) as a unit-mass spring.
        stiffness = (2 * math.pi / response) ** 2
        anim.setMass_(1.0)
        anim.setStiffness_(stiffness)
        anim.setDamping_(4 * math.pi * damping / response)
        anim.setFromValue_(src)
        anim.setToValue_(dst)
        anim.setDuration_(anim.settlingDuration())
        return anim

    def _morph(self, rect, radius, spring):
        """Spring the shape to rect/radius from wherever it is right now."""
        from Quartz import CATransaction
        body = self.body
        pres = body.presentationLayer() or body
        frm_b, frm_p, frm_r = pres.bounds(), pres.position(), pres.cornerRadius()
        to_b = ((0, 0), (rect.size.width, rect.size.height))
        to_p = (rect.origin.x + rect.size.width / 2, rect.origin.y + rect.size.height / 2)
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        body.setBounds_(to_b)
        body.setPosition_(to_p)
        body.setCornerRadius_(radius)
        CATransaction.commit()
        if reduce_motion():
            return 0.0
        dur = 0.0
        for key, src, dst in (("bounds", NSValue.valueWithRect_(frm_b), NSValue.valueWithRect_(to_b)),
                              ("position", NSValue.valueWithPoint_(frm_p), NSValue.valueWithPoint_(to_p)),
                              ("cornerRadius", frm_r, radius)):
            anim = self._spring(key, src, dst, *spring)
            body.addAnimation_forKey_(anim, key)
            dur = max(dur, anim.duration())
        return dur

    def _fade_content(self, alpha, duration, delay=0.0):
        def run(ctx):
            ctx.setDuration_(0.0 if reduce_motion() else duration)
            self.content.animator().setAlphaValue_(alpha)
        if delay and not reduce_motion():
            self._after(delay, lambda: NSAnimationContext.runAnimationGroup_(run))
        else:
            NSAnimationContext.runAnimationGroup_(run)

    @staticmethod
    def _after(delay, fn):
        from Foundation import NSTimer
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(delay, False, lambda t: fn())

    def show(self, version, target):
        from AppKit import (NSPanel, NSScreen, NSImageView, NSFontAttributeName,
                            NSForegroundColorAttributeName, NSAttributedString,
                            NSWindowStyleMaskBorderless,
                            NSWindowStyleMaskNonactivatingPanel)
        from Quartz import CALayer, CGWindowLevelForKey, kCGMainMenuWindowLevelKey
        screens = list(NSScreen.screens())
        screen = next((sc for sc in screens if sc.safeAreaInsets().top > 0),
                      NSScreen.mainScreen() or screens[0])
        notch_h = screen.safeAreaInsets().top
        top = max(notch_h, NSStatusBar.systemStatusBar().thickness())
        w, h = self.W, top + self.BODY
        pad = 40                # room for the shadow and the spring's overshoot
        win_w, win_h = w + 2 * pad, h + pad
        if self.panel is None:
            p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, win_w, win_h),
                NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
                NSBackingStoreBuffered, False)
            p.setReleasedWhenClosed_(False)
            # Above the menu bar, which the panel's top overlaps.
            p.setLevel_(CGWindowLevelForKey(kCGMainMenuWindowLevelKey) + 2)
            p.setBackgroundColor_(NSColor.clearColor())
            p.setOpaque_(False)
            p.setHasShadow_(False)   # the shape casts its own, so it follows the spring
            p.setBecomesKeyOnlyIfNeeded_(True)
            # canJoinAllSpaces | stationary | fullScreenAuxiliary
            p.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))
            self.panel = p
        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, win_w, win_h))
        # The shape lives on its own subview with no subviews, so AppKit never
        # re-lays out the layer we're springing.
        stage = NSView.alloc().initWithFrame_(root.bounds())
        stage.setWantsLayer_(True)
        root.addSubview_(stage)
        body = CALayer.layer()
        body.setBackgroundColor_(NSColor.blackColor().CGColor())
        body.setMaskedCorners_(1 | 2)          # bottom-left, bottom-right only
        body.setCornerCurve_("continuous")
        body.setShadowOpacity_(0.35)
        body.setShadowRadius_(14)
        body.setShadowOffset_((0, -4))
        stage.layer().addSublayer_(body)
        self.body = body

        # Where it grows from: the physical notch, else a small pill.
        left, right = screen.auxiliaryTopLeftArea(), screen.auxiliaryTopRightArea()
        notch_w = screen.frame().size.width - left.size.width - right.size.width \
            if notch_h > 0 else 0
        cw = notch_w if 0 < notch_w < w else 120
        ch = notch_h if notch_h > 0 else top
        self.closed = (NSMakeRect(pad + (w - cw) / 2, win_h - ch, cw, ch), 10.0)
        self.open = (NSMakeRect(pad, win_h - h, w, h), 22.0)

        content = NSView.alloc().initWithFrame_(NSMakeRect(pad, win_h - h, w, h))
        content.setAlphaValue_(0.0)
        root.addSubview_(content)
        self.content = content

        icon = NSImageView.alloc().initWithFrame_(NSMakeRect(16, 21, 40, 40))
        icon.setImage_(NSApplication.sharedApplication().applicationIconImage())
        content.addSubview_(icon)

        def label(text, y, hgt, font, alpha):
            lbl = NSTextField.labelWithString_(text)
            lbl.setFrame_(NSMakeRect(66, y, w - 66 - 108, hgt))
            lbl.setFont_(font)
            lbl.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(alpha))
            lbl.setLineBreakMode_(NSLineBreakByTruncatingTail)
            content.addSubview_(lbl)
        label(f"HumanType {version} is available", 42, 18,
              NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold), 1.0)
        label("A new version is ready to install.", 24, 16,
              NSFont.systemFontOfSize_(11.5), 0.7)

        def button(title, y, hgt, sel, fill, weight, alpha):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(w - 16 - 84, y, 84, hgt))
            b.setBordered_(False)
            b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                title, {NSFontAttributeName: NSFont.systemFontOfSize_weight_(12, weight),
                        NSForegroundColorAttributeName:
                            NSColor.whiteColor().colorWithAlphaComponent_(alpha)}))
            b.setTarget_(target)
            b.setAction_(sel)
            if fill is not None:
                b.setWantsLayer_(True)
                b.layer().setBackgroundColor_(fill.CGColor())
                b.layer().setCornerRadius_(hgt / 2)
            content.addSubview_(b)
        button("Update now", 43, 24, b"updateNow:", NSColor.controlAccentColor(),
               NSFontWeightSemibold, 1.0)
        button("Later", 15, 22, b"updateLater:", None, NSFontWeightMedium, 0.75)

        p = self.panel
        p.setContentView_(root)
        f = screen.frame()
        p.setFrame_display_(NSMakeRect(f.origin.x + f.size.width / 2 - win_w / 2,
                                       f.origin.y + f.size.height - win_h, win_w, win_h), True)
        # Start tucked into the notch, then spring open and bring the contents in
        # once the shape is most of the way out.
        rect, radius = self.closed
        self.body.setBounds_(((0, 0), (rect.size.width, rect.size.height)))
        self.body.setPosition_((rect.origin.x + rect.size.width / 2,
                                rect.origin.y + rect.size.height / 2))
        self.body.setCornerRadius_(radius)
        p.setAlphaValue_(1.0)
        p.orderFrontRegardless()
        self._morph(*self.open, self.OPEN_SPRING)
        self._fade_content(1.0, 0.2, delay=0.12)

    def hide(self):
        if self.panel is None or not self.panel.isVisible():
            return
        p = self.panel
        self._fade_content(0.0, 0.12)
        dur = self._morph(*self.closed, self.CLOSE_SPRING)
        # Once it's back inside the notch, take the window away.
        self._after(max(dur, 0.12), lambda: p.orderOut_(None))


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


# The 2021+ MacBook Pro keyboard (ANSI), laid out from Apple's own top-down
# product photo: (kind, main legend, shifted legend / symbol, width in key units,
# keycode). Kinds set where the legends sit, as printed on the real keys.
KB_FN_ROW = (("word_left", "esc", None, 1.5, 53),
             ("fn_key", "F1", "sun.min", 1, 122), ("fn_key", "F2", "sun.max", 1, 120),
             ("fn_key", "F3", "rectangle.3.group", 1, 99), ("fn_key", "F4", "magnifyingglass", 1, 118),
             ("fn_key", "F5", "mic", 1, 96), ("fn_key", "F6", "moon", 1, 97),
             ("fn_key", "F7", "backward", 1, 98), ("fn_key", "F8", "playpause", 1, 100),
             ("fn_key", "F9", "forward", 1, 101), ("fn_key", "F10", "speaker", 1, 109),
             ("fn_key", "F11", "speaker.wave.1", 1, 103), ("fn_key", "F12", "speaker.wave.3", 1, 111),
             ("touchid", "", None, 1, None))


def _pairs(chars, shifted, codes):
    return tuple(("pair" if s else "char", c, s, 1, k) for c, s, k in zip(chars, shifted, codes))


KB_ROWS = (
    _pairs("`1234567890-=", "~!@#$%^&*()_+", (50, 18, 19, 20, 21, 23, 22, 26, 28, 25, 29, 27, 24))
    + (("word_right", "delete", None, 1.5, 51),),
    (("word_left", "tab", None, 1.5, 48),)
    + _pairs("QWERTYUIOP[]\\", "          {}|", (12, 13, 14, 15, 17, 16, 32, 34, 31, 35, 33, 30, 42)),
    (("word_left", "caps lock", None, 1.75, 57),)
    + _pairs("ASDFGHJKL;'", "         :\"", (0, 1, 2, 3, 5, 4, 38, 40, 37, 41, 39))
    + (("word_right", "return", None, 1.75, 36),),
    (("word_left", "shift", None, 2.25, 56),)
    + _pairs("ZXCVBNM,./", "       <>?", (6, 7, 8, 9, 11, 45, 46, 43, 47, 44))
    + (("word_right", "shift", None, 2.25, 60),),
    (("globe", "fn", None, 1, 63), ("mod", "control", "⌃", 1, 59), ("mod", "option", "⌥", 1, 58),
     ("mod", "command", "⌘", 1.25, 55), ("char", "", None, 5, 49),
     ("mod", "command", "⌘", 1.25, 54), ("mod", "option", "⌥", 1, 61), ("arrows", "", None, 3, None)),
)


def binding_keycodes(binding):
    """The keys to light up for a shortcut: its key plus the left-hand modifiers."""
    keycode, mods = binding
    keys = {keycode}
    for mask, kc in ((kCGEventFlagMaskControl, 59), (kCGEventFlagMaskAlternate, 58),
                     (kCGEventFlagMaskCommand, 55), (kCGEventFlagMaskShift, 56)):
        if mods & mask:
            keys.add(kc)
    return keys


class KeyboardView(NSView):
    """A drawn MacBook Pro keyboard. Keys in `lit` glow blue: the shortcut to press."""

    def isFlipped(self):
        return True

    @objc.python_method
    def set_lit(self, keycodes):
        self._lit = set(keycodes)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _key(self, rect, kind, main, extra, keycode):
        from AppKit import (NSBezierPath, NSShadow, NSGraphicsContext,
                            NSMutableParagraphStyle, NSFontAttributeName,
                            NSForegroundColorAttributeName, NSParagraphStyleAttributeName,
                            NSImageSymbolConfiguration, NSString)
        lit = keycode is not None and keycode in getattr(self, "_lit", ())
        x, y, w, h = rect.origin.x, rect.origin.y, rect.size.width, rect.size.height
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, h * 0.14, h * 0.14)
        NSGraphicsContext.saveGraphicsState()
        if lit:
            glow = NSShadow.alloc().init()
            glow.setShadowColor_(NSColor.systemBlueColor().colorWithAlphaComponent_(0.95))
            glow.setShadowBlurRadius_(16)
            glow.setShadowOffset_((0, 0))
            glow.set()
            NSColor.systemBlueColor().setFill()
        else:
            NSColor.colorWithWhite_alpha_(0.13, 1.0).setFill()
        path.fill()
        NSGraphicsContext.restoreGraphicsState()
        NSColor.colorWithWhite_alpha_(1.0, 0.4 if lit else 0.05).setStroke()
        path.setLineWidth_(0.75)
        path.stroke()

        ink = NSColor.whiteColor() if lit else NSColor.colorWithWhite_alpha_(0.86, 1.0)
        para = NSMutableParagraphStyle.alloc().init()

        def text(s, size, r, align=NSTextAlignmentCenter, weight=NSFontWeightLight):
            para.setAlignment_(align)
            NSString.stringWithString_(s).drawInRect_withAttributes_(r, {
                NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, weight),
                NSForegroundColorAttributeName: ink, NSParagraphStyleAttributeName: para})

        def symbol(name, size, cx, cy):
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
            if img is None:
                return
            cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, NSFontWeightRegular) \
                .configurationByApplyingConfiguration_(
                    NSImageSymbolConfiguration.configurationWithPaletteColors_([ink]))
            img = img.imageWithSymbolConfiguration_(cfg)
            sz = img.size()
            img.drawInRect_(NSMakeRect(cx - sz.width / 2, cy - sz.height / 2, sz.width, sz.height))

        small = max(8.0, h * 0.2)
        if kind == "fn_key":
            symbol(extra, h * 0.24, x + w / 2, y + h * 0.36)
            text(main, h * 0.17, NSMakeRect(x, y + h * 0.66, w, h * 0.24))
        elif kind == "touchid":
            ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x + w / 2 - h * 0.38, y + h * 0.12,
                                                                     h * 0.76, h * 0.76))
            NSColor.colorWithWhite_alpha_(1.0, 0.12).setStroke()
            ring.setLineWidth_(1.0)
            ring.stroke()
        elif kind == "pair":
            text(extra, h * 0.27, NSMakeRect(x, y + h * 0.12, w, h * 0.36))
            text(main, h * 0.3, NSMakeRect(x, y + h * 0.48, w, h * 0.4))
        elif kind == "char" and main:
            text(main, h * 0.33, NSMakeRect(x, y + (h - h * 0.45) / 2, w, h * 0.5))
        elif kind == "word_left":
            text(main, small, NSMakeRect(x + 7, y + h - small - 7, w - 12, small + 3), 0)
        elif kind == "word_right":
            text(main, small, NSMakeRect(x + 5, y + h - small - 7, w - 12, small + 3),
                 NSTextAlignmentRight)
        elif kind == "mod":
            text(extra, h * 0.27, NSMakeRect(x, y + h * 0.12, w, h * 0.36))
            text(main, small, NSMakeRect(x, y + h - small - h * 0.16, w, small + 3))
        elif kind == "globe":
            text(main, small, NSMakeRect(x + 5, y + 5, w - 11, small + 3), NSTextAlignmentRight)
            symbol("globe", small + 1, x + 13, y + h - small / 2 - 8)
        elif kind == "arrow":
            text(main, h * 0.42, NSMakeRect(x, y + (h - h * 0.62) / 2, w, h * 0.62))

    def drawRect_(self, dirty):
        from AppKit import NSBezierPath
        b = self.bounds()
        # Just the black keyboard: no aluminium case or speaker grilles.
        well = b
        NSColor.colorWithWhite_alpha_(0.03, 1.0).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(well, 14, 14).fill()

        pad = 6.0
        gap = well.size.width * 0.0105
        unit = (well.size.width - 2 * pad + gap) / 14.5        # one key unit, gap included
        key_h = (well.size.height - 2 * pad - 5 * gap) / 6     # every row full height
        y = well.origin.y + pad
        x = well.origin.x + pad
        for kind, main, extra, wu, kc in KB_FN_ROW:
            w = wu * unit - gap
            self._key(NSMakeRect(x, y, w, key_h), kind, main, extra, kc)
            x += w + gap
        y += key_h + gap
        for row in KB_ROWS:
            x = well.origin.x + pad
            for kind, main, extra, wu, kc in row:
                w = wu * unit - gap
                if kind == "arrows":
                    aw = (w - 2 * gap) / 3
                    half = (key_h - gap) / 2
                    lo = y + half + gap
                    self._key(NSMakeRect(x, lo, aw, half), "arrow", "◀", None, 123)
                    self._key(NSMakeRect(x + aw + gap, y, aw, half), "arrow", "▲", None, 126)
                    self._key(NSMakeRect(x + aw + gap, lo, aw, half), "arrow", "▼", None, 125)
                    self._key(NSMakeRect(x + 2 * (aw + gap), lo, aw, half), "arrow", "▶", None, 124)
                else:
                    self._key(NSMakeRect(x, y, w, key_h), kind, main, extra, kc)
                x += w + gap
            y += key_h + gap


class _CountingKeyboard:
    """Wraps the keyboard a run types through and counts what it emits, so
    key-by-key mode can step past the engine's pause-only steps (a hesitation
    before a word, noticing a typo) and always type something per press."""

    def __init__(self, kb):
        self.kb = kb
        self.count = 0

    def type(self, ch):
        self.count += 1
        self.kb.type(ch)

    def press(self, key):
        self.count += 1
        self.kb.press(key)

    def release(self, key):
        self.kb.release(key)


class _ViewKeyboard:
    """The tour's keyboard: the real typing engine (speed, pauses, typos and
    their fixes) writes straight into the practice box, so it types the moment
    a run starts, wherever focus is."""

    def __init__(self, view, on_change=None):
        self.view = view
        self.on_change = on_change

    def _edit(self, fn):
        s = self.view.textStorage().mutableString()
        fn(s)
        # Plain-string edits on an emptied view fall back to black text.
        self.view.setTextColor_(NSColor.labelColor())
        self.view.setFont_(NSFont.systemFontOfSize_(15))
        self.view.scrollRangeToVisible_((s.length(), 0))
        self.view.setSelectedRange_((s.length(), 0))
        if self.on_change:
            self.on_change()

    def type(self, ch):
        self._edit(lambda s: s.appendString_(ch))

    def press(self, key):
        if key == ht.Key.backspace:
            self._edit(lambda s: s.length() and s.deleteCharactersInRange_((s.length() - 1, 1)))
        elif key == ht.Key.enter:
            self.type("\n")
        elif key == ht.Key.tab:
            self.type("\t")

    def release(self, key):
        pass


ONBOARD_SAMPLE = ("Hi! This is HumanType typing for you. It moves at a natural speed, "
                  "takes little pauses between words, and now and then makes a typo "
                  "and fixes it, just like a real person would.")


class Onboarding(NSObject):
    """First-run tour, drawn over the main window: paste text, then press each
    real shortcut (Start, Pause, Lock, Stop, Type clipboard) and watch a
    practice run react. The practice run is a real one, so every shortcut
    works as usual at any step; the app reports what happened via event().
    Skip anytime."""

    # (key, SF Symbol, title, body; {start} etc. are filled with the shortcuts)
    STEPS = (
        ("welcome", "keyboard", "Welcome to HumanType",
         "HumanType types your text for you, one key at a time, like a real person: "
         "natural speed, little pauses, even the odd typo it fixes. This quick tour "
         "takes about a minute."),
        ("permission", "lock.shield", "Let HumanType type for you",
         "macOS only lets an app press keys for you once you allow it. Open "
         "Settings, switch on HumanType under Accessibility, then come back here."),
        ("paste", "doc.on.clipboard", "Paste your text",
         "Everything starts with the text box. Paste what you want typed. "
         "There's a sample in it already, so you can practise with that."),
        ("start", "play.fill", "Start typing",
         "Press {start_stop} to start. For real, it waits {countdown} seconds first so you "
         "can click into your document: if nothing happens right away, just wait. "
         "Here it starts straight away."),
        ("pause", "pause.fill", "Pause and resume",
         "Press {pause} to pause. Press it again to carry on where it left off."),
        ("lock", "lock.fill", "Lock your keyboard",
         "{lock_body}"),
        ("stop", "stop.fill", "Stop anytime",
         "Press {start_stop} again to stop typing straight away. The same shortcut "
         "starts and stops."),
        ("type_paste", "doc.on.doc", "Type what you copied",
         "Copy some text, then press {type_paste}: HumanType types it right where "
         "you're typing, in any app. ⌘V still pastes like normal."),
        ("destruct", "flame.fill", "Self-destruct",
         "Need it gone fast? Hold {destruct} together for a moment: HumanType stops, "
         "wipes the text you haven't saved and quits on the spot. Try it here, it "
         "won't quit."),
        ("done", "checkmark", "You're all set",
         "Change the speed, realism and every shortcut in Settings. You can replay "
         "this tour anytime from the HumanType menu in the menu bar."),
    )
    KEY_STEPS = {"start": ("start_stop",), "pause": ("pause",), "lock": ("lock",),
                 "stop": ("start_stop",), "type_paste": ("type_paste",),
                 "destruct": ("destruct",)}

    def initWithController_(self, ctrl):
        self = objc.super(Onboarding, self).init()
        if self is None:
            return None
        self.ctrl = ctrl
        self.index = 0
        self._demo_text = ONBOARD_SAMPLE
        self._blocked = 0
        self._presses = 0
        self._token = 0             # invalidates pending auto-advances
        self._build()
        return self

    # ── Layout ───────────────────────────────────────────────────────────

    @objc.python_method
    def _build(self):
        c = self.ctrl
        self.view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        self.view.addSubview_(aurora_view(self.view.bounds()))
        self.skip_btn = c._glass_button("Skip tour", b"skip:", NSMakeRect(W - M - 104, H - 46, 104, 28), size=12)
        self.skip_btn.setTarget_(self)
        self.view.addSubview_(self.skip_btn)
        self._dots = []
        n, gap = len(self.STEPS), 16
        x0 = (W - (n - 1) * gap) / 2 - 3.5
        for i in range(n):
            d = color_view(NSMakeRect(x0 + i * gap, H - 36, 7, 7), NSColor.labelColor(), 3.5)
            self.view.addSubview_(d)
            self._dots.append(d)
        self.stage = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H - 56))
        self.stage.setWantsLayer_(True)
        self.view.addSubview_(self.stage)

        # The practice box is shared by the key steps, so the run carries on
        # from one step to the next.
        bw, bh = W - 2 * M - 40, 80
        self.practice, pc = glass_panel(NSMakeRect((W - bw) / 2, 132, bw, bh), radius=20)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 6, bw - 24, bh - 12))
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        tv = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        tv.setRichText_(False)
        tv.setDrawsBackground_(False)
        tv.setFont_(NSFont.systemFontOfSize_(15))
        tv.setTextColor_(NSColor.labelColor())
        tv.setInsertionPointColor_(NSColor.controlAccentColor())
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setAutomaticTextReplacementEnabled_(False)
        tv.setTextContainerInset_((6, 8))
        tv.setVerticallyResizable_(True)
        tv.setDelegate_(self)       # textDidChange_ hides the hint as you type
        scroll.setDocumentView_(tv)
        # Hint under the (transparent) text view while the box is empty.
        self.practice_hint = c._label("", NSMakeRect(20, 0, bw - 40, 20), size=14, dim=True)
        pc.addSubview_(self.practice_hint)
        pc.addSubview_(scroll)
        self.practice_tv = tv

        # The paste box (step 2), prefilled with the sample.
        self.paste_card, pcc = glass_panel(NSMakeRect((W - bw) / 2, 130, bw, 190), radius=20)
        ps = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 12, bw - 24, 166))
        ps.setDrawsBackground_(False)
        ps.setBorderType_(0)
        ps.setHasVerticalScroller_(True)
        ps.setAutohidesScrollers_(True)
        ptv = NSTextView.alloc().initWithFrame_(ps.contentView().bounds())
        ptv.setRichText_(False)
        ptv.setDrawsBackground_(False)
        ptv.setFont_(NSFont.systemFontOfSize_(15))
        ptv.setTextColor_(NSColor.labelColor())
        ptv.setInsertionPointColor_(NSColor.controlAccentColor())
        ptv.setTextContainerInset_((6, 8))
        ptv.setVerticallyResizable_(True)
        ptv.setAutomaticQuoteSubstitutionEnabled_(False)
        ptv.setString_(ONBOARD_SAMPLE)
        ps.setDocumentView_(ptv)
        pcc.addSubview_(ps)
        self.paste_tv = ptv

    @objc.python_method
    def _show_step(self):
        c = self.ctrl
        self._token += 1
        self._presses = 0
        key, symbol, title, body = self.STEPS[self.index]
        for i, d in enumerate(self._dots):
            d.set_color(NSColor.whiteColor() if i == self.index
                        else NSColor.whiteColor().colorWithAlphaComponent_(0.18))
        for v in list(self.stage.subviews()):
            v.removeFromSuperview()
        shortcuts = {k: binding_label(b) for k, b in c._bindings().items()}
        shortcuts["countdown"] = c._countdown_secs
        shortcuts["lock_body"] = (
            f"Press {shortcuts['lock']} to lock it. Now every key you press types the next "
            f"letter of your text, whatever the key. Press {shortcuts['lock']} again to "
            "unlock: typing pauses and your keyboard is yours."
            if c._mash_mode else
            f"Press {shortcuts['lock']} to lock your keyboard, then mash any keys you like: "
            f"nothing gets through, only HumanType's typing does. Press {shortcuts['lock']} "
            "again to unlock.")
        # Text-only screens sit in the middle; the rest leave room below.
        top = H - 56 - (140 if key in ("welcome", "done") else 0)
        if key in self.KEY_STEPS:
            top += 90   # the keyboard is the picture on these steps: no icon

        icon, ic = glass_panel(NSMakeRect((W - 76) / 2, top - 116, 76, 76), radius=38)
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
        if img is not None:
            from AppKit import NSImageView, NSImageSymbolConfiguration
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 76, 76))
            iv.setImage_(img)
            iv.setSymbolConfiguration_(
                NSImageSymbolConfiguration.configurationWithPointSize_weight_(28, NSFontWeightSemibold))
            iv.setContentTintColor_(NSColor.whiteColor())
            ic.addSubview_(iv)
        if key not in self.KEY_STEPS:
            self.stage.addSubview_(icon)
        self.stage.addSubview_(c._label(title, NSMakeRect(M, top - 166, W - 2 * M, 34), size=26,
                                        weight=NSFontWeightBold, align=NSTextAlignmentCenter))
        b = NSTextField.wrappingLabelWithString_(body.format(**shortcuts))
        b.setFrame_(NSMakeRect(70, top - 244, W - 140, 70))
        b.setFont_(NSFont.systemFontOfSize_(14))
        b.setTextColor_(NSColor.secondaryLabelColor())
        b.setAlignment_(NSTextAlignmentCenter)
        self.stage.addSubview_(b)

        self.feedback = c._label("", NSMakeRect(M, 104, W - 2 * M, 18), size=13,
                                 weight=NSFontWeightSemibold, align=NSTextAlignmentCenter,
                                 color=THEME_ACCENT)
        if key == "paste":
            self.stage.addSubview_(self.paste_card)
            self.view.window().makeFirstResponder_(self.paste_tv)
        elif key in self.KEY_STEPS:
            kw, kh = W - 24, 250
            kb = KeyboardView.alloc().initWithFrame_(NSMakeRect((W - kw) / 2, 220, kw, kh))
            kb.set_lit(binding_keycodes(c._bindings()[self.KEY_STEPS[key][0]]))
            self.stage.addSubview_(kb)
            self.stage.addSubview_(self.practice)
            pw = self.practice.frame().size.width
            self.practice_hint.setFrame_(NSMakeRect(20, 30, pw - 40, 20))
            self.practice_hint.setStringValue_(
                f"Press {binding_label(c.start_stop_binding)} and watch it type here.")
            self.practice_hint.setHidden_(c._typing_gen is not None
                                          or bool(str(self.practice_tv.string())))
        if key in self.KEY_STEPS:
            self.stage.addSubview_(self.feedback)
        self._ran_this_step = False
        if key in ("pause", "lock", "stop", "destruct"):
            token = self._token
            NotchUpdatePanel._after(0.6, lambda: token == self._token and c._typing_gen is None
                                    and not self._ran_this_step and self.start_practice())
        elif c._practice_run and self.practice_visible():
            self.view.window().makeFirstResponder_(self.practice_tv)

        primary = {"welcome": "Start the tour", "done": "Start using HumanType"}.get(key, "Next")
        btn = c._glass_button(primary, b"next:", NSMakeRect((W - 240) / 2, 52, 240, 44),
                              prominent=True, size=15)
        btn.setTarget_(self)
        self.stage.addSubview_(btn)
        self._primary = btn
        if key == "permission":
            self._build_permission(top - 330)
        if key in self.KEY_STEPS:
            hint = c._label("Try the shortcut, or press Next to skip this step.",
                            NSMakeRect(M, 24, W - 2 * M, 16), size=11, gray=True,
                            align=NSTextAlignmentCenter)
            self.stage.addSubview_(hint)
        self.skip_btn.setHidden_(key == "done")

        if not reduce_motion():
            self.stage.setAlphaValue_(0.0)
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.28)
            self.stage.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()

    # ── Navigation ───────────────────────────────────────────────────────

    @objc.python_method
    def show(self, step=None):
        c = self.ctrl
        c.showWindow_(None)
        c.window.contentView().addSubview_(self.view)
        keys = [k for k, *_ in self.STEPS]
        self.index = keys.index(step) if step in keys else 0
        self._show_step()

    # ── Permission step ──────────────────────────────────────────────────
    # Accessibility lets HumanType post keystrokes and run its shortcut tap.
    # The step watches for the switch, then tries to start the tap right
    # away; only if macOS still refuses does it ask for a quick reopen.

    @objc.python_method
    def _build_permission(self, y):
        c = self.ctrl
        cw = W - 2 * M - 40
        card, cc = glass_panel(NSMakeRect((W - cw) / 2, y, cw, 76), radius=14)
        from AppKit import NSImageView, NSImageSymbolConfiguration
        iv = NSImageView.alloc().initWithFrame_(NSMakeRect(18, 26, 24, 24))
        iv.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "accessibility", "Accessibility"))
        iv.setSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPointSize_weight_(17, NSFontWeightMedium))
        iv.setContentTintColor_(NSColor.whiteColor())
        cc.addSubview_(iv)
        cc.addSubview_(c._label("Accessibility", NSMakeRect(54, 40, 220, 18), size=13.5,
                                weight=NSFontWeightSemibold))
        self._perm_detail = c._label("", NSMakeRect(54, 18, cw - 54 - 190, 18), size=12, gray=True)
        cc.addSubview_(self._perm_detail)
        self._perm_btn = c._glass_button("Open Settings", b"permAction:",
                                         NSMakeRect(cw - 18 - 160, 20, 160, 36), size=13)
        self._perm_btn.setTarget_(self)
        cc.addSubview_(self._perm_btn)
        self._perm_ok = c._label("", NSMakeRect(cw - 18 - 160, 28, 160, 20), size=13,
                                 weight=NSFontWeightSemibold, align=NSTextAlignmentRight,
                                 color=NSColor.systemGreenColor())
        cc.addSubview_(self._perm_ok)
        self.stage.addSubview_(card)
        self._perm_note = c._label("", NSMakeRect(M, y - 30, W - 2 * M, 18), size=12,
                                   gray=True, align=NSTextAlignmentCenter)
        self.stage.addSubview_(self._perm_note)
        self._perm_state = None
        self._settings_opened_at = getattr(self, "_settings_opened_at", None)
        self._perm_poll(self._token)

    @objc.python_method
    def _perm_poll(self, token):
        if token != self._token or self.view.superview() is None:
            return
        c = self.ctrl
        trusted = c._is_trusted()
        if c._tap is None:
            c._install_global_tap()          # works as soon as macOS allows it
        if c._tap is not None and trusted is False:
            trusted = True
        # The tap is the real test: macOS often keeps reporting "not trusted"
        # to an app until it restarts, even with the switch on.
        opened = self._settings_opened_at and time.time() - self._settings_opened_at > 4
        state = "ok" if c._tap is not None else ("reopen" if trusted or opened else "off")
        if state != self._perm_state:
            self._perm_state = state
            if state == "ok":
                self._perm_detail.setStringValue_("Allowed. You're good to go.")
                self._perm_btn.setHidden_(True)
                self._perm_ok.setStringValue_("✓ Allowed")
                self._perm_note.setStringValue_("")
            elif state == "reopen":
                stale = c._just_relaunched
                self._perm_detail.setStringValue_(
                    "Still blocked: the switch is from an older copy." if stale
                    else "Switched it on? Reopen to apply it.")
                self._perm_btn.setTitle_("Fix permission" if stale else "Reopen HumanType")
                self._perm_btn.setHidden_(False)
                self._perm_ok.setStringValue_("")
                self._perm_note.setStringValue_(
                    "macOS often needs HumanType to restart before it notices. "
                    "It reopens right here, at this step.")
            else:
                self._perm_detail.setStringValue_("Not allowed yet.")
                self._perm_btn.setTitle_("Open Settings")
                self._perm_btn.setHidden_(False)
                self._perm_ok.setStringValue_("")
                self._perm_note.setStringValue_(
                    "In Privacy & Security → Accessibility, switch on HumanType. "
                    "This screen updates by itself.")
        NotchUpdatePanel._after(1.0, lambda: self._perm_poll(token))

    def permAction_(self, sender):
        c = self.ctrl
        if self._perm_state == "reopen" and c._just_relaunched:
            # Already reopened and still blocked: the switch is a stale one.
            c._fix_permission()
            self._settings_opened_at = time.time()
            c._just_relaunched = False
        elif self._perm_state == "reopen":
            NSUserDefaults.standardUserDefaults().setObject_forKey_("permission", "ht_resume_tour")
            c._relaunch()
        else:
            self._settings_opened_at = time.time()
            c._prompt_accessibility()
            c._open_accessibility_settings()

    @objc.python_method
    def current(self):
        return self.STEPS[self.index][0]

    @objc.python_method
    def expected(self):
        return self.KEY_STEPS.get(self.current(), ())

    def next_(self, sender):
        if self.current() == "done":
            self._close()
            return
        self._leave_step()
        self.index += 1
        self._show_step()

    def skip_(self, sender):
        self._close()

    @objc.python_method
    def _leave_step(self):
        if self.current() == "paste":
            text = str(self.paste_tv.string()).strip() or ONBOARD_SAMPLE
            self._demo_text = text[:220]
            self.practice_tv.setString_("")

    @objc.python_method
    def _advance_later(self, delay):
        token = self._token
        def go():
            if token == self._token and self.view.superview() is not None:
                self.next_(None)
        NotchUpdatePanel._after(delay, go)

    @objc.python_method
    def _close(self):
        c = self.ctrl
        if c._practice_run and c.state is not None:
            c.state.stop()
            c._finish(False)
        if c._kbd_locked:
            c.toggleLock_(None)
        self._token += 1
        NSUserDefaults.standardUserDefaults().setBool_forKey_(True, "ht_onboarded")
        text = str(self.paste_tv.string()).strip()
        if text and text != ONBOARD_SAMPLE and not str(self.ctrl.text_view.string()).strip():
            self.ctrl.text_view.setString_(text)
            self.ctrl.textDidChange_(None)
        self.view.removeFromSuperview()
        self.ctrl._onboarding = None
        self.ctrl._sync_dnd_remap()
        self.ctrl._show_tab("type", animate=False)

    # ── Practice run ─────────────────────────────────────────────────────
    # The real engine types into the practice box, real keystrokes and all,
    # and every shortcut does exactly what it does in normal use, at any step.
    # The app reports what happened through event(); the tour reacts.

    def textDidChange_(self, notification):
        self.practice_hint.setHidden_(bool(str(self.practice_tv.string())))

    @objc.python_method
    def practice_visible(self):
        return self.practice.superview() is not None

    @objc.python_method
    def start_practice(self, text=None, event="start"):
        c = self.ctrl
        if c._typing_gen is not None or not self.practice_visible():
            return False
        self._ran_this_step = True
        self.practice_tv.setString_("")
        self.practice_hint.setHidden_(True)
        self.view.window().makeFirstResponder_(self.practice_tv)
        c._practice_run = True
        c._run_typing(text if text is not None else self._demo_text, 0,
                      kb=_ViewKeyboard(self.practice_tv))
        c._from_window = False
        c._set_status("Typing into the practice box…")
        self.event(event)
        return True

    @objc.python_method
    def event(self, name):
        """Something happened (a shortcut, a button, the run finishing)."""
        if self.view.superview() is None:
            return
        keys = {k: binding_label(b) for k, b in self.ctrl._bindings().items()}
        n = self._blocked
        msg = {
            "start": "It's typing. Nice!",
            "pause": f"Paused. Press {keys['pause']} again to resume.",
            "resume": "Typing again, right where it stopped.",
            "lock": (f"Locked. Press any keys: each one types the next letter. {keys['lock']} unlocks."
                     if self.ctrl._mash_mode else
                     f"Locked. Mash some keys: nothing gets through. {keys['lock']} unlocks."),
            "unlock": (f"Unlocked: typing paused, your keyboard is back. ({n} key{'s' if n != 1 else ''})"
                       if self.ctrl._mash_mode else
                       f"Unlocked. {n} key{'s' if n != 1 else ''} blocked."),
            "stop": f"Stopped. {keys['start_stop']} starts it again.",
            "done": f"Complete. {keys['start_stop']} types it again.",
            "type_paste": "It's typing what you copied.",
        }.get(name)
        if msg and getattr(self, "feedback", None) is not None:
            self.feedback.setStringValue_(msg)
        if name == "lock":
            self._blocked = 0
        step = self.current()
        if {"start": "start", "pause": "resume", "lock": "unlock", "stop": "stop",
                "type_paste": "type_paste"}.get(step) == name:
            self._advance_later(1.8)
        elif name in ("done", "stop") and step in ("pause", "lock"):
            # These steps need something typing to act on: go again.
            token = self._token
            NotchUpdatePanel._after(1.2, lambda: token == self._token and self.start_practice())

    @objc.python_method
    def demo_destruct(self):
        """Self-destruct on its own tour page: do everything but quit."""
        c = self.ctrl
        self._token += 1                # no restart, no auto-advance from "stop"
        if c.state is not None and c._typing_gen is not None:
            c.state.stop()
            c._finish(False)
        self.practice_tv.setString_("")
        self.practice_hint.setHidden_(True)
        self.feedback.setStringValue_("Wiped. For real, HumanType would also quit right now.")
        self._advance_later(2.6)

    @objc.python_method
    def blocked_key(self):
        self._blocked += 1
        n = self._blocked
        if getattr(self, "feedback", None) is not None:
            verb = "typed it letter by letter" if self.ctrl._mash_mode else "blocked"
            self.feedback.setStringValue_(
                f"Locked · {n} key{'s' if n != 1 else ''} {verb}. "
                f"{binding_label(self.ctrl.lock_binding)} unlocks.")


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
        self._activity = None       # App Nap opt-out held for a typing run
        self._target_app = None     # the app being typed into (for Resume)
        self._holding = False       # typing waits while HumanType is frontmost
        self._onboarding = None     # the first-run tour, while it's on screen
        self._from_window = False   # the run was started with Start, not ⌃⌥V
        self._practice_run = False  # the tour's run, typing into its practice box
        self._dnd_remapped = False  # moon key currently sending F6 (see DND_AS_F6)
        self._chord_armed = False   # ⌃⌥⌘ held, self-destruct pending (see the tap)
        self._monitor = None
        self._record_button = None  # button currently awaiting a key press
        self._record_which = None   # which binding is being recorded
        self._asked_ax = False      # whether we've shown the Accessibility prompt
        d = NSUserDefaults.standardUserDefaults()
        # Did we just relaunch ourselves to apply a permission? (avoids loops)
        self._just_relaunched = bool(d.boolForKey_("relaunched_for_perm"))
        if self._just_relaunched:
            d.removeObjectForKey_("relaunched_for_perm")
        # One-time move to the current default shortcuts (⌘S, `, F6, ⌥⌘V, ⌃⌥⌘):
        # shortcuts saved by older versions are cleared once, then any the user
        # records from here on are kept.
        if d.integerForKey_("ht_shortcuts_version") < SHORTCUTS_VERSION:
            for name in ("start_stop", "pause", "stop", "lock", "type_paste", "destruct"):
                d.removeObjectForKey_(name + "_kc")
                d.removeObjectForKey_(name + "_mods")
            d.setInteger_forKey_(SHORTCUTS_VERSION, "ht_shortcuts_version")
        self.pause_binding = self._load_binding(d, "pause", ht.DEFAULT_PAUSE_BINDING)
        self.stop_binding = self._load_binding(d, "stop", ht.DEFAULT_STOP_BINDING)
        self.start_stop_binding = self._load_binding(
            d, "start_stop", ht.DEFAULT_START_STOP_BINDING)
        self.lock_binding = self._load_binding(d, "lock", ht.DEFAULT_LOCK_BINDING)
        self.type_paste_binding = self._load_binding(
            d, "type_paste", ht.DEFAULT_TYPE_PASTE_BINDING)
        self.destruct_binding = self._load_binding(
            d, "destruct", ht.DEFAULT_DESTRUCT_BINDING)
        self._kbd_locked = False
        # Settings (loaded fresh from defaults each time, saved on change)
        self._current_wpm       = int(d.integerForKey_("ht_wpm") or 55)
        if self._current_wpm < 10: self._current_wpm = 55
        self._countdown_secs    = int(d.integerForKey_("ht_countdown") or 5)
        if self._countdown_secs < 1: self._countdown_secs = 5
        self._realism_name      = d.stringForKey_("ht_realism") or "Natural"
        if self._realism_name not in REALISM: self._realism_name = "Natural"
        if self._realism_name == "You" and not d.dictionaryForKey_("ht_calibration"):
            self._realism_name = "Natural"
        self._auto_paste        = bool(d.boolForKey_("ht_auto_paste"))
        # Key-by-key: while locked, each key press types the next letter (on by default).
        self._mash_mode = (True if d.objectForKey_("ht_mash_mode") is None
                           else bool(d.boolForKey_("ht_mash_mode")))
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
        binding = (int(d.integerForKey_(prefix + "_kc")),
                   int(d.integerForKey_(prefix + "_mods")))
        if prefix in ALWAYS_ON and not binding[1] & HOTKEY_MODS:
            return default    # saved before bare keys were refused (see _record)
        return binding

    @objc.python_method
    def _save_binding(self, prefix, binding):
        d = NSUserDefaults.standardUserDefaults()
        d.setInteger_forKey_(binding[0], prefix + "_kc")
        d.setInteger_forKey_(binding[1], prefix + "_mods")

    def applicationDidFinishLaunching_(self, notification):
        # A brand-new install has no saved settings yet: that's who gets the tour.
        bid = NSBundle.mainBundle().bundleIdentifier()
        first_run = bool(bid) and not NSUserDefaults.standardUserDefaults() \
            .persistentDomainForName_(bid)
        self._build_main_menu()
        self._build_status_item()
        self._build_window()
        self._install_global_tap()

        self._update_info = None       # filled by background update check
        self._ready_dmg = None         # downloaded update waiting to install
        self._ready_version = None
        self._downloading = False
        self._installing = False
        self._relaunching = False      # permission relaunch: don't install on quit
        self._update_waiting = False   # panel deferred until the typing run ends
        self._notch = NotchUpdatePanel()
        # Check for updates quietly a few seconds after launch, then periodically.
        self._schedule(b"_checkForUpdates:", 4.0)
        if LicenseManager.is_licensed() or not LICENSING_ENABLED:
            self.showWindow_(None)
            resume = NSUserDefaults.standardUserDefaults().stringForKey_("ht_resume_tour")
            if resume:      # reopened from the tour's permission step: carry on there
                NSUserDefaults.standardUserDefaults().removeObjectForKey_("ht_resume_tour")
                self._resume_step = str(resume)
                self._schedule(b"showTutorial:", 0.3)
            elif first_run or "--onboarding" in sys.argv:
                self._schedule(b"showTutorial:", 0.3)
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
    # Same flow as Mac ID: a newer release downloads in the background, then a
    # panel drops out of the notch with Update now / Later. Update now swaps in
    # the new version and relaunches; Later installs it the next time HumanType
    # quits. The panel never takes focus, so it can't steal keystrokes.

    def _checkForUpdates_(self, _):
        """Scheduled on the main run loop: shortly after launch, then every
        UPDATE_INTERVAL. The network work runs on a background thread."""
        threading.Thread(target=self._check_bg, args=(False,), daemon=True).start()
        self._schedule(b"_checkForUpdates:", UPDATE_INTERVAL)

    def checkForUpdatesManually_(self, sender):
        """Menu item: show a downloaded update again, or check now."""
        if self._ready_dmg and os.path.exists(self._ready_dmg):
            self._presentUpdate_(None)
            return
        self._set_status("Checking for updates…")
        threading.Thread(target=self._check_bg, args=(True,), daemon=True).start()

    @objc.python_method
    def _on_main(self, sel, obj=None):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(sel, obj, False)

    @objc.python_method
    def _check_bg(self, manual):
        """Background: find a newer release, download it, then show the panel."""
        try:
            info = fetch_latest_release()
        except Exception as e:
            if manual:
                self._on_main(b"_updateCheckFailed:", str(e))
            return   # scheduled checks stay silent; the next one tries again
        if not info or _version_tuple(info["version"]) <= _version_tuple(APP_VERSION):
            if manual:
                self._on_main(b"_noUpdateFound:")
            return
        version, url = info["version"], info.get("download_url", "")
        self._update_info = info
        self._on_main(b"_showUpdateBadge:", info)
        if self._ready_version != version or not os.path.exists(self._ready_dmg or ""):
            if not url or self._downloading:
                return
            if manual:
                self._on_main(b"_setStatusText:", f"Downloading HumanType {version}…")
            self._downloading = True
            dmg = f"/tmp/HumanType_update_{version}.dmg"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": f"HumanType/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=60, context=TLS) as resp, \
                        open(dmg, "wb") as fh:
                    while chunk := resp.read(256 * 1024):
                        fh.write(chunk)
            except Exception as e:
                if manual:
                    self._on_main(b"_updateCheckFailed:", f"Download failed: {e}")
                return
            finally:
                self._downloading = False
            self._ready_dmg, self._ready_version = dmg, version
        self._on_main(b"_presentUpdate:")

    def _presentUpdate_(self, _):
        """Main thread: drop the panel out of the notch — but never mid-run;
        a run in progress shows it when it finishes (see _finish)."""
        if self._typing_gen is not None:
            self._update_waiting = True
            return
        self._update_waiting = False
        self._notch.show(self._ready_version, self)

    def updateNow_(self, sender):
        self._notch.hide()
        if self._ready_dmg and os.path.exists(self._ready_dmg):
            self._install_update(self._ready_dmg, relaunch=True)

    def updateLater_(self, sender):
        # Installs when HumanType quits (applicationWillTerminate_); the next
        # scheduled check shows the panel again if it's still running then.
        self._notch.hide()
        self._set_status(f"HumanType {self._ready_version} installs when you quit.")

    def applicationWillTerminate_(self, notification):
        self._sync_dnd_remap(force_off=True)
        if self._ready_dmg and not self._installing and not self._relaunching \
                and os.path.exists(self._ready_dmg):
            self._install_update(self._ready_dmg, relaunch=False)

    def _showUpdateBadge_(self, info):
        """Main thread: point the version label and menu item at the update."""
        version = info.get("version", "?") if info else "?"
        if hasattr(self, "_version_lbl"):
            self._version_lbl.setStringValue_(
                f"HumanType {APP_VERSION} · version {version} is available")
            self._version_lbl.setTextColor_(NSColor.controlAccentColor())
        if getattr(self, "_update_menu_item", None) is not None:
            self._update_menu_item.setTitle_(f"Install Update {version}…")

    def _setStatusText_(self, text):
        self._set_status(text)

    def _noUpdateFound_(self, _):
        self._set_status(f"You're up to date (v{APP_VERSION}).")

    def _updateCheckFailed_(self, err):
        self._set_status(f"Update check failed: {err}")

    @objc.python_method
    def _install_update(self, dmg, relaunch):
        """Start a self-deleting shell shim that waits for this process to quit,
        mounts the DMG, swaps in the new .app, and (optionally) relaunches."""
        from Foundation import NSBundle
        self._installing = True
        app_path = str(NSBundle.mainBundle().bundlePath())
        # Copy beside the old bundle, then swap: ditto *over* the old bundle
        # would merge, leaving stale files inside that break its signature.
        shim = f"""\
#!/bin/bash
# HumanType auto-updater shim — runs after the app quits
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done
sleep 0.5
MNT=$(hdiutil attach "{dmg}" -nobrowse -readonly 2>/dev/null | grep -o '/Volumes/.*' | head -1)
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
rm -f "{dmg}"
{'sleep 0.5; open "$APP"' if relaunch else ''}
rm -- "$0"   # self-delete
"""
        shim_path = "/tmp/humantype_updater.sh"
        with open(shim_path, "w") as fh:
            fh.write(shim)
        os.chmod(shim_path, 0o755)
        import subprocess
        subprocess.Popen(["/bin/bash", shim_path], start_new_session=True)
        if relaunch:
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
        item("Show Tutorial", b"showTutorial:")
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
        """A themed button (see ThemeButton). `prominent` makes it the white
        pill, for the one primary action."""
        btn = ThemeButton.buttonWithTitle_target_action_(title, self, sel)
        h = rect.size.height
        btn.setFrame_(rect)
        btn.setFont_(NSFont.systemFontOfSize_weight_(
            size, NSFontWeightSemibold if prominent else NSFontWeightMedium))
        if symbol:
            set_symbol(btn, symbol, only=icon_only, label=title)
        if icon_only:
            btn.setToolTip_(title)
            btn.setTitle_("")
        btn.style(prominent, h / 2 if (prominent or icon_only) else min(10, h / 2))
        return btn

    @objc.python_method
    def _button(self, title, sel, rect, *, accent=False):
        """Compat wrapper used by the shortcut rows."""
        return self._glass_button(title, sel, rect, prominent=accent)

    @objc.python_method
    def _section_header(self, text, parent, x, y, w):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(NSMakeRect(x - 16, y - 2, w, 18))
        lbl.setFont_(NSFont.systemFontOfSize_weight_(13.5, NSFontWeightSemibold))
        lbl.setTextColor_(NSColor.labelColor())
        parent.addSubview_(lbl)

    @objc.python_method
    def _separator(self, parent, x, y, w):
        parent.addSubview_(color_view(NSMakeRect(x, y, w, 1.0), THEME_BORDER))

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
        sl.setCell_(SpectrumSliderCell.alloc().init())
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
        from AppKit import NSAppearance, NSAppearanceNameDarkAqua
        self.window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self.window.center()

        # Layer 0: the dark backdrop with its glow.
        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        root.addSubview_(aurora_view(root.bounds()))
        self.window.setContentView_(root)

        # Pages, then the header above them.
        self._tab_type = self._page(root)
        self._build_type_page(self._tab_type)
        self._tab_snippets = self._page(root)
        self._build_snippets_page(self._tab_snippets)
        self._tab_calibrate = self._page(root)
        self._build_calibrate_page(self._tab_calibrate)
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
        """The header: app mark, name and a status pill on the left; the pages
        as icon + label buttons and a round settings button on the right."""
        y, hh = H - 64, 36
        mark = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "record.circle", "HumanType")
        from AppKit import NSImageView, NSImageSymbolConfiguration
        iv = NSImageView.alloc().initWithFrame_(NSMakeRect(M, y + 8, 20, 20))
        if mark is not None:
            iv.setImage_(mark)
            iv.setSymbolConfiguration_(
                NSImageSymbolConfiguration.configurationWithPointSize_weight_(15, NSFontWeightMedium))
            iv.setContentTintColor_(NSColor.whiteColor())
        root.addSubview_(iv)
        root.addSubview_(self._label("HumanType", NSMakeRect(M + 26, y + 9, 90, 18),
                                     size=13.5, weight=NSFontWeightSemibold))
        # Status pill: Ready / Typing / Paused / Locked.
        chip, cc = glass_panel(NSMakeRect(M + 118, y + 5, 112, 26), radius=13)
        self._chip_dot = color_view(NSMakeRect(11, 10, 6, 6), NSColor.systemGreenColor(), 3)
        cc.addSubview_(self._chip_dot)
        self._chip_lbl = self._label("Ready", NSMakeRect(23, 5, 84, 16), size=11.5,
                                     weight=NSFontWeightMedium)
        cc.addSubview_(self._chip_lbl)
        root.addSubview_(chip)
        self._chip = chip

        self._nav_btns = {}
        x = W - M
        gear = self._glass_button("Settings", b"showTabSettings:",
                                  NSMakeRect(x - hh, y, hh, hh), symbol="gearshape",
                                  icon_only=True, size=14)
        root.addSubview_(gear)
        self._nav_btns["settings"] = gear
        x -= hh + 10
        for title, sym, key, sel in reversed([t for t in TABS if t[2] != "settings"]):
            btn = NSButton.buttonWithTitle_target_action_(title, self, sel)
            btn.setBordered_(False)
            btn.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightMedium))
            set_symbol(btn, sym)
            bw = btn.fittingSize().width + 14
            btn.setFrame_(NSMakeRect(x - bw, y + 4, bw, 28))
            root.addSubview_(btn)
            self._nav_btns[key] = btn
            x -= bw + 6

    @objc.python_method
    def _set_chip(self, text, color):
        if getattr(self, "_chip_lbl", None) is not None:
            self._chip_lbl.setStringValue_(text)
            self._chip_dot.set_color(color)

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
            color=THEME_ACCENT, align=NSTextAlignmentRight)
        c.addSubview_(self._wpm_lbl)
        self._separator(c, 18, 48, cw - 36)
        c.addSubview_(self._label("Realism", NSMakeRect(18, 15, 70, 18),
                                  size=13, weight=NSFontWeightMedium))
        self._realism_seg = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            list(REALISM), 0, self, b"realismChanged:")   # 0 = select-one
        self._realism_seg.setFrame_(NSMakeRect(96, 11, 300, 26))
        self._realism_seg.setSelectedSegmentBezelColor_(_rgb(1, 1, 1, 0.2))
        c.addSubview_(self._realism_seg)
        self._realism_hint = self._label(
            "", NSMakeRect(cw - 160, 15, 142, 18), size=12, gray=True,
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
                                     THEME_ACCENT, pb_h / 2)
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

    # ── Calibrate page ───────────────────────────────────────────────────

    @objc.python_method
    def _build_calibrate_page(self, cp):
        self._page_title(cp, "Calibrate", "Tuned to your hands, not a preset.")
        cw = W - 2 * M
        top = TOP - 76
        self._label_into(cp, "TYPE NATURALLY FOR A MINUTE", NSMakeRect(M + 2, top - 2, cw, 14),
                         size=10.5, weight=NSFontWeightSemibold, color=THEME_ACCENT)
        self._calib_prompt = NSTextField.wrappingLabelWithString_(random.choice(CALIBRATE_PROMPTS))
        self._calib_prompt.setFrame_(NSMakeRect(M + 2, top - 46, cw - 4, 38))
        self._calib_prompt.setFont_(NSFont.systemFontOfSize_(13.5))
        self._calib_prompt.setTextColor_(NSColor.secondaryLabelColor())
        cp.addSubview_(self._calib_prompt)
        ch = 370
        card, cc = glass_panel(NSMakeRect(M, top - 58 - ch, cw, ch), radius=16)
        cp.addSubview_(card)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 12, cw - 24, ch - 24))
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        tv = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        tv.setRichText_(False)
        tv.setDrawsBackground_(False)
        tv.setFont_(NSFont.monospacedSystemFontOfSize_weight_(14, NSFontWeightRegular))
        tv.setTextColor_(NSColor.labelColor())
        tv.setInsertionPointColor_(THEME_ACCENT)
        tv.setTextContainerInset_((6, 8))
        tv.setVerticallyResizable_(True)
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setAutomaticTextReplacementEnabled_(False)
        tv.setAutomaticSpellingCorrectionEnabled_(False)
        scroll.setDocumentView_(tv)
        cc.addSubview_(scroll)
        self._calib_tv = tv
        sy = top - 58 - ch - 46
        self._calib_status = self._label("", NSMakeRect(M + 2, sy, cw - 150, 36), size=12,
                                         color=NSColor.systemGreenColor())
        self._calib_status.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11.5, NSFontWeightRegular))
        self._calib_status.setMaximumNumberOfLines_(2)
        cp.addSubview_(self._calib_status)
        cp.addSubview_(self._glass_button("Start over", b"calibReset:",
                                          NSMakeRect(W - M - 130, sy + 4, 130, 32), size=12))
        self._calib_events = []
        self._calib_monitor = None
        cal = self._calibration()
        self._calib_status.setStringValue_(
            self._calib_summary(cal) if cal else f"Type at least {CALIBRATE_KEYS} keys. "
            "Mistakes and fixes count too.")

    @objc.python_method
    def _label_into(self, parent, text, rect, **kw):
        lbl = self._label(text, rect, **kw)
        parent.addSubview_(lbl)
        return lbl

    @objc.python_method
    def _calibration(self):
        cal = NSUserDefaults.standardUserDefaults().dictionaryForKey_("ht_calibration")
        return dict(cal) if cal else None

    @objc.python_method
    def _calib_summary(self, cal):
        return (f"matched! ~{cal['wpm']} wpm, avg {cal['avg_key']:.2f}s/key, "
                f"{cal['corrections']} corrections.\n\"You\" is now tuned to you, applied immediately.")

    @objc.python_method
    def _calib_listen(self, on):
        """Record key-press timing only while the Calibrate page is showing."""
        if on and self._calib_monitor is None:
            def handler(event):
                if self.window.firstResponder() == self._calib_tv:
                    self._calib_key(time.time(), int(event.keyCode()))
                return event
            self._calib_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, handler)
        elif not on and self._calib_monitor is not None:
            NSEvent.removeMonitor_(self._calib_monitor)
            self._calib_monitor = None

    @objc.python_method
    def _calib_key(self, t, keycode):
        self._calib_events.append((t, keycode))
        n = len(self._calib_events)
        if n < CALIBRATE_KEYS:
            self._calib_status.setStringValue_(f"Keep going… {n}/{CALIBRATE_KEYS} keys")
            return
        if n != CALIBRATE_KEYS and n % 10:
            return                       # refine every 10 keys after the first match
        # The text view updates after the key event, so read it a beat later.
        self.performSelector_withObject_afterDelay_(b"calibApply:", None, 0.05)

    def calibApply_(self, _):
        cal = calibration_from_keys(self._calib_events, str(self._calib_tv.string()))
        if not cal:
            return
        d = NSUserDefaults.standardUserDefaults()
        d.setObject_forKey_(cal, "ht_calibration")
        self._realism_name = "You"
        d.setObject_forKey_("You", "ht_realism")
        d.removeObjectForKey_("ht_typo_override")
        self._apply_wpm(cal["wpm"])
        self._sync_realism()
        self._calib_status.setStringValue_(self._calib_summary(cal))

    def calibReset_(self, sender):
        self._calib_events = []
        self._calib_tv.setString_("")
        self._calib_prompt.setStringValue_(random.choice(CALIBRATE_PROMPTS))
        self._calib_status.setStringValue_(f"Type at least {CALIBRATE_KEYS} keys. "
                                           "Mistakes and fixes count too.")
        self.window.makeFirstResponder_(self._calib_tv)

    # ── Settings page ────────────────────────────────────────────────────

    @objc.python_method
    def _build_settings_panel(self, st):
        self._page_title(st, "Settings", "Tune the typing and set your global shortcuts.")
        y = TOP - 72                     # top of the next section
        cw, row_h = W - 2*M, 35

        def card(title, n_rows):
            """Section header + inset-grouped glass card; returns (content, h)."""
            nonlocal y
            self._section_header(title, st, M + 16, y - 14, 200)
            h = row_h * n_rows
            panel, c = glass_panel(NSMakeRect(M, y - 20 - h, cw, h), radius=18)
            st.addSubview_(panel)
            for i in range(1, n_rows):
                self._separator(c, 16, h - i * row_h, cw - 32)
            y -= 20 + h + 10
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

        c, h = card("Typing", 3)
        self._st_wpm_sl, self._st_wpm_lbl = slider_row(
            c, row(h, 0), "Speed", 10, 200, self._current_wpm,
            lambda v: f"{int(v)} wpm", b"settingsWpmChanged:")
        self._st_countdown_sl, self._st_countdown_lbl = slider_row(
            c, row(h, 1), "Countdown", 2, 20, self._countdown_secs,
            lambda v: f"{int(v)}s", b"settingsCountdownChanged:")
        self._st_countdown_sl.setAltIncrementValue_(1)

        override = NSUserDefaults.standardUserDefaults().floatForKey_("ht_typo_override")
        self._st_typo_sl, self._st_typo_lbl = slider_row(
            c, row(h, 2), "Typo rate", 0.0, 0.15, override,
            _typo_label, b"settingsTypoChanged:")

        c, h = card("Behaviour", 3)
        self._st_auto_paste = switch_row(
            c, row(h, 0), "Auto-paste clipboard text on open",
            self._auto_paste, b"settingsAutoPasteToggled:")
        self._st_strip_fmt = switch_row(
            c, row(h, 1), "Flatten line breaks and extra spaces",
            self._strip_fmt, b"settingsStripFmtToggled:")
        self._st_mash = switch_row(
            c, row(h, 2), "While locked, each key press types the next letter",
            self._mash_mode, b"settingsMashToggled:")

        c, h = card("Shortcuts", 6)
        self.start_stop_rec = shortcut_row(
            c, row(h, 0), "Start / Stop", self.start_stop_binding, b"recordStartStop:")
        self.pause_rec = shortcut_row(
            c, row(h, 1), "Pause / Resume", self.pause_binding, b"recordPause:")
        self.stop_rec = shortcut_row(
            c, row(h, 2), "Stop", self.stop_binding, b"recordStop:")
        self.lock_rec = shortcut_row(
            c, row(h, 3), "Lock keyboard", self.lock_binding, b"recordLock:")
        self.type_paste_rec = shortcut_row(
            c, row(h, 4), "Type clipboard", self.type_paste_binding,
            b"recordTypePaste:")
        self.destruct_rec = shortcut_row(
            c, row(h, 5), "Self-destruct: wipe & quit (hold)", self.destruct_binding,
            b"recordDestruct:")

        self._version_lbl = self._label(
            f"HumanType {APP_VERSION}", NSMakeRect(W - M - 200, TOP - 30, 200, 16), size=11,
            dim=True, align=NSTextAlignmentRight)
        st.addSubview_(self._version_lbl)

    # ── Tab switching ────────────────────────────────────────────────────

    @objc.python_method
    def _show_tab(self, name, animate=True):
        self._active_tab = name
        panels = {"type": self._tab_type,
                  "snippets": self._tab_snippets,
                  "calibrate": self._tab_calibrate,
                  "settings": self._tab_settings}
        for k, p in panels.items():
            p.setHidden_(k != name)
        self._calib_listen(name == "calibrate")
        # The current page's header button is white, the others dimmed.
        for k, btn in self._nav_btns.items():
            if k == "settings":
                btn.layer().setBackgroundColor_(
                    (_rgb(1, 1, 1, 0.16) if k == name else THEME_BUTTON).CGColor())
            else:
                btn.setContentTintColor_(NSColor.whiteColor() if k == name
                                         else _rgb(1, 1, 1, 0.5))

    def showTabType_(self, sender):
        self._show_tab("type")

    def showTabSnippets_(self, sender):
        self._show_tab("snippets")
        self._rebuild_snippet_list(str(self._snip_search.stringValue()))

    def showTabCalibrate_(self, sender):
        self._show_tab("calibrate")
        self.window.makeFirstResponder_(self._calib_tv)

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
        name = list(REALISM)[int(sender.selectedSegment())]
        if name == "You" and not self._calibration():
            self._sync_realism()            # keep the old choice until calibrated
            self.showTabCalibrate_(None)
            self._calib_status.setStringValue_("Type here first: \"You\" learns from your typing.")
            return
        self._realism_name = name
        if name == "You":
            self._apply_wpm(self._calibration()["wpm"])
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
        if self._realism_name == "You" and self._calibration():
            return self._calibration()["typo"]
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

    def settingsMashToggled_(self, sender):
        self._mash_mode = bool(sender.state())
        NSUserDefaults.standardUserDefaults().setBool_forKey_(self._mash_mode, "ht_mash_mode")

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
    def _set_status(self, text, done=False):
        self.status.setStringValue_(text)
        self.status.setTextColor_(NSColor.systemGreenColor() if done
                                  else NSColor.secondaryLabelColor())

    @objc.python_method
    def _set_pause_btn(self, paused):
        self.pause_btn.setTitle_("Resume" if paused else "Pause")
        set_symbol(self.pause_btn, "play.fill" if paused else "pause.fill")

    @objc.python_method
    def _set_running(self, running):
        self._set_chip("Typing" if running else "Ready",
                       THEME_ACCENT if running else NSColor.systemGreenColor())
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

    def showTutorial_(self, sender):
        if self._onboarding is None:
            self._onboarding = Onboarding.alloc().initWithController_(self)
            self._onboarding.show(getattr(self, "_resume_step", None))
            self._resume_step = None
            self._sync_dnd_remap()
        else:
            self.showWindow_(None)

    @objc.python_method
    def _sync_dnd_remap(self, force_off=False):
        """With F6 as the lock key, the moon key sends F6 only while it's in use:
        during a run, while locked, or while the tour is up. The rest of the
        time it's Do Not Disturb as normal."""
        want = (not force_off and self.lock_binding[0] == KEY_F6
                and (self._typing_gen is not None or self._kbd_locked
                     or self._onboarding is not None))
        if want == self._dnd_remapped:
            return
        self._dnd_remapped = want
        args = ["/usr/bin/hidutil", "property", "--matching", '{"Built-In":1}',
                "--set", DND_AS_F6 if want else '{"UserKeyMapping":[]}']
        try:
            if force_off:
                subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            else:
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def obTypePaste_(self, _):
        """Type-clipboard during the tour: types the clipboard into the practice box."""
        text = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
        if self._onboarding is not None and text and str(text).strip():
            self._onboarding.start_practice(str(text)[:400], event="type_paste")

    def start_(self, sender):
        if self._onboarding is not None:
            self._onboarding.start_practice()   # the tour types into its practice box
            return
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

        if not self._can_type():
            self._save_draft()
            if not self._asked_ax:
                # First time: the normal macOS ask. Start goes ahead by itself
                # as soon as the permission is on (no second click needed).
                self._asked_ax = True
                self._prompt_accessibility()
                self._request_post_access()
                self._open_accessibility_settings()
                self._set_status("Switch on HumanType in Accessibility. Typing starts by itself once it's on.")
                self._wait_for_permission(start_when_ready=True)
            else:
                self._permission_help()
            return

        self._run_typing(text, self._countdown_secs)
        self._from_window = True
        self._set_status(f"Click your target box — typing starts in {self._countdown_secs}s…")
        # Stay on screen, above the app being typed into, so Pause and Stop are a
        # click away. Typing only runs while another app is frontmost (typeStep:).
        self.window.setLevel_(NSFloatingWindowLevel)

    def typePasted_(self, text):
        """Type-clipboard shortcut: the target already has focus (that's where
        you pressed it), so type the clipboard text straight away."""
        if self._typing_gen is not None:
            return
        if not self._is_trusted():
            self._set_status("Turn on HumanType in Accessibility, then try again.")
            self._open_accessibility_settings()
            return
        self._cancel_typing_timers()
        text = str(text)
        if self._strip_fmt:
            text = " ".join(text.split())
        self._run_typing(text, 0)
        self._from_window = False
        self._set_status("Typing what you pasted…")

    @objc.python_method
    def _run_typing(self, text, countdown, kb=None):
        """Start a run: countdown seconds first (0 = start right away). `kb` is
        where keystrokes go: the real keyboard unless given (the tour's box)."""
        wpm = self._current_wpm
        mistake_rate = self._typo_rate()

        self.state = ht.TypingState()
        self._kb_counter = _CountingKeyboard(kb or ht._CTypesKeyboard())

        # Build the typing engine + its step generator.  All keystrokes fire on
        # the main run loop (countdownTick: → typeStep:) — TSM requires main thread.
        self.typist = ht.Typist(
            self._kb_counter, self.state, 60.0 / (wpm * 5.0), mistake_rate)
        cal = self._calibration() if self._realism_name == "You" else None
        if cal:                         # your measured rhythm, not a preset
            self.typist.variation = cal["variation"]
            self.typist.pause_rate = cal["pause_rate"]
        self._typing_gen = self.typist.steps(text)
        self._countdown_left = countdown
        # Progress tracking: count how many generator steps the text would take
        # (rough: len(text) * 1.05 for typo overhead)
        self._total_steps = max(1, int(len(text) * 1.05))
        self._done_steps  = 0

        self._set_running(True)
        # While typing, HumanType sits hidden behind the app it types into,
        # which is exactly when App Nap throttles timers; that would make the
        # keystroke cadence lurch. Opt out for the length of the run.
        self._sync_dnd_remap()
        self._activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            NSActivityUserInitiated | NSActivityLatencyCritical, "Typing text")
        if countdown > 0:
            self._set_chip(f"Starts in {countdown}s", NSColor.systemOrangeColor())
            self._schedule(b"countdownTick:", 1.0)
        else:
            # A beat so the shortcut's keys are let go of; held ⌘/⌃/⌥ is
            # stripped from our keys by the tap anyway.
            self._schedule(b"typeStep:", 0.15)

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
        # Two checks: macOS can keep answering "no" to one of them (until a
        # relaunch) while the other already knows the switch is on.
        try:
            from ApplicationServices import AXIsProcessTrusted
            if AXIsProcessTrusted():
                return True
        except Exception:
            return True
        try:
            from Quartz import CGPreflightPostEventAccess
            return bool(CGPreflightPostEventAccess())
        except Exception:
            return False

    @objc.python_method
    def _can_type(self):
        """Permission check used before typing: either macOS says yes, or the
        shortcut tap (which needs the same permission) installs right now."""
        if self._is_trusted():
            return True
        if self._tap is None:
            self._install_global_tap()
        return self._tap is not None

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
    def _request_post_access(self):
        try:
            from Quartz import CGRequestPostEventAccess
            CGRequestPostEventAccess()
        except Exception:
            pass

    @objc.python_method
    def _wait_for_permission(self, start_when_ready, seconds=180):
        """Poll for the permission; when it turns on, carry on (start typing
        if that's what was asked) without another click or prompt."""
        self._perm_wait_token = getattr(self, "_perm_wait_token", 0) + 1
        token, deadline = self._perm_wait_token, time.time() + seconds

        def poll():
            if token != self._perm_wait_token:
                return
            if self._can_type():
                self._set_status("Permission is on. You're good to go.", done=True)
                if start_when_ready and self._typing_gen is None and self._onboarding is None:
                    self.start_(None)
                return
            if time.time() < deadline:
                NotchUpdatePanel._after(1.0, poll)
        NotchUpdatePanel._after(1.0, poll)

    @objc.python_method
    def _permission_help(self):
        """Start was pressed again and macOS still refuses. Usually the switch
        in Settings belongs to an older copy of HumanType (macOS ties it to the
        app's signature), so it shows ON but doesn't apply. Offer the fix."""
        from AppKit import NSAlert
        a = NSAlert.alloc().init()
        a.setMessageText_("HumanType can't type yet")
        a.setInformativeText_(
            "If HumanType is already switched on in Privacy & Security → Accessibility, "
            "macOS is holding a permission from an older copy of the app.\n\n"
            "Fix permission clears that old entry and asks again: switch HumanType on "
            "once more and typing starts by itself.")
        a.addButtonWithTitle_("Fix permission")
        a.addButtonWithTitle_("Reopen HumanType")
        a.addButtonWithTitle_("Cancel")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        r = a.runModal()
        if r == 1000:
            self._fix_permission(start_when_ready=True)
        elif r == 1001:
            self._relaunch()

    @objc.python_method
    def _fix_permission(self, start_when_ready=False):
        """Clear HumanType's own (stale) Accessibility entry and ask afresh, so
        the switch the user turns on belongs to this copy of the app."""
        bid = str(NSBundle.mainBundle().bundleIdentifier() or "com.humantype.app")
        for service in ("Accessibility", "PostEvent"):
            try:
                subprocess.run(["/usr/bin/tccutil", "reset", service, bid],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except Exception:
                pass
        self._prompt_accessibility()
        self._request_post_access()
        self._open_accessibility_settings()
        self._set_status("Switch HumanType on again in Accessibility. Typing starts by itself once it's on.")
        self._wait_for_permission(start_when_ready)

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
        self._relaunching = True
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
                            kCGKeyboardEventKeycode, CGEventGetFlags,
                            CGEventSetFlags, kCGEventKeyUp,
                            kCGEventFlagMaskCommand, kCGEventFlagMaskControl,
                            kCGEventFlagMaskAlternate)
        HOLD_MODS = (kCGEventFlagMaskCommand | kCGEventFlagMaskControl
                     | kCGEventFlagMaskAlternate)

        def tap_callback(proxy, etype, event, refcon):
            if etype in (kCGEventTapDisabledByTimeout,
                         kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    CGEventTapEnable(self._tap, True)
                return event
            # Our own injected keystrokes carry a non-zero process id — always let
            # them through (this is what reaches the document while locked). They
            # inherit whatever modifiers are physically held, so if you're holding
            # ⌘/⌃/⌥ for a hotkey mid-typing, the next typed letter would land as
            # ⌘+letter (close tab, select all…). Strip those bits; Shift stays,
            # since that's how capitals are typed.
            pid = CGEventGetIntegerValueField(event, kCGEventSourceUnixProcessID)
            if pid != 0:
                # Only HumanType's own: other apps' shortcuts (Utter's ⌘V paste) keep ⌘.
                flags = int(CGEventGetFlags(event))
                if pid == os.getpid() and flags & HOLD_MODS:
                    CGEventSetFlags(event, flags & ~HOLD_MODS)
                return event
            if etype == kCGEventFlagsChanged:
                # Modifier-only shortcut (self-destruct = ⌃⌥⌘): arm when exactly
                # those are held, fire if they're still held 0.4s later with no
                # other key pressed (so ⌃⌥⌘+letter shortcuts elsewhere don't).
                if self.destruct_binding[0] == ht.KEY_MODS_ONLY and self._monitor is None:
                    held = int(CGEventGetFlags(event)) & ht.MOD_MASK
                    if held == self.destruct_binding[1]:
                        if not self._chord_armed:
                            self._chord_armed = True
                            self.performSelector_withObject_afterDelay_inModes_(
                                b"chordHeld:", None, 0.4, [NSRunLoopCommonModes])
                    else:
                        self._chord_armed = False
                return event          # never swallow modifier changes
            if etype != kCGEventKeyDown:
                return event
            self._chord_armed = False     # a real key: not the modifier-only chord

            if self._monitor is not None:
                return event      # recording a shortcut: let the recorder see it
            autorepeat = bool(CGEventGetIntegerValueField(
                event, kCGKeyboardEventAutorepeat))
            keycode = int(CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode))
            mods = int(CGEventGetFlags(event)) & ht.MOD_MASK
            key = (keycode, mods)
            typing = self._typing_gen is not None and self.state is not None
            # The tour is up: type-clipboard types into its practice box (the
            # window is frontmost, so the normal path below would skip it).
            if (self._onboarding is not None and key == self.type_paste_binding
                    and not typing):
                if not autorepeat:
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"obTypePaste:", None, 0.0, [NSRunLoopCommonModes])
                return None

            # Type clipboard: in another app, type the clipboard text instead of
            # pasting it (⌘V is untouched). In HumanType's own window, or when the
            # clipboard isn't text (images, files), the keys pass through.
            if (key == self.type_paste_binding and not typing
                    and not NSApplication.sharedApplication().isActive()):
                if autorepeat:
                    return None
                pasted = NSPasteboard.generalPasteboard().stringForType_(
                    NSPasteboardTypeString)
                if pasted and str(pasted).strip():
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"typePasted:", pasted, 0.0, [NSRunLoopCommonModes])
                    return None

            if key == self.destruct_binding:
                if not autorepeat:
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"selfDestruct:", None, 0.0, [NSRunLoopCommonModes])
                return None
            if not autorepeat:
                # Lock toggle — always active, checked first so it works even
                # while the keyboard is locked.
                if key == self.lock_binding and self._lock_live():
                    self._kbd_locked = not self._kbd_locked
                    self._mash_hold_on_unlock()
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
                        self.performSelector_withObject_afterDelay_inModes_(
                            b"typeStep:", None, 0.0, [NSRunLoopCommonModes])
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
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"typeStep:", None, 0.0, [NSRunLoopCommonModes])
                    return None
            else:
                # Autorepeat from HOLDING a hotkey: don't re-fire the action, but
                # still swallow the repeats so they can't leak into the page.
                if key == self.start_stop_binding or (
                        key == self.lock_binding and self._lock_live()):
                    return None
                if typing and key in (self.pause_binding, self.stop_binding):
                    return None

            # Keyboard lock: swallow every remaining physical key — including
            # autorepeat from a held/stray key — so a paw or a leaning finger
            # can't leak into the document during a take. Mouse is unaffected, so
            # you can always click the menu-bar lock to unlock.
            if self._kbd_locked:
                if self._mash_mode and typing:
                    # Key-by-key: this press types the next letter instead.
                    self.performSelector_withObject_afterDelay_inModes_(
                        b"mashStep:", None, 0.0, [NSRunLoopCommonModes])
                if self._onboarding is not None and not autorepeat:
                    self._onboarding.blocked_key()
                return None

            return event

        self._tap_callback = tap_callback
        tap = CGEventTapCreate(
            kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
            CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
            | CGEventMaskBit(kCGEventFlagsChanged),
            tap_callback, None)
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
        self._set_chip("Paused" if self.state.paused else "Typing",
                       NSColor.systemOrangeColor() if self.state.paused else THEME_ACCENT)
        if self._onboarding is not None:
            self._onboarding.event("pause" if self.state.paused else "resume")

    def toggleLock_(self, sender):
        self._kbd_locked = not self._kbd_locked
        self._mash_hold_on_unlock()
        self.syncLockUI_(None)

    @objc.python_method
    def _mash_hold_on_unlock(self):
        """Key-by-key mode: unlocking pauses the run in the same instant, so no
        letter types itself before your keyboard is back."""
        if (self._mash_mode and not self._kbd_locked and self.state is not None
                and self._typing_gen is not None and not self.state.paused):
            self.state.toggle_pause()

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
            self._set_chip("Locked", NSColor.systemOrangeColor())
        else:
            running = self._typing_gen is not None
            self._set_chip("Typing" if running else "Ready",
                           THEME_ACCENT if running else NSColor.systemGreenColor())
        if self._kbd_locked:
            self._set_status("Keyboard locked — physical keys ignored.")
            if hasattr(self, "_lock_lbl"):
                self._lock_lbl.setStringValue_("Keyboard locked · unlock from the menu bar")
        else:
            self._set_status("Keyboard unlocked.")
            if hasattr(self, "_lock_lbl"):
                self._lock_lbl.setStringValue_("")
        if (self._mash_mode and self.state is not None and self._typing_gen is not None
                and not self._kbd_locked and self.state.paused):
            self._set_pause_btn(True)   # key-by-key off: the run is held, type freely
            self._set_status("Paused. Lock again to keep typing key by key.")
            self._set_chip("Paused", NSColor.systemOrangeColor())
        self._sync_dnd_remap()
        if self._onboarding is not None:
            self._onboarding.event("lock" if self._kbd_locked else "unlock")

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
            self._set_chip(f"Starts in {self._countdown_left}s", NSColor.systemOrangeColor())
            self._schedule(b"countdownTick:", 1.0)
        else:
            self._set_status("Typing…")
            self._set_chip("Typing", THEME_ACCENT)
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
        if self.state.paused or (self._kbd_locked and self._mash_mode):
            # Paused, or key-by-key: each key press advances it (mashStep:).
            self._schedule(b"typeStep:", 0.12)
            return
        # HumanType itself is frontmost (the countdown ran out before you clicked
        # away, or you clicked its window): keys would land in its own text box,
        # so wait until another app has focus again.
        # (The tour's practice run writes into its own box, so it never waits.)
        if not self._practice_run and NSApplication.sharedApplication().isActive():
            if not self._holding:
                self._holding = True
                self._set_status("Click where you want it typed…")
            self._schedule(b"typeStep:", 0.12)
            return
        if self._holding:
            self._holding = False
            self._set_status("Typing…")
        if self._target_app is None:
            self._target_app = NSWorkspace.sharedWorkspace().frontmostApplication()
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
        self._target_app = None
        self._holding = False
        self._practice_run = False
        self.window.setLevel_(NSNormalWindowLevel)
        self._sync_dnd_remap()
        if self._activity is not None:
            NSProcessInfo.processInfo().endActivity_(self._activity)
            self._activity = None
        # Leave the always-on tap running; just reset state.
        self.state = None
        # Reset permission flag so if Accessibility is later revoked the user
        # gets the prompt again rather than a silent auto-relaunch.
        self._asked_ax = False
        self._set_running(False)
        if finished:
            # Stay open and say so, ready for the next one: the old text is
            # selected, so pasting replaces it.
            self._set_status("Complete. Paste your next text and press Start.", done=True)
            self.text_view.setSelectedRange_((0, len(str(self.text_view.string()))))
            if self._from_window and not self.window.isVisible():
                self.window.orderFrontRegardless()
        else:
            self._set_status("Stopped.")
        if self._onboarding is not None:
            self._onboarding.event("done" if finished else "stop")
        if self._update_waiting:
            self._presentUpdate_(None)

    def mashStep_(self, _):
        """Key-by-key mode: a physical key press while locked types exactly one
        keystroke of the text (the engine's next step, typos and fixes included)."""
        if self._typing_gen is None or self.state is None or self.state.stopped:
            return
        if not self._practice_run and NSApplication.sharedApplication().isActive():
            return                      # never type into HumanType's own window
        if self.state.paused:
            self.state.toggle_pause()
            self._set_pause_btn(False)
        if getattr(self, "_countdown_left", 0) > 0:     # skip the rest of the countdown
            self._cancel_typing_timers()
            self._countdown_left = 0
            self._schedule(b"typeStep:", 0.12)
        self._set_status("Typing key by key…")
        self._set_chip("Key by key", THEME_ACCENT)
        # One press = one keystroke: step past pause-only steps until the
        # engine has actually typed (or erased) something.
        before = self._kb_counter.count
        for _ in range(64):
            try:
                next(self._typing_gen)
            except StopIteration:
                self._finish(True)
                return
            except Exception:
                self._finish(False)
                return
            self._done_steps += 1
            if self._kb_counter.count > before:
                break
        self._update_progress(self._done_steps, self._total_steps)

    def togglePause_(self, sender):
        if self.state is None or self._typing_gen is None:
            return
        self.state.toggle_pause()
        self._set_pause_btn(self.state.paused)
        self._set_status("Paused." if self.state.paused else "Typing…")
        self._set_chip("Paused" if self.state.paused else "Typing",
                       NSColor.systemOrangeColor() if self.state.paused else THEME_ACCENT)
        if self._onboarding is not None:
            self._onboarding.event("pause" if self.state.paused else "resume")
        # Resumed from HumanType's own window: hand focus back to the app being
        # typed into, so typing carries on there (typeStep: waits until it has it).
        if (not self.state.paused and self._target_app is not None
                and NSApplication.sharedApplication().isActive()):
            self._target_app.activateWithOptions_(0)

    def stop_(self, sender):
        if self.state is not None:
            self.state.stop()
            self._schedule(b"typeStep:", 0.0)
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

    @objc.python_method
    def _lock_live(self):
        """The lock key works in every app when it has ⌘/⌃/⌥. A bare key (Tab)
        only acts during a run (countdown included) or while locked, so it
        stays an ordinary key the rest of the time."""
        return bool(self.lock_binding[1] & HOTKEY_MODS or self._typing_gen is not None
                    or self._kbd_locked or self._onboarding is not None)

    def recordLock_(self, sender):
        self._record("lock", self.lock_rec)

    def recordTypePaste_(self, sender):
        self._record("type_paste", self.type_paste_rec)

    def recordDestruct_(self, sender):
        self._record("destruct", self.destruct_rec)

    def chordHeld_(self, _):
        if self._chord_armed:
            self._chord_armed = False
            ob = self._onboarding
            if ob is not None and ob.current() == "destruct":
                ob.demo_destruct()      # the tour's own page: show it, don't quit
            else:
                self.selfDestruct_(None)

    def selfDestruct_(self, sender):
        """Self-destruct: stop any run, give the keyboard back, wipe everything
        unsaved (the text box and any saved draft) and quit at once. Saved
        snippets and settings stay; a downloaded update is not installed."""
        if self.state is not None:
            self.state.stop()
        self._cancel_typing_timers()
        self._typing_gen = None
        self._kbd_locked = False
        self.text_view.setString_("")
        d = NSUserDefaults.standardUserDefaults()
        d.removeObjectForKey_("draft_text")
        d.removeObjectForKey_("ht_resume_tour")
        d.synchronize()
        self._relaunching = True        # skip install-on-quit: leave now
        self._teardown_global_tap()
        NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def _bindings(self):
        return {"start_stop": self.start_stop_binding, "pause": self.pause_binding,
                "stop": self.stop_binding, "lock": self.lock_binding,
                "type_paste": self.type_paste_binding,
                "destruct": self.destruct_binding}

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
            # Start/Stop and Lock are live in every app all the time, so a bare
            # key (Tab, a letter) would stop working everywhere. Require ⌘/⌃/⌥.
            if which in ALWAYS_ON and not mods & HOTKEY_MODS:
                button.setTitle_(prev_label)
                self._set_status("Add ⌘, ⌃ or ⌥ — this shortcut works in every app.")
                return None
            # Reject a combo already assigned to another action — otherwise the
            # tap's first-match-wins order would silently disable one of them.
            # (Start/Stop and Stop may share one: ⌘S starts and stops.)
            share = {"start_stop", "stop"}
            taken = {k: v for k, v in self._bindings().items()
                     if k != which and not (which in share and k in share)}
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
            elif which == "type_paste":
                self.type_paste_binding = binding
            elif which == "destruct":
                self.destruct_binding = binding
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
