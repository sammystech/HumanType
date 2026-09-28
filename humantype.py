#!/usr/bin/env python3
"""humantype — paste text, then watch it get typed into whatever box has focus.

Each character is sent as its own real keystroke, so an app's edit/revision
history (Google Docs, etc.) records incremental typing rather than one big paste.
The cadence is deliberately uneven and includes natural-looking corrections so
the pattern reads like a person, not a metronome.

While it types you can use global hotkeys (work in any app):
    `            pause / resume typing  (the backtick key, left of "1")
    Ctrl+Alt+Q   stop and quit
    Ctrl-C       (in the terminal) abort
The pause key is swallowed, so tapping it never drops a stray character into
your document, and the script ignores the keys it types itself.

macOS note: this sends real keystrokes AND listens for the hotkeys, so the first
run will prompt you to grant your terminal app two permissions under
System Settings > Privacy & Security:
    - Accessibility       (to send keystrokes)
    - Input Monitoring    (to detect the hotkeys)
Grant both, then run again.

Dependency:  pip install pynput
"""

import argparse
import ctypes
import random
import subprocess
import sys
import threading
import time

try:
    from pynput.keyboard import Controller, Key, Listener
except ImportError:
    sys.exit(
        "Missing dependency 'pynput'. Install it with:\n\n    pip install pynput\n"
    )

# macOS Quartz bits: inside the listener these let us read a key event's
# details so we can (a) ignore the keystrokes the script injects itself and
# (b) swallow the pause key so it never lands in your document.
from Quartz import (
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGEventKeyDown,
    kCGEventSourceUnixProcessID,
    kCGKeyboardEventAutorepeat,
    kCGKeyboardEventKeycode,
)

# US-layout virtual key codes.
KEY_BACKTICK = 50   # the ` / ~ key, just left of "1"
KEY_Q = 12
KEY_S = 1
KEY_L = 37

# The modifier bits we care about. CGEvent flag masks share the same bit values
# as AppKit's NSEventModifierFlags, so the UI's key recorder can hand us masks
# in this exact space.
MOD_MASK = (kCGEventFlagMaskControl | kCGEventFlagMaskAlternate
            | kCGEventFlagMaskCommand | kCGEventFlagMaskShift)

# A "binding" is a (keycode, modifier_mask) tuple. mask == 0 means "no modifiers".
DEFAULT_PAUSE_BINDING = (KEY_BACKTICK, 0)
DEFAULT_STOP_BINDING = (KEY_Q, kCGEventFlagMaskControl | kCGEventFlagMaskAlternate)
# Ctrl+Alt+S — starts typing when idle, stops it when running.
DEFAULT_START_STOP_BINDING = (KEY_S, kCGEventFlagMaskControl | kCGEventFlagMaskAlternate)
# Ctrl+Alt+L — locks/unlocks the physical keyboard (auto-typed keys still flow).
DEFAULT_LOCK_BINDING = (KEY_L, kCGEventFlagMaskControl | kCGEventFlagMaskAlternate)


# Rough QWERTY neighbors, used to make simulated typos look plausible.
NEIGHBORS = {
    "a": "sqwz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wrsdf",
    "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn",
    "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "iklp",
    "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy",
    "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
    "z": "asx",
}


class _CTypesKeyboard:
    """Posts keystrokes using correct virtual key codes via ctypes.

    The root problem with pynput's Controller in a menu-bar (LSUIElement) app:
    - Characters IN pynput's mapping (only unshifted chars like 'a','b','1')
      are posted with the right VK and work fine.
    - Characters NOT in pynput's mapping (ALL uppercase letters, '!','@', etc.)
      fall back to CGEventKeyboardSetUnicodeString via PyObjC, which silently
      fails — posts vk=0 instead, so every char becomes 'a'/'A'.
    - Web browsers (Chrome/Safari for Google Docs) use the virtual key code to
      determine the character, not the Unicode string — so vk=0 always = 'a'.

    Fix: build a FULL char→(vk, needs_shift) map from UCKeyTranslate (the same
    low-level layout engine macOS itself uses) so every character — capitals,
    punctuation, special chars — gets the exact right virtual key code. For
    chars not on any physical key (emoji, curly quotes, etc.) we fall back to
    CGEventKeyboardSetUnicodeString via ctypes (works in native apps)."""

    _cg = None
    _cf = None

    @classmethod
    def _load_libs(cls):
        if cls._cg is not None:
            return
        cg = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cg.CGEventCreateKeyboardEvent.restype  = ctypes.c_void_p
        cg.CGEventCreateKeyboardEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
        cg.CGEventKeyboardSetUnicodeString.restype  = None
        cg.CGEventKeyboardSetUnicodeString.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint16)]
        cg.CGEventPost.restype  = None
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cls._cg = cg
        cf = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cf.CFRelease.restype  = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cls._cf = cf

    # Virtual key codes for special keys (stable across all keyboard layouts).
    _VK_SPECIAL = {Key.enter: 36, Key.tab: 48, Key.backspace: 51}

    # UCKeyTranslate modifier state value for Shift (Carbon shiftKey=0x200 >> 8).
    _SHIFT_MOD = 2

    def __init__(self):
        self._load_libs()
        # Build char → (vk, needs_shift) from the current keyboard layout.
        # Uses UCKeyTranslate so it works correctly on any layout.
        # Must be called on the main thread (TSM requirement).
        from pynput._util.darwin import keycode_context, keycode_to_string
        self._char_map = {}
        with keycode_context() as ctx:
            for vk in range(128):
                for mod in (0, self._SHIFT_MOD):
                    ch = keycode_to_string(ctx, vk, mod)
                    if ch and len(ch) == 1 and ch not in self._char_map:
                        self._char_map[ch] = (vk, mod != 0)

    def _post_key(self, vk, down):
        """Post one raw key event. Never touches CGEventSetFlags — altering flags
        strips internal bits (e.g. 0x20000000) that macOS needs for correct
        routing. Modifier state is managed by posting real shift key events
        (kCGEventFlagsChanged) instead."""
        ev = self._cg.CGEventCreateKeyboardEvent(None, vk, down)
        if ev:
            self._cg.CGEventPost(0, ev)   # 0 = kCGHIDEventTap
            self._cf.CFRelease(ev)

    # VK for the physical Shift key — posting key-down/up creates a
    # kCGEventFlagsChanged event, which updates the system modifier state so
    # subsequent character events naturally inherit the correct shift flag.
    _SHIFT_VK = 0x38

    def type(self, ch):
        entry = self._char_map.get(ch)
        if entry is not None:
            vk, shift = entry
            if shift:
                self._post_key(self._SHIFT_VK, True)   # shift down → system state = shift held
            self._post_key(vk, True)                    # char key down (inherits shift state)
            self._post_key(vk, False)                   # char key up
            if shift:
                self._post_key(self._SHIFT_VK, False)   # shift up → system state = no shift
        else:
            # Char not on any physical key — Unicode fallback (works in native
            # apps; web apps depend on their event handling).
            utf16 = ch.encode('utf-16-le')
            n = len(utf16) // 2
            buf = (ctypes.c_uint16 * n).from_buffer_copy(utf16)
            for down in (True, False):
                ev = self._cg.CGEventCreateKeyboardEvent(None, 0, down)
                if ev:
                    self._cg.CGEventKeyboardSetUnicodeString(ev, n, buf)
                    self._cg.CGEventPost(0, ev)
                    self._cf.CFRelease(ev)

    def press(self, key):
        if key in self._VK_SPECIAL:
            self._post_key(self._VK_SPECIAL[key], True)

    def release(self, key):
        if key in self._VK_SPECIAL:
            self._post_key(self._VK_SPECIAL[key], False)


class TypingState:
    """Shared pause/stop flags toggled by the hotkey thread, read by the typer."""

    def __init__(self):
        self._resume = threading.Event()
        self._resume.set()  # set == running, cleared == paused
        self._stop = threading.Event()

    def toggle_pause(self):
        if self._stop.is_set():
            return
        if self._resume.is_set():
            self.pause()
        else:
            self.resume()

    def pause(self):
        if not self._stop.is_set():
            self._resume.clear()

    def resume(self):
        self._resume.set()

    @property
    def paused(self):
        return not self._resume.is_set()

    def stop(self):
        self._stop.set()
        self._resume.set()  # unblock the typer so it can exit

    @property
    def stopped(self):
        return self._stop.is_set()

    def wait_while_paused(self):
        self._resume.wait()


class Typist:
    """Types text with human-like cadence: per-word speed bursts, drifting
    tempo, boundary pauses, hesitations, and occasional typos with corrections."""

    def __init__(self, kb, state, base, mistake_rate):
        self.kb = kb
        self.state = state
        self.base = base
        self.mistake = mistake_rate
        self.fatigue = 1.0       # slow random-walk of overall tempo
        self.tempo = 1.0         # phrase-level wave: stretches of fast / slow typing
        self.word_factor = 1.0   # per-word speed multiplier (typing in bursts)

    # --- low-level emission -------------------------------------------------

    def _emit(self, ch):
        if ch == "\n":
            self.kb.press(Key.enter)
            self.kb.release(Key.enter)
        elif ch == "\t":
            self.kb.press(Key.tab)
            self.kb.release(Key.tab)
        else:
            self.kb.type(ch)

    def _backspace_steps(self, count):
        """Generator: delete `count` chars, yielding the wait after each one."""
        for _ in range(count):
            self.kb.press(Key.backspace)
            self.kb.release(Key.backspace)
            yield random.uniform(0.03, 0.09)

    def _sleep(self, seconds):
        # Chunk long waits so a stop is honored quickly.
        end = time.time() + seconds
        while time.time() < end:
            if self.state.stopped:
                return
            time.sleep(min(0.05, end - time.time()))

    # --- timing model -------------------------------------------------------

    def _drift(self):
        self.fatigue = min(1.30, max(0.78, self.fatigue + random.uniform(-0.06, 0.06)))

    def _char_pause(self, ch):
        self._drift()
        d = max(0.0, random.gauss(self.base, self.base * 0.50))
        d *= self.fatigue * self.word_factor * self.tempo
        # Boundary pauses (not scaled — these are cognitive, not motor).
        if ch in ".!?":
            d += random.uniform(0.30, 0.85)
        elif ch in ",;:":
            d += random.uniform(0.12, 0.30)
        elif ch == "\n":
            d += random.uniform(0.20, 0.55)
        elif ch == " ":
            d += random.uniform(0.0, 0.08)
        # Rare mid-stream "thinking" hesitation.
        if random.random() < 0.012:
            d += random.uniform(0.4, 1.4)
        return d

    # --- typo helpers -------------------------------------------------------

    @staticmethod
    def _can_err(ch):
        return ch.isalpha() and ch.lower() in NEIGHBORS

    @staticmethod
    def _neighbor(ch):
        w = random.choice(NEIGHBORS[ch.lower()])
        return w.upper() if ch.isupper() else w

    def _mistake_steps(self, text, i):
        """Generator for one human-like error episode that ends with correct
        text. Emits one keystroke per yield (yielding the wait after it) and
        returns the number of source characters consumed."""
        ch = text[i]
        n = len(text)
        kind = random.choice(
            ["immediate", "immediate", "immediate", "delayed", "transpose", "double"]
        )

        # Swap this char with the next, notice, fix.
        if kind == "transpose" and i + 1 < n and self._can_err(text[i + 1]):
            a, b = ch, text[i + 1]
            self._emit(b); yield random.uniform(0.05, 0.12)
            self._emit(a); yield random.uniform(0.18, 0.45)
            yield from self._backspace_steps(2); yield random.uniform(0.06, 0.18)
            self._emit(a); yield random.uniform(0.05, 0.12)
            self._emit(b); yield self._char_pause(b)
            return 2

        # Type a wrong char, keep going a few letters, then notice and back up.
        if kind == "delayed" and i + 1 < n:
            k = random.randint(1, min(3, n - 1 - i))
            ahead = text[i + 1:i + 1 + k]
            if "\n" in ahead:                       # don't run past a line break
                ahead = ahead[:ahead.index("\n")]
                k = len(ahead)
            if k >= 1:
                self._emit(self._neighbor(ch)); yield random.uniform(0.06, 0.14)
                for c in ahead:
                    self._emit(c); yield random.uniform(0.06, 0.16)
                yield random.uniform(0.25, 0.70)          # delayed realization
                yield from self._backspace_steps(k + 1); yield random.uniform(0.08, 0.20)
                for c in ch + ahead:                      # confident retype
                    self._emit(c); yield random.uniform(0.05, 0.12)
                # No extra yield here — the for loop already yielded a motor-
                # delay after the last retype char. Adding a second yield with
                # no keystroke between them caused a visible double-pause stutter
                # after every delayed correction.
                return 1 + k

        # Fat-finger the same key twice, delete one.
        if kind == "double":
            self._emit(ch); yield random.uniform(0.04, 0.10)
            self._emit(ch); yield random.uniform(0.12, 0.30)
            yield from self._backspace_steps(1); yield self._char_pause(ch)
            return 1

        # Immediate substitution: wrong key, quick backspace, right key.
        self._emit(self._neighbor(ch)); yield random.uniform(0.10, 0.28)
        yield from self._backspace_steps(1); yield random.uniform(0.05, 0.16)
        self._emit(ch); yield self._char_pause(ch)
        return 1

    # --- main loop ----------------------------------------------------------

    def steps(self, text):
        """Generator at the heart of typing. Each `next()` emits exactly one
        keystroke (on the thread that advances it) and yields the number of
        seconds to wait before the next keystroke.

        Splitting emission from waiting lets two very different callers share
        one engine: the CLI advances it in a blocking loop (run() below), while
        the macOS UI advances it from a main-run-loop timer — which it MUST,
        because pynput's keystroke calls hit Text Services Manager APIs that
        assert they are on the main thread. Pause/stop are the caller's job;
        check them between advances."""
        i, n = 0, len(text)
        new_word = True
        while i < n:
            ch = text[i]
            if new_word:
                # Phrase-level tempo wave: drifts slowly, so the typing settles
                # into stretches that are noticeably faster or slower.
                self.tempo = min(1.45, max(0.68, self.tempo + random.uniform(-0.10, 0.10)))
                # Per-word burst speed layered on top of the wave.
                self.word_factor = random.uniform(0.75, 1.35)
                r = random.random()
                if r < 0.05:                          # ~1 in 20 words: a real beat
                    yield random.uniform(0.9, 1.9)    #   pause for a second-ish
                elif r < 0.15:                        # shorter pre-word hesitation
                    yield random.uniform(0.25, 0.75)
                new_word = False

            if self.mistake and self._can_err(ch) and random.random() < self.mistake:
                i += (yield from self._mistake_steps(text, i))
            else:
                self._emit(ch)
                yield self._char_pause(ch)
                i += 1

            if i > 0 and text[i - 1] in " \n":
                new_word = True

    def run(self, text):
        """Type `text` synchronously, blocking the calling thread. Returns True
        if finished, False if stopped early. Used by the CLI; the UI drives
        steps() from the main thread instead."""
        gen = self.steps(text)
        while True:
            self.state.wait_while_paused()
            if self.state.stopped:
                return False
            try:
                delay = next(gen)
            except StopIteration:
                return True
            self._sleep(delay)
            if self.state.stopped:
                return False


def build_intercept(state, pause_binding=DEFAULT_PAUSE_BINDING,
                    stop_binding=DEFAULT_STOP_BINDING):
    """Return a darwin_intercept callback for a pynput keyboard Listener.

    `pause_binding` and `stop_binding` are (keycode, modifier_mask) tuples. On a
    matching *physical* key press the pause toggles / typing stops, and the key
    is suppressed (return None) so it never reaches the focused app. Everything
    else passes through untouched."""
    pause_kc, pause_mods = pause_binding
    stop_kc, stop_mods = stop_binding

    def intercept(event_type, event):
        if event_type != kCGEventKeyDown:
            return event
        # Skip the keystrokes this script injects (they carry a process id;
        # physical key presses report 0).
        if CGEventGetIntegerValueField(event, kCGEventSourceUnixProcessID) != 0:
            return event
        # Skip auto-repeat from a held-down key.
        if CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat):
            return event

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        mods = CGEventGetFlags(event) & MOD_MASK

        if keycode == pause_kc and mods == pause_mods:
            state.toggle_pause()
            return None  # swallow — no stray character in your document
        if keycode == stop_kc and mods == stop_mods:
            state.stop()
            return None
        return event

    return intercept


def get_text(args):
    """Resolve the text to type from --file, --clipboard, an argument, or paste."""
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            return fh.read()
    if args.clipboard:
        out = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return out.stdout
    if args.text:
        return args.text
    print("Paste your text below. When done, press Ctrl-D (on a new line) to start.\n")
    return sys.stdin.read()


def countdown(seconds, state):
    """Sleep `seconds`, returning False early if the user hit stop."""
    end = time.time() + seconds
    while time.time() < end:
        if state.stopped:
            return False
        time.sleep(0.05)
    return True


def execute(text, *, wpm, mistake_rate, delay,
            pause_binding=DEFAULT_PAUSE_BINDING, stop_binding=DEFAULT_STOP_BINDING):
    """Start the hotkey listener and type `text`. Returns True if it finished,
    False if the user stopped it. Shared by the CLI and the GUI entry points."""
    base = 60.0 / (wpm * 5.0)
    state = TypingState()
    typist = Typist(_CTypesKeyboard(), state, base, mistake_rate)
    listener = Listener(
        darwin_intercept=build_intercept(state, pause_binding, stop_binding))
    listener.start()
    finished = False
    try:
        if countdown(delay, state):
            finished = typist.run(text)
    except KeyboardInterrupt:
        state.stop()
    finally:
        listener.stop()
    return finished


# --- GUI entry point (used by the .app bundle; no terminal needed) ----------

def _osascript(lines):
    """Run a multi-line AppleScript and return its trimmed stdout."""
    args = ["osascript"]
    for line in lines:
        args += ["-e", line]
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def _notify(message):
    """Show a non-modal macOS notification (does not steal keyboard focus)."""
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{message}" with title "HumanType"'],
        capture_output=True,
    )


SPEEDS = {"Slow (~35 wpm)": 35, "Normal (~55 wpm)": 55, "Fast (~80 wpm)": 80}
_CANCEL = "###CANCEL###"


def run_gui():
    """Double-click entry point: collect text + speed via native dialogs, then
    type into whatever the user focuses during a short countdown."""
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    if not text.strip():
        text = _osascript([
            "try",
            'set r to display dialog "Paste or type the text you want typed, '
            'then click Start." with title "HumanType" default answer "" '
            'buttons {"Cancel", "Start"} default button "Start"',
            "return text returned of r",
            "on error",
            f'return "{_CANCEL}"',
            "end try",
        ])
        if text == _CANCEL or not text.strip():
            return

    choice = _osascript([
        "try",
        'set c to choose from list {"Slow (~35 wpm)", "Normal (~55 wpm)", '
        '"Fast (~80 wpm)"} with title "HumanType" with prompt '
        '"Typing speed" default items {"Normal (~55 wpm)"}',
        f'if c is false then return "{_CANCEL}"',
        "return item 1 of c",
        "on error",
        f'return "{_CANCEL}"',
        "end try",
    ])
    if choice == _CANCEL:
        return
    wpm = SPEEDS.get(choice, 55)

    _notify("Click into your target box now — typing starts in 6 seconds.  "
            "Tap ` to pause, Ctrl+Alt+Q to stop.")
    finished = execute(text, wpm=wpm, mistake_rate=0.02, delay=6)
    _notify("Done." if finished else "Stopped.")


def main():
    p = argparse.ArgumentParser(
        description="Type pasted text into the focused window, human-like."
    )
    p.add_argument("text", nargs="?", help="Text to type (otherwise paste it in).")
    p.add_argument("-c", "--clipboard", action="store_true",
                   help="Use the current clipboard contents.")
    p.add_argument("-f", "--file", help="Read the text from a file.")
    p.add_argument("--wpm", type=float, default=55,
                   help="Approximate average typing speed in words/min (default 55).")
    p.add_argument("--delay", type=float, default=5,
                   help="Countdown seconds before typing starts (default 5).")
    p.add_argument("--mistakes", type=float, default=0.02, metavar="RATE",
                   help="Per-letter chance of a typo+correction (default 0.02).")
    p.add_argument("--perfect", action="store_true",
                   help="Disable typos entirely (still uneven cadence).")
    p.add_argument("--pause-keycode", type=int, default=KEY_BACKTICK, metavar="CODE",
                   help="macOS virtual key code for pause/resume "
                        f"(default {KEY_BACKTICK}, the ` key).")
    args = p.parse_args()

    text = get_text(args)
    if not text:
        sys.exit("Nothing to type.")

    mistake_rate = 0.0 if args.perfect else args.mistakes
    pause_label = "`" if args.pause_keycode == KEY_BACKTICK \
        else f"keycode {args.pause_keycode}"
    print(f"\nTyping {len(text)} chars at ~{args.wpm:g} wpm"
          f"{'' if args.perfect else f', typo rate {mistake_rate:g}'}.")
    print(f"Pause/resume: {pause_label}   |   Stop: Ctrl+Alt+Q")
    print(f"Click into your target text box now — starting in {args.delay:g}s "
          "(Ctrl-C to abort)...")

    finished = execute(text, wpm=args.wpm, mistake_rate=mistake_rate,
                       delay=args.delay, pause_binding=(args.pause_keycode, 0))
    print("\nDone." if finished else "\nStopped.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # Verify imports + object construction inside a bundle, then exit.
        build_intercept(TypingState(), (KEY_BACKTICK, 0))   # must be a tuple
        print("selftest OK")
        sys.exit(0)
    if "--gui" in sys.argv:
        run_gui()
        sys.exit(0)
    main()
