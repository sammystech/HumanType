import sys, os, subprocess
sys.path.insert(0, "/Users/samuelmittman/humantype-app")
from AppKit import *; from Foundation import *; from Quartz import *
import objc, faulthandler; faulthandler.dump_traceback_later(60, exit=True)
import humantype as ht, humantype_ui as ui
CMD, ALT = kCGEventFlagMaskCommand, kCGEventFlagMaskAlternate
res = []
def check(n, ok, info=""): res.append(ok); print(("PASS " if ok else "FAIL ") + n, info, flush=True)
def ev(kc, mods, pid=0):
    e = CGEventCreateKeyboardEvent(None, kc, True); CGEventSetFlags(e, mods)
    CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, pid); return e
typed = []
class KB:
    def __init__(self, *a): pass
    def type(self, ch): typed.append(ch)
    def press(self, k): pass
    def release(self, k): pass
ht._CTypesKeyboard = KB
class FakeApp:
    @classmethod
    def sharedApplication(cls): return cls
    @classmethod
    def isActive(cls): return True
    @classmethod
    def activateIgnoringOtherApps_(cls, f): pass
class WinProxy:
    def __init__(self, w): self.__dict__["_w"] = w
    def isKeyWindow(self): return False
    def __getattr__(self, k): return getattr(self._w, k)
def mapping():
    out = subprocess.run(["hidutil", "property", "--matching", '{"Built-In":1}', "--get", "UserKeyMapping"], capture_output=True, text=True).stdout
    return "4294967451" in out
class T(NSObject):
    def go_(self, _):
        c = self.c = ui.AppController.alloc().init()
        NSApplication.sharedApplication().setDelegate_(c); c.applicationDidFinishLaunching_(None); _cb = c._tap_callback; c._teardown_global_tap(); c._tap_callback = _cb; c._tap = 'isolated'; c._install_global_tap = lambda: None  # ignore the real keyboard
        c.window = WinProxy(c.window); ui.NSApplication = FakeApp
        c._is_trusted = lambda: True; c._apply_wpm(200)
        c.start_stop_binding, c.lock_binding, c.stop_binding = (1, CMD), (97, 0), (44, ALT)
        c.pause_binding, c.type_paste_binding = ht.DEFAULT_PAUSE_BINDING, (9, CMD | ALT)
        self.cb = lambda kc, m: c._tap_callback(None, kCGEventKeyDown, ev(kc, m), None)
        c.showTutorial_(None); self.ob = c._onboarding
        self.wait(b"a:", 0.6)
    @objc.python_method
    def wait(self, sel, t): self.performSelector_withObject_afterDelay_(sel, None, t)
    def a_(self, _):
        c, ob = self.c, self.ob
        check("moon key sends F6 while tour is up", mapping())
        ob.next_(None); ob.next_(None); ob.next_(None)
        check("hint shows in empty box", not ob.practice_hint.isHidden() and "watch it type" in str(ob.practice_hint.stringValue()))
        check("⌘S swallowed on Start step", ob.current() == "start" and self.cb(1, CMD) is None)
        self.wait(b"b:", 0.6)
    def b_(self, _):
        c, ob = self.c, self.ob
        check("⌘S started a real run in the practice box", c._typing_gen is not None and c._practice_run, repr(str(ob.practice_tv.string())))
        self.wait(b"c:", 2.0)
    def c_(self, _):
        c, ob = self.c, self.ob
        check("advanced to Pause step", ob.current() == "pause", ob.current())
        if c._typing_gen is None: ob.start_practice()
        self.wait(b"c2:", 0.3)
    def c2_(self, _):
        c = self.c
        check("` pauses", self.cb(50, 0) is None and c.state.paused)
        self.n = len(str(self.ob.practice_tv.string())); self.wait(b"e:", 0.5)
    def e_(self, _):
        check("nothing typed while paused", len(str(self.ob.practice_tv.string())) == self.n)
        self.cb(50, 0); self.wait(b"f:", 2.2)
    def f_(self, _):
        c, ob = self.c, self.ob
        check("resume -> Lock step", ob.current() == "lock", ob.current())
        self.wait(b"g:", 0.8)
    def g_(self, _):
        c, ob = self.c, self.ob
        check("F6 locks", self.cb(97, 0) is None and c._kbd_locked)
        for k in (0, 2, 3, 4): self.cb(k, 0)
        check("mashed keys blocked + counted", self.cb(5, 0) is None and ob._blocked == 5, str(ob._blocked))
        self.cb(97, 0); self.wait(b"h:", 2.2)
    def h_(self, _):
        c, ob = self.c, self.ob
        check("F6 unlocks -> Stop step", not c._kbd_locked and ob.current() == "stop", ob.current())
        self.wait(b"i:", 0.9)
    def i_(self, _):
        c, ob = self.c, self.ob
        if c._typing_gen is None: ob.start_practice()
        check("Stop step lights ⌘S", ob.KEY_STEPS["stop"] == ("start_stop",))
        check("⌘S stops the run", self.cb(1, CMD) is None)
        self.wait(b"j:", 0.5)
    def j_(self, _):
        check("run stopped", self.c._typing_gen is None)
        self.wait(b"k:", 2.0)
    def k_(self, _):
        c, ob = self.c, self.ob
        check("-> Type clipboard step", ob.current() == "type_paste", ob.current())
        pb = NSPasteboard.generalPasteboard(); self.saved = pb.stringForType_(NSPasteboardTypeString)
        pb.clearContents(); pb.setString_forType_("copied text", NSPasteboardTypeString)
        del typed[:]
        check("⌘⌥V swallowed", self.cb(9, CMD | ALT) is None)
        self.wait(b"l:", 1.5)
    def l_(self, _):
        c, ob = self.c, self.ob
        check("⌘⌥V typed the clipboard into the box", str(ob.practice_tv.string()).startswith("cop"), repr(str(ob.practice_tv.string())))
        pb = NSPasteboard.generalPasteboard(); pb.clearContents()
        if self.saved is not None: pb.setString_forType_(self.saved, NSPasteboardTypeString)
        if c.state: c.state.stop()
        self.wait(b"m:", 2.5)
    def m_(self, _):
        c, ob = self.c, self.ob
        check("-> Self-destruct step", ob.current() == "destruct", ob.current())
        self.quit_called = False
        def fake_quit(sender=None): self.quit_called = True
        c.selfDestruct_ = fake_quit
        self.wait(b"m1:", 1.2)
    def m1_(self, _):
        c, ob = self.c, self.ob
        check("practice run auto-started on the page", c._practice_run and len(str(ob.practice_tv.string())) > 0)
        e = CGEventCreate(None); CGEventSetType(e, kCGEventFlagsChanged)
        CGEventSetFlags(e, kCGEventFlagMaskControl | kCGEventFlagMaskAlternate | kCGEventFlagMaskCommand)
        CGEventSetIntegerValueField(e, kCGEventSourceUnixProcessID, 0)
        c._tap_callback(None, kCGEventFlagsChanged, e, None)
        self.wait(b"m2:", 0.7)
    def m2_(self, _):
        c, ob = self.c, self.ob
        check("holding ⌃⌥⌘ wipes the practice box", str(ob.practice_tv.string()) == "" and c._typing_gen is None)
        check("…but doesn't quit during the tour", not self.quit_called)
        self.wait(b"m3:", 2.9)
    def m3_(self, _):
        c, ob = self.c, self.ob
        check("-> Done step", ob.current() == "done", ob.current())
        ob.next_(None)
        check("tour closed, lock off", c._onboarding is None and not c._kbd_locked)
        self.wait(b"n:", 0.6)
    def n_(self, _):
        check("moon key back to Do Not Disturb", not mapping())
        NSUserDefaults.standardUserDefaults().removeObjectForKey_("ht_onboarded")
        print(f"{sum(res)}/{len(res)} passed", flush=True); os._exit(0)
app = NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
t = T.alloc().init(); t.performSelector_withObject_afterDelay_(b"go:", None, 0.3); app.run()
