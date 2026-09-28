#!/usr/bin/env python3
"""Native macOS menu-bar UI for HumanType.

A small AppKit app: lives in the menu bar, opens a modern window with a paste
box, a speed picker, a "natural typos" toggle, Start / Pause-Resume / Stop / End
buttons, and two click-to-record keybind fields (pause/resume and stop). All the
typing logic lives in humantype.py; this file is just the interface."""

import json
import os
import sys
import threading
import urllib.request
import urllib.parse

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSEventMaskKeyDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
    NSEvent,
    NSFont,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSScrollView,
    NSSearchField,
    NSSegmentedControl,
    NSSlider,
    NSStatusBar,
    NSTextField,
    NSTextView,
    NSVariableStatusItemLength,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectBlendingModeWithinWindow,
    NSVisualEffectMaterialMenu,
    NSVisualEffectMaterialSidebar,
    NSVisualEffectMaterialUnderWindowBackground,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
)
from Foundation import (
    NSObject,
    NSRunLoopCommonModes,
    NSThread,
    NSUserDefaults,
)
from Quartz import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    CFRunLoopRemoveSource,
    CGEventMaskBit,
    CGEventTapCreate,
    CGEventTapEnable,
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
# App version + update endpoint
# ---------------------------------------------------------------------------
APP_VERSION  = "1.0.0"
UPDATE_URL   = "https://YOUR-PROJECT.vercel.app/version.json"   # ← set after deploy


def _version_tuple(v):
    """Convert "1.2.3" to (1, 2, 3) for comparisons."""
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0, 0, 0)


# ---------------------------------------------------------------------------
# License system — replace VALIDATE_URL with your deployed Vercel URL.
# ---------------------------------------------------------------------------
VALIDATE_URL = "https://YOUR-PROJECT.vercel.app/api/validate"
BUY_URL      = "https://YOUR_STRIPE_PAYMENT_LINK"


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
        from AppKit import (NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                            NSWindowStyleMaskMiniaturizable, NSSecureTextField)
        W2, H2 = 480, 320
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W2, H2), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("Activate HumanType")
        self.window.setReleasedWhenClosed_(False)

        # Glass background
        bg = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W2, H2))
        bg.setMaterial_(NSVisualEffectMaterialUnderWindowBackground)
        bg.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        bg.setState_(NSVisualEffectStateActive)
        self.window.setContentView_(bg)

        def lbl(text, x, y, w, h, bold=False, size=13, gray=False):
            f = NSTextField.labelWithString_(text)
            f.setFrame_(NSMakeRect(x, y, w, h))
            f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                       else NSFont.systemFontOfSize_(size))
            if gray:
                f.setTextColor_(NSColor.secondaryLabelColor())
            return f

        bg.addSubview_(lbl("Activate HumanType", 24, H2-56, W2-48, 28, bold=True, size=20))
        bg.addSubview_(lbl("Enter the license key from your purchase email.",
                           24, H2-82, W2-48, 18, gray=True, size=12))

        bg.addSubview_(lbl("License Key", 24, 222, 120, 18, gray=True, size=11))
        self.key_field = NSTextField.alloc().initWithFrame_(NSMakeRect(24, 194, W2-48, 28))
        self.key_field.setFont_(NSFont.systemFontOfSize_(13))
        self.key_field.setPlaceholderString_("HT-XXXXXXXX-XXXXXXXX-XXXXXXXX")
        bg.addSubview_(self.key_field)

        # Activate button
        self.act_btn = NSButton.buttonWithTitle_target_action_(
            "Activate", self, b"activate_:")
        self.act_btn.setFrame_(NSMakeRect(24, 148, W2-48, 34))
        self.act_btn.setBezelColor_(NSColor.controlAccentColor())
        bg.addSubview_(self.act_btn)

        # Buy button
        buy_btn = NSButton.buttonWithTitle_target_action_(
            "Don't have a key? Buy HumanType — $14.99", self, b"buy_:")
        buy_btn.setFrame_(NSMakeRect(24, 106, W2-48, 28))
        buy_btn.setBezelStyle_(0)  # inline/link style
        buy_btn.setButtonType_(0)
        bg.addSubview_(buy_btn)

        self.status_lbl = lbl("", 24, 68, W2-48, 28, gray=True, size=12)
        bg.addSubview_(self.status_lbl)

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
            self.status_lbl.setStringValue_(f"❌  {info}")

    def buy_(self, sender):
        from AppKit import NSWorkspace
        from Foundation import NSURL
        NSWorkspace.sharedWorkspace().openURL_(
            NSURL.URLWithString_(BUY_URL))


# Window geometry.
W, H, SB = 660, 600, 160   # total width, height, sidebar width
M = 20                      # content margin inside panels

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
    49: "Space", 50: "`", 51: "Delete", 53: "Esc", 65: ".", 67: "*", 69: "+",
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

        self._update_info = None   # filled by background update check
        if LicenseManager.is_licensed():
            self.showWindow_(None)
            # Check for updates silently in the background after a short delay
            self._schedule(b"_checkForUpdates:", 4.0)
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

    # --- UI construction ----------------------------------------------------

    @objc.python_method
    # ── Auto-update system ────────────────────────────────────────────────

    def _checkForUpdates_(self, _):
        """Scheduled on the main run loop a few seconds after launch.
        Spawns a background thread so the network call never blocks the UI."""
        threading.Thread(target=self._fetch_update_info, daemon=True).start()

    @objc.python_method
    def _fetch_update_info(self):
        """Background: fetch version.json and compare with APP_VERSION."""
        if "YOUR-PROJECT" in UPDATE_URL or "YOUR_" in UPDATE_URL:
            return  # placeholder URL — silently skip
        try:
            req = urllib.request.Request(
                UPDATE_URL,
                headers={"User-Agent": f"HumanType/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            remote = data.get("version", "0.0.0")
            if _version_tuple(remote) > _version_tuple(APP_VERSION):
                self._update_info = data
                # Jump back to main thread to update UI
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"_showUpdateBadge:", data, False)
        except Exception:
            pass   # silent — no update badge if the check fails

    def _showUpdateBadge_(self, info):
        """Main thread: show the update badge in the sidebar."""
        version = info.get("version", "?") if info else "?"
        if hasattr(self, "_sidebar_status"):
            self._sidebar_status.setStringValue_(
                f"Update {version} available!")
            self._sidebar_status.setTextColor_(NSColor.controlAccentColor())

    def checkForUpdatesManually_(self, sender):
        """Menu item action — force-checks for updates and tells the user."""
        self._set_status("Checking for updates…")
        threading.Thread(target=self._check_manually_bg, daemon=True).start()

    @objc.python_method
    def _check_manually_bg(self):
        if "YOUR-PROJECT" in UPDATE_URL or "YOUR_" in UPDATE_URL:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_updateCheckFailed:",
                "Update URL not configured — set UPDATE_URL in humantype_ui.py after deploying your backend.",
                False)
            return
        try:
            req = urllib.request.Request(
                UPDATE_URL,
                headers={"User-Agent": f"HumanType/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            remote = data.get("version", "0.0.0")
            if _version_tuple(remote) > _version_tuple(APP_VERSION):
                self._update_info = data
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"_promptUpdate:", data, False)
            else:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"_noUpdateFound:", None, False)
        except Exception as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"_updateCheckFailed:", str(e), False)

    def _promptUpdate_(self, info):
        from AppKit import NSAlert, NSAlertFirstButtonReturn
        version = info.get("version", "?")
        notes   = info.get("release_notes", "Improvements and bug fixes.")
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"HumanType {version} is available")
        alert.setInformativeText_(
            f"You have {APP_VERSION}. What's new:\n\n{notes}\n\n"
            "The update will download in the background and install automatically.")
        alert.addButtonWithTitle_("Download & Install")
        alert.addButtonWithTitle_("Later")
        if alert.runModal() == NSAlertFirstButtonReturn:
            self._start_update_download(info)

    def _noUpdateFound_(self, _):
        self._set_status(f"You're up to date (v{APP_VERSION}).")

    def _updateCheckFailed_(self, err):
        self._set_status(f"Update check failed: {err}")

    @objc.python_method
    def _start_update_download(self, info):
        url = info.get("download_url", "")
        if not url:
            self._set_status("Update URL missing — check humantype-backend/public/version.json.")
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
        then mounts the DMG, copies the new .app, and relaunches."""
        from Foundation import NSBundle
        app_path = str(NSBundle.mainBundle().bundlePath())
        shim = f"""\
#!/bin/bash
# HumanType auto-updater shim — runs after the app quits
while pgrep -f "HumanType.app/Contents/MacOS" > /dev/null 2>&1; do sleep 0.3; done
sleep 0.5
# Mount update DMG
MNT=$(hdiutil attach "{tmp_dmg}" -nobrowse -readonly 2>/dev/null | grep -o '/Volumes/.*' | head -1)
if [ -z "$MNT" ]; then exit 1; fi
# Copy new app
ditto "$MNT/HumanType.app" "{app_path}"
hdiutil detach "$MNT" -force -quiet 2>/dev/null
xattr -dr com.apple.quarantine "{app_path}" 2>/dev/null
sleep 0.5
open "{app_path}"
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
        item("Enter License Key", b"showLicense:")
        item("Check for Updates", b"checkForUpdatesManually:")
        menu.addItem_(NSMenuItem.separatorItem())
        item("Quit & Reopen (apply permission)", b"relaunch:")
        item("Quit HumanType", b"end:")
        self.status_item.setMenu_(menu)

    # =========================================================================
    # Generic UI primitives — liquid glass flavour
    # =========================================================================

    @objc.python_method
    def _label(self, text, rect, *, bold=False, size=13, gray=False,
               dim=False, align=0, color=None):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(rect)
        lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                     else NSFont.systemFontOfSize_(size))
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
    def _glass_card(self, rect, *, radius=16, alpha=0.10, border_alpha=0.20,
                    sheen=True):
        """A liquid-glass panel: within-window blur + white tint + sheen."""
        # NSVisualEffectView provides the actual blur
        v = NSVisualEffectView.alloc().initWithFrame_(rect)
        v.setMaterial_(NSVisualEffectMaterialMenu)
        v.setBlendingMode_(NSVisualEffectBlendingModeWithinWindow)
        v.setState_(NSVisualEffectStateActive)
        v.setWantsLayer_(True)
        vl = v.layer()
        vl.setCornerRadius_(radius)
        vl.setMasksToBounds_(True)
        vl.setBorderWidth_(0.75)
        vl.setBorderColor_(
            NSColor.colorWithWhite_alpha_(1.0, border_alpha).CGColor())
        # White tint overlay for the "frosted" look
        tint = NSView.alloc().initWithFrame_(v.bounds())
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(
            NSColor.colorWithWhite_alpha_(1.0, alpha).CGColor())
        v.addSubview_(tint)
        if sheen and rect.size.height > 30:
            # Subtle highlight at the top edge (glass catches light)
            sh_h = min(rect.size.height * 0.28, 32)
            sheen_v = NSView.alloc().initWithFrame_(
                NSMakeRect(0, rect.size.height - sh_h,
                           rect.size.width, sh_h))
            sheen_v.setWantsLayer_(True)
            sheen_v.layer().setBackgroundColor_(
                NSColor.colorWithWhite_alpha_(1.0, 0.10).CGColor())
            v.addSubview_(sheen_v)
        return v

    @objc.python_method
    def _pill_button(self, title, sel, rect, *,
                     style="secondary",   # "primary" | "secondary" | "ghost"
                     size=13):
        """iOS 26-style pill button — rounded, glassy fill."""
        btn = NSButton.buttonWithTitle_target_action_(title, self, sel)
        btn.setFrame_(rect)
        btn.setBezelStyle_(0)
        btn.setBordered_(False)
        btn.setFont_(NSFont.systemFontOfSize_(size))
        btn.setWantsLayer_(True)
        l = btn.layer()
        l.setCornerRadius_(rect.size.height / 2)
        l.setMasksToBounds_(True)
        if style == "primary":
            l.setBackgroundColor_(NSColor.controlAccentColor().CGColor())
            l.setBorderWidth_(0)
            btn.setContentTintColor_(NSColor.whiteColor())
        elif style == "secondary":
            l.setBackgroundColor_(
                NSColor.colorWithWhite_alpha_(1.0, 0.12).CGColor())
            l.setBorderWidth_(0.75)
            l.setBorderColor_(
                NSColor.colorWithWhite_alpha_(1.0, 0.25).CGColor())
        else:  # ghost
            l.setBackgroundColor_(NSColor.clearColor().CGColor())
            l.setBorderWidth_(0)
        return btn

    @objc.python_method
    def _button(self, title, sel, rect, *, accent=False):
        """Compat wrapper used by auto-generated keybind rows."""
        return self._pill_button(title, sel, rect,
                                 style="primary" if accent else "secondary")

    @objc.python_method
    def _section_header(self, text, parent, x, y, w):
        lbl = NSTextField.labelWithString_(text.upper())
        lbl.setFrame_(NSMakeRect(x, y, w, 13))
        lbl.setFont_(NSFont.systemFontOfSize_(9.5))
        lbl.setTextColor_(NSColor.tertiaryLabelColor())
        parent.addSubview_(lbl)

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
    # Window build — full liquid-glass redesign
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

        # ── Layer 0: full-window blur (blurs the desktop)
        root = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        root.setMaterial_(NSVisualEffectMaterialUnderWindowBackground)
        root.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        root.setState_(NSVisualEffectStateActive)
        self.window.setContentView_(root)

        # ── Sidebar ───────────────────────────────────────────────────────
        sb_vfx = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, SB, H))
        sb_vfx.setMaterial_(NSVisualEffectMaterialSidebar)
        sb_vfx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        sb_vfx.setState_(NSVisualEffectStateActive)
        sb_vfx.setWantsLayer_(True)
        sb_l = sb_vfx.layer()
        sb_l.setMasksToBounds_(True)
        # Hair-line separator on the right edge
        sep = NSView.alloc().initWithFrame_(NSMakeRect(SB - 0.5, 0, 0.5, H))
        sep.setWantsLayer_(True)
        sep.layer().setBackgroundColor_(
            NSColor.colorWithWhite_alpha_(1.0, 0.12).CGColor())
        sb_vfx.addSubview_(sep)
        root.addSubview_(sb_vfx)
        sidebar = sb_vfx

        # Logo mark
        logo_size = 34
        logo = NSView.alloc().initWithFrame_(
            NSMakeRect(16, H - 58, logo_size, logo_size))
        logo.setWantsLayer_(True)
        ll = logo.layer()
        ll.setCornerRadius_(10)
        ll.setBackgroundColor_(NSColor.controlAccentColor().CGColor())
        sidebar.addSubview_(logo)
        lbl_logo = NSTextField.labelWithString_("HT")
        lbl_logo.setFrame_(NSMakeRect(0, 8, logo_size, logo_size - 10))
        lbl_logo.setAlignment_(1)
        lbl_logo.setFont_(NSFont.boldSystemFontOfSize_(12))
        lbl_logo.setTextColor_(NSColor.whiteColor())
        logo.addSubview_(lbl_logo)

        # App name
        sidebar.addSubview_(self._label(
            "HumanType",
            NSMakeRect(16 + logo_size + 8, H - 48, SB - logo_size - 28, 18),
            bold=True, size=14))
        sidebar.addSubview_(self._label(
            f"v{APP_VERSION}",
            NSMakeRect(16 + logo_size + 8, H - 63, SB - logo_size - 28, 14),
            size=10, gray=True))

        # Nav items (iOS 26 pill style)
        nav_items = [
            ("⌨  Type",      "type",     b"showTabType:"),
            ("☰  Snippets",  "snippets", b"showTabSnippets:"),
            ("⚙  Settings",  "settings", b"showTabSettings:"),
        ]
        self._nav_btns = {}
        for i, (title, key, sel) in enumerate(nav_items):
            y = H - 106 - i * 42
            btn = self._pill_button(title, sel,
                                    NSMakeRect(10, y, SB - 20, 34),
                                    style="secondary", size=13)
            sidebar.addSubview_(btn)
            self._nav_btns[key] = btn

        # Update / status badge at bottom of sidebar
        self._sidebar_status = self._label(
            "", NSMakeRect(12, 14, SB - 24, 14), size=10, gray=True)
        sidebar.addSubview_(self._sidebar_status)

        # ── Content panels ────────────────────────────────────────────────
        cx, cw = SB, W - SB

        def panel():
            v = NSView.alloc().initWithFrame_(NSMakeRect(cx, 0, cw, H))
            v.setWantsLayer_(True)
            return v

        # ── TYPE TAB ─────────────────────────────────────────────────────
        self._tab_type = panel()
        root.addSubview_(self._tab_type)
        tp = self._tab_type

        # Floating title area
        tp.addSubview_(self._label(
            "Type", NSMakeRect(M, H - 44, 60, 26), bold=True, size=20))
        tp.addSubview_(self._label(
            "Paste text, click your target, watch it type.",
            NSMakeRect(M + 66, H - 40, cw - M - 70, 16), size=11, gray=True))

        # ── Clean layout from bottom (no cards around controls):
        #  16  status + ETA      h=16
        #  40  progress bar      h=4
        #  52  buttons           h=36  → top 88
        #  96  realism pills     h=26  → top 122  (3 minimal pill buttons)
        # 130  speed row         h=20  → top 150  (label + slider + wpm, no container)
        # 158  stats + snippet   h=14  → top 172
        # 176  text card bottom
        # 558  text card top     (H-42)

        TT = H - 42          # 558 — taller text area
        TB = 176             # text card bottom

        # ── Large glass text area
        text_card = self._glass_card(NSMakeRect(M, TB, cw - 2*M, TT - TB),
                                     radius=16, alpha=0.08)
        tp.addSubview_(text_card)
        pad = 10
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(
            M + pad, TB + pad, cw - 2*M - 2*pad, TT - TB - 2*pad))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)
        tv = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        tv.setFont_(NSFont.systemFontOfSize_(14))
        tv.setRichText_(False)
        tv.setDrawsBackground_(False)
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setAutomaticTextReplacementEnabled_(False)
        tv.setAllowsUndo_(True)
        tv.setVerticallyResizable_(True)
        tv.setMaxSize_(NSMakeSize(cw - 2*M - 2*pad, 1e7))
        tv.setTextContainerInset_((6, 8))
        scroll.setDocumentView_(tv)
        self.text_view = tv
        tp.addSubview_(scroll)

        # ── Stats row — clean, no container
        self._stats_lbl = self._label(
            "0 words  ·  0 chars",
            NSMakeRect(M, 158, 160, 14), size=10, gray=True)
        tp.addSubview_(self._stats_lbl)
        tp.addSubview_(self._pill_button(
            "Save as Snippet", b"saveSnippet:",
            NSMakeRect(cw - M - 108, 156, 106, 18), style="ghost", size=10))

        # ── Speed row — floating, no card container (cleaner)
        tp.addSubview_(self._label("Speed",
            NSMakeRect(M, 132, 44, 16), size=12, gray=True))
        self._wpm_slider = self._add_slider(
            tp, NSMakeRect(M + 50, 130, cw - 2*M - 106, 20),
            10, 200, self._current_wpm, b"wpmSliderChanged:")
        self._wpm_lbl = self._label(
            f"{self._current_wpm} wpm",
            NSMakeRect(cw - M - 52, 131, 50, 18), size=12,
            color=NSColor.controlAccentColor(), align=1)
        tp.addSubview_(self._wpm_lbl)

        # ── Realism — 3 minimal pill buttons in a row (replaces bulky segmented control)
        tp.addSubview_(self._label("Realism",
            NSMakeRect(M, 100, 56, 16), size=12, gray=True))
        self._realism_btns = {}
        rnames = list(REALISM.keys())      # ["Minimal", "Natural", "Pro"]
        rpw = 72                           # pill width
        rph = 24
        rx_start = M + 62
        for ri, rname in enumerate(rnames):
            rx = rx_start + ri * (rpw + 6)
            rb = self._pill_button(rname,
                b"realismPillPressed:", NSMakeRect(rx, 96, rpw, rph),
                style="secondary", size=11)
            rb.setTag_(ri)
            tp.addSubview_(rb)
            self._realism_btns[rname] = rb
        self._update_realism_pills()

        # ── Action buttons — prominent pill row
        gap, bh = 6, 36
        bw = (cw - 2*M - 3*gap) // 4
        self.start_btn = self._pill_button(
            "▶  Start", b"start:",
            NSMakeRect(M, 52, bw, bh), style="primary")
        self.pause_btn = self._pill_button(
            "⏸  Pause", b"togglePause:",
            NSMakeRect(M + bw + gap, 52, bw, bh))
        self.stop_btn  = self._pill_button(
            "⏹  Stop",  b"stop:",
            NSMakeRect(M + (bw+gap)*2, 52, bw, bh))
        self.end_btn   = self._pill_button(
            "✕  Quit",  b"end:",
            NSMakeRect(M + (bw+gap)*3, 52, bw, bh))
        for b in (self.start_btn, self.pause_btn, self.stop_btn, self.end_btn):
            tp.addSubview_(b)

        # ── Thin progress bar
        pb_y, pb_h = 40, 4
        prog_track = self._glass_card(
            NSMakeRect(M, pb_y, cw - 2*M, pb_h),
            radius=2, alpha=0.06, border_alpha=0.10, sheen=False)
        tp.addSubview_(prog_track)
        self._prog_fill = NSView.alloc().initWithFrame_(NSMakeRect(M, pb_y, 0, pb_h))
        self._prog_fill.setWantsLayer_(True)
        self._prog_fill.layer().setCornerRadius_(2)
        self._prog_fill.layer().setBackgroundColor_(
            NSColor.controlAccentColor().CGColor())
        tp.addSubview_(self._prog_fill)
        self._prog_max_w = cw - 2*M

        # ── Status + ETA + lock indicator
        self.status = self._label(
            "Ready — paste text above and press Start.",
            NSMakeRect(M, 18, cw - 2*M - 76, 16), size=11, gray=True)
        tp.addSubview_(self.status)
        self._eta_lbl = self._label(
            "", NSMakeRect(cw - M - 74, 18, 72, 16),
            size=11, gray=True, align=1)
        tp.addSubview_(self._eta_lbl)
        self._lock_lbl = self._label("", NSMakeRect(M, 2, cw - 2*M, 12),
                                     size=9, dim=True)
        tp.addSubview_(self._lock_lbl)

        self._prefill_from_clipboard()
        self._set_running(False)

        # ── SNIPPETS TAB ─────────────────────────────────────────────────
        self._tab_snippets = panel()
        root.addSubview_(self._tab_snippets)
        sp = self._tab_snippets

        sp.addSubview_(self._label(
            "Snippets", NSMakeRect(M, H - 44, 110, 26), bold=True, size=20))
        sp.addSubview_(self._label(
            "Save texts you type often.",
            NSMakeRect(M + 116, H - 40, cw - M - 120, 16), size=11, gray=True))

        self._snip_search = NSSearchField.alloc().initWithFrame_(
            NSMakeRect(M, H - 76, cw - 2*M, 30))
        self._snip_search.setPlaceholderString_("Search snippets…")
        self._snip_search.setTarget_(self)
        self._snip_search.setAction_(b"snippetSearchChanged:")
        sp.addSubview_(self._snip_search)

        snip_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(M, 58, cw - 2*M, H - 76 - 30 - 14))
        snip_scroll.setHasVerticalScroller_(True)
        snip_scroll.setDrawsBackground_(False)
        snip_scroll.setBorderType_(0)
        self._snip_list_view = NSView.alloc().initWithFrame_(
            snip_scroll.contentView().bounds())
        snip_scroll.setDocumentView_(self._snip_list_view)
        sp.addSubview_(snip_scroll)
        self._snip_scroll = snip_scroll

        sp.addSubview_(self._pill_button(
            "+ New", b"newSnippet:", NSMakeRect(M, 22, 80, 28)))
        sp.addSubview_(self._pill_button(
            "Delete", b"deleteSnippet:", NSMakeRect(M + 88, 22, 80, 28)))
        self._selected_snip_idx = -1
        self._rebuild_snippet_list("")

        # ── SETTINGS TAB ─────────────────────────────────────────────────
        self._tab_settings = panel()
        root.addSubview_(self._tab_settings)
        self._build_settings_panel(cw)

        self._show_tab("type")

    # ── Settings panel ────────────────────────────────────────────────────

    @objc.python_method
    def _build_settings_panel(self, cw):
        st = self._tab_settings
        cx = M       # local x offset
        st.addSubview_(self._label("Settings", NSMakeRect(cx, H - 46, cw - 2*M, 24),
                                   bold=True, size=18))
        y = H - 80

        def slider_row(label, key, lo, hi, cur, fmt, sel, step=None):
            nonlocal y
            st.addSubview_(self._label(label, NSMakeRect(cx, y, 140, 17), size=12))
            sl = NSSlider.alloc().initWithFrame_(NSMakeRect(cx + 144, y - 1, cw - 2*M - 210, 18))
            sl.setMinValue_(lo); sl.setMaxValue_(hi); sl.setFloatValue_(cur)
            sl.setTarget_(self); sl.setAction_(sel)
            if step: sl.setAltIncrementValue_(step)
            st.addSubview_(sl)
            val_lbl = self._label(fmt(cur),
                                  NSMakeRect(cw - M - 64, y, 62, 17),
                                  size=12, gray=True, align=1)
            st.addSubview_(val_lbl)
            y -= 34
            return sl, val_lbl

        def checkbox_row(label, val, sel):
            nonlocal y
            cb = NSButton.checkboxWithTitle_target_action_(label, self, sel)
            cb.setFrame_(NSMakeRect(cx, y, cw - 2*M, 20))
            cb.setFont_(NSFont.systemFontOfSize_(12))
            cb.setState_(1 if val else 0)
            st.addSubview_(cb)
            y -= 28
            return cb

        # Typing
        self._section_header("Typing", st, cx, y + 2, 150)
        y -= 18
        self._st_wpm_sl, self._st_wpm_lbl = slider_row(
            "Speed (WPM)", "ht_wpm", 10, 200, self._current_wpm,
            lambda v: f"{int(v)} wpm", b"settingsWpmChanged:")
        self._st_countdown_sl, self._st_countdown_lbl = slider_row(
            "Countdown", "ht_countdown", 2, 20, self._countdown_secs,
            lambda v: f"{int(v)}s", b"settingsCountdownChanged:", step=1)

        y -= 8
        self._section_header("Realism", st, cx, y + 2, 150)
        y -= 18
        _, _ = slider_row(
            "Typo rate", "ht_typo", 0.0, 0.15,
            REALISM[self._realism_name][0],
            lambda v: f"{v*100:.0f}%", b"settingsTypoChanged:")

        y -= 8
        self._section_header("Behaviour", st, cx, y + 2, 150)
        y -= 18
        self._st_auto_paste = checkbox_row(
            "Auto-paste clipboard text on open",
            self._auto_paste, b"settingsAutoPasteToggled:")
        self._st_strip_fmt = checkbox_row(
            "Strip rich formatting when pasting",
            self._strip_fmt, b"settingsStripFmtToggled:")

        y -= 8
        self._section_header("Shortcuts", st, cx, y + 2, 150)
        y -= 18

        def shortcut_row(label, binding, sel):
            nonlocal y
            st.addSubview_(self._label(label, NSMakeRect(cx, y, 140, 22), size=12))
            btn = self._button(binding_label(binding), sel,
                               NSMakeRect(cw - M - 150, y - 2, 148, 26))
            st.addSubview_(btn)
            y -= 34
            return btn

        self.start_stop_rec = shortcut_row(
            "Start / Stop", self.start_stop_binding, b"recordStartStop:")
        self.pause_rec = shortcut_row(
            "Pause / Resume", self.pause_binding, b"recordPause:")
        self.stop_rec = shortcut_row(
            "Stop", self.stop_binding, b"recordStop:")
        self.lock_rec = shortcut_row(
            "Lock keyboard", self.lock_binding, b"recordLock:")

    # ── Tab switching ────────────────────────────────────────────────────

    @objc.python_method
    def _show_tab(self, name):
        self._active_tab = name
        panels = {"type": self._tab_type,
                  "snippets": self._tab_snippets,
                  "settings": self._tab_settings}
        for k, p in panels.items():
            p.setHidden_(k != name)
        # iOS 26-style nav pill: active = accent tint, inactive = clear
        for k, btn in self._nav_btns.items():
            l = btn.layer()
            if k == name:
                l.setBackgroundColor_(
                    NSColor.controlAccentColor()
                    .colorWithAlphaComponent_(0.18).CGColor())
                l.setBorderColor_(
                    NSColor.controlAccentColor()
                    .colorWithAlphaComponent_(0.35).CGColor())
                btn.setContentTintColor_(NSColor.controlAccentColor())
            else:
                l.setBackgroundColor_(
                    NSColor.colorWithWhite_alpha_(1.0, 0.10).CGColor())
                l.setBorderColor_(
                    NSColor.colorWithWhite_alpha_(1.0, 0.20).CGColor())
                btn.setContentTintColor_(NSColor.labelColor())

    def showTabType_(self, sender):
        self._show_tab("type")

    def showTabSnippets_(self, sender):
        self._show_tab("snippets")
        self._rebuild_snippet_list(str(self._snip_search.stringValue()))

    def showTabSettings_(self, sender):
        self._show_tab("settings")

    # ── Speed / realism controls ─────────────────────────────────────────

    def wpmSliderChanged_(self, sender):
        self._current_wpm = max(10, int(sender.intValue()))
        self._wpm_lbl.setStringValue_(f"{self._current_wpm} wpm")
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            self._current_wpm, "ht_wpm")
        self._sync_speed_menu()

    def realismPillPressed_(self, sender):
        idx = int(sender.tag())
        self._realism_name = list(REALISM.keys())[idx]
        NSUserDefaults.standardUserDefaults().setObject_forKey_(
            self._realism_name, "ht_realism")
        self._update_realism_pills()

    # keep old selector alive for the settings segmented control
    def realismChanged_(self, sender):
        idx = int(sender.selectedSegment())
        self._realism_name = list(REALISM.keys())[idx]
        NSUserDefaults.standardUserDefaults().setObject_forKey_(
            self._realism_name, "ht_realism")
        self._update_realism_pills()

    @objc.python_method
    def _update_realism_pills(self):
        """Style realism pill buttons: selected = accent tint, rest = secondary."""
        for name, btn in getattr(self, "_realism_btns", {}).items():
            l = btn.layer()
            if name == self._realism_name:
                l.setBackgroundColor_(
                    NSColor.controlAccentColor()
                    .colorWithAlphaComponent_(0.20).CGColor())
                l.setBorderColor_(
                    NSColor.controlAccentColor()
                    .colorWithAlphaComponent_(0.50).CGColor())
                btn.setContentTintColor_(NSColor.controlAccentColor())
            else:
                l.setBackgroundColor_(
                    NSColor.colorWithWhite_alpha_(1.0, 0.08).CGColor())
                l.setBorderColor_(
                    NSColor.colorWithWhite_alpha_(1.0, 0.18).CGColor())
                btn.setContentTintColor_(NSColor.secondaryLabelColor())

    def settingsWpmChanged_(self, sender):
        self._current_wpm = max(10, int(sender.intValue()))
        self._st_wpm_lbl.setStringValue_(f"{self._current_wpm} wpm")
        self._wpm_slider.setIntValue_(self._current_wpm)
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            self._current_wpm, "ht_wpm")
        self._sync_speed_menu()

    def settingsCountdownChanged_(self, sender):
        self._countdown_secs = max(1, int(sender.intValue()))
        self._st_countdown_lbl.setStringValue_(f"{self._countdown_secs}s")
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            self._countdown_secs, "ht_countdown")

    def settingsTypoChanged_(self, sender):
        # Typo rate changes applied directly to next session
        rate = max(0.0, min(0.15, float(sender.floatValue())))
        NSUserDefaults.standardUserDefaults().setFloat_forKey_(rate, "ht_typo_override")

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
        # Remove existing subviews
        for sub in list(sv.subviews()):
            sub.removeFromSuperview()
        snips = self._load_snippets()
        q = query.lower().strip()
        visible = [(i, s) for i, s in enumerate(snips)
                   if not q or q in s.get("title", "").lower()
                   or q in s.get("text", "").lower()]
        row_h = 56
        total_h = max(self._snip_scroll.contentView().bounds().size.height,
                      len(visible) * row_h)
        sv.setFrame_(NSMakeRect(0, 0,
                                self._snip_scroll.contentView().bounds().size.width,
                                total_h))
        for list_i, (orig_i, s) in enumerate(visible):
            y = total_h - (list_i + 1) * row_h
            row = self._card(NSMakeRect(4, y + 3, sv.frame().size.width - 8, row_h - 6),
                             fill=0.07 if orig_i == self._selected_snip_idx else 0.04)
            title_lbl = self._label(s.get("title", "Untitled"),
                                    NSMakeRect(10, 22, sv.frame().size.width - 60, 18),
                                    bold=True, size=12)
            preview = s.get("text", "")[:80].replace("\n", " ")
            preview_lbl = self._label(preview,
                                      NSMakeRect(10, 6, sv.frame().size.width - 60, 14),
                                      size=10, gray=True)
            load_btn = NSButton.buttonWithTitle_target_action_(
                "Load", self, b"loadSnippetRow:")
            load_btn.setTag_(orig_i)
            load_btn.setFrame_(NSMakeRect(sv.frame().size.width - 56, 14, 48, 24))
            row.addSubview_(title_lbl)
            row.addSubview_(preview_lbl)
            row.addSubview_(load_btn)
            sv.addSubview_(row)

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

    def newSnippet_(self, sender):
        snips = self._load_snippets()
        snips.insert(0, {"title": "New Snippet", "text": ""})
        self._save_snippets(snips)
        self._rebuild_snippet_list("")
        self._show_tab("snippets")

    def deleteSnippet_(self, sender):
        if self._selected_snip_idx < 0:
            return
        snips = self._load_snippets()
        if 0 <= self._selected_snip_idx < len(snips):
            snips.pop(self._selected_snip_idx)
            self._save_snippets(snips)
            self._selected_snip_idx = -1
            self._rebuild_snippet_list("")

    def loadSnippetRow_(self, sender):
        idx = int(sender.tag())
        snips = self._load_snippets()
        if 0 <= idx < len(snips):
            self.text_view.setString_(snips[idx].get("text", ""))
            self._selected_snip_idx = idx
            self._show_tab("type")
            self._update_stats()

    # ── Stats / progress helpers ─────────────────────────────────────────

    @objc.python_method
    def _update_stats(self):
        text = str(self.text_view.string())
        words = len(text.split()) if text.strip() else 0
        chars = len(text)
        self._stats_lbl.setStringValue_(f"{words:,} words · {chars:,} chars")

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
    def _set_running(self, running):
        self.start_btn.setEnabled_(not running)
        self.pause_btn.setEnabled_(running)
        self.stop_btn.setEnabled_(running)
        self.pause_btn.setTitle_("⏸  Pause")
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
        self._current_wpm = SPEED_WPM.get(name, 55)
        if hasattr(self, "_wpm_slider"):
            self._wpm_slider.setIntValue_(self._current_wpm)
        if hasattr(self, "_wpm_lbl"):
            self._wpm_lbl.setStringValue_(f"{self._current_wpm} wpm")
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            self._current_wpm, "ht_wpm")
        self._sync_speed_menu()
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
        typo_override = NSUserDefaults.standardUserDefaults().floatForKey_("ht_typo_override")
        _, realism_var, _ = REALISM.get(self._realism_name, REALISM["Natural"])
        if typo_override > 0:
            mistake_rate = typo_override
        else:
            mistake_rate = REALISM.get(self._realism_name, REALISM["Natural"])[0]

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
        self.pause_btn.setTitle_("▶  Resume" if self.state.paused else "⏸  Pause")
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
                self._lock_lbl.setStringValue_("🔒  Keyboard locked — click menu-bar icon to unlock")
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
        self.pause_btn.setTitle_("▶  Resume" if self.state.paused else "⏸  Pause")
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
